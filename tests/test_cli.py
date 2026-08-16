import pytest

from ultrascope.cli import (build_parser, format_measurements, parse_args,
                            parse_channels)
from ultrascope.export import png_path_for


def test_defaults_match_the_documented_behaviour():
    args = parse_args([])
    assert args.mode == "normal"
    assert args.out == "waveform.csv"
    assert args.channel_list == [1, 2]
    assert args.trigger_timeout == 30.0
    # Nothing else is set, so nothing else gets written to the instrument.
    assert args.acquire is None
    assert args.timebase is None
    assert args.trigger_level is None


@pytest.mark.parametrize("given, expected", [
    ("neg", "negative"),
    ("pos", "positive"),
    ("negative", "negative"),
])
def test_slope_shorthand_expands(given, expected):
    assert parse_args(["--trigger-slope", given]).trigger_slope == expected


def test_channel_list_parsing():
    assert parse_channels("1") == [1]
    assert parse_channels("1,2") == [1, 2]


def test_deep_memory_invocation_from_the_readme():
    args = parse_args(["--single", "--mode", "raw",
                       "--memdepth", "long", "--channels", "1"])
    assert args.single is True
    assert args.mode == "raw"
    assert args.memdepth == "long"
    assert args.channel_list == [1]


def test_unknown_choices_are_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--mode", "turbo"])


def test_png_path_derives_from_the_csv_path():
    assert png_path_for("waveform.csv") == "waveform.png"
    assert png_path_for("out/run3.csv") == "out/run3.png"


def test_vertical_and_horizontal_options_default_to_unset():
    # Unset means "never written", which is what makes the tool safe to run
    # against a hand-dialled setup.
    args = parse_args([])
    assert args.probe is None
    assert args.offset is None
    assert args.position is None
    assert args.load_setup is None
    assert args.save_setup is None


def test_setup_and_probe_options_parse():
    args = parse_args(["--probe", "10", "--offset", "0.5",
                       "--position", "1e-4", "--load-setup", "s.json"])
    assert args.probe == 10.0
    assert args.offset == 0.5
    assert args.position == 1e-4
    assert args.load_setup == "s.json"


def test_measurement_line_marks_missing_values():
    text = format_measurements({"Vpp": 1.5, "Freq": None})
    assert "Vpp=1.5 V" in text
    assert "Freq=--" in text


def test_timed_trigger_options_default_to_unset():
    args = parse_args([])
    assert args.trigger_condition is None
    assert args.trigger_width is None


def test_timed_trigger_options_parse():
    args = parse_args(["--trigger-mode", "pulse",
                       "--trigger-condition", "+Width <",
                       "--trigger-width", "100e-9"])
    assert args.trigger_mode == "pulse"
    assert args.trigger_condition == "+Width <"
    assert args.trigger_width == 100e-9


# --- offline analysis (--open) ----------------------------------------------

import numpy as np  # noqa: E402

from ultrascope.analysis import MATH_OPS  # noqa: E402
from ultrascope.cli import format_readings  # noqa: E402
from ultrascope.export import save_csv  # noqa: E402
from ultrascope.profile import DS1000E  # noqa: E402
from ultrascope.waveform import Waveform, time_axis  # noqa: E402


@pytest.fixture
def capture_file(tmp_path):
    t = time_axis(600, 1e-3, 0.0, DS1000E)
    path = tmp_path / "capture.csv"
    save_csv(str(path), Waveform(t=t,
                                 channels={1: np.sin(2 * np.pi * 1000 * t),
                                           2: np.cos(2 * np.pi * 1000 * t)},
                                 timebase=1e-3, time_offset=0.0))
    return str(path)


def test_open_is_off_by_default():
    assert parse_args([]).open_path is None


def test_open_accepts_the_analysis_options(capture_file):
    args = parse_args(["--open", capture_file, "--measure", "--spectrum",
                       "--math", "CH1*CH2", "--window", "blackman"])
    assert args.open_path == capture_file
    assert args.measure and args.spectrum
    assert args.math == "CH1*CH2"
    assert args.window == "blackman"


@pytest.mark.parametrize("option", [
    ["--timebase", "1e-3"],
    ["--trigger-level", "1.5"],
    ["--single"],
    ["--resource", "USB0::x"],
    ["--save-setup", "s.json"],
])
def test_open_refuses_options_that_would_need_an_instrument(option, capture_file):
    # Accepting these silently would leave the user believing a setting was
    # applied to a scope that was never contacted.
    with pytest.raises(SystemExit):
        parse_args(["--open", capture_file] + option)


def test_open_still_allows_choosing_channels(capture_file):
    # --channels selects what to read out of the file, so it is not instrument
    # state and must keep working.
    args = parse_args(["--open", capture_file, "--channels", "1"])
    assert args.channel_list == [1]


def test_analysing_a_file_never_constructs_a_scope(capture_file, monkeypatch,
                                                   capsys):
    import ultrascope.cli as cli

    def explode(*_a, **_k):
        raise AssertionError("--open must not touch the instrument")

    monkeypatch.setattr(cli.Scope, "connect", explode)
    cli.main(["--open", capture_file, "--measure", "--spectrum",
              "--math", "CH1*CH2"])

    out = capsys.readouterr().out
    assert "600 points" in out
    assert "CH1  Vtop=" in out          # local measurements, no scope involved
    assert "CH1*CH2" in out
    assert "peak=" in out


def test_a_file_that_is_not_a_waveform_exits_with_a_message(tmp_path):
    import ultrascope.cli as cli

    path = tmp_path / "notes.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--open", str(path)])
    assert "not a waveform CSV" in str(exit_info.value)


def test_math_over_a_single_channel_file_says_so(tmp_path, capsys):
    import ultrascope.cli as cli

    t = time_axis(64, 1e-3, 0.0, DS1000E)
    path = tmp_path / "one.csv"
    save_csv(str(path), Waveform(t=t, channels={1: np.sin(t * 5000)},
                                 timebase=1e-3, time_offset=0.0))
    cli.main(["--open", str(path), "--math", "CH1-CH2"])
    assert "needs both channels" in capsys.readouterr().out


def test_readings_are_formatted_with_their_own_units():
    rows = {"Vpp": ("Vpp", 1.5, "V\u00b2"), "Freq": ("Freq", None, "Hz")}
    text = format_readings(rows)
    assert "Vpp=1.5 V\u00b2" in text
    assert "Freq=--" in text


@pytest.mark.parametrize("op", MATH_OPS)
def test_every_math_operation_is_accepted_on_the_command_line(op, capture_file):
    assert parse_args(["--open", capture_file, "--math", op]).math == op
