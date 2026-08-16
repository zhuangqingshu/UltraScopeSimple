"""The byte -> volts path, which has no hardware in it and is easy to get wrong."""

import numpy as np
import pytest

from ultrascope.profile import DS1000E
from ultrascope.waveform import (Waveform, WaveformError, decode, parse_block,
                                 time_axis)


def block(payload: bytes) -> bytes:
    """Wrap bytes in an IEEE 488.2 definite-length block the way the scope does."""
    length = str(len(payload))
    return b"#" + str(len(length)).encode() + length.encode() + payload


def test_parse_block_round_trip():
    payload = bytes(range(256))
    assert parse_block(block(payload)) == payload


def test_parse_block_ignores_trailing_bytes():
    # The scope appends a terminator after the counted payload.
    assert parse_block(block(b"abc") + b"\n") == b"abc"


def test_parse_block_rejects_a_missing_header():
    with pytest.raises(WaveformError):
        parse_block(b"no header here")


def test_parse_block_rejects_a_short_transfer():
    # The instrument declares 1M bytes in RAW mode however deep the memory is,
    # then stops sending. Slicing without this check made every dead transfer
    # look like a successful small capture.
    truncated = block(b"x" * 1000)[:200]
    with pytest.raises(WaveformError, match="truncated"):
        parse_block(truncated)


def test_the_truncation_error_reports_both_lengths():
    full = block(b"y" * 500)
    header_len = len(full) - 500
    with pytest.raises(WaveformError) as excinfo:
        parse_block(full[:header_len + 105])
    message = str(excinfo.value)
    assert "500" in message          # declared
    assert "105" in message          # arrived


def test_an_exactly_complete_block_is_accepted():
    payload = b"z" * 64
    assert parse_block(block(payload)) == payload


def test_centre_code_is_zero_volts_at_zero_offset():
    # Codes arrive inverted, so the on-the-wire byte for centre is 255 - 130.
    volts = decode(bytes([255 - 130]), DS1000E, volt_scale=1.0, volt_offset=0.0)
    assert volts[0] == pytest.approx(0.0)


def test_one_division_above_centre_is_one_volt_scale():
    raw = 255 - int(DS1000E.code_center + DS1000E.codes_per_div)
    volts = decode(bytes([raw]), DS1000E, volt_scale=0.5, volt_offset=0.0)
    assert volts[0] == pytest.approx(0.5)


def test_volt_offset_shifts_the_trace_down():
    payload = bytes([255 - 130])
    centred = decode(payload, DS1000E, volt_scale=1.0, volt_offset=0.0)
    shifted = decode(payload, DS1000E, volt_scale=1.0, volt_offset=2.0)
    assert shifted[0] - centred[0] == pytest.approx(-2.0)


def test_decode_is_monotonic_in_the_wire_byte():
    # Inverted codes mean a larger byte must decode to a *lower* voltage.
    volts = decode(bytes([10, 20, 30]), DS1000E, volt_scale=1.0, volt_offset=0.0)
    assert volts[0] > volts[1] > volts[2]


def test_time_axis_spans_the_full_screen_width():
    t = time_axis(600, timebase=1e-3, time_offset=0.0, profile=DS1000E)
    assert len(t) == 600
    # 12 horizontal divisions, centred on the offset.
    assert t[0] == pytest.approx(-6e-3)
    assert t[-1] == pytest.approx(6e-3)


def test_time_axis_is_centred_on_the_time_offset():
    t = time_axis(101, timebase=1e-3, time_offset=5e-3, profile=DS1000E)
    assert t[50] == pytest.approx(5e-3)


def test_waveform_reports_points_and_channels():
    wave = Waveform(t=np.zeros(10), channels={2: np.zeros(10), 1: np.zeros(10)},
                    timebase=1e-3, time_offset=0.0)
    assert wave.npoints == 10
    assert wave.channel_ids == [1, 2]
