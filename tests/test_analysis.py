"""Cursor arithmetic. No instrument involved, so this is fully testable."""

import numpy as np
import pytest

from ultrascope.analysis import (DBVPEAK, DBVRMS, DEFAULT_WINDOW, OFF, TIME,
                                 VOLTAGE, VPEAK, VRMS, WINDOWS,
                                 cursor_readings, default_cursor_positions,
                                 crossings, levels, measurements,
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
    assert np.all(np.isfinite(spec.values(DBVRMS)))
    assert np.all(np.isfinite(spec.values(DBVPEAK)))


def test_the_peak_referenced_decibels_match_the_magnitudes():
    spec = spectrum(sine_wave(amplitude=1.0), 1)
    _frequency, volts = spec.peak()
    assert spec.values(DBVPEAK).max() == pytest.approx(20 * np.log10(volts),
                                                       abs=0.01)


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


# --- trace parameters -------------------------------------------------------

FINE = 4000          # enough samples that quantisation does not dominate


def axis(npoints=FINE):
    return time_axis(npoints, 1e-3, 0.0, DS1000E)


def trapezoid(t, period, rise, duty=0.5, low=0.0, high=5.0):
    """A pulse train whose edges take exactly ``rise`` seconds."""
    phase = np.mod(t, period)
    up = np.clip(phase / rise, 0, 1)
    down = np.clip((phase - duty * period) / rise, 0, 1)
    return low + (high - low) * np.clip(up - down, 0, 1)


def measure(volts, t=None):
    t = axis(len(volts)) if t is None else t
    wave = Waveform(t=t, channels={1: volts}, timebase=1e-3, time_offset=0.0)
    return {k: v for k, (_, v, _) in measurements(wave, 1).items()}


# --- levels ---

def test_levels_find_where_the_samples_settle_not_the_extremes():
    t = axis()
    volts = trapezoid(t, 1e-3, 1e-9, low=0.0, high=5.0)
    lvl = levels(volts)
    assert lvl.top == pytest.approx(5.0, abs=0.01)
    assert lvl.base == pytest.approx(0.0, abs=0.01)
    assert lvl.amplitude == pytest.approx(5.0, abs=0.02)


def test_an_overshoot_does_not_drag_the_top_up_with_it():
    # Timing an edge to 90% of the *peak* rather than the settled level would
    # read short, so top must ignore the spike.
    t = axis()
    volts = trapezoid(t, 2e-3, 1e-9)
    volts = volts + 1.0 * (np.mod(t, 2e-3) < 20e-6)   # a brief spike to 6 V
    lvl = levels(volts)
    assert lvl.top == pytest.approx(5.0, abs=0.05)
    assert lvl.maximum == pytest.approx(6.0, abs=0.05)


def test_reference_levels_are_fractions_of_the_amplitude():
    lvl = levels(trapezoid(axis(), 1e-3, 1e-9, low=1.0, high=3.0))
    assert lvl.reference(0.0) == pytest.approx(1.0, abs=0.02)
    assert lvl.reference(0.5) == pytest.approx(2.0, abs=0.02)
    assert lvl.reference(1.0) == pytest.approx(3.0, abs=0.02)


def test_a_flat_trace_has_equal_levels_and_no_amplitude():
    lvl = levels(np.full(100, 2.5))
    assert lvl.top == lvl.base == 2.5
    assert lvl.amplitude == 0.0


def test_an_empty_trace_has_no_levels():
    assert levels(np.array([])) is None


# --- crossings ---

def test_a_crossing_is_interpolated_between_samples():
    # Snapping to the nearest sample would quantise every timing measurement.
    t = np.array([0.0, 1.0])
    assert crossings(t, np.array([0.0, 10.0]), 2.5) == pytest.approx([0.25])


def test_crossings_can_be_filtered_by_direction():
    t = np.linspace(0, 3, 4)
    volts = np.array([0.0, 10.0, 0.0, 10.0])
    assert len(crossings(t, volts, 5.0, rising=True)) == 2
    assert len(crossings(t, volts, 5.0, rising=False)) == 1
    assert len(crossings(t, volts, 5.0)) == 3


def test_a_trace_that_never_reaches_the_level_has_no_crossings():
    assert crossings(np.linspace(0, 1, 10), np.zeros(10), 5.0) == []


# --- timing ---

def test_rise_time_measures_the_ten_to_ninety_transition():
    # A linear ramp of 100 us spends exactly 80 us between 10% and 90%.
    t = axis()
    volts = trapezoid(t, 4e-3, 100e-6)
    assert measure(volts, t)["Rise"] == pytest.approx(80e-6, rel=0.02)


def test_fall_time_matches_a_symmetric_edge():
    t = axis()
    volts = trapezoid(t, 4e-3, 100e-6)
    assert measure(volts, t)["Fall"] == pytest.approx(80e-6, rel=0.02)


def test_frequency_and_period_agree():
    t = axis()
    result = measure(trapezoid(t, 1e-3, 1e-6), t)
    assert result["Period"] == pytest.approx(1e-3, rel=0.02)
    assert result["Freq"] == pytest.approx(1000.0, rel=0.02)


@pytest.mark.parametrize("duty", [0.25, 0.5, 0.75])
def test_duty_cycle_tracks_the_pulse_train(duty):
    t = axis()
    result = measure(trapezoid(t, 1e-3, 1e-6, duty=duty), t)
    assert result["Duty"] == pytest.approx(duty * 100, abs=1.0)


def test_positive_and_negative_widths_fill_the_period():
    t = axis()
    result = measure(trapezoid(t, 1e-3, 1e-6, duty=0.3), t)
    assert result["+Width"] == pytest.approx(0.3e-3, rel=0.05)
    assert result["-Width"] == pytest.approx(0.7e-3, rel=0.05)


def test_a_sine_reports_the_frequency_it_was_built_with():
    t = axis()
    result = measure(2.0 * np.sin(2 * np.pi * 1000 * t), t)
    assert result["Freq"] == pytest.approx(1000.0, rel=0.01)
    assert result["Duty"] == pytest.approx(50.0, abs=1.0)


# --- amplitude ---

def test_a_clean_trace_reports_no_overshoot():
    # The histogram bin centre alone used to report most of a percent here.
    t = axis()
    result = measure(trapezoid(t, 1e-3, 1e-6), t)
    assert result["Over"] == pytest.approx(0.0, abs=0.2)
    assert result["Pre"] == pytest.approx(0.0, abs=0.2)


def test_overshoot_is_reported_as_a_percentage_of_amplitude():
    t = axis()
    volts = trapezoid(t, 2e-3, 1e-9)
    volts = volts + 0.5 * (np.mod(t, 2e-3) < 20e-6)   # 0.5 V over a 5 V step
    assert measure(volts, t)["Over"] == pytest.approx(10.0, abs=1.5)


def test_rms_and_mean_of_a_sine():
    t = axis()
    result = measure(2.0 * np.sin(2 * np.pi * 1000 * t), t)
    assert result["Vrms"] == pytest.approx(2.0 / np.sqrt(2), rel=0.02)
    assert result["Vavg"] == pytest.approx(0.0, abs=0.05)


def test_peak_to_peak_uses_the_extremes_not_the_settled_levels():
    t = axis()
    volts = trapezoid(t, 2e-3, 1e-9)
    volts = volts + 1.0 * (np.mod(t, 2e-3) < 20e-6)
    result = measure(volts, t)
    assert result["Vpp"] == pytest.approx(6.0, abs=0.05)
    assert result["Vamp"] == pytest.approx(5.0, abs=0.1)


# --- degenerate input ---

def test_a_flat_trace_reports_no_timing_rather_than_nonsense():
    result = measure(np.full(500, 1.0))
    assert result["Freq"] is None
    assert result["Rise"] is None
    assert result["Duty"] is None


def test_an_absent_channel_measures_nothing():
    wave = Waveform(t=axis(100), channels={1: np.zeros(100)},
                    timebase=1e-3, time_offset=0.0)
    assert measurements(wave, 2) == {}


def test_every_reading_carries_a_label_and_a_unit():
    wave = Waveform(t=axis(), channels={1: trapezoid(axis(), 1e-3, 1e-6)},
                    timebase=1e-3, time_offset=0.0)
    for key, (label, _value, unit) in measurements(wave, 1).items():
        assert label == key
        assert unit in ("V", "s", "Hz", "%")


# --- maths between channels -------------------------------------------------

from ultrascope.analysis import (MATH_OFF, MATH_OPS, math_trace,  # noqa: E402
                                 math_unit, measurements_of, xy_pairs)


def two_channel(npoints=600, timebase=1e-3):
    t = time_axis(npoints, timebase, 0.0, DS1000E)
    return Waveform(t=t,
                    channels={1: np.sin(2 * np.pi * 1000 * t),
                              2: np.cos(2 * np.pi * 1000 * t)},
                    timebase=timebase, time_offset=0.0)


@pytest.mark.parametrize("op, expected", [
    ("CH1+CH2", lambda a, b: a + b),
    ("CH1-CH2", lambda a, b: a - b),
    ("CH1*CH2", lambda a, b: a * b),
])
def test_each_operation_combines_the_two_channels(op, expected):
    wave = two_channel()
    a, b = wave.channels[1], wave.channels[2]
    assert math_trace(wave, op) == pytest.approx(expected(a, b))


def test_off_produces_no_trace():
    assert math_trace(two_channel(), MATH_OFF) is None


def test_a_single_channel_capture_has_no_math_trace():
    # Rather than raising: switching channels off mid-session is ordinary, and
    # the caller's answer is simply "nothing to draw".
    t = time_axis(64, 1e-3, 0.0, DS1000E)
    wave = Waveform(t=t, channels={1: np.zeros(64)}, timebase=1e-3,
                    time_offset=0.0)
    assert all(math_trace(wave, op) is None for op in MATH_OPS)


def test_an_unknown_operation_is_rejected():
    with pytest.raises(ValueError, match="unknown math operation"):
        math_trace(two_channel(), "CH1/CH2")


def test_a_product_of_two_voltages_is_not_in_volts():
    assert math_unit("CH1*CH2") == "V²"
    assert math_unit("CH1+CH2") == "V"


def test_the_product_of_sine_and_cosine_is_a_double_frequency_half_amplitude():
    # sin(x)cos(x) = sin(2x)/2 -- an independent check that the maths lands on
    # the samples rather than merely returning an array of the right shape.
    wave = two_channel()
    rows = measurements_of(wave.t, math_trace(wave, "CH1*CH2"), "V²")
    assert rows["Freq"][1] == pytest.approx(2000.0, rel=1e-3)
    # Peak to peak of sin(2x)/2 is 1. Vamp reads a shade under, because the
    # histogram levels sit just inside the peaks of a sinusoid -- Vpp is the
    # extremes and is the one to check exactly here.
    assert rows["Vpp"][1] == pytest.approx(1.0, rel=1e-3)


def test_measurement_rows_carry_the_unit_they_were_given():
    wave = two_channel()
    rows = measurements_of(wave.t, math_trace(wave, "CH1*CH2"), "V²")
    assert rows["Vpp"][2] == "V²"
    assert rows["Freq"][2] == "Hz"      # a frequency is a frequency regardless


def test_measurements_over_a_channel_still_report_volts():
    rows = measurements(two_channel(), 1)
    assert rows["Vpp"][2] == "V"


# --- XY ---------------------------------------------------------------------

def test_xy_returns_the_two_channels_point_for_point():
    wave = two_channel()
    x, y = xy_pairs(wave, 1, 2)
    assert x is wave.channels[1] and y is wave.channels[2]


def test_quadrature_traces_trace_a_circle():
    # sin against cos is the unit circle; every point sits on radius 1. This is
    # the property that makes XY worth having: a phase relationship is a shape.
    x, y = xy_pairs(two_channel(), 1, 2)
    assert np.hypot(x, y) == pytest.approx(np.ones(len(x)), abs=1e-9)


def test_xy_against_a_missing_channel_is_none():
    wave = two_channel()
    assert xy_pairs(wave, 1, 3) is None


def test_xy_of_a_channel_against_itself_is_allowed():
    # Degenerate but not an error: it draws the 45-degree line, which is a
    # perfectly good way to confirm the axes are what you think they are.
    x, y = xy_pairs(two_channel(), 1, 1)
    assert x is y


# --- how much of the frequency axis is worth showing ------------------------

from ultrascope.analysis import CYCLES_FOR_A_SHARP_PEAK  # noqa: E402


def sine(frequency, npoints=600, timebase=1e-3):
    t = time_axis(npoints, timebase, 0.0, DS1000E)
    return Waveform(t=t, channels={1: np.sin(2 * np.pi * frequency * t)},
                    timebase=timebase, time_offset=0.0)


def test_the_span_covers_the_peak_and_its_harmonics():
    # Nyquist follows from the timebase and says nothing about where the signal
    # is: at 1 ms/div it is 25 kHz, so a 1 kHz tone occupies the leftmost 4%.
    spec = spectrum(sine(1000.0), 1)
    assert spec.nyquist > 20e3
    assert spec.display_span() == pytest.approx(10e3, rel=0.2)


def test_the_span_never_runs_past_nyquist():
    # A high peak times ten would leave most of the axis showing nothing.
    spec = spectrum(sine(20000.0), 1)
    assert spec.display_span() <= spec.nyquist


def test_a_very_low_peak_still_leaves_several_bins_in_view():
    # Ten harmonics of a peak one bin up would be a handful of pixels wide.
    spec = spectrum(sine(100.0, timebase=1e-3), 1)
    assert spec.display_span() >= spec.resolution * 10


def test_a_flat_trace_falls_back_to_the_whole_axis():
    t = time_axis(600, 1e-3, 0.0, DS1000E)
    flat = Waveform(t=t, channels={1: np.zeros(600)}, timebase=1e-3,
                    time_offset=0.0)
    spec = spectrum(flat, 1)
    assert spec.display_span() == pytest.approx(spec.nyquist)


def test_cycles_counts_periods_in_the_record():
    # 1 kHz over 12 ms of record is twelve cycles, and equals peak over bin
    # spacing because both are one over a time.
    spec = spectrum(sine(1000.0, timebase=1e-3), 1)
    assert spec.cycles() == pytest.approx(12.0, rel=0.1)


def test_too_few_cycles_is_detectable():
    # This is the real reason a 1 kHz square wave looks like a smudge at
    # 200 us/div: 2.4 cycles in the record, so the fundamental falls between
    # bins. Verified against the instrument -- it read 832 Hz for a signal the
    # scope's own counter put at 998 Hz.
    spec = spectrum(sine(1000.0, timebase=200e-6), 1)
    assert spec.cycles() < CYCLES_FOR_A_SHARP_PEAK
    assert spectrum(sine(1000.0, timebase=2e-3), 1).cycles() > CYCLES_FOR_A_SHARP_PEAK


def test_enough_cycles_puts_the_peak_on_the_right_frequency():
    frequency, volts = spectrum(sine(1000.0, timebase=2e-3), 1).peak()
    assert frequency == pytest.approx(1000.0, rel=0.05)
    assert volts == pytest.approx(1.0, rel=0.05)


# --- vertical scales --------------------------------------------------------

from ultrascope.analysis import SPECTRUM_SCALES, is_logarithmic  # noqa: E402


def test_a_sine_reads_its_peak_on_the_peak_scale():
    spec = spectrum(sine_wave(amplitude=1.0), 1)
    assert spec.peak_in(VPEAK)[1] == pytest.approx(1.0, abs=0.02)


def test_the_same_sine_reads_its_rms_on_the_rms_scale():
    # This is what the instrument's own FFT shows, and it is a factor of
    # root two below the peak -- not the same number under another name.
    spec = spectrum(sine_wave(amplitude=1.0), 1)
    assert spec.peak_in(VRMS)[1] == pytest.approx(1.0 / np.sqrt(2), abs=0.02)


def test_the_two_decibel_scales_differ_by_exactly_three_db():
    # 20*log10(sqrt(2)) = 3.0103. Labelling a peak-referenced axis "dBV" --
    # which conventionally means dB relative to one volt RMS -- read this much
    # high against both the convention and the instrument.
    spec = spectrum(sine_wave(amplitude=2.0), 1)
    assert (spec.peak_in(DBVPEAK)[1] - spec.peak_in(DBVRMS)[1]) \
        == pytest.approx(20 * np.log10(np.sqrt(2)), abs=1e-9)


def test_decibels_follow_from_the_linear_scale_they_reference():
    spec = spectrum(sine_wave(amplitude=1.5), 1)
    assert spec.values(DBVRMS) == pytest.approx(
        20 * np.log10(np.maximum(spec.values(VRMS), 1e-12)))


def test_the_bins_account_for_the_whole_signal():
    # Parseval: the RMS of the trace equals the root sum of squares of the bin
    # RMS values. This is what pins down the two exceptions below -- get them
    # wrong and the total comes out 25% low.
    wave = sine_wave(amplitude=1.0, offset=2.0)
    volts = wave.channels[1]
    spec = spectrum(wave, 1, "rectangular")
    assert float(np.sqrt(np.sum(spec.rms ** 2))) == pytest.approx(
        float(np.sqrt(np.mean(volts ** 2))), rel=1e-9)


def test_dc_is_not_divided_by_root_two():
    # DC is not a sinusoid: its RMS is its own value.
    spec = spectrum(sine_wave(amplitude=1.0, offset=3.0), 1, "rectangular")
    assert spec.rms[0] == pytest.approx(spec.magnitudes[0])
    assert spec.rms[0] == pytest.approx(3.0, abs=0.02)


def test_the_nyquist_bin_of_an_even_record_is_not_either():
    # It alternates between two values rather than tracing a sinusoid.
    spec = spectrum(sine_wave(npoints=600), 1, "rectangular")
    assert spec.even_length
    assert spec.rms[-1] == pytest.approx(spec.magnitudes[-1])


def test_an_odd_length_record_has_no_nyquist_bin_to_except():
    spec = spectrum(sine_wave(npoints=601), 1, "rectangular")
    assert not spec.even_length
    assert spec.rms[-1] == pytest.approx(spec.magnitudes[-1] / np.sqrt(2))


def test_ordinary_bins_are_divided_by_root_two():
    spec = spectrum(sine_wave(amplitude=1.0), 1)
    peak_bin = int(np.argmax(spec.magnitudes[3:])) + 3
    assert spec.rms[peak_bin] == pytest.approx(
        spec.magnitudes[peak_bin] / np.sqrt(2))


@pytest.mark.parametrize("scale", SPECTRUM_SCALES)
def test_every_scale_produces_one_value_per_bin(scale):
    spec = spectrum(sine_wave(), 1)
    assert len(spec.values(scale)) == len(spec.freqs)
    assert np.all(np.isfinite(spec.values(scale)))


def test_an_unknown_scale_is_rejected():
    with pytest.raises(ValueError, match="unknown scale"):
        spectrum(sine_wave(), 1).values("dBm")


def test_only_the_decibel_scales_are_logarithmic():
    assert [s for s in SPECTRUM_SCALES if is_logarithmic(s)] == [DBVRMS, DBVPEAK]


def test_the_peak_sits_at_the_same_frequency_on_every_scale():
    spec = spectrum(sine_wave(frequency=2000.0), 1)
    assert len({spec.peak_in(s)[0] for s in SPECTRUM_SCALES}) == 1
