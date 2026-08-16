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
from ultrascope.profile import DS1000E
from ultrascope.waveform import Waveform, time_axis

@pytest.fixture(scope="module")
def root():
    """One hidden Tk root for the module; re-creating one per test is flaky."""
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless CI
        pytest.skip(f"no display for Tk: {exc}")
    window.withdraw()
    yield window
    window.destroy()


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
