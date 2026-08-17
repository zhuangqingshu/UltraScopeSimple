"""The waveform canvas: drawing, the trigger-level marker, and mouse interaction.

Dragging only moves matplotlib artists and the view; the SCPI write happens
once, on mouse release. Writing during ``motion_notify_event`` would put dozens
of commands on the USBTMC link per drag and stall it.

The App assigns the hooks below; they default to doing nothing so the canvas
can be built and drawn without an instrument.
"""

from __future__ import annotations

import tkinter as tk
from collections import deque
from tkinter import ttk
from typing import Optional

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .. import analysis
from ..units import eng
from ..waveform import Waveform
from . import state as st

Y_MARGIN = 1.15
# A perfectly flat trace has no extent to scale, so give it a window anyway.
FLAT_TRACE_HALF_SPAN = 1.0
# How close to the marker the cursor must be to grab it, as a fraction of the
# visible voltage span.
GRAB_TOLERANCE = 0.03
# One scroll notch nudges the level by this fraction of a division.
SCROLL_STEP_DIV = 0.2

TIME_DOMAIN = "time"
SPECTRUM_DOMAIN = "spectrum"
XY_DOMAIN = "xy"
DOMAINS = (TIME_DOMAIN, SPECTRUM_DOMAIN, XY_DOMAIN)
# How much of the spectrum to show below the strongest bin, and how much air
# to leave above it.
SPECTRUM_DB_RANGE = 100.0
SPECTRUM_DB_HEADROOM = 10.0

MATH_COLOUR = "#ff66cc"
XY_COLOUR = "#66ff99"
# Padding around the XY figure, as a fraction of each axis' extent.
XY_MARGIN = 0.1

# Reference overlay and persistence.
REFERENCE_COLOUR = "#8888ff"
MAX_PERSISTENCE = 32
# Oldest surviving frame is drawn at this alpha; newer ones fade up to 1.
FAINTEST_ALPHA = 0.08


def _padded(values):
    """An axis range around some data, with room to breathe.

    A flat trace has no extent of its own, so it gets a fixed window instead of
    a zero-width one that matplotlib would silently widen for itself.
    """
    low, high = float(np.min(values)), float(np.max(values))
    if high <= low:
        return low - FLAT_TRACE_HALF_SPAN, high + FLAT_TRACE_HALF_SPAN
    pad = (high - low) * XY_MARGIN
    return low - pad, high + pad


