"""
config.py — Shared mutable receiver configuration.

Holds the live settings for rtl_fm and the encoder.  Any coroutine can
update settings via ``apply()``; background tasks watch ``changed`` to
detect when a restart or parameter update is needed.
"""

import asyncio
import os

# ---------------------------------------------------------------------------
# Live config object
# ---------------------------------------------------------------------------

class ReceiverConfig:
    """Thread-safe(ish) receiver settings shared across async tasks."""

    def __init__(self) -> None:
        self.freq:         int   = int(os.environ.get("RTL_FREQ",         "161500000"))
        self.gain:         int   = int(os.environ.get("RTL_GAIN",         "0"))
        self.ppm:          int   = int(os.environ.get("RTL_PPM",          "0"))
        self.sample_rate:  int   = int(os.environ.get("AUDIO_SAMPLE_RATE","48000"))
        self.device_index: int   = int(os.environ.get("RTL_DEVICE_INDEX", "0"))
        self.squelch_db:   float = float(os.environ.get("SQUELCH_DB",     "-40"))

        # Set when any rtl_fm-affecting field changes (freq/gain/ppm/device).
        # read_fm waits on this to know when to restart the subprocess.
        self.restart_event: asyncio.Event = asyncio.Event()

    def apply(self, updates: dict) -> tuple[bool, bool]:
        """Apply *updates* dict to the config.

        Returns ``(needs_restart, needs_encoder_update)`` booleans.
        ``needs_restart`` is True when rtl_fm must be restarted (freq/gain/ppm
        changed).  ``needs_encoder_update`` is True when encoder-only params
        (squelch_db) changed.
        """
        restart_fields = {"freq", "gain", "ppm", "device_index"}
        needs_restart = False
        needs_encoder_update = False

        for key, raw_value in updates.items():
            if key == "freq":
                val = int(raw_value)
                if val != self.freq:
                    self.freq = val
                    needs_restart = True
            elif key == "gain":
                val = int(raw_value)
                if val != self.gain:
                    self.gain = val
                    needs_restart = True
            elif key == "ppm":
                val = int(raw_value)
                if val != self.ppm:
                    self.ppm = val
                    needs_restart = True
            elif key == "device_index":
                val = int(raw_value)
                if val != self.device_index:
                    self.device_index = val
                    needs_restart = True
            elif key == "squelch_db":
                val = float(raw_value)
                if val != self.squelch_db:
                    self.squelch_db = val
                    needs_encoder_update = True

        if needs_restart:
            self.restart_event.set()

        return needs_restart, needs_encoder_update

    def to_dict(self) -> dict:
        return {
            "freq":         self.freq,
            "gain":         self.gain,
            "ppm":          self.ppm,
            "sample_rate":  self.sample_rate,
            "device_index": self.device_index,
            "squelch_db":   self.squelch_db,
        }

    # Common VHF marine channel frequencies (MHz → Hz)
    MARINE_CHANNELS: dict[str, int] = {
        "Ch 06": 156_300_000,
        "Ch 08": 156_400_000,
        "Ch 09": 156_450_000,
        "Ch 10": 156_500_000,
        "Ch 11": 156_550_000,
        "Ch 12": 156_600_000,
        "Ch 13": 156_650_000,
        "Ch 14": 156_700_000,
        "Ch 16": 156_800_000,
        "Ch 22A": 157_100_000,
        "Ch 67": 156_375_000,
        "Ch 68": 156_425_000,
        "Ch 69": 156_475_000,
        "Ch 70": 156_525_000,
        "Ch 71": 156_575_000,
        "Ch 72": 156_625_000,
        "Ch 73": 156_675_000,
        "Ch 77": 156_875_000,
    }


# Single shared instance — imported by main.py, fm_receiver.py, encoder.py
config = ReceiverConfig()
