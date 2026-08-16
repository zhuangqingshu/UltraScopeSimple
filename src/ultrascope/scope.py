"""SCPI facade for the Rigol DS1102E / DS1000D-E.

Every setter takes ``None`` for "leave this alone". That is the contract the
command-line tool depends on: a setting you do not pass is never written, so
running against a setup dialled in by hand cannot disturb it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from . import waveform as wf
from .profile import DEFAULT_PROFILE, DeviceProfile
from .transport import (TIMEOUT_NORM_MS, TIMEOUT_RAW_MS, PyVisaTransport,
                        Transport)
from .units import eng, scpi_number

SOURCE_MAP = {"1": "CHAN1", "2": "CHAN2", "ch1": "CHAN1", "ch2": "CHAN2",
              "chan1": "CHAN1", "chan2": "CHAN2", "ext": "EXT", "acline": "ACLINE"}


class ScopeError(RuntimeError):
    pass


@dataclass
class ChannelSettings:
    on: bool
    volt_scale: float
    coupling: str
    probe: Optional[float] = None
    volt_offset: Optional[float] = None


@dataclass
class ScopeSettings:
    """A snapshot of the front panel.

    Serves both jobs: the GUI mirrors it onto its panels, and ``to_dict`` /
    ``from_dict`` are the on-disk setup-file format shared by the GUI's
    Save/Load setup buttons and the CLI's --save-setup / --load-setup.
    """

    timebase: float
    acquire_type: str
    average: int
    memory_depth: str
    trigger_mode: str
    channels: Dict[int, ChannelSettings]
    time_offset: float = 0.0
    idn: str = ""
    # Some trigger modes do not expose all of these.
    trigger_source: Optional[str] = None
    trigger_slope: Optional[str] = None
    trigger_sweep: Optional[str] = None
    trigger_level: Optional[float] = None
    trigger_holdoff: Optional[float] = None
    # PULSE / SLOPE only; None everywhere else.
    trigger_condition: Optional[str] = None
    trigger_condition_time: Optional[float] = None

    # The dict form is a published file format: keep the key names and the
    # string-keyed "channels" map stable so old setup files keep loading.
    _TRIGGER_KEYS = (("source", "trigger_source"), ("slope", "trigger_slope"),
                     ("sweep", "trigger_sweep"), ("level", "trigger_level"),
                     ("holdoff", "trigger_holdoff"),
                     ("condition", "trigger_condition"),
                     ("condition_time", "trigger_condition_time"))

    def to_dict(self) -> dict:
        trigger = {"mode": self.trigger_mode}
        for key, attr in self._TRIGGER_KEYS:
            value = getattr(self, attr)
            if value is not None:
                trigger[key] = value
        return {
            "idn": self.idn,
            "timebase": self.timebase,
            "time_offset": self.time_offset,
            "acq_type": self.acquire_type,
            "acq_average": self.average,
            "acq_memdepth": self.memory_depth,
            "trigger": trigger,
            "channels": {
                str(ch): {"on": c.on, "probe": c.probe, "scale": c.volt_scale,
                          "offset": c.volt_offset, "coupling": c.coupling}
                for ch, c in sorted(self.channels.items())
            },
        }

    @classmethod
    def from_dict(cls, state: dict) -> "ScopeSettings":
        trigger = state.get("trigger") or {}
        settings = cls(
            timebase=state.get("timebase", 0.0),
            acquire_type=state.get("acq_type", ""),
            average=state.get("acq_average", 0),
            memory_depth=state.get("acq_memdepth", ""),
            trigger_mode=trigger.get("mode", ""),
            time_offset=state.get("time_offset", 0.0),
            idn=state.get("idn", ""),
            channels={
                int(ch): ChannelSettings(
                    on=info.get("on", False), volt_scale=info.get("scale", 1.0),
                    coupling=info.get("coupling", ""), probe=info.get("probe"),
                    volt_offset=info.get("offset"))
                for ch, info in (state.get("channels") or {}).items()
            },
        )
        for key, attr in cls._TRIGGER_KEYS:
            if key in trigger:
                setattr(settings, attr, trigger[key])
        return settings


class Scope:
    """Thin wrapper over the DS1000E's SCPI command set.

    Not thread-safe: the GUI funnels every call through a single worker thread.
    """

    def __init__(self, transport: Transport,
                 profile: DeviceProfile = DEFAULT_PROFILE,
                 idn: str = ""):
        self.transport = transport
        self.profile = profile
        self.idn = idn or self.query("*IDN?")

    @classmethod
    def connect(cls, resource: Optional[str] = None, rm=None,
                profile: DeviceProfile = DEFAULT_PROFILE) -> "Scope":
        """Open the given VISA resource, or the first scope found."""
        from .discovery import list_scopes

        if resource is None:
            found = list_scopes(rm, profile)
            if not found:
                raise ScopeError("No USB instrument found. "
                                 "Check that UltraSigma can see the scope first.")
            resource = found[0]
        return cls(PyVisaTransport(resource, rm), profile)

    @property
    def resource(self) -> str:
        return getattr(self.transport, "resource", "")

    # --- plumbing ---------------------------------------------------------

    def write(self, cmd: str) -> None:
        self.transport.write(cmd)

    def query(self, cmd: str) -> str:
        return self.transport.query(cmd)

    def qfloat(self, cmd: str) -> float:
        return float(self.query(cmd))

    def close(self) -> None:
        try:
            # Remote mode locks the front panel; hand control back to the user.
            self.write(":KEY:FORC")
        except Exception:
            pass
        self.transport.close()

    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- acquisition control ---------------------------------------------

    def run(self) -> None:
        self.write(":RUN")

    def stop(self) -> None:
        self.write(":STOP")

    def auto(self) -> None:
        self.write(":AUTO")

    def force_trigger(self) -> None:
        self.write(":FORC")

    def trigger_status(self) -> str:
        return self.query(":TRIG:STAT?").upper()

    def trigger_mode(self) -> str:
        return self.query(":TRIG:MODE?").upper()

    def trigger_subsys(self):
        """Map the spelled-out mode onto the abbreviated command subtree."""
        mode = self.trigger_mode()
        sub = self.profile.trigger_subsystems.get(mode)
        if sub is None:
            raise ScopeError(f"Unrecognised trigger mode {mode!r}")
        return mode, sub

    def set_trigger_mode(self, mode: str) -> None:
        self.write(f":TRIG:MODE {mode.upper()}")
        time.sleep(0.2)  # the scope needs a moment to switch subsystems

    def set_trigger(self, source=None, slope=None, level=None,
                    coupling=None, sweep=None, holdoff=None) -> None:
        """Write only the trigger settings that were actually supplied."""
        mode, sub = self.trigger_subsys()

        if source is not None:
            self.write(f":TRIG:{sub}:SOUR {SOURCE_MAP.get(str(source).lower(), source)}")
        if slope is not None:
            self.write(f":TRIG:{sub}:SLOP {slope.upper()}")
        if coupling is not None:
            self.write(f":TRIG:{sub}:COUP {coupling.upper()}")
        if level is not None:
            self.write(f":TRIG:{sub}:LEV {scpi_number(level)}")
        if holdoff is not None:
            # Route through the setter so the range check applies here too.
            self.set_holdoff(holdoff)
        if sweep is not None:
            if mode == "ALTERNATION":
                raise ScopeError("Alternation mode has no sweep setting.")
            self.write(f":TRIG:{sub}:SWE {sweep.upper()}")

    def trigger_level(self) -> float:
        _, sub = self.trigger_subsys()
        return self.qfloat(f":TRIG:{sub}:LEV?")

    def trigger_source(self) -> str:
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SOUR?")

    def trigger_slope(self) -> str:
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SLOP?")

    def trigger_sweep(self) -> str:
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SWE?")

    def trigger_channel(self) -> Optional[int]:
        """The channel number the trigger looks at, or None for EXT/ACLINE."""
        src = self.trigger_source().upper()
        if "2" in src:
            return 2
        if "1" in src:
            return 1
        return None

    def trigger_level_50(self) -> float:
        """Put the trigger level at the midpoint of the source channel's signal.

        This is what pressing the front panel's LEVEL knob does.
        """
        ch = self.trigger_channel()
        if ch is None:
            raise ScopeError("50% needs CHAN1 or CHAN2 as the trigger source.")
        vmax = self.qfloat(f":MEAS:VMAX? CHAN{ch}")
        vmin = self.qfloat(f":MEAS:VMIN? CHAN{ch}")
        if abs(vmax) > 1e37 or abs(vmin) > 1e37:
            raise ScopeError(f"No measurable signal on CH{ch}.")
        level = (vmax + vmin) / 2.0
        self.set_trigger(level=level)
        return level

    def clamp_trigger_level(self, level: float,
                            ch: Optional[int] = None) -> float:
        """Clamp to the division window the scope accepts.

        Out-of-range levels are rejected silently, which just looks like
        "setting the level did nothing".
        """
        ch = ch if ch is not None else self.trigger_channel()
        if ch is None:
            return level
        limit = self.profile.trigger_level_divs * self.volt_scale(ch)
        return max(-limit, min(limit, level))

    # --- timed trigger conditions (PULSE, SLOPE) ---------------------------

    def timed_trigger_spec(self, mode: Optional[str] = None):
        """The condition/duration parameters the current mode offers.

        Raises if the mode has none — EDGE fires on a level, and VIDEO and the
        pattern modes take a different shape of parameter entirely.
        """
        mode = (mode or self.trigger_mode()).upper()
        spec = self.profile.timed_triggers.get(mode)
        if spec is None:
            supported = ", ".join(sorted(self.profile.timed_triggers))
            raise ScopeError(
                f"{mode} trigger has no width/time condition (only {supported} do).")
        return mode, spec

    def has_timed_trigger(self, mode: Optional[str] = None) -> bool:
        return (mode or self.trigger_mode()).upper() in self.profile.timed_triggers

    def trigger_condition(self) -> str:
        """Which of the six +/- width conditions the mode is set to."""
        _, spec = self.timed_trigger_spec()
        return self.query(f":TRIG:{spec.subtree}:{spec.condition_leaf}?")

    def trigger_condition_time(self) -> float:
        """The pulse width, or the slope time, depending on the mode."""
        _, spec = self.timed_trigger_spec()
        return self.qfloat(f":TRIG:{spec.subtree}:{spec.time_leaf}?")

    def set_trigger_condition(self, condition=None, seconds=None) -> None:
        """Write only the condition parameters that were supplied."""
        _, spec = self.timed_trigger_spec()

        if condition is not None:
            keyword = spec.keyword_for(str(condition))
            if keyword is None:
                raise ScopeError(
                    f"Unknown trigger condition {condition!r}; expected one of "
                    f"{list(spec.conditions)}")
            self.write(f":TRIG:{spec.subtree}:{spec.condition_leaf} {keyword}")

        if seconds is not None:
            if not spec.time_min <= seconds <= spec.time_max:
                raise ScopeError(
                    f"Width/time must be between {eng(spec.time_min, 's')} and "
                    f"{eng(spec.time_max, 's')}.")
            self.write(f":TRIG:{spec.subtree}:{spec.time_leaf} "
                       f"{scpi_number(seconds)}")

    def holdoff(self) -> float:
        return self.qfloat(":TRIG:HOLD?")

    def set_holdoff(self, seconds: float) -> None:
        low, high = self.profile.holdoff_min, self.profile.holdoff_max
        if not low <= seconds <= high:
            raise ScopeError(f"Holdoff must be between {eng(low, 's')} "
                             f"and {eng(high, 's')}.")
        self.write(f":TRIG:HOLD {scpi_number(seconds)}")

    def single(self, timeout_s: float = 30.0, poll: float = 0.2,
               should_abort: Optional[Callable[[], bool]] = None) -> bool:
        """Arm a one-shot capture and block until the scope reports STOP.

        Returns True if a real trigger arrived, False on timeout/abort.
        """
        mode, sub = self.trigger_subsys()
        if mode != "ALTERNATION":
            self.write(f":TRIG:{sub}:SWE SINGLE")
        self.run()
        time.sleep(0.3)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if should_abort is not None and should_abort():
                self.stop()
                return False
            if self.trigger_status() == "STOP":
                return True
            time.sleep(poll)

        self.stop()
        return False

    # --- acquisition settings --------------------------------------------

    def set_acquire(self, atype=None, average=None, memdepth=None) -> None:
        if atype is not None:
            self.write(f":ACQ:TYPE {atype.upper()}")
        if average is not None:
            low, high = self.profile.average_min, self.profile.average_max
            if not low <= int(average) <= high:
                raise ScopeError(f"Average count must be between {low} and {high}.")
            self.write(":ACQ:TYPE AVERAGE")
            self.write(f":ACQ:AVER {int(average)}")
        if memdepth is not None:
            self.write(f":ACQ:MEMD {memdepth.upper()}")

    def acquire_type(self) -> str:
        return self.query(":ACQ:TYPE?")

    def average_count(self) -> str:
        return self.query(":ACQ:AVER?")

    def memory_depth(self) -> str:
        return self.query(":ACQ:MEMD?")

    # --- horizontal / vertical -------------------------------------------

    def timebase(self) -> float:
        return self.qfloat(":TIM:SCAL?")

    def time_offset(self) -> float:
        return self.qfloat(":TIM:OFFS?")

    def set_timebase(self, sec_per_div: float) -> None:
        self.write(f":TIM:SCAL {scpi_number(sec_per_div)}")

    def set_time_offset(self, seconds: float) -> None:
        self.write(f":TIM:OFFS {scpi_number(seconds)}")

    def channel_on(self, ch: int) -> bool:
        return self.query(f":CHAN{ch}:DISP?") in ("1", "ON")

    def set_channel_on(self, ch: int, on: bool) -> None:
        self.write(f":CHAN{ch}:DISP {'ON' if on else 'OFF'}")

    def volt_scale(self, ch: int) -> float:
        return self.qfloat(f":CHAN{ch}:SCAL?")

    def set_volt_scale(self, ch: int, volts_per_div: float) -> None:
        self.write(f":CHAN{ch}:SCAL {scpi_number(volts_per_div)}")

    def volt_offset(self, ch: int) -> float:
        return self.qfloat(f":CHAN{ch}:OFFS?")

    def set_volt_offset(self, ch: int, volts: float) -> None:
        self.write(f":CHAN{ch}:OFFS {scpi_number(volts)}")

    def set_coupling(self, ch: int, coupling: str) -> None:
        self.write(f":CHAN{ch}:COUP {coupling.upper()}")

    def coupling(self, ch: int) -> str:
        return self.query(f":CHAN{ch}:COUP?")

    def probe(self, ch: int) -> float:
        return self.qfloat(f":CHAN{ch}:PROB?")

    def set_probe(self, ch: int, ratio: float) -> None:
        """Set the probe attenuation.

        The scope rescales volts/div and the offset to match, so this must be
        written *before* any scale or offset you also intend to set.
        """
        ratio = float(ratio)
        if ratio not in self.profile.probe_ratios:
            raise ScopeError(
                f"Probe ratio must be one of {list(self.profile.probe_ratios)}")
        self.write(f":CHAN{ch}:PROB {ratio:g}")

    # --- state snapshot ----------------------------------------------------

    def snapshot(self, channels: Iterable[int] = (1, 2)) -> ScopeSettings:
        """Read back the current front-panel state.

        Used both to mirror the instrument onto the GUI panel and to write
        setup files.
        """
        settings = ScopeSettings(
            idn=self.idn,
            timebase=self.timebase(),
            time_offset=self.time_offset(),
            acquire_type=self.acquire_type().upper(),
            average=int(float(self.average_count())),
            memory_depth=self.memory_depth().upper(),
            trigger_mode=self.trigger_mode(),
            channels={ch: ChannelSettings(on=self.channel_on(ch),
                                          volt_scale=self.volt_scale(ch),
                                          coupling=self.coupling(ch).upper(),
                                          probe=self.probe(ch),
                                          volt_offset=self.volt_offset(ch))
                      for ch in channels},
        )
        # Not every trigger mode exposes all of these, so gather them one by
        # one rather than letting the first gap abandon the rest. The condition
        # keyword keeps the instrument's own casing: the mixed case is the SCPI
        # short form, and upper-casing it loses that.
        for attr, getter, upper in (
                ("trigger_source", self.trigger_source, True),
                ("trigger_slope", self.trigger_slope, True),
                ("trigger_sweep", self.trigger_sweep, True),
                ("trigger_level", self.trigger_level, False),
                ("trigger_holdoff", self.holdoff, False),
                ("trigger_condition", self.trigger_condition, False),
                ("trigger_condition_time", self.trigger_condition_time, False)):
            try:
                value = getter()
            except Exception:
                continue
            if upper and isinstance(value, str):
                value = value.upper()
            setattr(settings, attr, value)
        return settings

    def restore(self, state) -> List[str]:
        """Apply a snapshot. Missing values are simply left alone.

        Returns human-readable warnings for anything that failed; one
        unsupported setting must not abort the whole restore.
        """
        if not isinstance(state, ScopeSettings):
            state = ScopeSettings.from_dict(state)

        warnings: List[str] = []

        def attempt(label, action):
            try:
                action()
            except Exception as exc:
                warnings.append(f"{label}: {exc}")

        for ch, info in sorted(state.channels.items()):
            # Probe first: it rescales volts/div and offset underneath us.
            if info.probe is not None:
                attempt(f"CH{ch} probe",
                        lambda c=ch, v=info.probe: self.set_probe(c, v))
            if info.coupling:
                attempt(f"CH{ch} coupling",
                        lambda c=ch, v=info.coupling: self.set_coupling(c, v))
            attempt(f"CH{ch} scale",
                    lambda c=ch, v=info.volt_scale: self.set_volt_scale(c, v))
            if info.volt_offset is not None:
                attempt(f"CH{ch} offset",
                        lambda c=ch, v=info.volt_offset: self.set_volt_offset(c, v))
            attempt(f"CH{ch} display",
                    lambda c=ch, v=info.on: self.set_channel_on(c, v))

        attempt("timebase", lambda: self.set_timebase(state.timebase))
        attempt("time offset", lambda: self.set_time_offset(state.time_offset))
        attempt("acquire", lambda: self.set_acquire(
            state.acquire_type or None,
            state.average if state.acquire_type == "AVERAGE" else None,
            state.memory_depth or None))

        if state.trigger_mode:
            attempt("trigger mode",
                    lambda: self.set_trigger_mode(state.trigger_mode))
        attempt("trigger", lambda: self.set_trigger(
            source=state.trigger_source, slope=state.trigger_slope,
            level=state.trigger_level, sweep=state.trigger_sweep,
            holdoff=state.trigger_holdoff))

        # Only meaningful once the mode above has been applied.
        if (state.trigger_condition is not None
                or state.trigger_condition_time is not None):
            attempt("trigger condition", lambda: self.set_trigger_condition(
                condition=state.trigger_condition,
                seconds=state.trigger_condition_time))

        return warnings

    # --- measurements ------------------------------------------------------

    MEASUREMENTS = (("Vpp", "VPP"), ("Vmax", "VMAX"), ("Vmin", "VMIN"),
                    ("Vavg", "VAVerage"), ("Vrms", "VRMS"),
                    ("Freq", "FREQuency"), ("Period", "PERiod"))

    def measure(self, ch: int) -> Dict[str, Optional[float]]:
        """Read the handful of automatic measurements worth showing live.

        The scope returns a large sentinel (>1e37) when a value is unavailable,
        e.g. frequency on a flat trace; those come back as None.
        """
        out: Dict[str, Optional[float]] = {}
        for label, cmd in self.MEASUREMENTS:
            try:
                value = self.qfloat(f":MEAS:{cmd}? CHAN{ch}")
            except Exception:
                out[label] = None
                continue
            out[label] = None if abs(value) > 1e37 else value
        return out

    # --- waveform ----------------------------------------------------------

    def read_channel(self, ch: int, points: str = "normal"):
        """Return the volts array for one channel.

        points='normal' gives the displayed samples; 'raw' reads deep memory,
        which only works while the acquisition is stopped.
        """
        if points == "raw":
            self.stop()
            self.write(":WAV:POIN:MODE RAW")
            timeout = TIMEOUT_RAW_MS
        else:
            self.write(":WAV:POIN:MODE NORM")
            timeout = TIMEOUT_NORM_MS

        with self.transport.timeout(timeout):
            self.write(f":WAV:DATA? CHAN{ch}")
            raw = self.transport.read_raw()

        try:
            payload = wf.parse_block(raw)
        except wf.WaveformError as exc:
            raise ScopeError(f"CH{ch}: {exc}")

        return wf.decode(payload, self.profile,
                         self.volt_scale(ch), self.volt_offset(ch))

    def capture(self, channels: Iterable[int] = (1, 2), points: str = "normal",
                skip_off: bool = True) -> wf.Waveform:
        """Read several channels and build a shared time axis."""
        traces = {}
        for ch in channels:
            if skip_off and not self.channel_on(ch):
                continue
            traces[ch] = self.read_channel(ch, points)

        if not traces:
            raise ScopeError("No channels captured. Turn a channel on and retry.")

        npts = min(len(v) for v in traces.values())
        traces = {ch: v[:npts] for ch, v in traces.items()}

        scale, offset = self.timebase(), self.time_offset()
        return wf.Waveform(
            t=wf.time_axis(npts, scale, offset, self.profile),
            channels=traces,
            timebase=scale,
            time_offset=offset,
            points_mode=points,
        )