class PlotCanvas:
    """Draws captures and owns the trigger-level marker and pan gestures."""

    def __init__(self, parent):
        self.container = ttk.Frame(parent)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(9, 5.5), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor("#101010")
        self.ax.grid(True, color="#404040", linewidth=0.5)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Voltage (V)")
        self.figure.tight_layout()

        self.level_line = self.ax.axhline(0.0, color="#ff5555", linewidth=1.2,
                                          linestyle="--", visible=False)
        self.level_tag = self.ax.annotate(
            "T", xy=(1.0, 0.0), xycoords=("axes fraction", "data"),
            xytext=(3, 0), textcoords="offset points",
            color="#ff5555", fontsize=8, va="center", visible=False)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        self.measurements = tk.StringVar(value="")
        ttk.Label(self.container, textvariable=self.measurements,
                  font=("Consolas", 9), justify="left")\
            .grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.lines = {}

        # Mirror of the instrument state the gestures need. The App keeps these
        # current so a drag does not have to query the scope mid-gesture.
        self.volt_scales = {1: 1.0, 2: 1.0}
        self.volt_offsets = {1: 0.0, 2: 0.0}
        self.time_offset = 0.0

        self.dragging_level = False
        self.pan = None

        # Local measurement cursors. Purely a view concern: they never reach
        # the instrument, so they keep working while disconnected.
        self.cursor_mode = analysis.OFF
        self.cursor_positions = [None, None]
        self.dragging_cursor = None
        self.cursor_lines = [
            self.ax.axvline(0.0, color="#66ff99", linewidth=1.0,
                            linestyle=":", visible=False),
            self.ax.axvline(0.0, color="#66ff99", linewidth=1.0,
                            linestyle=":", visible=False),
        ]
        # Spectrum view. Drawn on the same axes; the time-domain artists are
        # hidden rather than destroyed so switching back is instant.
        self.domain = TIME_DOMAIN
        (self.spectrum_line,) = self.ax.plot([], [], color="#ff9d3a",
                                             linewidth=1.0, visible=False)
        self.spectrum_window = analysis.DEFAULT_WINDOW
        # None = choose a span from the signal; 0 or less = the full axis out
        # to Nyquist; anything else is a fixed upper frequency in Hz.
        self.spectrum_span = None

        # XY view: one channel against the other, time implicit. Drawn on the
        # same axes for the same reason as the spectrum -- switching back is
        # instant and the gestures stay attached to a single axes object.
        (self.xy_line,) = self.ax.plot([], [], color=XY_COLOUR, linewidth=1.0,
                                       visible=False)
        self.xy_channels = (1, 2)

        # A trace computed from the two channels. Local arithmetic, not
        # :MATH:OPER, so it costs no SCPI and works on a loaded file.
        self.math_op = analysis.MATH_OFF
        (self.math_line,) = self.ax.plot([], [], color=MATH_COLOUR,
                                         linewidth=1.0, linestyle="-",
                                         visible=False)

        # A stored trace to compare against, and a trail of recent frames.
        # Both are view-only: neither is ever sent anywhere.
        self.reference = None
        self.reference_lines = {}
        self.persistence_depth = 0
        self.persistence_lines = deque()

        self.spectrum_readout = tk.StringVar(value="")
        ttk.Label(self.container, textvariable=self.spectrum_readout,
                  font=("Consolas", 9), foreground="#c26a00",
                  justify="left").grid(row=3, column=0, sticky="w")

        self.cursors = tk.StringVar(value="")
        ttk.Label(self.container, textvariable=self.cursors,
                  font=("Consolas", 9), foreground="#0a7", justify="left")\
            .grid(row=2, column=0, sticky="w")

        # --- hooks the App assigns ---
        self.on_level_commit = lambda level: None
        self.on_pan_commit = lambda time_offset, ch, volt_offset: None
        self.on_status = lambda text: None
        self.active_channel = lambda: 1
        self.trigger_channel = lambda: None
        self.enabled = lambda: False

        for event, handler in (("button_press_event", self._on_press),
                               ("motion_notify_event", self._on_motion),
                               ("button_release_event", self._on_release),
                               ("scroll_event", self._on_scroll)):
            self.canvas.mpl_connect(event, handler)

    def grid(self, **kwargs) -> None:
        self.container.grid(**kwargs)

    # ------------------------------------------------------------- drawing

    def show(self, wave: Waveform) -> None:
        self._push_persistence(wave)
        for ch in wave.channel_ids:
            volts = wave.channels[ch]
            line = self.lines.get(ch)
            if line is None:
                (line,) = self.ax.plot(wave.t, volts,
                                       color=st.CHANNEL_COLOURS.get(ch, "#cccccc"),
                                       linewidth=1.0, label=f"CH{ch}")
                self.lines[ch] = line
                self.ax.legend(loc="upper right", fontsize=8)
            else:
                line.set_data(wave.t, volts)
                line.set_visible(True)

        for ch, line in self.lines.items():
            if ch not in wave.channels:
                line.set_visible(False)

        self._draw_math(wave)

        if self.pan is None:   # a drag in progress owns the view; don't fight it
            self.ax.set_xlim(wave.t[0], wave.t[-1])
            self.ax.set_ylim(*self.y_limits(wave))
        if self.cursor_mode != analysis.OFF:
            self.cursors.set(self._cursor_text())
        self.canvas.draw_idle()

    def y_limits(self, wave: Waveform):
        """The vertical window, following where the trace actually sits.

        Centring on zero instead would undo the vertical offset: you drag the
        trace up, the write lands, and the next live frame yanks the axis back
        to a symmetric range with half the screen wasted.
        """
        traces = list(wave.channels.values())
        if self.reference is not None:
            # Keep the baseline framed too, or comparing against it is blind.
            traces += list(self.reference.channels.values())
        if self.math_line.get_visible():
            # A math trace drawn off-screen is worse than no math trace.
            traces.append(np.asarray(self.math_line.get_ydata()))
        low = min(float(np.min(v)) for v in traces)
        high = max(float(np.max(v)) for v in traces)
        if self.level_line.get_visible():
            # Keep the marker on screen even when it sits outside the signal.
            low, high = min(low, self.level), max(high, self.level)

        centre = (low + high) / 2.0
        half = (high - low) / 2.0 * Y_MARGIN
        if half <= 0:
            half = FLAT_TRACE_HALF_SPAN   # a DC level has no extent of its own
        return centre - half, centre + half

    def show_local_measurements(self, wave: Optional[Waveform],
                                channel: Optional[int]) -> None:
        """Parameters computed here rather than asked of the instrument."""
        rows = (analysis.measurements(wave, channel)
                if wave is not None and channel is not None else {})
        self._show_readings(f"CH{channel} local", rows)

    def show_math_measurements(self, wave: Optional[Waveform]) -> None:
        """The same parameters, over the computed trace instead of a channel."""
        values = self.math_values(wave)
        rows = (analysis.measurements_of(wave.t, values,
                                         analysis.math_unit(self.math_op))
                if values is not None else {})
        self._show_readings(self.math_op, rows)

    def _show_readings(self, title: str, rows) -> None:
        if not rows:
            self.measurements.set("")
            return
        cells = [f"{label}={eng(value, unit) if value is not None else '--':>10}"
                 for label, value, unit in rows.values()]
        # Two rows keep the dozen readings legible under the plot.
        half = (len(cells) + 1) // 2
        first = f"{title:<10}" + "  ".join(cells[:half])
        second = " " * 10 + "  ".join(cells[half:])
        self.measurements.set(first + "\n" + second)

    def show_measurements(self, stats) -> None:
        lines = []
        for ch, values in sorted(stats.items()):
            parts = []
            for label, value in values.items():
                unit = "Hz" if label == "Freq" else ("s" if label == "Period" else "V")
                shown = eng(value, unit) if value is not None else "--"
                parts.append(f"{label}={shown:>12}")
            lines.append(f"CH{ch}  " + "  ".join(parts))
        self.measurements.set("\n".join(lines))

    def savefig(self, path: str, dpi: int = 150) -> None:
        self.figure.savefig(path, dpi=dpi, bbox_inches="tight")

    # ------------------------------------------------------------- math trace

    def set_math(self, op: str) -> None:
        """Choose the arithmetic combination of the two channels to overlay."""
        self.math_op = op or analysis.MATH_OFF
        if self.math_op == analysis.MATH_OFF:
            self.math_line.set_visible(False)
            self.math_line.set_label("_math")
            self._refresh_legend()
            self.canvas.draw_idle()

    def _draw_math(self, wave: Waveform) -> None:
        """Update the math overlay for a freshly drawn capture.

        Hidden rather than left stale when the capture lacks an operand: a
        single-channel capture cannot produce CH1-CH2, and keeping the previous
        result on screen would read as a current measurement.
        """
        values = (analysis.math_trace(wave, self.math_op)
                  if self.math_op != analysis.MATH_OFF else None)
        if values is None:
            if self.math_line.get_visible():
                self.math_line.set_visible(False)
                self.math_line.set_label("_math")
                self._refresh_legend()
            return

        unit = analysis.math_unit(self.math_op)
        self.math_line.set_data(wave.t, values)
        # The unit rides in the legend because the y axis is in volts and a
        # product of two voltages is not: mixing them on one axis is only
        # honest if the label says which is which.
        self.math_line.set_label(f"{self.math_op} ({unit})")
        self.math_line.set_visible(self.domain == TIME_DOMAIN)
        self._refresh_legend()

    def math_values(self, wave: Optional[Waveform]):
        """The math trace for a capture, or None -- for the readout below."""
        if wave is None or self.math_op == analysis.MATH_OFF:
            return None
        return analysis.math_trace(wave, self.math_op)

    # ------------------------------------------------------------------- XY

    def show_xy(self, wave: Waveform, x_channel: int, y_channel: int) -> None:
        """Plot one channel against the other, time implicit."""
        self.xy_channels = (x_channel, y_channel)
        pairs = analysis.xy_pairs(wave, x_channel, y_channel)
        if pairs is None:
            self.xy_line.set_data([], [])
            self.xy_line.set_visible(False)
            self.spectrum_readout.set(
                f"XY needs both CH{x_channel} and CH{y_channel} in the capture.")
            self.canvas.draw_idle()
            return

        x, y = pairs
        self.xy_line.set_data(x, y)
        self.xy_line.set_visible(True)
        self.ax.set_xlabel(f"CH{x_channel} (V)")
        self.ax.set_ylabel(f"CH{y_channel} (V)")
        self.ax.set_xlim(*_padded(x))
        self.ax.set_ylim(*_padded(y))
        self.spectrum_readout.set(
            f"XY  x=CH{x_channel}  y=CH{y_channel}  {len(x)} points")
        self.canvas.draw_idle()

    # ------------------------------------------------------ reference trace

    def set_reference(self, wave: Optional[Waveform]) -> None:
        """Freeze a capture as the baseline to compare against, or clear it."""
        self.reference = wave
        for line in self.reference_lines.values():
            line.remove()
        self.reference_lines = {}

        if wave is not None:
            for ch in wave.channel_ids:
                (line,) = self.ax.plot(wave.t, wave.channels[ch],
                                       color=REFERENCE_COLOUR, linewidth=1.0,
                                       linestyle="--", alpha=0.8,
                                       label=f"REF{ch}")
                self.reference_lines[ch] = line
        self._refresh_legend()
        self.canvas.draw_idle()

    def _refresh_legend(self) -> None:
        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(handles, labels, loc="upper right", fontsize=8)

    # --------------------------------------------------------- persistence

    def set_persistence(self, depth: int) -> None:
        """Keep this many previous frames on screen, fading with age."""
        self.persistence_depth = max(0, min(int(depth), MAX_PERSISTENCE))
        self.clear_persistence()

    def clear_persistence(self) -> None:
        while self.persistence_lines:
            self.persistence_lines.popleft().remove()
        self.canvas.draw_idle()

    def _push_persistence(self, wave: Waveform) -> None:
        """Leave a fading copy of the frame that is about to be replaced."""
        if self.persistence_depth <= 0 or self.domain != TIME_DOMAIN:
            return
        for ch in wave.channel_ids:
            (ghost,) = self.ax.plot(wave.t, wave.channels[ch],
                                    color=st.CHANNEL_COLOURS.get(ch, "#cccccc"),
                                    linewidth=0.8)
            # Kept out of the legend: one entry per ghost would swamp it.
            ghost.set_label("_ghost")
            self.persistence_lines.append(ghost)

        limit = self.persistence_depth * max(1, len(wave.channel_ids))
        while len(self.persistence_lines) > limit:
            self.persistence_lines.popleft().remove()

        total = len(self.persistence_lines)
        for age, ghost in enumerate(self.persistence_lines):
            # Oldest first in the deque, so alpha climbs towards the newest.
            ghost.set_alpha(FAINTEST_ALPHA
                            + (1.0 - FAINTEST_ALPHA) * (age + 1) / (total + 1))

    # ------------------------------------------------------------- spectrum

    def set_domain(self, domain: str, window: Optional[str] = None) -> None:
        """Switch the canvas between the trace, its spectrum, and the XY figure.

        The three views share one axes object, so only the artists change. That
        keeps every mouse handler attached to the same axes and makes switching
        back instant, at the cost of having to relabel the axes each time.
        """
        if domain not in DOMAINS:
            raise ValueError(f"unknown domain {domain!r}; expected one of {list(DOMAINS)}")
        self.domain = domain
        if window is not None:
            self.spectrum_window = window

        time_domain = domain == TIME_DOMAIN
        for line in self.lines.values():
            line.set_visible(time_domain)
        for line in self.reference_lines.values():
            line.set_visible(time_domain)
        self.math_line.set_visible(time_domain
                                   and self.math_op != analysis.MATH_OFF
                                   and len(self.math_line.get_xdata()) > 0)
        self.spectrum_line.set_visible(domain == SPECTRUM_DOMAIN)
        # Only reveal the XY trace if there is one; show_xy hides it again when
        # the capture is missing an axis.
        self.xy_line.set_visible(domain == XY_DOMAIN
                                 and len(self.xy_line.get_xdata()) > 0)
        if not time_domain:
            self.clear_persistence()
            # The level marker and cursors measure volts against time; in
            # neither other view do they mean anything, so take them off rather
            # than leave them mislabelled.
            self.level_line.set_visible(False)
            self.level_tag.set_visible(False)
            self.set_cursor_mode(analysis.OFF)

        legend = self.ax.get_legend()
        if legend is not None:
            legend.set_visible(time_domain)

        if domain == SPECTRUM_DOMAIN:
            self.ax.set_xlabel("Frequency (Hz)")
            self.ax.set_ylabel("Magnitude (dBV)")
        elif domain == XY_DOMAIN:
            x_channel, y_channel = self.xy_channels
            self.ax.set_xlabel(f"CH{x_channel} (V)")
            self.ax.set_ylabel(f"CH{y_channel} (V)")
        else:
            self.ax.set_xlabel("Time (s)")
            self.ax.set_ylabel("Voltage (V)")
            self.spectrum_readout.set("")
        self.canvas.draw_idle()

    def show_spectrum(self, wave: Waveform, channel: Optional[int] = None) -> None:
        """Draw the spectrum of one channel of a capture."""
        if channel is None:
            channel = wave.channel_ids[0] if wave.channel_ids else None
        spec = (analysis.spectrum(wave, channel, self.spectrum_window)
                if channel is not None else None)
        if spec is None:
            self.spectrum_line.set_data([], [])
            self.spectrum_readout.set("No spectrum for this capture.")
            self.canvas.draw_idle()
            return

        db = spec.db
        self.spectrum_line.set_data(spec.freqs, db)
        self.spectrum_line.set_visible(True)

        span = self._spectrum_limit(spec)
        self.ax.set_xlim(0.0, span)
        # Scale to the part on screen: a bin beyond the right edge setting the
        # top would push the visible trace down out of the window.
        shown = db[spec.freqs <= span]
        top = float(np.max(shown)) if len(shown) else float(np.max(db))
        self.ax.set_ylim(top - SPECTRUM_DB_RANGE, top + SPECTRUM_DB_HEADROOM)

        self.spectrum_readout.set(self._spectrum_text(channel, spec, span))
        self.canvas.draw_idle()

    def set_spectrum_span(self, span: Optional[float]) -> None:
        """Limit the frequency axis. None means pick a span from the signal."""
        self.spectrum_span = span

    def _spectrum_limit(self, spec) -> float:
        """The right-hand edge of the frequency axis."""
        if self.spectrum_span is None:
            return spec.display_span() or spec.nyquist
        if self.spectrum_span <= 0:
            return spec.nyquist
        # A fixed span wider than the data has nothing to show beyond Nyquist.
        return min(self.spectrum_span, spec.nyquist)

    def _spectrum_text(self, channel: int, spec, span: float) -> str:
        frequency, volts = spec.peak()
        peak = ("--" if frequency is None
                else f"{eng(frequency, 'Hz')} @ {eng(volts, 'V')}")
        text = (f"CH{channel}  peak={peak}   window={spec.window}"
                f"   res={eng(spec.resolution, 'Hz')}"
                f"   span={eng(span, 'Hz')} of {eng(spec.nyquist, 'Hz')}"
                f"   fs={eng(spec.sample_rate, 'Sa/s')}")

        cycles = spec.cycles()
        if cycles is not None and cycles < analysis.CYCLES_FOR_A_SHARP_PEAK:
            # The usual reason a peak looks like a smear. The record holds too
            # few cycles for a bin to land on the signal, and no amount of
            # windowing fixes that -- only a slower timebase does.
            text += (f"\nonly {cycles:.1f} cycles on screen: the peak is split "
                     f"between bins and reads low. Slow the timebase down.")
        return text

    # -------------------------------------------------------------- cursors

    def set_cursor_mode(self, mode: str) -> None:
        """Switch between off, time and voltage cursors.

        Fresh cursors are dropped a third and two thirds of the way across the
        current view, so they land somewhere useful rather than at the origin.
        """
        self.cursor_mode = mode
        if mode == analysis.OFF:
            for line in self.cursor_lines:
                line.set_visible(False)
            self.cursor_positions = [None, None]
            self.cursors.set("")
            self.canvas.draw_idle()
            return

        low, high = (self.ax.get_xlim() if mode == analysis.TIME
                     else self.ax.get_ylim())
        self.cursor_positions = list(analysis.default_cursor_positions(low, high))
        self._redraw_cursors()

    def _redraw_cursors(self) -> None:
        horizontal = self.cursor_mode == analysis.VOLTAGE
        for line, position in zip(self.cursor_lines, self.cursor_positions):
            if position is None or self.cursor_mode == analysis.OFF:
                line.set_visible(False)
                continue
            # axvline and axhline differ only in which pair of data the artist
            # holds, so one artist can serve both by rewriting both.
            if horizontal:
                line.set_xdata([0.0, 1.0])
                line.set_ydata([position, position])
                line.set_transform(self.ax.get_yaxis_transform())
            else:
                line.set_xdata([position, position])
                line.set_ydata([0.0, 1.0])
                line.set_transform(self.ax.get_xaxis_transform())
            line.set_visible(True)

        self.cursors.set(self._cursor_text())
        self.canvas.draw_idle()

    def _cursor_text(self) -> str:
        rows = analysis.cursor_readings(self.cursor_mode, *self.cursor_positions)
        if not rows:
            return ""
        parts = [f"{label}={eng(value, unit) if value is not None else '--':>11}"
                 for label, value, unit in rows]
        return "Cursor  " + "  ".join(parts)

    def _cursor_axis_value(self, event) -> Optional[float]:
        if self.cursor_mode == analysis.TIME:
            return event.xdata
        if self.cursor_mode == analysis.VOLTAGE:
            return event.ydata
        return None

    def _grab_cursor(self, event) -> Optional[int]:
        value = self._cursor_axis_value(event)
        if value is None:
            return None
        low, high = (self.ax.get_xlim() if self.cursor_mode == analysis.TIME
                     else self.ax.get_ylim())
        tolerance = abs(high - low) * GRAB_TOLERANCE
        return analysis.nearest_cursor(self.cursor_positions, value, tolerance)

    # -------------------------------------------------------- level marker

    @property
    def level(self) -> float:
        return float(self.level_line.get_ydata()[0])

    def show_level(self, level: float) -> None:
        """Move the marker without touching the instrument."""
        self.level_line.set_ydata([level, level])
        self.level_line.set_visible(True)
        self.level_tag.set_visible(True)
        self.level_tag.xy = (1.0, level)
        self.canvas.draw_idle()

    def level_span(self) -> float:
        """The scope only accepts a level within +/-6 divisions of centre."""
        ch = self.trigger_channel()
        if not ch:
            return float("inf")
        return 6.0 * self.volt_scales.get(ch, 1.0)

    def clamp_level(self, level: float) -> float:
        limit = self.level_span()
        clamped = max(-limit, min(limit, level))
        if clamped != level:
            self.on_status(f"Level clamped to +/-6 div ({eng(limit, 'V')}).")
        return clamped

    def _commit_level(self, level: float) -> None:
        level = self.clamp_level(level)
        self.show_level(level)
        self.on_level_commit(level)

    def _near_level(self, event) -> bool:
        if event.ydata is None or not self.level_line.get_visible():
            return False
        low, high = self.ax.get_ylim()
        return abs(event.ydata - self.level) < abs(high - low) * GRAB_TOLERANCE

    # ------------------------------------------------------------ gestures

    def _on_press(self, event) -> None:
        if self.domain != TIME_DOMAIN:
            return
        # Cursors are local, so unlike the rest of the gestures they stay live
        # while disconnected; check them before the enabled() gate.
        if event.inaxes is self.ax and event.button == 1:
            grabbed = self._grab_cursor(event)
            if grabbed is not None:
                self.dragging_cursor = grabbed
                return

        if not self.enabled() or event.inaxes is not self.ax or event.button != 1:
            return
        if self._near_level(event):
            self.dragging_level = True
        elif event.dblclick:
            # Double-clicking anywhere drops the level right there.
            self._commit_level(event.ydata)
        else:
            self._pan_start(event)

    def _on_motion(self, event) -> None:
        if self.dragging_cursor is not None:
            value = self._cursor_axis_value(event)
            if value is not None:
                self.cursor_positions[self.dragging_cursor] = value
                self._redraw_cursors()
            return
        if self.pan is not None:
            self._pan_move(event)
            return
        if event.inaxes is not self.ax or event.ydata is None:
            return
        if self.dragging_level:
            self.show_level(event.ydata)  # local only; the write waits for release
        else:
            cursor = "sb_v_double_arrow" if self._near_level(event) else "fleur"
            try:
                self.canvas.get_tk_widget().configure(cursor=cursor)
            except tk.TclError:
                pass

    def _on_release(self, event) -> None:
        if self.dragging_cursor is not None:
            # Nothing to commit: a cursor is a local measurement, not a setting.
            self.dragging_cursor = None
            return
        if self.pan is not None:
            self._pan_finish(event)
            return
        if not self.dragging_level:
            return
        self.dragging_level = False
        level = event.ydata
        self._commit_level(self.level if level is None else level)

    def _on_scroll(self, event) -> None:
        """Fine adjustment: one notch moves the level a fifth of a division."""
        if self.domain != TIME_DOMAIN:
            return
        if not self.enabled() or event.inaxes is not self.ax:
            return
        ch = self.trigger_channel()
        step = self.volt_scales.get(ch, 1.0) * SCROLL_STEP_DIV if ch else 0.05
        self._commit_level(self.level + event.step * step)

    # --------------------------------------------------------- drag-to-pan

    def _pan_start(self, event) -> None:
        ch = self.active_channel()
        self.pan = {
            "px": (event.x, event.y),
            "xlim": self.ax.get_xlim(),
            "ylim": self.ax.get_ylim(),
            "time_offset": self.time_offset,
            "ch": ch,
            "volt_offset": self.volt_offsets.get(ch, 0.0),
        }

    def _pan_delta(self, pan, event):
        """Convert the pixel drag into data units against the pre-drag limits."""
        box = self.ax.get_window_extent()
        x0, y0 = pan["px"]
        xlim, ylim = pan["xlim"], pan["ylim"]
        dx = (event.x - x0) / box.width * (xlim[1] - xlim[0])
        dy = (event.y - y0) / box.height * (ylim[1] - ylim[0])
        return dx, dy

    def _pan_move(self, event) -> None:
        if event.x is None or event.y is None:
            return
        dx, dy = self._pan_delta(self.pan, event)
        xlim, ylim = self.pan["xlim"], self.pan["ylim"]
        self.ax.set_xlim(xlim[0] - dx, xlim[1] - dx)
        self.ax.set_ylim(ylim[0] - dy, ylim[1] - dy)
        self.canvas.draw_idle()

    def _pan_finish(self, event) -> None:
        pan, self.pan = self.pan, None
        if event.x is None or event.y is None:
            return

        dx, dy = self._pan_delta(pan, event)
        if abs(dx) < 1e-15 and abs(dy) < 1e-15:
            return              # a plain click, not a drag

        ch = pan["ch"]
        # Displayed volts are raw volts minus the channel offset, so moving the
        # trace up by dy means decreasing the offset by the same amount.
        time_offset = pan["time_offset"] - dx
        volt_offset = pan["volt_offset"] - dy

        self.time_offset = time_offset
        self.volt_offsets[ch] = volt_offset
        self.on_pan_commit(time_offset, ch, volt_offset)
