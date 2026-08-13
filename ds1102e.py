"""
Shared communication layer for the Rigol DS1102E / DS1000D-E series.

Used by both ds1102e_dump.py (command line) and ds1102e_scope.py (GUI).

The DS1000E speaks Rigol's *legacy* SCPI dialect: there is no :WAV:PREamble,
so the byte -> volts conversion is the fixed-scale one for that generation
(codes are inverted, centered on 130, and 25 codes == 1 vertical division).
"""

import time

import numpy as np
import pyvisa

# The DS1000E is slow; deep-memory reads of 1M points need a very long timeout.
TIMEOUT_NORM_MS = 10_000
TIMEOUT_RAW_MS = 120_000

# :TRIG:MODE? answers with a full word, but the command subtrees are abbreviated.
TRIG_SUBSYS = {
    "EDGE": "EDGE",
    "PULSE": "PULS",
    "VIDEO": "VIDEO",
    "SLOPE": "SLOP",
    "PATTERN": "PATT",
    "DURATION": "DUR",
    "ALTERNATION": "ALT",
}

SOURCE_MAP = {"1": "CHAN1", "2": "CHAN2", "ch1": "CHAN1", "ch2": "CHAN2",
              "chan1": "CHAN1", "chan2": "CHAN2", "ext": "EXT", "acline": "ACLINE"}

