# AGENTS.md

Python package for Rigol DS1102E / DS1000D-E oscilloscopes. CLI + Tkinter GUI over a shared instrument layer. Full design: `docs/ARCHITECTURE.md`.

## Commands

- `pip install -e ".[gui,test]"`
- `pytest` — offline unit tests; `pytest tests/test_cli.py::test_channel_list_parsing` for one
- `ultrascope-dump` (CLI) / `ultrascope-gui` (GUI); `python -m ultrascope.cli` / `python -m ultrascope.gui` also work
- No lint or build step.

## Architecture

Layering is strictly one-directional: `cli.py`/`gui/` → `scope.py` → `waveform.py` + `profile.py` → `transport.py`.

- `transport.py` — the only module importing pyvisa in the capture path (`discovery.py` also does, to enumerate resources; `tests/test_layering.py` enforces the rest). `Transport` protocol + `PyVisaTransport` + `FakeTransport`. Timeouts change via the `timeout(ms)` context manager.
- `profile.py` — `DeviceProfile` holds every per-model hardware fact, including the PULSE/SLOPE condition specs. New model = new profile, not edits to the decode path.
- `waveform.py` — pure `parse_block` / `decode` / `time_axis` + the `Waveform` value object. `parse_block` rejects short transfers rather than truncating.
- `analysis.py` — local measurement over a `Waveform` (cursors, sampling, FFT, trace parameters, channel maths, XY). No SCPI, so it works while disconnected. Channel maths is local by choice, not via `:MATH:OPER`.
- `scope.py` — `Scope` SCPI facade; `Scope.connect()` convenience constructor; `snapshot()` → `ScopeSettings`.
- `gui/` — `worker.py` / `state.py` / `panels.py` / `plot.py` / `app.py`. Works with no instrument: Open CSV sets `last_capture` and every analysis panel reads only that. Panels that must stay live while disconnected are listed in `App.ALWAYS_ENABLED`. The sidebar is a notebook driven by `App.TABS`; a panel missing from it is built but never gridded (a test catches that). Each tab scrolls when it outgrows the window.

### Invariants

- Numeric SCPI parameters must be plain decimals. The scope accepts a command like `:TIM:SCAL 5e-5` and then silently ignores it, so exponent notation makes a setting appear not to work. Every float written to the instrument goes through `units.scpi_number()`; 14 of the 32 timebase steps were broken this way before it existed.
- Setters take `None` for "leave alone" — a setting the user did not pass is never written. Asserted in `tests/test_scope.py`; keep it that way.
- `Scope` is not thread-safe. All I/O on the `Worker` thread, widgets only on the Tk thread. The UI never holds a `Scope`; `Worker.connect()` creates it on its own thread.
- Live mode is the worker's idle path: empty job queue + `streaming` set ⇒ capture a frame.

## Hardware/SCPI quirks (verified in code)

- Legacy SCPI dialect: **no `:WAV:PREamble`**. Fixed-scale conversion — codes inverted, centred on 130, 25 codes/division. Lives in `profile.py`; do not re-inline.
- No per-sample timing: the time axis spans 12 horizontal divisions centred on the time offset.
- Deep memory (`points="raw"`) requires STOP and the 120 s timeout; `normal` is 600 points.
- `:TRIG:MODE?` returns a full word, subtrees are abbreviated — always via `trigger_subsys()`. ALTERNATION has no sweep.
- PULSE/SLOPE command spellings in `profile.py` are unverified and untested on hardware (the bundled manual is the User's Guide, no SCPI reference). All spellings live in `DS1000E_PULSE_TRIGGER`/`DS1000E_SLOPE_TRIGGER`; tests assert composition, not spelling.
- Measurements return >1e37 when unavailable → mapped to `None`. Average count 2–256.
- USB driver comes from UltraSigma; without it VISA enumerates nothing.
- `close()` sends `:KEY:FORC` to return front-panel control.

## Testing

`FakeTransport` replays scripted SCPI answers and canned 488.2 blocks — decoding, trigger routing, the not-passed-not-written contract and CLI parsing all run offline. Real VISA traffic and GUI interaction still need a connected scope.

## Repo constraints

- `.gitignore` excludes `官方软件/`, capture outputs (`*.csv`, `*.png`), build/test artifacts.
- Docs are Chinese: `README.md` usage, `docs/ARCHITECTURE.md` design, `HANDOVER.md` rationale + verification status, `ROADMAP.md` what's next. Keep in sync with CLI/module changes. `CLAUDE.md` is the fuller version of this file — update together.
- Commit messages are written in Chinese.
