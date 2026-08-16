"""Command-line waveform export for the Rigol DS1102E / DS1000D-E.

Any setting you don't pass is left exactly as it is on the scope, so this is
safe to run against a setup you dialed in by hand.

Examples:
    ultrascope-dump
        600-pt screen data from whatever is currently displayed.

    ultrascope-dump --single --trigger-level 1.5 --trigger-slope neg
        Arm a one-shot falling-edge trigger at 1.5 V, wait for it, then dump.

    ultrascope-dump --single --mode raw --memdepth long --channels 1
        One-shot deep-memory capture, 1M points on CH1.

    ultrascope-dump --acquire average --average 16 --plot
        Averaged acquisition, write CSV + PNG.

    ultrascope-dump --trigger-mode pulse --trigger-condition "+Width <" --trigger-width 100e-9
        Trigger on positive pulses narrower than 100 ns.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from . import export
from .scope import Scope, ScopeError
from .setup_file import load_setup, save_setup
from .units import eng

SLOPE_ALIASES = {"pos": "positive", "neg": "negative"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ultrascope-dump",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    ap.add_argument("--resource", help="VISA resource string (auto-detected if omitted)")
    ap.add_argument("--channels", default="1,2", help="comma-separated, e.g. 1 or 1,2")
    ap.add_argument("--mode", choices=["normal", "raw"], default="normal",
                    help="normal = 600 displayed points; raw = deep memory (needs STOP)")
    ap.add_argument("--out", default="waveform.csv")
    ap.add_argument("--plot", action="store_true", help="also write <out>.png")
    ap.add_argument("--measure", action="store_true",
                    help="print Vpp/Vrms/frequency etc. for each channel")

    trig = ap.add_argument_group("trigger")
    trig.add_argument("--trigger-mode",
                      choices=["edge", "pulse", "video", "slope", "pattern",
                               "duration", "alternation"])
    trig.add_argument("--trigger-source",
                      choices=["1", "2", "ch1", "ch2", "chan1", "chan2", "ext", "acline"])
    trig.add_argument("--trigger-slope", choices=["positive", "negative", "pos", "neg"])
    trig.add_argument("--trigger-level", type=float, metavar="VOLTS")
    trig.add_argument("--trigger-coupling", choices=["dc", "ac", "hf", "lf"])
    trig.add_argument("--trigger-holdoff", type=float, metavar="SECONDS")
    trig.add_argument("--sweep", choices=["auto", "normal", "single"],
                      help="sweep mode; --single is a shortcut for 'single' + wait")
    trig.add_argument("--single", action="store_true",
                      help="arm a one-shot capture and wait for the trigger")
    trig.add_argument("--trigger-timeout", type=float, default=30.0, metavar="SECONDS",
                      help="how long --single waits before giving up (default 30)")
    trig.add_argument("--trigger-condition", metavar="COND",
                      help="pulse/slope condition, e.g. '+Width <' or '-Slope >' "
                           "(pulse and slope trigger modes only)")
    trig.add_argument("--trigger-width", type=float, metavar="SECONDS",
                      help="pulse width or slope time, 20ns..10s "
                           "(pulse and slope trigger modes only)")

    acq = ap.add_argument_group("acquisition")
    acq.add_argument("--acquire", choices=["normal", "average", "peakdetect"])
    acq.add_argument("--average", type=int, metavar="N",
                     help="average count, 2..256 (implies --acquire average)")
    acq.add_argument("--memdepth", choices=["normal", "long"],
                     help="'long' is required for a full 1M-point --mode raw")
    acq.add_argument("--timebase", type=float, metavar="SEC_PER_DIV")

    vert = ap.add_argument_group("vertical / horizontal")
    vert.add_argument("--probe", type=float, metavar="RATIO",
                      help="probe attenuation applied to --channels, e.g. 1 or 10")
    vert.add_argument("--offset", type=float, metavar="VOLTS",
                      help="vertical offset applied to --channels")
    vert.add_argument("--position", type=float, metavar="SECONDS",
                      help="horizontal position (:TIM:OFFS)")

    setup = ap.add_argument_group("setup files")
    setup.add_argument("--load-setup", metavar="PATH",
                       help="apply a JSON setup saved by the GUI or --save-setup, "
                            "before any other option is applied")
    setup.add_argument("--save-setup", metavar="PATH",
                       help="write the scope's full state to JSON and exit")
    return ap


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    args.trigger_slope = SLOPE_ALIASES.get(args.trigger_slope, args.trigger_slope)
    args.channel_list = parse_channels(args.channels)
    return args


def parse_channels(text: str) -> List[int]:
    return [int(c) for c in text.split(",")]


def format_measurements(stats) -> str:
    parts = []
    for label, value in stats.items():
        unit = "Hz" if label == "Freq" else ("s" if label == "Period" else "V")
        parts.append(f"{label}={eng(value, unit) if value is not None else '--'}")
    return "  ".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)

    try:
        scope = Scope.connect(args.resource)
    except ScopeError as exc:
        sys.exit(str(exc))

    with scope:
        print("Connected:", scope.idn)

        if args.save_setup:
            save_setup(args.save_setup, scope.snapshot())
            print("Wrote", args.save_setup)
            return

        # A setup file is the baseline; explicit options override it below.
        if args.load_setup:
            for warning in scope.restore(load_setup(args.load_setup)):
                print("setup:", warning)
            print("Applied", args.load_setup)

        for ch in args.channel_list:
            # Probe first: it rescales volts/div and the offset underneath.
            if args.probe is not None:
                scope.set_probe(ch, args.probe)
            if args.offset is not None:
                scope.set_volt_offset(ch, args.offset)

        scope.set_acquire(args.acquire, args.average, args.memdepth)
        if args.timebase is not None:
            scope.set_timebase(args.timebase)
        if args.position is not None:
            scope.set_time_offset(args.position)

        if args.trigger_mode:
            scope.set_trigger_mode(args.trigger_mode)
        scope.set_trigger(
            source=args.trigger_source,
            slope=args.trigger_slope,
            level=args.trigger_level,
            coupling=args.trigger_coupling,
            holdoff=args.trigger_holdoff,
            # --single arms the sweep itself, so don't set it twice.
            sweep=None if args.single else args.sweep,
        )
        # Only meaningful in PULSE/SLOPE, and only after the mode is applied.
        if args.trigger_condition is not None or args.trigger_width is not None:
            scope.set_trigger_condition(condition=args.trigger_condition,
                                        seconds=args.trigger_width)

        print(f"Trigger: {scope.trigger_mode()}, status {scope.trigger_status()}")

        if args.single:
            print(f"Armed, waiting up to {args.trigger_timeout:.0f} s for a trigger...")
            if scope.single(args.trigger_timeout):
                print("Triggered.")
            else:
                print("Timed out; reading whatever is in memory.")

        wave = scope.capture(args.channel_list, points=args.mode)
        for ch in wave.channel_ids:
            print(f"CH{ch}: {len(wave.channels[ch])} points")

        export.save_csv(args.out, wave)
        print("Wrote", args.out)

        if args.measure:
            for ch in wave.channel_ids:
                print(f"CH{ch}  " + format_measurements(scope.measure(ch)))

        if args.plot:
            png = export.save_png(export.png_path_for(args.out), wave)
            print("Wrote", png)


if __name__ == "__main__":
    main()
