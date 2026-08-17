"""Vertical windowing, which has to follow the trace rather than centre on zero.

The plot canvas needs a Tk root, so these tests build a hidden one. The gesture
handlers are exercised through plain namespaces standing in for matplotlib
events.
"""

import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest

from ultrascope import analysis
from ultrascope.gui.plot import SPECTRUM_DOMAIN, TIME_DOMAIN
from ultrascope.profile import DS1000E
from ultrascope.waveform import Waveform, time_axis

# ``root`` comes from conftest: only one Tk root may exist per process, and
# re-creating one per module fails with a TclError that looks like a broken
# Tk installation.


@pytest.fixture
def canvas(root):
    from ultrascope.gui.plot import PlotCanvas

    return PlotCanvas(tk.Frame(root))


def wave_between(low, high, npoints=200):
    """A sine filling exactly [low, high]."""
    t = time_axis(npoints, 1e-3, 0.0, DS1000E)
    centre, half = (low + high) / 2.0, (high - low) / 2.0
    return Waveform(t=t, channels={1: centre + half * np.sin(t * 5000)},
                    timebase=1e-3, time_offset=0.0)


def test_window_follows_a_trace_that_sits_off_centre(canvas):
    # A trace dragged up to sit around +2 V must stay framed around +2 V.
    # Centring on zero here is what used to undo the vertical offset.
    low, high = canvas.y_limits(wave_between(1.0, 3.0))
    assert low > 0
    assert (low + high) / 2 == pytest.approx(2.0, abs=0.05)


def test_window_contains_the_whole_trace(canvas):
    low, high = canvas.y_limits(wave_between(-0.5, 4.0))
    assert low < -0.5
    assert high > 4.0


def test_window_leaves_a_margin_rather_than_touching_the_extremes(canvas):
    low, high = canvas.y_limits(wave_between(-1.0, 1.0))
    assert high == pytest.approx(1.15, abs=0.01)
    assert low == pytest.approx(-1.15, abs=0.01)


def test_a_flat_trace_still_gets_a_usable_window(canvas):
    from ultrascope.gui.plot import FLAT_TRACE_HALF_SPAN

    t = time_axis(50, 1e-3, 0.0, DS1000E)
    flat = Waveform(t=t, channels={1: np.full(50, 2.0)},
                    timebase=1e-3, time_offset=0.0)
    low, high = canvas.y_limits(flat)
    assert high - low == pytest.approx(2 * FLAT_TRACE_HALF_SPAN)
    assert low < 2.0 < high


def test_the_trigger_marker_is_kept_in_view(canvas):
    canvas.show_level(5.0)
    low, high = canvas.y_limits(wave_between(-0.2, 0.2))
    assert high > 5.0


def test_a_hidden_marker_does_not_stretch_the_window(canvas):
    canvas.level_line.set_ydata([50.0, 50.0])
    canvas.level_line.set_visible(False)
    low, high = canvas.y_limits(wave_between(-1.0, 1.0))
    assert high < 2.0


