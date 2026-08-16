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
