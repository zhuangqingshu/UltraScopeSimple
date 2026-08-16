"""Cursor arithmetic. No instrument involved, so this is fully testable."""

import numpy as np
import pytest

from ultrascope.analysis import (OFF, TIME, VOLTAGE, cursor_readings,
                                 default_cursor_positions, nearest_cursor,
                                 sample_at)
from ultrascope.profile import DS1000E
from ultrascope.waveform import Waveform, time_axis


def labels(rows):
    return [label for label, _, _ in rows]


def value_of(rows, label):
    return next(value for name, value, _ in rows if name == label)


# --- readings ---------------------------------------------------------------

def test_time_cursors_report_positions_gap_and_frequency():
    rows = cursor_readings(TIME, 1e-3, 3e-3)
    assert labels(rows) == ["1", "2", "dT", "1/dT"]
    assert value_of(rows, "dT") == pytest.approx(2e-3)
    assert value_of(rows, "1/dT") == pytest.approx(500.0)


def test_voltage_cursors_have_no_frequency_row():
    rows = cursor_readings(VOLTAGE, -1.0, 2.5)
    assert labels(rows) == ["1", "2", "dV"]
    assert value_of(rows, "dV") == pytest.approx(3.5)


def test_the_gap_keeps_its_sign():
    # Dragging cursor 2 to the left of cursor 1 should read negative, not be
    # silently absolute; the sign tells you which way round they are.
    assert value_of(cursor_readings(TIME, 3e-3, 1e-3), "dT") == pytest.approx(-2e-3)


def test_coincident_time_cursors_give_no_frequency_instead_of_infinity():
    rows = cursor_readings(TIME, 1e-3, 1e-3)
    assert value_of(rows, "dT") == 0
    assert value_of(rows, "1/dT") is None


def test_off_mode_and_missing_positions_produce_nothing():
    assert cursor_readings(OFF, 1.0, 2.0) == []
    assert cursor_readings(TIME, None, 2.0) == []
    assert cursor_readings(TIME, 1.0, None) == []


def test_units_match_the_mode():
    assert {unit for _, _, unit in cursor_readings(VOLTAGE, 0.0, 1.0)} == {"V"}
    assert {unit for _, _, unit in cursor_readings(TIME, 0.0, 1.0)} == {"s", "Hz"}


# --- sampling ---------------------------------------------------------------

def ramp_wave(npoints=101):
    t = time_axis(npoints, 1e-3, 0.0, DS1000E)
    return Waveform(t=t, channels={1: np.linspace(0.0, 10.0, npoints)},
                    timebase=1e-3, time_offset=0.0)


def test_sampling_hits_the_exact_value_at_a_sample():
    wave = ramp_wave()
    assert sample_at(wave, 1, wave.t[50]) == pytest.approx(5.0)


def test_sampling_interpolates_between_samples():
    wave = ramp_wave()
    midway = (wave.t[10] + wave.t[11]) / 2
    assert sample_at(wave, 1, midway) == pytest.approx(
        (wave.channels[1][10] + wave.channels[1][11]) / 2)


def test_sampling_outside_the_capture_returns_nothing():
    # Clamping to the end sample would look like a real measurement.
    wave = ramp_wave()
    assert sample_at(wave, 1, wave.t[0] - 1.0) is None
    assert sample_at(wave, 1, wave.t[-1] + 1.0) is None


def test_sampling_an_absent_channel_returns_nothing():
    assert sample_at(ramp_wave(), 2, 0.0) is None


# --- placement and grabbing -------------------------------------------------

def test_fresh_cursors_land_inside_the_visible_span():
    low, high = -2.0, 4.0
    a, b = default_cursor_positions(low, high)
    assert low < a < b < high


def test_fresh_cursors_are_evenly_placed():
    a, b = default_cursor_positions(0.0, 3.0)
    assert a == pytest.approx(1.0)
    assert b == pytest.approx(2.0)


def test_the_nearer_cursor_is_the_one_grabbed():
    assert nearest_cursor([1.0, 5.0], 1.4, tolerance=1.0) == 0
    assert nearest_cursor([1.0, 5.0], 4.7, tolerance=1.0) == 1


def test_nothing_is_grabbed_beyond_the_tolerance():
    assert nearest_cursor([1.0, 5.0], 3.0, tolerance=0.5) is None


def test_unplaced_cursors_are_skipped():
    assert nearest_cursor([None, 5.0], 5.1, tolerance=1.0) == 1
    assert nearest_cursor([None, None], 5.0, tolerance=1.0) is None