def test_a_drag_in_progress_keeps_its_own_view(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.pan = {"px": (0, 0), "xlim": canvas.ax.get_xlim(),
                  "ylim": (-9.0, 9.0), "time_offset": 0.0, "ch": 1,
                  "volt_offset": 0.0}
    canvas.ax.set_ylim(-9.0, 9.0)
    canvas.show(wave_between(-1.0, 1.0))
    assert canvas.ax.get_ylim() == (-9.0, 9.0)


def test_pan_moves_the_trace_with_the_cursor(canvas):
    # Dragging up must lower the channel offset: displayed volts are raw volts
    # minus the offset. Getting this backwards makes the trace run away.
    canvas.show(wave_between(-1.0, 1.0))
    committed = []
    canvas.on_pan_commit = lambda *args: committed.append(args)
    canvas.enabled = lambda: True
    canvas.volt_offsets[1] = 0.0

    press = SimpleNamespace(inaxes=canvas.ax, button=1, dblclick=False,
                            x=300, y=300, xdata=0.0, ydata=0.0)
    canvas._on_press(press)
    release = SimpleNamespace(inaxes=canvas.ax, button=1,
                              x=300, y=340, xdata=0.0, ydata=0.0)
    canvas._on_release(release)

    assert committed, "a drag must commit exactly once, on release"
    _time_offset, ch, volt_offset = committed[0]
    assert ch == 1
    assert volt_offset < 0     # dragged up => offset decreases


# --- measurement cursors ----------------------------------------------------

def press(canvas, **kwargs):
    return SimpleNamespace(inaxes=canvas.ax, button=1, dblclick=False,
                           x=0, y=0, **kwargs)


def test_cursors_start_off(canvas):
    assert canvas.cursor_mode == analysis.OFF
    assert not any(line.get_visible() for line in canvas.cursor_lines)


def test_turning_cursors_on_places_them_inside_the_view(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.TIME)
    low, high = canvas.ax.get_xlim()
    assert all(low < p < high for p in canvas.cursor_positions)
    assert all(line.get_visible() for line in canvas.cursor_lines)


def test_turning_cursors_off_hides_and_clears_them(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.TIME)
    canvas.set_cursor_mode(analysis.OFF)
    assert canvas.cursor_positions == [None, None]
    assert not any(line.get_visible() for line in canvas.cursor_lines)
    assert canvas.cursors.get() == ""


def test_the_readout_reports_the_gap_and_its_reciprocal(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.TIME)
    canvas.cursor_positions = [0.0, 1e-3]
    canvas._redraw_cursors()
    text = canvas.cursors.get()
    assert "dT=" in text and "1 ms" in text
    assert "1 kHz" in text          # one cycle of a 1 kHz signal


def test_voltage_cursors_report_volts_not_time(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.VOLTAGE)
    canvas.cursor_positions = [-2.0, 2.0]
    canvas._redraw_cursors()
    text = canvas.cursors.get()
    assert "dV=" in text and "4 V" in text
    assert "dT" not in text


def test_dragging_grabs_the_nearer_cursor(canvas):
    canvas.show(wave_between(-3.0, 3.0))
    canvas.set_cursor_mode(analysis.VOLTAGE)
    canvas.cursor_positions = [-2.0, 2.0]
    canvas._on_press(press(canvas, xdata=0.0, ydata=-1.98))
    assert canvas.dragging_cursor == 0
    canvas._on_motion(press(canvas, xdata=0.0, ydata=0.0))
    assert canvas.cursor_positions[0] == pytest.approx(0.0)
    canvas._on_release(press(canvas, xdata=0.0, ydata=0.0))
    assert canvas.dragging_cursor is None


def test_a_cursor_drag_commits_nothing_to_the_instrument(canvas):
    # Cursors are a local measurement; they must never reach the scope.
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.VOLTAGE)
    canvas.cursor_positions = [-0.5, 0.5]
    committed = []
    canvas.on_level_commit = lambda *a: committed.append(a)
    canvas.on_pan_commit = lambda *a: committed.append(a)
    canvas.enabled = lambda: True

    canvas._on_press(press(canvas, xdata=0.0, ydata=-0.5))
    canvas._on_motion(press(canvas, xdata=0.0, ydata=0.2))
    canvas._on_release(press(canvas, xdata=0.0, ydata=0.2))
    assert committed == []


def test_cursors_work_while_disconnected(canvas):
    # enabled() is False, which gates the level marker and panning; grabbing a
    # cursor must still work so an existing capture can be measured offline.
    canvas.show(wave_between(-1.0, 1.0))
    canvas.enabled = lambda: False
    canvas.set_cursor_mode(analysis.VOLTAGE)
    canvas.cursor_positions = [-0.5, 0.5]
    canvas._on_press(press(canvas, xdata=0.0, ydata=-0.5))
    assert canvas.dragging_cursor == 0


def test_a_press_away_from_any_cursor_does_not_grab_one(canvas):
    canvas.show(wave_between(-3.0, 3.0))
    canvas.enabled = lambda: False
    canvas.set_cursor_mode(analysis.VOLTAGE)
    canvas.cursor_positions = [-2.0, 2.0]
    canvas._on_press(press(canvas, xdata=0.0, ydata=0.0))
    assert canvas.dragging_cursor is None