# Volts/div and sec/div steps the DS1102E front panel actually offers.
VOLT_SCALES = [2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
TIME_SCALES = [
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
]


def eng(value, unit):
    """Format a value with an SI prefix, the way the scope's own display does."""
    if value == 0:
        return f"0 {unit}"
    for factor, prefix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""),
                           (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if abs(value) >= factor:
            scaled = value / factor
            text = f"{scaled:.6g}"
            return f"{text} {prefix}{unit}"
    return f"{value:.3g} {unit}"


def list_scopes(rm=None):
    rm = rm or pyvisa.ResourceManager()
    return [r for r in rm.list_resources() if "::DS1" in r or r.startswith("USB")]


class ScopeError(RuntimeError):
    pass


class Scope:
    """Thin wrapper over the DS1000E's SCPI command set.

    Not thread-safe: the GUI funnels every call through a single worker thread.
    """

    def __init__(self, resource=None, rm=None):
        self.rm = rm or pyvisa.ResourceManager()
        if resource is None:
            found = list_scopes(self.rm)
            if not found:
                raise ScopeError("No USB instrument found. "
                                 "Check that UltraSigma can see the scope first.")
            resource = found[0]
        self.resource = resource
        self.dev = self.rm.open_resource(resource)
        self.dev.timeout = TIMEOUT_NORM_MS
        self.dev.chunk_size = 1024 * 1024
        self.idn = self.dev.query("*IDN?").strip()

    # --- plumbing ---------------------------------------------------------

    def write(self, cmd):
        self.dev.write(cmd)

    def query(self, cmd):
        return self.dev.query(cmd).strip()

    def qfloat(self, cmd):
        return float(self.query(cmd))

    def close(self):
        try:
            # Remote mode locks the front panel; hand control back to the user.
            self.dev.write(":KEY:FORC")
        except Exception:
            pass
        try:
            self.dev.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- acquisition control ---------------------------------------------

    def run(self):
        self.write(":RUN")

    def stop(self):
        self.write(":STOP")

    def auto(self):
        self.write(":AUTO")

    def force_trigger(self):
        self.write(":FORC")

    def trigger_status(self):
        return self.query(":TRIG:STAT?").upper()

    def trigger_mode(self):
        return self.query(":TRIG:MODE?").upper()

    def trigger_subsys(self):
        mode = self.trigger_mode()
        sub = TRIG_SUBSYS.get(mode)
        if sub is None:
            raise ScopeError(f"Unrecognised trigger mode {mode!r}")
        return mode, sub

    def set_trigger_mode(self, mode):
        self.write(f":TRIG:MODE {mode.upper()}")
        time.sleep(0.2)  # the scope needs a moment to switch subsystems

    def set_trigger(self, source=None, slope=None, level=None,
                    coupling=None, sweep=None, holdoff=None):
        """Write only the trigger settings that were actually supplied."""
        mode, sub = self.trigger_subsys()

        if source is not None:
            self.write(f":TRIG:{sub}:SOUR {SOURCE_MAP.get(str(source).lower(), source)}")
        if slope is not None:
            self.write(f":TRIG:{sub}:SLOP {slope.upper()}")
        if coupling is not None:
            self.write(f":TRIG:{sub}:COUP {coupling.upper()}")
        if level is not None:
            self.write(f":TRIG:{sub}:LEV {level}")
        if holdoff is not None:
            self.write(f":TRIG:HOLD {holdoff}")
        if sweep is not None:
            if mode == "ALTERNATION":
                raise ScopeError("Alternation mode has no sweep setting.")
            self.write(f":TRIG:{sub}:SWE {sweep.upper()}")

    def trigger_level(self):
        _, sub = self.trigger_subsys()
        return self.qfloat(f":TRIG:{sub}:LEV?")

    def trigger_source(self):
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SOUR?")

    def trigger_slope(self):
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SLOP?")

    def trigger_sweep(self):
        _, sub = self.trigger_subsys()
        return self.query(f":TRIG:{sub}:SWE?")

    def single(self, timeout_s=30.0, poll=0.2, should_abort=None):
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

    def set_acquire(self, atype=None, average=None, memdepth=None):
        if atype is not None:
            self.write(f":ACQ:TYPE {atype.upper()}")
        if average is not None:
            if not 2 <= int(average) <= 256:
                raise ScopeError("Average count must be between 2 and 256.")
            self.write(":ACQ:TYPE AVERAGE")
            self.write(f":ACQ:AVER {int(average)}")
        if memdepth is not None:
            self.write(f":ACQ:MEMD {memdepth.upper()}")

    # --- horizontal / vertical -------------------------------------------

    def timebase(self):
        return self.qfloat(":TIM:SCAL?")

    def time_offset(self):
        return self.qfloat(":TIM:OFFS?")

    def set_timebase(self, sec_per_div):
        self.write(f":TIM:SCAL {sec_per_div}")

    def set_time_offset(self, seconds):
        self.write(f":TIM:OFFS {seconds}")

    def channel_on(self, ch):
        return self.query(f":CHAN{ch}:DISP?") in ("1", "ON")

    def set_channel_on(self, ch, on):
        self.write(f":CHAN{ch}:DISP {'ON' if on else 'OFF'}")

    def volt_scale(self, ch):
        return self.qfloat(f":CHAN{ch}:SCAL?")

    def set_volt_scale(self, ch, volts_per_div):
        self.write(f":CHAN{ch}:SCAL {volts_per_div}")

    def volt_offset(self, ch):
        return self.qfloat(f":CHAN{ch}:OFFS?")

    def set_volt_offset(self, ch, volts):
        self.write(f":CHAN{ch}:OFFS {volts}")

    def set_coupling(self, ch, coupling):
        self.write(f":CHAN{ch}:COUP {coupling.upper()}")

    def coupling(self, ch):
        return self.query(f":CHAN{ch}:COUP?")

    # --- measurements ------------------------------------------------------

    def measure(self, ch):
        """Read the handful of automatic measurements worth showing live.

        The scope returns a large sentinel (>1e37) when a value is unavailable,
        e.g. frequency on a flat trace; those come back as None.
        """
        out = {}
        for label, cmd in (("Vpp", "VPP"), ("Vmax", "VMAX"), ("Vmin", "VMIN"),
                           ("Vavg", "VAVerage"), ("Vrms", "VRMS"),
                           ("Freq", "FREQuency"), ("Period", "PERiod")):
            try:
                value = self.qfloat(f":MEAS:{cmd}? CHAN{ch}")
            except Exception:
                out[label] = None
                continue
            out[label] = None if abs(value) > 1e37 else value
        return out

    # --- waveform ----------------------------------------------------------

    def read_channel(self, ch, points="normal"):
        """Return the volts array for one channel.

        points='normal' gives the 600 displayed samples; 'raw' reads deep
        memory, which only works while the acquisition is stopped.
        """
        if points == "raw":
            self.stop()
            self.write(":WAV:POIN:MODE RAW")
            self.dev.timeout = TIMEOUT_RAW_MS
        else:
            self.write(":WAV:POIN:MODE NORM")
            self.dev.timeout = TIMEOUT_NORM_MS

        try:
            self.write(f":WAV:DATA? CHAN{ch}")
            raw = self.dev.read_raw()
        finally:
            self.dev.timeout = TIMEOUT_NORM_MS

        # IEEE 488.2 definite-length block: '#' + <n digits> + <n-digit length>
        if raw[:1] != b"#":
            raise ScopeError(f"CH{ch}: unexpected block header {raw[:12]!r}")
        ndigits = int(raw[1:2])
        header_len = 2 + ndigits
        payload = raw[header_len : header_len + int(raw[2:header_len])]

        data = np.frombuffer(payload, dtype=np.uint8).astype(np.float64)
        data = 255.0 - data  # the DS1000E sends inverted codes

        vscale = self.volt_scale(ch)
        voffset = self.volt_offset(ch)
        return (data - 130.0 - voffset / vscale * 25.0) / 25.0 * vscale

    def capture(self, channels=(1, 2), points="normal", skip_off=True):
        """Read several channels and build a shared time axis.

        Returns (time array, {channel: volts array}).
        """
        traces = {}
        for ch in channels:
            if skip_off and not self.channel_on(ch):
                continue
            traces[ch] = self.read_channel(ch, points)

        if not traces:
            raise ScopeError("No channels captured. Turn a channel on and retry.")

        npts = min(len(v) for v in traces.values())
        traces = {ch: v[:npts] for ch, v in traces.items()}

        # The screen spans 12 horizontal divisions, centered on the time offset.
        scale, offset = self.timebase(), self.time_offset()
        t = np.linspace(offset - 6 * scale, offset + 6 * scale, npts)
        return t, traces


def save_csv(path, t, traces):
    order = sorted(traces)
    cols = [t] + [traces[ch] for ch in order]
    header = "Time(s)," + ",".join(f"CH{ch}(V)" for ch in order)
    np.savetxt(path, np.column_stack(cols), delimiter=",",
               header=header, comments="", fmt="%.9g")
