"""Local analysis over a captured waveform.

Nothing here talks to the instrument: the samples are already in hand, so these
measurements are plain arithmetic. That also makes them more precise than the
scope's own readouts, which are limited by a 320x234 screen.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .waveform import Waveform

# (label, value, unit) rows, ready for the caller to format.
Reading = Tuple[str, Optional[float], str]

TIME = "time"
VOLTAGE = "voltage"
OFF = "off"

CURSOR_MODES = (OFF, TIME, VOLTAGE)


def cursor_readings(mode: str, a: Optional[float],
                    b: Optional[float]) -> List[Reading]:
    """Rows describing two cursors: their positions, the gap, and 1/gap.

    ``1/dT`` is the point of time cursors — measuring a period by eye and
    inverting it is how you read a frequency off a trace. It is None for a zero
    gap rather than infinity, so the caller can show a dash.
    """
    if mode == TIME:
        unit, delta_label = "s", "dT"
    elif mode == VOLTAGE:
        unit, delta_label = "V", "dV"
    else:
        return []

    if a is None or b is None:
        return []

    delta = b - a
    rows: List[Reading] = [("1", a, unit), ("2", b, unit),
                           (delta_label, delta, unit)]
    if mode == TIME:
        rows.append(("1/dT", (1.0 / delta) if delta else None, "Hz"))
    return rows


def sample_at(wave: Waveform, channel: int, t: float) -> Optional[float]:
    """The trace value at a time, linearly interpolated between samples.

    Returns None outside the captured span rather than clamping to an end
    sample, which would read as a real measurement.
    """
    volts = wave.channels.get(channel)
    if volts is None or len(wave.t) == 0:
        return None
    if t < wave.t[0] or t > wave.t[-1]:
        return None
    return float(np.interp(t, wave.t, volts))


def default_cursor_positions(low: float, high: float) -> Tuple[float, float]:
    """Where to drop a fresh pair of cursors in a visible span."""
    span = high - low
    return low + span / 3.0, low + 2.0 * span / 3.0


def nearest_cursor(positions: Sequence[Optional[float]], value: float,
                   tolerance: float) -> Optional[int]:
    """Index of the cursor within tolerance of a value, or None."""
    best, best_gap = None, tolerance
    for index, position in enumerate(positions):
        if position is None:
            continue
        gap = abs(position - value)
        if gap <= best_gap:
            best, best_gap = index, gap
    return best
