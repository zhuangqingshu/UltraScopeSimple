# AGENTS.md

Python toolset for Rigol DS1102E / DS1000D-E oscilloscopes. Flat repo, three scripts, no tests/lint/CI. Runs against real hardware only.

## Commands

- `python ds1102e_dump.py` — CLI capture/export (no GUI deps beyond numpy/pyvisa)
- `python ds1102e_scope.py` — Tkinter GUI (needs matplotlib)
- No test, lint, or build commands exist. Verification is manual against a connected scope.

## Architecture

- `ds1102e.py` — shared comm layer (`Scope` class). The other two files import it as `ds1102e`; run scripts from repo root.
- `ds1102e_dump.py` — argparse CLI. Only passes options the user supplies; everything else stays as set on the instrument.
- `ds1102e_scope.py` — Tk GUI. All instrument I/O runs on a single worker thread (queue + `Worker` class); the Tk thread only touches widgets. Never call `Scope` from the Tk thread or from two threads at once (`Scope` is not thread-safe).

## Hardware/SCPI quirks (verified in code)

- DS1000E speaks a legacy SCPI dialect: **no `:WAV:PREamble`**. Byte→volt conversion is the fixed scale: codes inverted (`255 - data`), centered on 130, 25 codes per vertical division.
- Time axis in `capture()` is always 12 horizontal divisions centered on the time offset (`np.linspace(offset - 6*scale, offset + 6*scale, npts)`).
- Deep-memory (`points="raw"`) reads only work while acquisition is STOPPED and need the long timeout (120 s); 1M-point reads are slow. `normal` is 600 displayed points.
- The scope's USB driver comes from official UltraSigma software; VISA auto-detect fails without it.
- `close()` sends `:KEY:FORC` to hand front-panel control back to the user.

## Repo constraints

- `.gitignore` excludes `官方软件/` (vendor .rar installers), `*.csv` (capture outputs), and `__pycache__/`. Keep those out of commits.
- README.md is the usage doc; keep it in sync when CLI options change.
