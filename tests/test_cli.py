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
