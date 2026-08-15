"""The waveform canvas and the measurement readout under it."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from ..units import eng
from ..waveform import Waveform
from . import state as st

Y_MARGIN = 1.15


class PlotCanvas:
    """Draws captures and keeps one matplotlib line per channel.

    Lines are reused across frames and only toggled invisible when a channel
    goes away, which is what keeps live mode from rebuilding the axes.
    """

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

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        self.measurements = tk.StringVar(value="")
        ttk.Label(self.container, textvariable=self.measurements,
                  font=("Consolas", 9), justify="left")\
            .grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.lines = {}

    def grid(self, **kwargs) -> None:
        self.container.grid(**kwargs)

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

        span = max(np.max(np.abs(v)) for v in wave.channels.values())
        span = span * Y_MARGIN or 1.0
        self.ax.set_xlim(wave.t[0], wave.t[-1])
        self.ax.set_ylim(-span, span)
        self.canvas.draw_idle()

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
