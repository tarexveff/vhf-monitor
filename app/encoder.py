"""
encoder.py — PCM chunker with RMS/squelch computation.

Consumes raw 16-bit signed mono PCM chunks from an asyncio.Queue, computes
signal level, and broadcasts:
  - binary frames: raw IEEE 754 float32 PCM (little-endian) for direct Web
    Audio API playback in the browser — no codec negotiation required.
  - text frames:   JSON signal metadata (rms, db, squelch state).

Squelch threshold is read from the shared config on every frame so UI changes
take effect immediately without restarting.
"""

import array
import asyncio
import logging
import os
import struct
from math import log10, sqrt

from app.config import config

logger = logging.getLogger(__name__)

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


def pcm16_to_float32(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit signed PCM bytes to float32 PCM bytes (range -1.0 to 1.0).

    The browser's Web Audio API works natively with float32 samples, so we
    convert here to avoid any JS typed-array juggling on the client side.
    """
    samples = array.array("h", pcm_bytes)
    floats  = array.array("f", (s / 32768.0 for s in samples))
    return floats.tobytes()


# ---------------------------------------------------------------------------
# Encoder loop
# ---------------------------------------------------------------------------

async def encode_loop(
    queue: asyncio.Queue,
    broadcast_audio_fn,
    broadcast_signal_fn,
) -> None:
    """Continuously read PCM chunks, convert to float32, and broadcast.

    Sends raw float32 PCM as binary WebSocket frames — the browser plays these
    directly via the Web Audio API without any codec decoding step.

    Squelch threshold is taken from ``config.squelch_db`` on every frame so
    changes made via the UI take effect without restarting.
    """
    global current_signal

    sample_rate: int = int(os.environ.get("AUDIO_SAMPLE_RATE", "48000"))
    # 20 ms chunks: 48000 * 0.020 = 960 samples, 2 bytes each = 1920 bytes
    frame_size:  int = sample_rate // 50
    chunk_bytes: int = frame_size * 2   # 16-bit mono

    logger.info(
        "Audio loop ready: sample_rate=%d frame_size=%d chunk_bytes=%d "
        "(sending raw float32 PCM — no Opus encoding)",
        sample_rate, frame_size, chunk_bytes,
    )

    while True:
        pcm: bytes = await queue.get()

        if len(pcm) != chunk_bytes:
            continue

        rms, db = compute_rms(pcm)
        squelch_active = db > config.squelch_db
        current_signal = {"rms": rms, "db": round(db, 1), "squelch": squelch_active}

        # Convert to float32 and broadcast as binary frame
        float32_data = pcm16_to_float32(pcm)
        await broadcast_audio_fn(float32_data)
        await broadcast_signal_fn(current_signal)
