"""
Command-line waveform export for the Rigol DS1102E / DS1000D-E.

Any setting you don't pass is left exactly as it is on the scope, so this is
safe to run against a setup you dialed in by hand. See ds1102e.py for the
communication layer, and ds1102e_scope.py for the GUI version.

Examples:
    python ds1102e_dump.py
        600-pt screen data from whatever is currently displayed.

    python ds1102e_dump.py --single --trigger-level 1.5 --trigger-slope neg
        Arm a one-shot falling-edge trigger at 1.5 V, wait for it, then dump.

    python ds1102e_dump.py --single --mode raw --memdepth long --channels 1
        One-shot deep-memory capture, 1M points on CH1.

    python ds1102e_dump.py --acquire average --average 16 --plot
        Averaged acquisition, write CSV + PNG.
"""

import argparse
import json
import sys

import ds1102e as rig


def build_parser():
    ap = argparse.ArgumentParser(
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


def main():
    args = build_parser().parse_args()
    if args.trigger_slope in ("pos", "neg"):
        args.trigger_slope = {"pos": "positive", "neg": "negative"}[args.trigger_slope]

    channels = [int(c) for c in args.channels.split(",")]

    try:
        scope = rig.Scope(args.resource)
    except rig.ScopeError as exc:
        sys.exit(str(exc))

    with scope:
        print("Connected:", scope.idn)

        if args.save_setup:
            with open(args.save_setup, "w", encoding="utf-8") as fh:
                json.dump(scope.snapshot(), fh, indent=2, ensure_ascii=False)
            print("Wrote", args.save_setup)
            return

        # A setup file is the baseline; explicit options override it below.
        if args.load_setup:
            with open(args.load_setup, encoding="utf-8") as fh:
                for warning in scope.restore(json.load(fh)):
                    print("setup:", warning)
            print("Applied", args.load_setup)

        for ch in channels:
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
        print(f"Trigger: {scope.trigger_mode()}, status {scope.trigger_status()}")

        if args.single:
            print(f"Armed, waiting up to {args.trigger_timeout:.0f} s for a trigger...")
            if scope.single(args.trigger_timeout):
                print("Triggered.")
            else:
                print("Timed out; reading whatever is in memory.")

        t, traces = scope.capture(channels, points=args.mode)
        for ch, volts in sorted(traces.items()):
            print(f"CH{ch}: {len(volts)} points")

        rig.save_csv(args.out, t, traces)
        print("Wrote", args.out)

        if args.measure:
            for ch in sorted(traces):
                stats = scope.measure(ch)
                parts = []
                for label, value in stats.items():
                    unit = "Hz" if label == "Freq" else ("s" if label == "Period" else "V")
                    parts.append(f"{label}="
                                 f"{rig.eng(value, unit) if value is not None else '--'}")
                print(f"CH{ch}  " + "  ".join(parts))

        if args.plot:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(10, 5))
            for ch in sorted(traces):
                ax.plot(t, traces[ch], linewidth=0.8, label=f"CH{ch}")
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Voltage (V)")
            ax.grid(True, alpha=0.3)
            ax.legend()
            png = args.out.rsplit(".", 1)[0] + ".png"
            fig.savefig(png, dpi=150, bbox_inches="tight")
            print("Wrote", png)


if __name__ == "__main__":
    main()
