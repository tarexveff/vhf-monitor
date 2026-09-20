"""
fm_receiver.py — rtl_fm subprocess manager and raw PCM stdout reader.

Starts `rtl_fm` as a managed subprocess, reads raw signed 16-bit PCM audio
from its stdout in fixed-size chunks, and pushes each chunk onto a shared
asyncio.Queue for the audio pipeline.  Watches the shared ReceiverConfig for
setting changes and restarts rtl_fm automatically when freq/gain/ppm change.
"""

import asyncio
import logging

from app.config import config

logger = logging.getLogger(__name__)

# 20 ms of 48 kHz 16-bit mono: 48000 samples/s × 2 bytes/sample × 0.020 s
CHUNK_BYTES = 1920


# ---------------------------------------------------------------------------
# subprocess launcher
# ---------------------------------------------------------------------------

async def launch_rtl_fm() -> asyncio.subprocess.Process:
    """Start *rtl_fm* using current settings from the shared config."""
    cmd = [
        "rtl_fm",
        "-f", str(config.freq),
        "-M", "fm",
        "-s", "200000",
        "-r", str(config.sample_rate),
        "-d", str(config.device_index),
        "-g", str(config.gain),
        "-p", str(config.ppm),
        "-",
    ]
    logger.info("Starting rtl_fm: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    asyncio.ensure_future(_stream_stderr(proc))
    return proc


async def _stream_stderr(proc: asyncio.subprocess.Process) -> None:
    """Log rtl_fm stderr at WARNING so startup errors are always visible."""
    assert proc.stderr is not None
    async for line in proc.stderr:
        logger.warning("rtl_fm stderr: %s", line.decode(errors="replace").rstrip())


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

async def read_fm(queue: asyncio.Queue) -> None:
    """Launch rtl_fm, stream PCM chunks onto *queue*, handle config changes.

    Watches ``config.restart_event`` — when it fires (because freq/gain/ppm
    changed via the UI), the running rtl_fm process is terminated and a new
    one is started with the updated settings.
    """
    proc: asyncio.subprocess.Process | None = None
    back_off = 2.0

    async def _kill(p: asyncio.subprocess.Process) -> None:
        if p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                p.kill()

    try:
        proc = await launch_rtl_fm()

        while True:
            assert proc.stdout is not None

            # Race: either a PCM chunk arrives or a restart is requested.
            # proc.stdout is an asyncio.StreamReader — .read() is a coroutine,
            # so we wrap it directly in a Task (NOT asyncio.to_thread).
            read_task    = asyncio.create_task(proc.stdout.read(CHUNK_BYTES))
            restart_task = asyncio.create_task(config.restart_event.wait())

            done, pending = await asyncio.wait(
                {read_task, restart_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Always cancel the loser
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            # --- Restart requested by config change ---
            if restart_task in done:
                config.restart_event.clear()
                logger.info("Config changed — restarting rtl_fm")
                await _kill(proc)
                back_off = 2.0
                proc = await launch_rtl_fm()
                # Drain stale queue entries so old audio isn't played
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                continue

            # --- PCM chunk read ---
            chunk = read_task.result()

            if not chunk:
                # stdout closed — rtl_fm has exited unexpectedly
                await proc.wait()
                logger.warning(
                    "rtl_fm exited with code %s; restarting in %.0f s",
                    proc.returncode, back_off,
                )
                await asyncio.sleep(back_off)
                back_off = min(back_off * 2, 30.0)
                proc = await launch_rtl_fm()
                continue

            back_off = 2.0
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass  # drop oldest-ish frame rather than block

    except asyncio.CancelledError:
        if proc is not None:
            await _kill(proc)
        raise
