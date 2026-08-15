# AGENTS.md

Python toolset for Rigol DS1102E / DS1000D-E oscilloscopes. Flat repo, three scripts, no tests/lint/CI. Runs against real hardware only.

## Commands

- `python ds1102e_dump.py` — CLI capture/export (no GUI deps beyond numpy/pyvisa)
- `python ds1102e_scope.py` — Tkinter GUI (needs matplotlib)
- No test, lint, or build commands exist. Verification is manual against a connected scope.

## Architecture

- `ds1102e.py` — shared comm layer (`Scope` class). The other two files import it as `ds1102e`; run scripts from repo root.
- `ds1102e_dump.py` — argparse CLI. Only passes options the user supplies; everything else stays as set on the instrument.
- `ds1102e_scope.py` — Tk GUI. All instrument I/O runs on a single worker thread (queue + `Worker` class); the Tk thread only touches widgets. Never call `Scope` from the Tk thread or from two threads at once (`Scope` is not thread-safe). Worker results are routed by tag: a job submitted with tag `foo` lands in `App._on_foo`.
- `ROADMAP.md` — staged feature plan; stage 1 is done. Tick items there when they land.

## GUI interaction rules

- **Mouse drags must not write SCPI per motion event.** Trigger-level drag and drag-to-pan both mutate only local state / matplotlib limits while the button is down, then issue one write on release. Live streaming at ~5-10 fps already saturates the USBTMC link.
- `_on_trace` skips axis-limit updates while `self.pan` is set, so live refresh doesn't fight an in-progress drag.
- Displayed volts are raw volts *minus* the channel offset, so dragging a trace up by Δ means writing `offset - Δ`.
- Probe ratio must be written before volts/div and offset: changing it rescales both.
- Trigger level outside ±6 divisions is silently ignored by the scope — always clamp (`Scope.clamp_trigger_level`) and tell the user.

## Hardware/SCPI quirks (verified in code)

- DS1000E speaks a legacy SCPI dialect: **no `:WAV:PREamble`**. Byte→volt conversion is the fixed scale: codes inverted (`255 - data`), centered on 130, 25 codes per vertical division.
- Time axis in `capture()` is always 12 horizontal divisions centered on the time offset (`np.linspace(offset - 6*scale, offset + 6*scale, npts)`).
- Deep-memory (`points="raw"`) reads only work while acquisition is STOPPED and need the long timeout (120 s); 1M-point reads are slow. `normal` is 600 displayed points.
- The scope's USB driver comes from official UltraSigma software; VISA auto-detect fails without it.
- `close()` sends `:KEY:FORC` to hand front-panel control back to the user.
- `:TRIG:HOLD` accepts 500 ns .. 1.5 s; out-of-range values are ignored silently, as are out-of-range trigger levels.
- Measurement queries return a >1e37 sentinel when the value is unavailable (e.g. frequency of a flat trace); `Scope.measure` maps those to `None`.
- `Scope.snapshot()` / `Scope.restore()` are the JSON setup format shared by the GUI Setup panel and the CLI `--save-setup` / `--load-setup`. `restore()` collects per-setting failures as a warning list instead of aborting.

## Repo constraints

- `.gitignore` excludes `官方软件/` (vendor .rar installers), `*.csv` (capture outputs), and `__pycache__/`. Keep those out of commits.
- README.md is the usage doc; keep it in sync when CLI options change.
