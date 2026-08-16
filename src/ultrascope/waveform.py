"""Waveform block parsing, byte -> volts decoding, and the captured value object.

These are pure functions over bytes and numbers: no instrument, no VISA. They
carry the conversion this project is most likely to get wrong, so they are the
part worth testing hardest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from .profile import DeviceProfile


class WaveformError(ValueError):
    pass


def parse_block(raw: bytes) -> bytes:
    """Strip an IEEE 488.2 definite-length block header.

    Layout is '#' + one digit N + an N-digit byte count + the payload.

    The declared length is checked against what actually arrived. Slicing
    without that check truncates silently, and on this instrument that is not
    hypothetical: in RAW mode it declares 1 M bytes whatever the memory depth
    and then stops sending after ~12 K, so every short read would look exactly
    like a successful small capture. The caller has no other way to tell "this
    is all the data" from "the transfer died half way".
    """
    if raw[:1] != b"#":
        raise WaveformError(f"unexpected block header {raw[:12]!r}")
    ndigits = int(raw[1:2])
    header_len = 2 + ndigits
    length = int(raw[2:header_len])

    available = len(raw) - header_len
    if available < length:
        raise WaveformError(
            f"truncated block: header declares {length} bytes, "
            f"only {available} arrived")
    return raw[header_len:header_len + length]


def decode(payload: bytes, profile: DeviceProfile,
           volt_scale: float, volt_offset: float) -> np.ndarray:
    """Convert raw sample codes to volts using the profile's fixed scale."""
    data = np.frombuffer(payload, dtype=np.uint8).astype(np.float64)
    if profile.code_inverted:
        data = 255.0 - data
    codes = profile.codes_per_div
    centred = data - profile.code_center - volt_offset / volt_scale * codes
    return centred / codes * volt_scale


def time_axis(npoints: int, timebase: float, time_offset: float,
              profile: DeviceProfile) -> np.ndarray:
    """Build the shared time axis.

    The instrument gives no per-sample timing: the record spans the profile's
    horizontal divisions, centred on the time offset.
    """
    half = profile.h_divisions / 2.0
    return np.linspace(time_offset - half * timebase,
                       time_offset + half * timebase, npoints)


@dataclass
class Waveform:
    """One capture: a shared time axis plus per-channel volts, with context.

    Carrying the acquisition metadata alongside the samples is what lets the
    export and plotting layers work without querying the instrument again.
    """

    t: np.ndarray
    channels: Dict[int, np.ndarray]
    timebase: float
    time_offset: float
    points_mode: str = "normal"
    captured_at: float = field(default_factory=time.time)

    @property
    def npoints(self) -> int:
        return len(self.t)

    @property
    def channel_ids(self) -> List[int]:
        return sorted(self.channels)
