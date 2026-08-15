# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python package for Rigol DS1102E / DS1000D-E oscilloscopes ("UltraScopeSimple", a lightweight alternative to UltraScope). Ships a CLI and a Tkinter GUI over a shared instrument layer.

## Commands

```bash
pip install -e ".[gui,test]"
```

- `pytest` — unit tests; no hardware needed (see Testing below)
- `pytest tests/test_scope.py::test_alternation_has_no_sweep_setting` — single test
- `ultrascope-dump` / `python -m ultrascope.cli` — CLI capture/export
- `ultrascope-gui` / `python -m ultrascope.gui` — GUI
- No lint or build step is configured.

## Architecture

Strictly one-directional layering — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full rationale and the module-by-module design.

```
cli.py / gui/   →  scope.py  →  waveform.py + profile.py  →  transport.py
```

- `transport.py` — **the only module that imports pyvisa.** `Transport` protocol, `PyVisaTransport`, and `FakeTransport` for tests. Timeout changes go through the `timeout(ms)` context manager, never by assigning to the device.
- `profile.py` — `DeviceProfile` holds every per-model hardware fact (code inversion/centre/codes-per-div, horizontal divisions, scale tables, trigger subtree map, average limits). New model = new profile, not edits to the decode path.
- `waveform.py` — pure functions `parse_block` / `decode` / `time_axis` plus the `Waveform` value object. No instrument access; this is the most test-covered code.
- `scope.py` — `Scope` SCPI facade, built on a `Transport` + `DeviceProfile`. `Scope.connect()` is the convenience constructor. `snapshot()` returns a `ScopeSettings` the GUI mirrors onto its panels.
- `gui/` — `worker.py` (the one thread allowed to touch the instrument), `state.py` (combobox option tables), `panels.py`, `plot.py`, `app.py` (assembly + `(tag, kind, payload)` dispatch to `_on_<tag>`).

### Invariants that must not be broken

- **Setters take `None` for "leave alone".** A setting the user did not pass is never written, so the CLI is safe to run against a hand-dialled setup. `tests/test_scope.py` asserts this; keep it asserted.
- **`Scope` is not thread-safe.** All instrument I/O runs on the `Worker` thread; the Tk thread only touches widgets. The UI never constructs or holds a `Scope` — `Worker.connect()` does, on its own thread.
- Live mode *is* the worker's idle path: job queue empty + `streaming` set ⇒ capture a frame.

## Hardware/SCPI quirks (verified in code)

- DS1000E speaks a legacy SCPI dialect: **no `:WAV:PREamble`**. Byte→volt conversion is fixed-scale — codes inverted (`255 - data`), centred on 130, 25 codes per vertical division. All of this is in `profile.py`; do not re-inline it.
- The instrument reports no per-sample timing: the time axis spans 12 horizontal divisions centred on the time offset.
- Deep-memory (`points="raw"`) reads only work while acquisition is STOPPED and need `TIMEOUT_RAW_MS` (120 s); 1M-point reads are slow. `normal` is 600 displayed points.
- `:TRIG:MODE?` returns a full word but the command subtrees are abbreviated — go through `Scope.trigger_subsys()`, never interpolate the mode. ALTERNATION has no sweep setting.
- Measurements return a >1e37 sentinel when unavailable; `measure()` maps those to `None`.
- Average count is limited to 2–256.
- The USB driver comes from official UltraSigma software; VISA enumeration finds nothing without it.
- `close()` sends `:KEY:FORC` to hand front-panel control back to the user.

## Testing

`FakeTransport` replays scripted SCPI answers and canned 488.2 blocks, so decoding, trigger routing, the "not passed ⇒ not written" contract, and CLI parsing are all testable offline. Everything above the instrument layer — real VISA traffic, GUI interaction — still needs a connected scope and manual verification.

## Repo constraints

- `.gitignore` excludes `官方软件/` (vendor .rar installers), capture outputs (`*.csv`, `*.png`), and build/test artifacts. Keep those out of commits.
- README.md (Chinese) is the usage doc and `docs/ARCHITECTURE.md` (Chinese) the design doc; keep both in sync when CLI options or module boundaries change. `AGENTS.md` is a condensed version of this file — update together.
- Commit messages in this repo are written in Chinese.
