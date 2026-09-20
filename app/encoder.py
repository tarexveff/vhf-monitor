"""
encoder.py — PCM → Opus encoder with RMS/squelch computation.

Consumes raw 16-bit signed mono PCM chunks from an asyncio.Queue,
encodes each chunk to an Opus frame, and broadcasts both the encoded
audio and signal metadata to connected WebSocket clients.

Squelch threshold is read from the shared config on every frame so
UI changes take effect immediately without restarting the encoder.
"""

import array
import asyncio
import logging
import os
from math import log10, sqrt

from app.config import config

logger = logging.getLogger(__name__)

try:
    import opuslib
except ImportError:
    opuslib = None  # type: ignore[assignment]
    logger.error(
        "opuslib is not installed — Opus encoding unavailable. "
        "Install it with: pip install opuslib"
    )

# ---------------------------------------------------------------------------
# Module-level signal state — exported for the /status endpoint
# ---------------------------------------------------------------------------

current_signal: dict = {"rms": 0.0, "db": -96.0, "squelch": False}


# ---------------------------------------------------------------------------
# RMS / dBFS helper
# ---------------------------------------------------------------------------

def compute_rms(pcm_bytes: bytes) -> tuple[float, float]:
    """Return ``(rms, db_fs)`` for a block of 16-bit signed PCM samples."""
    samples = array.array("h", pcm_bytes)
    if not samples:
        return 0.0, -96.0
    rms = sqrt(sum(s * s for s in samples) / len(samples))
    db_fs = 20.0 * log10(rms / 32768.0) if rms > 0 else -96.0
    return rms, db_fs


# ---------------------------------------------------------------------------
# Encoder loop
# ---------------------------------------------------------------------------

async def encode_loop(
    queue: asyncio.Queue,
    broadcast_audio_fn,
    broadcast_signal_fn,
) -> None:
    """Continuously read PCM chunks, encode to Opus, and broadcast results.

    Squelch threshold is taken from ``config.squelch_db`` on every frame so
    changes made via the UI take effect without restarting the encoder.
    """
    global current_signal

    sample_rate: int = int(os.environ.get("AUDIO_SAMPLE_RATE", "48000"))
    frame_size:  int = sample_rate // 50       # 960 samples @ 20 ms
    chunk_bytes: int = frame_size * 1 * 2      # mono, 2 bytes/sample

    if opuslib is None:
        logger.error("encode_loop: opuslib unavailable — loop will not start.")
        return

    encoder = opuslib.Encoder(sample_rate, 1, opuslib.APPLICATION_VOIP)
    logger.info(
        "Opus encoder ready: sample_rate=%d frame_size=%d chunk_bytes=%d",
        sample_rate, frame_size, chunk_bytes,
    )

    while True:
        pcm: bytes = await queue.get()

        if len(pcm) != chunk_bytes:
            continue

        rms, db = compute_rms(pcm)
        # Read squelch from shared config — updated live by UI without restart
        squelch_active = db > config.squelch_db
        current_signal = {"rms": rms, "db": round(db, 1), "squelch": squelch_active}

        try:
            opus_frame: bytes = encoder.encode(pcm, frame_size)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Opus encode error (frame skipped): %s", exc)
            continue

        await broadcast_audio_fn(opus_frame)
        await broadcast_signal_fn(current_signal)
