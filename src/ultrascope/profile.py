"""Per-model hardware facts.

Everything the DS1000E generation does differently from a modern Rigol lives
here, so that adding a model means adding a profile rather than hunting for
magic numbers inside the decode path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

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