def test_the_readout_refreshes_when_a_new_frame_arrives(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_cursor_mode(analysis.TIME)
    canvas.cursor_positions = [0.0, 2e-3]
    canvas.show(wave_between(-1.0, 1.0))
    assert "dT=" in canvas.cursors.get()


# --- spectrum view ----------------------------------------------------------

def tone(frequency=1000.0, amplitude=1.0, npoints=600):
    t = time_axis(npoints, 1e-3, 0.0, DS1000E)
    return Waveform(t=t, channels={1: amplitude * np.sin(2 * np.pi * frequency * t)},
                    timebase=1e-3, time_offset=0.0)


def test_the_canvas_starts_in_the_time_domain(canvas):
    assert canvas.domain == TIME_DOMAIN
    assert not canvas.spectrum_line.get_visible()


def test_switching_to_the_spectrum_swaps_the_visible_traces(canvas):
    canvas.show(tone())
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert canvas.spectrum_line.get_visible()
    assert not canvas.lines[1].get_visible()
    assert "Frequency" in canvas.ax.get_xlabel()
    assert "dBV" in canvas.ax.get_ylabel()


def test_switching_back_restores_the_trace(canvas):
    canvas.show(tone())
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.set_domain(TIME_DOMAIN)
    assert canvas.lines[1].get_visible()
    assert not canvas.spectrum_line.get_visible()
    assert "Time" in canvas.ax.get_xlabel()
    assert canvas.spectrum_readout.get() == ""


def test_the_readout_names_the_peak(canvas):
    canvas.show(tone(frequency=2000.0))
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(frequency=2000.0), 1)
    text = canvas.spectrum_readout.get()
    assert "peak=" in text and "kHz" in text
    assert "window=" in text and "res=" in text


def test_the_window_choice_reaches_the_transform(canvas):
    canvas.set_domain(SPECTRUM_DOMAIN, window="blackman")
    canvas.show_spectrum(tone(), 1)
    assert "blackman" in canvas.spectrum_readout.get()


def test_entering_the_spectrum_takes_down_time_domain_overlays(canvas):
    # A trigger level in volts and cursors in seconds mean nothing against a
    # frequency axis; leaving them up would mislabel real numbers.
    canvas.show(tone())
    canvas.show_level(0.5)
    canvas.set_cursor_mode(analysis.TIME)
    canvas.set_domain(SPECTRUM_DOMAIN)
    assert not canvas.level_line.get_visible()
    assert canvas.cursor_mode == analysis.OFF


def test_gestures_are_suppressed_in_the_spectrum(canvas):
    canvas.show(tone())
    canvas.enabled = lambda: True
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas._on_press(press(canvas, xdata=1000.0, ydata=-20.0))
    assert canvas.pan is None
    assert canvas.dragging_level is False
    assert canvas.dragging_cursor is None


def test_a_capture_with_no_usable_channel_says_so(canvas):
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 2)      # channel 2 is not in the capture
    assert "No spectrum" in canvas.spectrum_readout.get()


def test_the_view_is_scaled_around_the_strongest_bin(canvas):
    from ultrascope.gui.plot import SPECTRUM_DB_HEADROOM, SPECTRUM_DB_RANGE

    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    low, high = canvas.ax.get_ylim()
    assert high - low == pytest.approx(SPECTRUM_DB_RANGE + SPECTRUM_DB_HEADROOM)


# --- reference trace --------------------------------------------------------

def test_no_reference_to_begin_with(canvas):
    assert canvas.reference is None
    assert canvas.reference_lines == {}


