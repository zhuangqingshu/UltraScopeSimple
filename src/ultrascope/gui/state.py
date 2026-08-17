"""Mapping between instrument values and the strings shown in comboboxes.

The old GUI formatted every candidate scale with eng() and string-compared to
find the selected value back, which silently dropped the setting whenever the
instrument reported something that did not format to an exact table entry
(0.0200000001 V, say). The table is built once here and reverse lookups fall
back to the nearest supported step.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..units import eng


class OptionTable:
    """An ordered set of instrument values with their display labels."""

    def __init__(self, values: Sequence[float], unit: str):
        self.values = tuple(values)
        self.unit = unit
        self.labels = tuple(eng(v, unit) for v in self.values)
        self._by_label = dict(zip(self.labels, self.values))

    def value_for(self, label: str) -> Optional[float]:
        """The instrument value behind a label, or None if nothing is selected."""
        if not label:
            return None
        return self._by_label.get(label)

    def label_for(self, value: Optional[float]) -> str:
        """The label closest to a value the instrument reported."""
        if value is None or not self.values:
            return ""
        nearest = min(self.values, key=lambda v: abs(v - value))
        return eng(nearest, self.unit)


def probe_labels(profile=None) -> tuple:
    """Probe ratios as the front panel writes them: 1X, 10X, ..."""
    from ..profile import DEFAULT_PROFILE

    ratios = (profile or DEFAULT_PROFILE).probe_ratios
    return tuple(f"{int(r)}X" for r in ratios)


def timed_trigger_spec(mode: str, profile=None):
    """The PULSE/SLOPE condition spec for a mode, or None if it has none."""
    from ..profile import DEFAULT_PROFILE

    return (profile or DEFAULT_PROFILE).timed_triggers.get((mode or "").upper())


def condition_labels(mode: str, profile=None) -> tuple:
    spec = timed_trigger_spec(mode, profile)
    return tuple(spec.conditions) if spec else ()


def condition_label_for(mode: str, reported: str, profile=None) -> str:
    """Map the keyword the instrument reports back onto its display label."""
    spec = timed_trigger_spec(mode, profile)
    if not spec or not reported:
        return ""
    keyword = spec.keyword_for(reported)
    for label, candidate in spec.conditions.items():
        if candidate == keyword:
            return label
    return ""


# Upper frequency for the spectrum view. None asks the analysis layer to pick
# one from the signal; 0 means the whole axis out to Nyquist. Nyquist follows
# from the timebase, so at a fast timebase an audio-frequency signal otherwise
# sits in the leftmost few pixels of the plot.
SPECTRUM_SPANS = (("Auto", None), ("Full", 0.0), ("1 kHz", 1e3),
                  ("10 kHz", 1e4), ("100 kHz", 1e5), ("1 MHz", 1e6))
SPECTRUM_SPAN_LABELS = tuple(label for label, _value in SPECTRUM_SPANS)
DEFAULT_SPECTRUM_SPAN = "Auto"


def spectrum_span_for(label: str):
    """The frequency behind a span label, or None for Auto."""
    return dict(SPECTRUM_SPANS).get(label or DEFAULT_SPECTRUM_SPAN)


ACQ_TYPES = ("NORMAL", "AVERAGE", "PEAKDETECT")
AVERAGE_COUNTS = ("2", "4", "8", "16", "32", "64", "128", "256")
MEMORY_DEPTHS = ("NORMAL", "LONG")
COUPLINGS = ("DC", "AC", "GND")
TRIGGER_MODES = ("EDGE", "PULSE", "VIDEO", "SLOPE",
                 "PATTERN", "DURATION", "ALTERNATION")
TRIGGER_SOURCES = ("CHAN1", "CHAN2", "EXT", "ACLINE")


def normalise_trigger_source(text: str) -> str:
    """Map what the instrument reports onto the combobox's own options.

    ``:TRIG:EDGE:SOUR?`` answers ``CH1`` while the panel offers ``CHAN1``;
    setting the raw answer would put a value in the readonly combobox that is
    not one of its options.
    """
    value = (text or "").strip().upper()
    if value in TRIGGER_SOURCES:
        return value
    if "1" in value:
        return "CHAN1"
    if "2" in value:
        return "CHAN2"
    return value
TRIGGER_SLOPES = ("POSITIVE", "NEGATIVE")
TRIGGER_SWEEPS = ("AUTO", "NORMAL", "SINGLE")
TRIGGER_COUPLINGS = ("DC", "AC", "HF", "LF")

CHANNEL_COLOURS = {1: "#f0d000", 2: "#00d0f0"}
