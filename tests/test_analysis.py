"""Cursor arithmetic. No instrument involved, so this is fully testable."""

import numpy as np
import pytest

from ultrascope.analysis import (DEFAULT_WINDOW, OFF, TIME, VOLTAGE, WINDOWS,
                                 cursor_readings, default_cursor_positions,
                                 nearest_cursor, sample_at, sample_rate,
                                 spectrum)
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


# --- spectrum ---------------------------------------------------------------

def sine_wave(frequency=1000.0, amplitude=1.0, offset=0.0,
              npoints=600, timebase=1e-3):
    t = time_axis(npoints, timebase, 0.0, DS1000E)
    volts = offset + amplitude * np.sin(2 * np.pi * frequency * t)
    return Waveform(t=t, channels={1: volts}, timebase=timebase, time_offset=0.0)


def test_sample_rate_comes_from_the_synthesised_axis():
    # 600 points across 12 divisions of 1 ms.
    wave = sine_wave()
    assert sample_rate(wave) == pytest.approx(599 / 12e-3, rel=1e-6)


def test_sample_rate_of_a_degenerate_capture_is_zero():
    flat = Waveform(t=np.zeros(1), channels={1: np.zeros(1)},
                    timebase=1e-3, time_offset=0.0)
    assert sample_rate(flat) == 0.0
    assert spectrum(flat, 1) is None


@pytest.mark.parametrize("window", WINDOWS)
def test_a_one_volt_sine_reads_one_volt_whatever_the_window(window):
    # Without dividing by the window's coherent gain every window would
    # under-report the amplitude, quietly and by a different factor each.
    spec = spectrum(sine_wave(amplitude=1.0), 1, window)
    _frequency, volts = spec.peak()
    assert volts == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize("amplitude", [0.1, 0.5, 2.0, 5.0])
def test_the_peak_tracks_the_amplitude(amplitude):
    spec = spectrum(sine_wave(amplitude=amplitude), 1)
    _frequency, volts = spec.peak()
    assert volts == pytest.approx(amplitude, rel=0.03)


def test_the_peak_lands_on_the_signal_frequency():
    spec = spectrum(sine_wave(frequency=2000.0), 1)
    frequency, _volts = spec.peak()
    assert frequency == pytest.approx(2000.0, abs=spec.resolution)


def test_dc_offset_shows_in_the_zero_bin_at_its_real_size():
    spec = spectrum(sine_wave(amplitude=1.0, offset=0.5), 1, "rectangular")
    assert spec.magnitudes[0] == pytest.approx(0.5, abs=0.02)


def test_the_peak_ignores_dc_so_an_offset_does_not_win():
    # A large offset would otherwise make every spectrum report 0 Hz.
    spec = spectrum(sine_wave(amplitude=0.2, offset=5.0), 1)
    frequency, _volts = spec.peak()
    assert frequency == pytest.approx(1000.0, abs=spec.resolution)


def test_frequencies_run_from_dc_to_nyquist():
    spec = spectrum(sine_wave(), 1)
    assert spec.freqs[0] == 0.0
    assert spec.freqs[-1] == pytest.approx(spec.sample_rate / 2, rel=1e-6)


def test_resolution_is_the_bin_spacing():
    spec = spectrum(sine_wave(), 1)
    assert spec.resolution == pytest.approx(spec.freqs[1] - spec.freqs[0])
    # 12 ms of record cannot resolve better than about 83 Hz.
    assert spec.resolution == pytest.approx(1 / 12e-3, rel=0.05)


def test_silence_reads_as_the_floor_rather_than_minus_infinity():
    t = time_axis(600, 1e-3, 0.0, DS1000E)
    silent = Waveform(t=t, channels={1: np.zeros(600)},
                      timebase=1e-3, time_offset=0.0)
    spec = spectrum(silent, 1)
    assert np.all(np.isfinite(spec.db))


def test_db_matches_the_magnitudes():
    spec = spectrum(sine_wave(amplitude=1.0), 1)
    _frequency, volts = spec.peak()
    assert spec.db.max() == pytest.approx(20 * np.log10(volts), abs=0.01)


def test_an_absent_channel_has_no_spectrum():
    assert spectrum(sine_wave(), 2) is None


def test_an_unknown_window_is_rejected():
    with pytest.raises(ValueError, match="unknown window"):
        spectrum(sine_wave(), 1, "triangular")


def test_the_default_window_is_one_of_the_supported_ones():
    assert DEFAULT_WINDOW in WINDOWS


@pytest.mark.parametrize("window", WINDOWS)
def test_a_large_offset_never_beats_the_signal(window):
    # 5 V of DC under a 0.2 V signal: the window smears the offset across its
    # main lobe, so skipping bin 0 alone is not enough.
    spec = spectrum(sine_wave(amplitude=0.2, offset=5.0), 1, window)
    frequency, volts = spec.peak()
    assert frequency == pytest.approx(1000.0, abs=spec.resolution)
    assert volts == pytest.approx(0.2, rel=0.1)


def test_every_window_declares_a_main_lobe_width():
    from ultrascope.analysis import WINDOW_MAIN_LOBE_BINS

    assert set(WINDOW_MAIN_LOBE_BINS) == set(WINDOWS)
    assert WINDOW_MAIN_LOBE_BINS["rectangular"] == 1   # narrowest
    assert all(v >= 1 for v in WINDOW_MAIN_LOBE_BINS.values())


def test_a_signal_close_to_dc_is_still_found():
    # The skip must not be so wide that low-frequency signals are lost.
    spec = spectrum(sine_wave(frequency=500.0, amplitude=1.0), 1, "rectangular")
    frequency, _volts = spec.peak()
    assert frequency == pytest.approx(500.0, abs=spec.resolution)
