"""Local analysis over a captured waveform.

Nothing here talks to the instrument: the samples are already in hand, so these
measurements are plain arithmetic. That also makes them more precise than the
scope's own readouts, which are limited by a 320x234 screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .waveform import Waveform

# (label, value, unit) rows, ready for the caller to format.
Reading = Tuple[str, Optional[float], str]

TIME = "time"
VOLTAGE = "voltage"
OFF = "off"

CURSOR_MODES = (OFF, TIME, VOLTAGE)


WINDOWS = ("rectangular", "hann", "hamming", "blackman")
DEFAULT_WINDOW = "hann"

# Half-width of each window's main lobe, in bins. A DC offset does not sit only
# in bin 0: the window smears it across this many bins either side, so a large
# offset otherwise shows up as a huge "peak" one or two bins up from DC and
# beats the real signal.
WINDOW_MAIN_LOBE_BINS = {
    "rectangular": 1,
    "hann": 2,
    "hamming": 2,
    "blackman": 3,
}

# Magnitudes below this read as the floor rather than -inf dB.
DB_FLOOR = 1e-12


def _window(name: str, n: int) -> np.ndarray:
    if name == "rectangular":
        return np.ones(n)
    if name == "hann":
        return np.hanning(n)
    if name == "hamming":
        return np.hamming(n)
    if name == "blackman":
        return np.blackman(n)
    raise ValueError(f"unknown window {name!r}; expected one of {list(WINDOWS)}")


def sample_rate(wave: Waveform) -> float:
    """Samples per second implied by the time axis.

    The instrument reports no timing, so this comes from the synthesised axis:
    the record is assumed to span the profile's horizontal divisions. With the
    600-point screen record that works out to 600 / (12 * timebase), which is
    far below the real acquisition rate -- the screen data is decimated. So the
    spectrum is of what is displayed, and anything above its Nyquist has
    already been aliased by the instrument before we ever see it.
    """
    span = float(wave.t[-1] - wave.t[0])
    if span <= 0 or wave.npoints < 2:
        return 0.0
    return (wave.npoints - 1) / span


@dataclass
class Spectrum:
    """A single-sided amplitude spectrum, in volts per bin."""

    freqs: np.ndarray
    magnitudes: np.ndarray
    window: str
    sample_rate: float

    @property
    def db(self) -> np.ndarray:
        """Magnitudes as dBV, floored so silence is not -inf."""
        return 20.0 * np.log10(np.maximum(self.magnitudes, DB_FLOOR))

    @property
    def resolution(self) -> float:
        """Spacing between bins -- the finest frequency difference resolvable."""
        return float(self.freqs[1] - self.freqs[0]) if len(self.freqs) > 1 else 0.0

    def peak(self) -> Tuple[Optional[float], Optional[float]]:
        """The strongest bin clear of DC, as (frequency, volts).

        The bins around DC are skipped, not just bin 0: a trace with an offset
        leaks that offset across the window's main lobe, and a 5 V offset under
        a 1 V signal would otherwise report the leak instead of the signal.
        """
        start = WINDOW_MAIN_LOBE_BINS.get(self.window, 1)
        if len(self.magnitudes) <= start:
            return None, None
        index = int(np.argmax(self.magnitudes[start:])) + start
        return float(self.freqs[index]), float(self.magnitudes[index])


def spectrum(wave: Waveform, channel: int,
             window: str = DEFAULT_WINDOW) -> Optional[Spectrum]:
    """Amplitude spectrum of one channel, scaled so a peak reads in volts.

    The scaling matters: a 1 V amplitude sine should read 1 V at its bin. That
    needs both the single-sided doubling and a division by the window's
    coherent gain, without which every window would quietly under-report.
    """
    volts = wave.channels.get(channel)
    if volts is None or len(volts) < 2:
        return None
    rate = sample_rate(wave)
    if rate <= 0:
        return None

    n = len(volts)
    taper = _window(window, n)
    coherent_gain = float(np.mean(taper))
    if coherent_gain == 0:
        return None

    bins = np.fft.rfft(volts * taper)
    magnitudes = 2.0 * np.abs(bins) / (n * coherent_gain)
    # DC and, for an even-length record, Nyquist are not mirrored, so undo the
    # doubling applied above for them.
    magnitudes[0] /= 2.0
    if n % 2 == 0:
        magnitudes[-1] /= 2.0

    return Spectrum(freqs=np.fft.rfftfreq(n, d=1.0 / rate),
                    magnitudes=magnitudes, window=window, sample_rate=rate)


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
