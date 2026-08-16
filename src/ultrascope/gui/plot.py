"""The waveform canvas: drawing, the trigger-level marker, and mouse interaction.

Dragging only moves matplotlib artists and the view; the SCPI write happens
once, on mouse release. Writing during ``motion_notify_event`` would put dozens
of commands on the USBTMC link per drag and stall it.

The App assigns the hooks below; they default to doing nothing so the canvas
can be built and drawn without an instrument.
"""

from __future__ import annotations

import tkinter as tk
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
# How much of the spectrum to show below the strongest bin, and how much air
# to leave above it.
SPECTRUM_DB_RANGE = 100.0
SPECTRUM_DB_HEADROOM = 10.0


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
        low = min(float(np.min(v)) for v in wave.channels.values())
        high = max(float(np.max(v)) for v in wave.channels.values())
        if self.level_line.get_visible():
            # Keep the marker on screen even when it sits outside the signal.
            low, high = min(low, self.level), max(high, self.level)

        centre = (low + high) / 2.0
        half = (high - low) / 2.0 * Y_MARGIN
        if half <= 0:
            half = FLAT_TRACE_HALF_SPAN   # a DC level has no extent of its own
        return centre - half, centre + half

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

    # ------------------------------------------------------------- spectrum

    def set_domain(self, domain: str, window: Optional[str] = None) -> None:
        """Switch the canvas between the trace and its spectrum."""
        self.domain = domain
        if window is not None:
            self.spectrum_window = window

        showing_spectrum = domain == SPECTRUM_DOMAIN
        for line in self.lines.values():
            line.set_visible(not showing_spectrum)
        self.spectrum_line.set_visible(showing_spectrum)
        if showing_spectrum:
            # The level marker and cursors measure volts against time; neither
            # means anything here, so take them off rather than mislabel them.
            self.level_line.set_visible(False)
            self.level_tag.set_visible(False)
            self.set_cursor_mode(analysis.OFF)
            self.ax.set_xlabel("Frequency (Hz)")
            self.ax.set_ylabel("Magnitude (dBV)")
            legend = self.ax.get_legend()
            if legend is not None:
                legend.set_visible(False)
        else:
            self.ax.set_xlabel("Time (s)")
            self.ax.set_ylabel("Voltage (V)")
            self.spectrum_readout.set("")
            legend = self.ax.get_legend()
            if legend is not None:
                legend.set_visible(True)
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
        self.ax.set_xlim(0.0, spec.freqs[-1])
        top = float(np.max(db))
        self.ax.set_ylim(top - SPECTRUM_DB_RANGE, top + SPECTRUM_DB_HEADROOM)

        frequency, volts = spec.peak()
        peak = ("--" if frequency is None
                else f"{eng(frequency, 'Hz')} @ {eng(volts, 'V')}")
        self.spectrum_readout.set(
            f"CH{channel}  peak={peak}   window={spec.window}"
            f"   res={eng(spec.resolution, 'Hz')}"
            f"   fs={eng(spec.sample_rate, 'Sa/s')}")
        self.canvas.draw_idle()

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
