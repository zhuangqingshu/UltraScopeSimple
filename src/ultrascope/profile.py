"""Per-model hardware facts.

Everything the DS1000E generation does differently from a modern Rigol lives
here, so that adding a model means adding a profile rather than hunting for
magic numbers inside the decode path.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

# ":TRIG:MODE?" answers with a full word, but the command subtrees are abbreviated.
DS1000E_TRIGGER_SUBSYS = {
    "EDGE": "EDGE",
    "PULSE": "PULS",
    "VIDEO": "VIDEO",
    "SLOPE": "SLOP",
    "PATTERN": "PATT",
    "DURATION": "DUR",
    "ALTERNATION": "ALT",
}

# Volts/div and sec/div steps the DS1102E front panel actually offers.
DS1000E_VOLT_SCALES = (
    2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0,
)
DS1000E_TIME_SCALES = (
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
)

DS1000E_PROBE_RATIOS = (1.0, 5.0, 10.0, 50.0, 100.0, 500.0, 1000.0)


def _timed_conditions(noun: str) -> "OrderedDict[str, str]":
    """The six condition options a timed trigger offers.

    The User's Guide lists them as "(>, <, =) positive pulse" and the same
    three for negative, in that order.
    """
    return OrderedDict(
        (f"{sign}{noun} {symbol}", f"{sign}{keyword}")
        for sign in ("+", "-")
        for symbol, keyword in ((">", "GREaterthan"), ("<", "LESSthan"),
                                ("=", "EQUal"))
    )


@dataclass(frozen=True)
class TimedTriggerSpec:
    """A trigger mode that fires on a condition over a duration.

    PULSE and SLOPE are the same shape: pick one of six conditions, then give
    it a time. Only the subtree and the leaf holding the time differ.

    .. warning::
       **The SCPI spellings below are unverified.** The manual shipped in
       ``docs/`` is the User's Guide, which documents the front panel and
       contains no commands at all; these names come from the project's earlier
       notes. This instrument ignores a misspelt command silently, so a wrong
       name here looks exactly like "the setting does nothing".

       Everything that builds these commands reads them from this object, so
       checking them against a real Programming Guide is a single edit here.
       The ranges and the condition list *are* from the User's Guide and are
       independent of the spellings.
    """

    subtree: str
    condition_leaf: str
    time_leaf: str
    conditions: Mapping[str, str]
    # User's Guide: "Pulse Width range 20ns ~10s"; slope time is the same.
    time_min: float = 20e-9
    time_max: float = 10.0

    def keyword_for(self, condition: str) -> Optional[str]:
        """Accept either a display label ('+Width >') or the SCPI keyword."""
        if condition in self.conditions:
            return self.conditions[condition]
        wanted = condition.strip().upper()
        for label, keyword in self.conditions.items():
            if wanted in (label.upper(), keyword.upper()):
                return keyword
        return None


DS1000E_PULSE_TRIGGER = TimedTriggerSpec(
    subtree="PULS", condition_leaf="MODE", time_leaf="WIDT",
    conditions=_timed_conditions("Width"))

DS1000E_SLOPE_TRIGGER = TimedTriggerSpec(
    subtree="SLOP", condition_leaf="MODE", time_leaf="TIME",
    conditions=_timed_conditions("Slope"))


@dataclass(frozen=True)
class DeviceProfile:
    """Fixed characteristics of one oscilloscope generation."""

    name: str

    # --- waveform byte -> volts ---
    # With no :WAV:PREamble to ask, the conversion is a fixed scale: the codes
    # arrive inverted, sit around a fixed centre, and a vertical division is a
    # fixed number of codes.
    has_preamble: bool = False
    code_inverted: bool = True
    code_center: float = 130.0
    codes_per_div: float = 25.0

    # --- screen geometry ---
    # The instrument reports no per-sample timing, so the time axis is derived
    # from the timebase across this many horizontal divisions.
    h_divisions: int = 12
    screen_points: int = 600

    # --- front-panel ranges ---
    volt_scales: Tuple[float, ...] = DS1000E_VOLT_SCALES
    time_scales: Tuple[float, ...] = DS1000E_TIME_SCALES
    probe_ratios: Tuple[float, ...] = DS1000E_PROBE_RATIOS

    trigger_subsystems: Mapping[str, str] = field(
        default_factory=lambda: dict(DS1000E_TRIGGER_SUBSYS))
    # Modes that take a condition plus a duration. Anything not listed here
    # has no such parameters (EDGE) or a different shape (VIDEO, PATTERN).
    timed_triggers: Mapping[str, TimedTriggerSpec] = field(
        default_factory=lambda: {"PULSE": DS1000E_PULSE_TRIGGER,
                                 "SLOPE": DS1000E_SLOPE_TRIGGER})

    # --- limits ---
    # The scope ignores out-of-range values silently rather than reporting an
    # error, which just looks like "the setting did nothing". Range-check here.
    average_min: int = 2
    average_max: int = 256
    # User's Guide states 100 ns - 1.5 s in two places: the specification table
    # ("Trigger Holdoff range 100ns~1.5s") and the trigger menu, where the
    # instrument's own "Holdoff Reset" sets 100 ns. An earlier 500 ns floor here
    # rejected the scope's own default, which made restore() refuse to reapply a
    # setup saved right after a front-panel reset.
    holdoff_min: float = 100e-9
    holdoff_max: float = 1.5
    # A trigger level further than this many divisions from centre is rejected.
    trigger_level_divs: float = 6.0

    def is_scope_resource(self, resource: str) -> bool:
        """Whether a VISA resource string plausibly belongs to this family."""
        return "::DS1" in resource or resource.startswith("USB")


DS1000E = DeviceProfile(name="Rigol DS1000D/E")

DEFAULT_PROFILE = DS1000E