def test_storing_a_reference_draws_it_alongside_the_live_trace(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_reference(wave_between(-2.0, 2.0))
    assert canvas.reference is not None
    assert set(canvas.reference_lines) == {1}
    assert canvas.lines[1].get_visible()


def test_clearing_a_reference_removes_its_artist(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_reference(wave_between(-2.0, 2.0))
    canvas.set_reference(None)
    assert canvas.reference is None
    assert canvas.reference_lines == {}


def test_replacing_a_reference_does_not_leave_the_old_one_behind(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    for _ in range(3):
        canvas.set_reference(wave_between(-2.0, 2.0))
    assert len(canvas.reference_lines) == 1


def test_the_view_frames_the_reference_too(canvas):
    # A baseline drawn off screen is no use for comparing against.
    live = wave_between(-0.2, 0.2)
    canvas.set_reference(wave_between(-5.0, 5.0))
    low, high = canvas.y_limits(live)
    assert low < -5.0 and high > 5.0


def test_the_reference_is_hidden_in_the_spectrum(canvas):
    canvas.show(wave_between(-1.0, 1.0))
    canvas.set_reference(wave_between(-2.0, 2.0))
    canvas.set_domain(SPECTRUM_DOMAIN)
    assert not canvas.reference_lines[1].get_visible()
    canvas.set_domain(TIME_DOMAIN)
    assert canvas.reference_lines[1].get_visible()


# --- persistence ------------------------------------------------------------

def test_persistence_is_off_to_begin_with(canvas):
    assert canvas.persistence_depth == 0
    canvas.show(wave_between(-1.0, 1.0))
    assert len(canvas.persistence_lines) == 0


def test_frames_accumulate_up_to_the_chosen_depth(canvas):
    canvas.set_persistence(3)
    for _ in range(10):
        canvas.show(wave_between(-1.0, 1.0))
    assert len(canvas.persistence_lines) == 3


def test_older_frames_are_fainter_than_newer_ones(canvas):
    canvas.set_persistence(4)
    for _ in range(4):
        canvas.show(wave_between(-1.0, 1.0))
    alphas = [ghost.get_alpha() for ghost in canvas.persistence_lines]
    assert alphas == sorted(alphas)          # oldest first, so alpha climbs
    assert 0 < alphas[0] < alphas[-1] <= 1.0


def test_the_depth_counts_frames_not_traces(canvas):
    # Two channels per frame must still leave exactly `depth` frames of trail.
    canvas.set_persistence(3)
    t = time_axis(200, 1e-3, 0.0, DS1000E)
    two = Waveform(t=t, channels={1: np.zeros(200), 2: np.ones(200)},
                   timebase=1e-3, time_offset=0.0)
    for _ in range(8):
        canvas.show(two)
    assert len(canvas.persistence_lines) == 6      # 3 frames x 2 channels


def test_clearing_the_trail_removes_every_ghost(canvas):
    canvas.set_persistence(5)
    for _ in range(5):
        canvas.show(wave_between(-1.0, 1.0))
    canvas.clear_persistence()
    assert len(canvas.persistence_lines) == 0


def test_changing_the_depth_starts_a_fresh_trail(canvas):
    canvas.set_persistence(5)
    for _ in range(5):
        canvas.show(wave_between(-1.0, 1.0))
    canvas.set_persistence(2)
    assert len(canvas.persistence_lines) == 0


def test_the_depth_is_capped(canvas):
    from ultrascope.gui.plot import MAX_PERSISTENCE

    canvas.set_persistence(9999)
    assert canvas.persistence_depth == MAX_PERSISTENCE
    canvas.set_persistence(-4)
    assert canvas.persistence_depth == 0


def test_ghosts_stay_out_of_the_legend(canvas):
    # One legend entry per ghost would bury the channel labels.
    canvas.set_persistence(3)
    for _ in range(3):
        canvas.show(wave_between(-1.0, 1.0))
    labels = [ghost.get_label() for ghost in canvas.persistence_lines]
    assert all(label.startswith("_") for label in labels)


def test_the_trail_is_dropped_when_the_spectrum_opens(canvas):
    canvas.set_persistence(3)
    for _ in range(3):
        canvas.show(wave_between(-1.0, 1.0))
    canvas.set_domain(SPECTRUM_DOMAIN)
    assert len(canvas.persistence_lines) == 0


# --- math overlay and the XY view -------------------------------------------

from ultrascope import analysis as an  # noqa: E402
from ultrascope.gui.plot import XY_DOMAIN  # noqa: E402


def quadrature(npoints=200, amplitude=1.0):
    t = time_axis(npoints, 1e-3, 0.0, DS1000E)
    return Waveform(t=t,
                    channels={1: amplitude * np.sin(2 * np.pi * 1000 * t),
                              2: amplitude * np.cos(2 * np.pi * 1000 * t)},
                    timebase=1e-3, time_offset=0.0)


def test_the_math_trace_is_hidden_until_an_operation_is_chosen(canvas):
    canvas.show(quadrature())
    assert not canvas.math_line.get_visible()


def test_choosing_an_operation_draws_it_on_the_next_frame(canvas):
    canvas.set_math("CH1+CH2")
    canvas.show(quadrature())
    assert canvas.math_line.get_visible()
    assert canvas.math_line.get_ydata() == pytest.approx(
        quadrature().channels[1] + quadrature().channels[2])


def test_the_legend_entry_names_the_unit(canvas):
    # The y axis is in volts and a product of two voltages is not, so the label
    # is the only thing keeping the plot honest.
    canvas.set_math("CH1*CH2")
    canvas.show(quadrature())
    assert canvas.math_line.get_label() == "CH1*CH2 (V\u00b2)"


def test_a_capture_missing_an_operand_hides_the_stale_math_trace(canvas):
    canvas.set_math("CH1-CH2")
    canvas.show(quadrature())
    assert canvas.math_line.get_visible()

    t = time_axis(200, 1e-3, 0.0, DS1000E)
    canvas.show(Waveform(t=t, channels={1: np.zeros(200)}, timebase=1e-3,
                         time_offset=0.0))
    # Leaving the previous difference on screen would read as a current one.
    assert not canvas.math_line.get_visible()


def test_the_vertical_window_frames_the_math_trace(canvas):
    # CH1+CH2 of two 3 V traces reaches beyond either of them; a math trace
    # drawn off-screen is worse than no math trace.
    canvas.set_math("CH1+CH2")
    wave = quadrature(amplitude=3.0)
    canvas.show(wave)
    low, high = canvas.y_limits(wave)
    values = wave.channels[1] + wave.channels[2]
    assert low < float(np.min(values))
    assert high > float(np.max(values))


def test_switching_off_hides_the_math_trace(canvas):
    canvas.set_math("CH1+CH2")
    canvas.show(quadrature())
    canvas.set_math(an.MATH_OFF)
    assert not canvas.math_line.get_visible()


def test_the_xy_view_plots_one_channel_against_the_other(canvas):
    wave = quadrature()
    canvas.set_domain(XY_DOMAIN)
    canvas.show_xy(wave, 1, 2)
    assert canvas.xy_line.get_visible()
    assert canvas.xy_line.get_xdata() == pytest.approx(wave.channels[1])
    assert canvas.xy_line.get_ydata() == pytest.approx(wave.channels[2])


def test_the_xy_axes_are_labelled_with_their_channels(canvas):
    canvas.set_domain(XY_DOMAIN)
    canvas.show_xy(quadrature(), 2, 1)
    assert canvas.ax.get_xlabel() == "CH2 (V)"
    assert canvas.ax.get_ylabel() == "CH1 (V)"


def test_xy_says_so_when_a_channel_is_missing(canvas):
    t = time_axis(50, 1e-3, 0.0, DS1000E)
    one = Waveform(t=t, channels={1: np.zeros(50)}, timebase=1e-3,
                   time_offset=0.0)
    canvas.set_domain(XY_DOMAIN)
    canvas.show_xy(one, 1, 2)
    assert not canvas.xy_line.get_visible()
    assert "needs both" in canvas.spectrum_readout.get()


def test_the_time_domain_artists_step_aside_for_the_xy_view(canvas):
    canvas.set_math("CH1+CH2")
    canvas.show(quadrature())
    canvas.set_domain(XY_DOMAIN)
    canvas.show_xy(quadrature(), 1, 2)
    assert not canvas.lines[1].get_visible()
    assert not canvas.math_line.get_visible()
    assert not canvas.spectrum_line.get_visible()
    assert canvas.xy_line.get_visible()


def test_returning_to_the_time_view_restores_them(canvas):
    canvas.set_math("CH1+CH2")
    canvas.show(quadrature())
    canvas.set_domain(XY_DOMAIN)
    canvas.set_domain(TIME_DOMAIN)
    assert canvas.lines[1].get_visible()
    assert canvas.math_line.get_visible()
    assert not canvas.xy_line.get_visible()


def test_gestures_are_inert_in_the_xy_view(canvas):
    # Volts against volts: dragging it would map onto nothing sensible, and the
    # trigger level marker measures a quantity the view does not show.
    canvas.enabled = lambda: True
    canvas.show(quadrature())
    canvas.set_domain(XY_DOMAIN)
    canvas._on_press(SimpleNamespace(inaxes=canvas.ax, button=1, dblclick=False,
                                     xdata=0.0, ydata=0.0, x=10, y=10))
    assert canvas.pan is None
    assert not canvas.dragging_level


def test_an_unknown_view_is_rejected(canvas):
    with pytest.raises(ValueError, match="unknown domain"):
        canvas.set_domain("waterfall")


# --- how much of the frequency axis is shown --------------------------------

def tone(frequency=1000.0, npoints=600, timebase=1e-3):
    t = time_axis(npoints, timebase, 0.0, DS1000E)
    return Waveform(t=t, channels={1: np.sin(2 * np.pi * frequency * t)},
                    timebase=timebase, time_offset=0.0)


def test_the_span_defaults_to_the_signal_not_to_nyquist(canvas):
    # The complaint this fixes: at a fast timebase Nyquist is hundreds of kHz
    # and an audio-frequency signal is a smudge against the left edge.
    wave = tone()
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(wave, 1)
    spec = an.spectrum(wave, 1)
    assert canvas.ax.get_xlim()[1] == pytest.approx(spec.display_span())
    assert canvas.ax.get_xlim()[1] < spec.nyquist / 2


def test_full_span_reaches_nyquist(canvas):
    wave = tone()
    canvas.set_spectrum_span(0.0)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(wave, 1)
    assert canvas.ax.get_xlim()[1] == pytest.approx(an.spectrum(wave, 1).nyquist)


def test_a_fixed_span_is_honoured(canvas):
    canvas.set_spectrum_span(5000.0)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert canvas.ax.get_xlim()[1] == pytest.approx(5000.0)


def test_a_fixed_span_beyond_nyquist_stops_at_nyquist(canvas):
    # There is no data past Nyquist, so honouring 1 MHz literally would leave
    # most of the plot empty.
    wave = tone()
    canvas.set_spectrum_span(1e6)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(wave, 1)
    assert canvas.ax.get_xlim()[1] == pytest.approx(an.spectrum(wave, 1).nyquist)


def test_the_vertical_range_follows_what_is_on_screen(canvas):
    # Scaling to a peak outside the span would push the visible trace off the
    # bottom of the window.
    t = time_axis(600, 1e-3, 0.0, DS1000E)
    volts = np.sin(2 * np.pi * 500 * t) + 50.0 * np.sin(2 * np.pi * 20000 * t)
    wave = Waveform(t=t, channels={1: volts}, timebase=1e-3, time_offset=0.0)

    canvas.set_spectrum_span(2000.0)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(wave, 1)
    low, high = canvas.ax.get_ylim()
    assert high < 20.0        # the 50 V component at 20 kHz is out of view


def test_the_readout_warns_when_too_few_cycles_are_on_screen(canvas):
    # 1 kHz at 200 us/div is 2.4 cycles: the peak straddles bins and reads low.
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(timebase=200e-6), 1)
    assert "cycles on screen" in canvas.spectrum_readout.get()
    assert "timebase" in canvas.spectrum_readout.get()


def test_no_warning_once_the_record_holds_enough_cycles(canvas):
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(timebase=2e-3), 1)
    assert "cycles on screen" not in canvas.spectrum_readout.get()


def test_the_readout_says_how_much_of_the_axis_is_shown(canvas):
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert "span=" in canvas.spectrum_readout.get()


# --- the vertical scale of the spectrum -------------------------------------

def test_the_spectrum_defaults_to_the_scale_the_instrument_uses(canvas):
    assert canvas.spectrum_scale == an.DBVRMS


def test_the_axis_is_labelled_with_the_chosen_unit(canvas):
    canvas.set_spectrum_scale(an.VRMS)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert canvas.ax.get_ylabel() == "Magnitude (Vrms)"


def test_switching_scale_changes_the_plotted_values(canvas):
    wave = tone()
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.set_spectrum_scale(an.VPEAK)
    canvas.show_spectrum(wave, 1)
    peak_volts = float(np.max(canvas.spectrum_line.get_ydata()))

    canvas.set_spectrum_scale(an.VRMS)
    canvas.show_spectrum(wave, 1)
    rms_volts = float(np.max(canvas.spectrum_line.get_ydata()))
    assert rms_volts == pytest.approx(peak_volts / np.sqrt(2), rel=1e-6)


def test_a_linear_axis_starts_at_zero(canvas):
    # The point of a linear scale is that small bins look small; starting it
    # just under the peak the way a dB axis does would defeat that.
    canvas.set_spectrum_scale(an.VRMS)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert canvas.ax.get_ylim()[0] == 0.0


def test_a_decibel_axis_keeps_a_fixed_range_below_the_peak(canvas):
    from ultrascope.gui.plot import SPECTRUM_DB_HEADROOM, SPECTRUM_DB_RANGE

    canvas.set_spectrum_scale(an.DBVRMS)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    low, high = canvas.ax.get_ylim()
    assert high - low == pytest.approx(SPECTRUM_DB_RANGE + SPECTRUM_DB_HEADROOM)
    assert low < 0        # a 1 V tone sits near 0 dB, so the floor is below it


def test_the_readout_quotes_the_peak_in_the_chosen_unit(canvas):
    canvas.set_spectrum_scale(an.VRMS)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(tone(), 1)
    assert "Vrms" in canvas.spectrum_readout.get()


def test_a_flat_trace_on_a_linear_axis_still_gets_a_window(canvas):
    t = time_axis(600, 1e-3, 0.0, DS1000E)
    silent = Waveform(t=t, channels={1: np.zeros(600)}, timebase=1e-3,
                      time_offset=0.0)
    canvas.set_spectrum_scale(an.VPEAK)
    canvas.set_domain(SPECTRUM_DOMAIN)
    canvas.show_spectrum(silent, 1)
    low, high = canvas.ax.get_ylim()
    assert high > low
