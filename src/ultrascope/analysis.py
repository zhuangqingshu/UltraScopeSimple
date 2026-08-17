"""Local analysis over a captured waveform.

Nothing here talks to the instrument: the samples are already in hand, so these
measurements are plain arithmetic. That also makes them more precise than the
scope's own readouts, which are limited by a 320x234 screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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

# How many harmonics of the strongest component to keep in view by default.
# The axis runs to Nyquist, which is set by the timebase and has nothing to do
# with where the signal is: a 1 kHz square wave at 200 us/div lands in the first
# 4% of a 125 kHz axis and reads as a smudge against the left edge.
DISPLAY_HARMONICS = 10
# Never zoom in so far that only a handful of bins are visible.
MIN_DISPLAY_BINS = 20

# Below this many cycles in the record, the peak straddles bins: its frequency
# reads wrong and its amplitude reads low. The cure is a slower timebase, so
# the readout says so rather than leaving it to be discovered.
CYCLES_FOR_A_SHARP_PEAK = 10.0


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

    @property
    def nyquist(self) -> float:
        return float(self.freqs[-1]) if len(self.freqs) else 0.0

    def cycles(self) -> Optional[float]:
        """How many cycles of the peak component the record holds.

        Equal to peak frequency over bin spacing, since both are one over a
        time. This is the number that decides whether the FFT can resolve the
        signal at all: at two cycles the fundamental falls between bins and is
        split across them, so it reads at the wrong frequency and too small.
        """
        frequency, _volts = self.peak()
        if not frequency or self.resolution <= 0:
            return None
        return frequency / self.resolution

    def display_span(self, harmonics: int = DISPLAY_HARMONICS) -> float:
        """A frequency range worth looking at, rather than the whole axis.

        Nyquist follows from the timebase and says nothing about where the
        signal is. Showing the peak and some harmonics puts the interesting
        part across the plot instead of in its leftmost few pixels; the caller
        can still ask for the full range.
        """
        frequency, volts = self.peak()
        # With nothing above the floor there is no peak to centre on, and
        # zooming would just magnify a patch of silence.
        if not frequency or not volts or volts <= DB_FLOOR:
            return self.nyquist
        wanted = max(frequency * harmonics, self.resolution * MIN_DISPLAY_BINS)
        return min(self.nyquist, wanted)


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


# Reference levels for edge timing, as fractions of top-to-base amplitude.
LOW_REF, MID_REF, HIGH_REF = 0.1, 0.5, 0.9
LEVEL_HISTOGRAM_BINS = 64


@dataclass
class Levels:
    """The two logic levels of a trace, plus its extremes.

    ``top`` and ``base`` are *not* max and min: a pulse that overshoots settles
    at a top well below its peak, and timing a rise to 90% of the peak instead
    of 90% of the settled level would read short. They come from where the
    samples actually cluster.
    """

    top: float
    base: float
    maximum: float
    minimum: float

    @property
    def amplitude(self) -> float:
        return self.top - self.base

    def reference(self, fraction: float) -> float:
        return self.base + self.amplitude * fraction


def levels(volts: np.ndarray,
           bins: int = LEVEL_HISTOGRAM_BINS) -> Optional[Levels]:
    """Find the settled high and low levels by histogram.

    A trace spends most of its time at its two levels, so the fullest bin in
    each half of the range is a better estimate than the extremes.
    """
    if len(volts) == 0:
        return None
    low, high = float(np.min(volts)), float(np.max(volts))
    if low == high:
        return Levels(top=high, base=low, maximum=high, minimum=low)

    counts, edges = np.histogram(volts, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2.0
    midpoint = (low + high) / 2.0

    def settled(half: np.ndarray, fallback: float) -> float:
        if not half.any():
            return fallback
        index = int(np.nonzero(half)[0][int(np.argmax(counts[half]))])
        # Average the samples inside the winning bin rather than taking its
        # centre: a bin is wide enough that the centre alone leaves a clean
        # trace reporting most of a percent of overshoot that is not there.
        inside = volts[(volts >= edges[index]) & (volts <= edges[index + 1])]
        return float(np.mean(inside)) if len(inside) else float(centres[index])

    lower_half = centres <= midpoint
    return Levels(top=settled(~lower_half, high), base=settled(lower_half, low),
                  maximum=high, minimum=low)


def crossings(t: np.ndarray, volts: np.ndarray, level: float,
              rising: Optional[bool] = None) -> List[float]:
    """Times at which the trace crosses a level, linearly interpolated.

    Interpolating matters: snapping to the nearest sample would quantise every
    timing measurement to the sample interval, which at 600 screen points is
    coarse enough to swamp a rise time.
    """
    above = volts >= level
    result: List[float] = []
    for index in np.nonzero(np.diff(above.astype(np.int8)))[0]:
        going_up = bool(above[index + 1])
        if rising is not None and going_up != rising:
            continue
        v0, v1 = float(volts[index]), float(volts[index + 1])
        if v1 == v0:
            continue
        span = (level - v0) / (v1 - v0)
        result.append(float(t[index] + span * (t[index + 1] - t[index])))
    return result


def _first_edge_time(t, volts, lvl: Levels, rising: bool) -> Optional[float]:
    """Duration of the first 10%-90% transition in the requested direction."""
    low = crossings(t, volts, lvl.reference(LOW_REF if rising else HIGH_REF),
                    rising=rising)
    high = crossings(t, volts, lvl.reference(HIGH_REF if rising else LOW_REF),
                     rising=rising)
    for start in low:
        later = [end for end in high if end > start]
        if later:
            return later[0] - start
    return None


def measurements(wave: Waveform, channel: int) -> Dict[str, Reading]:
    """Standard trace parameters, computed locally.

    Computed from the captured samples rather than asked of the instrument, so
    they work on a saved capture and while disconnected. The trade-off is
    resolution: the screen record is 600 points, so a rise time shorter than a
    few sample intervals cannot be measured this way -- ask the scope instead.
    """
    volts = wave.channels.get(channel)
    if volts is None:
        return {}
    return measurements_of(wave.t, volts)


def measurements_of(t: np.ndarray, volts: np.ndarray,
                    unit: str = "V") -> Dict[str, Reading]:
    """The same parameters over any trace, not just a captured channel.

    Split out so a computed trace -- a math result, say -- can be measured with
    the identical code rather than a parallel copy of it. ``unit`` carries
    through to the voltage rows, because CH1*CH2 is not in volts.
    """
    if volts is None or len(volts) < 2 or len(t) != len(volts):
        return {}

    lvl = levels(volts)
    rows: Dict[str, Reading] = {
        "Vtop": ("Vtop", lvl.top, unit),
        "Vbase": ("Vbase", lvl.base, unit),
        "Vamp": ("Vamp", lvl.amplitude, unit),
        "Vpp": ("Vpp", lvl.maximum - lvl.minimum, unit),
        "Vavg": ("Vavg", float(np.mean(volts)), unit),
        "Vrms": ("Vrms", float(np.sqrt(np.mean(np.square(volts)))), unit),
    }

    rise = _first_edge_time(t, volts, lvl, rising=True)
    fall = _first_edge_time(t, volts, lvl, rising=False)
    rows["Rise"] = ("Rise", rise, "s")
    rows["Fall"] = ("Fall", fall, "s")

    mid = lvl.reference(MID_REF)
    ups = crossings(t, volts, mid, rising=True)
    downs = crossings(t, volts, mid, rising=False)

    period = (ups[1] - ups[0]) if len(ups) >= 2 else None
    rows["Period"] = ("Period", period, "s")
    rows["Freq"] = ("Freq", (1.0 / period) if period else None, "Hz")

    positive = _first_pulse(ups, downs)
    negative = _first_pulse(downs, ups)
    rows["+Width"] = ("+Width", positive, "s")
    rows["-Width"] = ("-Width", negative, "s")
    rows["Duty"] = ("Duty", (positive / period * 100.0)
                    if (positive and period) else None, "%")

    if lvl.amplitude > 0:
        rows["Over"] = ("Over", (lvl.maximum - lvl.top) / lvl.amplitude * 100.0, "%")
        rows["Pre"] = ("Pre", (lvl.base - lvl.minimum) / lvl.amplitude * 100.0, "%")
    else:
        rows["Over"] = ("Over", None, "%")
        rows["Pre"] = ("Pre", None, "%")
    return rows


def _first_pulse(starts: Sequence[float],
                 ends: Sequence[float]) -> Optional[float]:
    """Width of the first interval that opens in ``starts`` and shuts in ``ends``."""
    for start in starts:
        later = [end for end in ends if end > start]
        if later:
            return later[0] - start
    return None


# --- maths between channels -------------------------------------------------

MATH_OFF = "off"
# Kept as display strings because that is also what the combobox shows; the
# operand channels are read out of the name rather than passed separately.
MATH_OPS = ("CH1+CH2", "CH1-CH2", "CH1*CH2")

# Products of two voltages are not voltages. Saying so matters on a plot whose
# other traces are in volts.
MATH_UNITS = {"CH1+CH2": "V", "CH1-CH2": "V", "CH1*CH2": "V²"}


def math_trace(wave: Waveform, op: str) -> Optional[np.ndarray]:
    """Combine two channels arithmetically.

    Done here rather than through ``:MATH:OPER`` for two reasons: both traces
    are already in hand so numpy is more flexible than the instrument's three
    fixed operations, and it costs no SCPI command whose spelling would need
    verifying. It also works on a capture loaded from a file.

    Returns None when the capture does not hold both operands.
    """
    if op in (None, "", MATH_OFF):
        return None
    if op not in MATH_UNITS:
        raise ValueError(f"unknown math operation {op!r}; expected one of {list(MATH_OPS)}")

    a = wave.channels.get(1)
    b = wave.channels.get(2)
    if a is None or b is None or len(a) != len(b):
        return None

    if op == "CH1+CH2":
        return a + b
    if op == "CH1-CH2":
        return a - b
    return a * b


def math_unit(op: str) -> str:
    return MATH_UNITS.get(op, "V")


# --- XY (Lissajous) ---------------------------------------------------------


def xy_pairs(wave: Waveform, x_channel: int,
             y_channel: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """One channel plotted against another, with time as the implicit parameter.

    Phase differences and I/V curves are obvious this way and nearly invisible
    on two time-domain traces. Both channels share the capture's time axis, so
    the samples already correspond point for point -- no resampling needed.
    """
    x = wave.channels.get(x_channel)
    y = wave.channels.get(y_channel)
    if x is None or y is None or len(x) != len(y) or len(x) == 0:
        return None
    return x, y


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
