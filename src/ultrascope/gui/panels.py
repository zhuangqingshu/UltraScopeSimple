"""Control panel widgets.

Each panel owns its widgets and Tk variables and reports changes through
callbacks. No panel knows about Scope, the worker, or VISA.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

from .. import analysis
from . import state as st

# The CLI's --trigger-timeout default; kept in step with cli.py.
DEFAULT_SINGLE_TIMEOUT_S = 30.0


def labelled_combo(parent, label: str, values: Sequence[str], row: int,
                   command: Callable[[], None], width: int = 13):
    """A label + readonly combobox on one grid row. Returns its StringVar."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
    var = tk.StringVar()
    combo = ttk.Combobox(parent, textvariable=var, values=list(values),
                         state="readonly", width=width)
    combo.grid(row=row, column=1, columnspan=2, sticky="ew", pady=1)
    combo.bind("<<ComboboxSelected>>", lambda _e: command())
    return var


def labelled_entry(parent, label: str, row: int, command: Callable[[], None],
                   width: int = 13):
    """A numeric entry that applies on Enter or when focus leaves."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
    var = tk.StringVar()
    widget = ttk.Entry(parent, textvariable=var, width=width)
    widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=1)
    widget.bind("<Return>", lambda _e: command())
    widget.bind("<FocusOut>", lambda _e: command())
    return var


def parse_float(text) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class ScrollableColumn:
    """A column of panels that scrolls when it is taller than the window.

    The panels are grouped into notebook tabs so that none of them normally
    needs scrolling, but a tab is only as short as whatever is on it: shrink the
    window, or add a panel, and the bottom one would silently fall off the
    screen. That is the failure this guards -- the scrollbar appears only when
    the content genuinely does not fit.

    Tk has no scrollable frame, so this is the usual Canvas-with-a-window
    construction: the frame lives inside a Canvas, and the Canvas scrolls it.
    """

    # How far one notch of the wheel scrolls.
    WHEEL_UNITS = 3

    def __init__(self, parent):
        self.outer = ttk.Frame(parent)
        self.outer.rowconfigure(0, weight=1)
        self.outer.columnconfigure(0, weight=1)

        background = ttk.Style().lookup("TFrame", "background") or None
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, borderwidth=0,
                                background=background)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self.outer, orient="vertical",
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scrolled)

        self.inner = ttk.Frame(self.canvas)
        self.inner.columnconfigure(0, weight=1)
        self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                anchor="nw")

        self.inner.bind("<Configure>", self._on_content_resized)
        self.canvas.bind("<Configure>", self._on_view_resized)
        # bind_all is the only way to see the wheel over a child widget, so
        # every column hears every notch and each checks whether the pointer is
        # actually over its own canvas.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._on_wheel, add="+")

    def grid(self, **kwargs) -> None:
        self.outer.grid(**kwargs)

    def _on_content_resized(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # Let the column ask for exactly the width its widest panel needs; the
        # sidebar is packed against the plot, which takes the rest.
        self.canvas.configure(width=self.inner.winfo_reqwidth())

    def _on_view_resized(self, event) -> None:
        # Panels stretch to the full width rather than keeping their natural
        # one, so a combobox lines up with the group box around it.
        self.canvas.itemconfigure(self.window, width=event.width)

    def _on_scrolled(self, first: str, last: str) -> None:
        """Show the scrollbar only while there is something to scroll to."""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scrollbar.grid_remove()
        else:
            self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scrollbar.set(first, last)

    def _contains_pointer(self) -> bool:
        try:
            under = self.canvas.winfo_containing(*self.canvas.winfo_pointerxy())
        except tk.TclError:
            return False
        while under is not None:
            if under is self.canvas:
                return True
            under = getattr(under, "master", None)
        return False

    def _on_wheel(self, event) -> None:
        if not self._contains_pointer():
            return
        # Windows and macOS report a signed delta; X11 sends button 4/5.
        if getattr(event, "num", None) in (4, 5):
            direction = -1 if event.num == 4 else 1
        else:
            direction = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(direction * self.WHEEL_UNITS, "units")


class Panel:
    """A titled group box that can be enabled or disabled as a unit."""

    title = ""

    def __init__(self, parent):
        self.frame = ttk.LabelFrame(parent, text=self.title, padding=6)

    def grid(self, row: int) -> None:
        self.frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))

    def set_enabled(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        for child in self.frame.winfo_children():
            target = "readonly" if (on and isinstance(child, ttk.Combobox)) else state
            try:
                child.configure(state=target)
            except tk.TclError:
                pass


class ConnectionPanel(Panel):
    title = "Connection"

    def __init__(self, parent, on_refresh: Callable[[], None],
                 on_toggle: Callable[[], None]):
        super().__init__(parent)
        box = self.frame

        self.resource = tk.StringVar()
        self.resource_combo = ttk.Combobox(box, textvariable=self.resource, width=34)
        self.resource_combo.grid(row=0, column=0, columnspan=2,
                                 sticky="ew", pady=(0, 4))

        ttk.Button(box, text="Refresh", command=on_refresh)\
            .grid(row=1, column=0, sticky="ew", padx=(0, 3))
        self.connect_button = ttk.Button(box, text="Connect", command=on_toggle)
        self.connect_button.grid(row=1, column=1, sticky="ew")

        self.idn = tk.StringVar(value="not connected")
        ttk.Label(box, textvariable=self.idn, wraplength=250, foreground="#555")\
            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def set_resources(self, resources: Sequence[str]) -> None:
        self.resource_combo["values"] = list(resources)
        if resources and not self.resource.get():
            self.resource.set(resources[0])

    def show_connected(self, idn: str) -> None:
        self.idn.set(idn)
        self.connect_button.configure(text="Disconnect")

    def show_disconnected(self) -> None:
        self.idn.set("not connected")
        self.connect_button.configure(text="Connect")


class AcquisitionPanel(Panel):
    title = "Acquisition"

    def __init__(self, parent, on_run, on_stop, on_auto, on_single, on_force,
                 on_live, on_apply):
        super().__init__(parent)
        box = self.frame

        for column, (label, command) in enumerate(
                (("Run", on_run), ("Stop", on_stop), ("Auto", on_auto))):
            ttk.Button(box, text=label, command=command, width=8)\
                .grid(row=0, column=column, padx=1, sticky="ew")

        ttk.Button(box, text="Single", command=on_single, width=8)\
            .grid(row=1, column=0, padx=1, pady=(3, 0), sticky="ew")
        ttk.Button(box, text="Force", command=on_force, width=8)\
            .grid(row=1, column=1, padx=1, pady=(3, 0), sticky="ew")

        self.live = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Live", variable=self.live, command=on_live)\
            .grid(row=1, column=2, padx=1, pady=(3, 0))

        self.acq_type = labelled_combo(box, "Type", st.ACQ_TYPES, 2, on_apply)
        self.average = labelled_combo(box, "Averages", st.AVERAGE_COUNTS, 3, on_apply)
        self.memory = labelled_combo(box, "Memory", st.MEMORY_DEPTHS, 4, on_apply)
        # Mirrors the CLI's --trigger-timeout; Single blocks the worker for
        # this long, so it is worth being able to shorten it.
        self.single_timeout = labelled_entry(box, "Single wait (s)", 5,
                                             lambda: None)
        self.single_timeout.set(str(DEFAULT_SINGLE_TIMEOUT_S))

    def single_timeout_seconds(self) -> float:
        value = parse_float(self.single_timeout.get())
        return value if value and value > 0 else DEFAULT_SINGLE_TIMEOUT_S


class ChannelPanel(Panel):
    def __init__(self, parent, ch: int, volt_table: st.OptionTable,
                 active_var: tk.IntVar, on_apply: Callable[[int], None]):
        self.ch = ch
        self.title = f"Channel {ch}"
        super().__init__(parent)
        box = self.frame
        apply = lambda: on_apply(ch)  # noqa: E731

        self.on = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Display", variable=self.on, command=apply)\
            .grid(row=0, column=0, sticky="w")
        # The active channel is the one vertical dragging and the wheel move.
        ttk.Radiobutton(box, text="Active", value=ch, variable=active_var)\
            .grid(row=0, column=1, sticky="w")

        # Probe sits above V/div because getting it wrong scales every voltage
        # reading by 10x, and that is hard to spot from the trace alone.
        self.probe = labelled_combo(box, "Probe", st.probe_labels(), 1, apply)
        self.volt_table = volt_table
        self.scale = labelled_combo(box, "V/div", volt_table.labels, 2, apply)
        self.coupling = labelled_combo(box, "Coupling", st.COUPLINGS, 3, apply)
        self.offset = labelled_entry(box, "Offset (V)", 4, apply)

    def volt_scale(self) -> Optional[float]:
        return self.volt_table.value_for(self.scale.get())

    def probe_ratio(self) -> Optional[float]:
        text = self.probe.get()
        return float(text.rstrip("Xx")) if text else None

    def volt_offset(self) -> Optional[float]:
        return parse_float(self.offset.get())


class HorizontalPanel(Panel):
    title = "Horizontal"

    def __init__(self, parent, time_table: st.OptionTable, on_apply):
        super().__init__(parent)
        self.time_table = time_table
        self.timebase = labelled_combo(self.frame, "s/div", time_table.labels,
                                       0, on_apply)
        self.position = labelled_entry(self.frame, "Position (s)", 1, on_apply)

    def seconds_per_div(self) -> Optional[float]:
        return self.time_table.value_for(self.timebase.get())

    def time_offset(self) -> Optional[float]:
        return parse_float(self.position.get())


class TriggerPanel(Panel):
    title = "Trigger"

    def __init__(self, parent, on_mode_change, on_apply, on_level_50):
        super().__init__(parent)
        box = self.frame

        self.mode = labelled_combo(box, "Mode", st.TRIGGER_MODES, 0, on_mode_change)
        self.source = labelled_combo(box, "Source", st.TRIGGER_SOURCES, 1, on_apply)
        self.slope = labelled_combo(box, "Slope", st.TRIGGER_SLOPES, 2, on_apply)
        self.sweep = labelled_combo(box, "Sweep", st.TRIGGER_SWEEPS, 3, on_apply)
        self.coupling = labelled_combo(box, "Coupling", st.TRIGGER_COUPLINGS, 4, on_apply)

        ttk.Label(box, text="Level (V)").grid(row=5, column=0, sticky="w", pady=(3, 0))
        self.level = tk.StringVar(value="0")
        entry = ttk.Entry(box, textvariable=self.level, width=12)
        entry.grid(row=5, column=1, sticky="ew", pady=(3, 0))
        entry.bind("<Return>", lambda _e: on_apply())
        ttk.Button(box, text="Set", command=on_apply)\
            .grid(row=6, column=0, sticky="ew", pady=(3, 0), padx=(0, 3))
        ttk.Button(box, text="50%", command=on_level_50)\
            .grid(row=6, column=1, sticky="ew", pady=(3, 0))
        ttk.Label(box, text="drag the red line, or scroll over the plot",
                  foreground="#777", font=("", 8))\
            .grid(row=7, column=0, columnspan=2, sticky="w")
        self.holdoff = labelled_entry(box, "Holdoff (s)", 8, on_apply)

        # PULSE and SLOPE add a condition plus a duration. The group is built
        # once and shown only for the modes that have one, so switching to EDGE
        # cannot leave a stale width on screen.
        self.condition_frame = ttk.Frame(box)
        self.condition_frame.grid(row=9, column=0, columnspan=2, sticky="ew")
        self.condition_frame.columnconfigure(1, weight=1)
        self.condition = labelled_combo(self.condition_frame, "Condition",
                                        (), 0, on_apply)
        self.width = labelled_entry(self.condition_frame, "Width/Time (s)",
                                    1, on_apply)
        self.condition_combo = self.condition_frame.grid_slaves(row=0, column=1)[0]
        self.condition_frame.grid_remove()

        self.status = tk.StringVar(value="--")
        ttk.Label(box, textvariable=self.status, foreground="#0a6")\
            .grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def show_conditions(self, labels: Sequence[str]) -> None:
        """Reveal the condition group for a mode that has one, or hide it."""
        if labels:
            self.condition_combo["values"] = list(labels)
            self.condition_frame.grid()
        else:
            self.condition_frame.grid_remove()
            self.condition.set("")
            self.width.set("")

    def has_conditions(self) -> bool:
        return bool(self.condition_frame.winfo_manager())

    def width_seconds(self) -> Optional[float]:
        return parse_float(self.width.get())

    def level_volts(self) -> Optional[float]:
        return parse_float(self.level.get())

    def holdoff_seconds(self) -> Optional[float]:
        return parse_float(self.holdoff.get())

    def channel(self) -> Optional[int]:
        """Which channel's volts/div governs the level, or None for EXT/ACLINE."""
        src = self.source.get().upper()
        if "2" in src:
            return 2
        if "1" in src:
            return 1
        return None


class FilePanel(Panel):
    """Reading and writing captures.

    Only the deep-memory button needs the instrument. Opening a saved capture
    and writing the one on screen are pure file operations, so the panel as a
    whole stays live while disconnected and gates that one button itself --
    everything downstream of a capture (cursors, spectrum, XY, math, the local
    measurements) then works on an archived waveform exactly as on a live one.
    """

    title = "Capture file"

    def __init__(self, parent, on_open, on_csv, on_png, on_deep):
        super().__init__(parent)
        box = self.frame
        ttk.Button(box, text="Open CSV...", command=on_open)\
            .grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(box, text="Save CSV", command=on_csv)\
            .grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(3, 0))
        ttk.Button(box, text="Save PNG", command=on_png)\
            .grid(row=1, column=1, sticky="ew", pady=(3, 0))
        self.deep_button = ttk.Button(box, text="Deep memory capture (RAW)",
                                      command=on_deep)
        self.deep_button.grid(row=2, column=0, columnspan=2, sticky="ew",
                              pady=(3, 0))
        self.source = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.source, foreground="#777",
                  font=("", 8), wraplength=240)\
            .grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self.deep_button.configure(state="disabled")

    def set_enabled(self, on: bool) -> None:
        # Only the instrument-backed button follows the connection.
        self.deep_button.configure(state="normal" if on else "disabled")


class CursorPanel(Panel):
    """Local measurement cursors.

    These never reach the instrument, so unlike every other panel this one
    stays usable while disconnected -- you can measure a capture you already
    have on screen.
    """

    title = "Cursors"

    def __init__(self, parent, on_mode_change):
        super().__init__(parent)
        self.mode = tk.StringVar(value=analysis.OFF)
        for column, mode in enumerate(analysis.CURSOR_MODES):
            ttk.Radiobutton(self.frame, text=mode.capitalize(), value=mode,
                            variable=self.mode, command=on_mode_change)                .grid(row=0, column=column, sticky="w", padx=(0, 6))
        ttk.Label(self.frame, text="drag the dotted lines on the plot",
                  foreground="#777", font=("", 8))            .grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))


class ReferencePanel(Panel):
    """A stored trace to compare the live one against.

    Local like the cursors: the reference is only ever drawn, never sent.
    """

    title = "Reference"

    def __init__(self, parent, on_store, on_load, on_clear):
        super().__init__(parent)
        ttk.Button(self.frame, text="Store current", command=on_store)            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(self.frame, text="Load CSV", command=on_load)            .grid(row=0, column=1, sticky="ew")
        ttk.Button(self.frame, text="Clear", command=on_clear)            .grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        self.status = tk.StringVar(value="none stored")
        ttk.Label(self.frame, textvariable=self.status, foreground="#777",
                  font=("", 8), wraplength=240)            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))


class PersistencePanel(Panel):
    """Fading trail of recent frames -- shows jitter and rare glitches that a
    single frame hides."""

    title = "Persistence"

    def __init__(self, parent, on_change, on_clear):
        super().__init__(parent)
        self.depth = labelled_combo(self.frame, "Frames",
                                    ("0", "2", "5", "10", "20", "32"),
                                    0, on_change)
        self.depth.set("0")
        ttk.Button(self.frame, text="Clear trail", command=on_clear)            .grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3, 0))

    def frames(self) -> int:
        text = self.depth.get()
        return int(text) if text.isdigit() else 0


class MeasurePanel(Panel):
    """Chooses where the readout under the plot comes from.

    The instrument's own measurements use its full acquisition and are the more
    accurate of the two, but need a connection. The local ones are computed
    from the captured samples, so they also work on a capture already on screen
    while disconnected -- at the resolution of the 600-point screen record.
    """

    title = "Measurements"

    INSTRUMENT = "instrument"
    LOCAL = "local"

    def __init__(self, parent, on_change):
        super().__init__(parent)
        self.source = tk.StringVar(value=self.INSTRUMENT)
        for column, (value, label) in enumerate(
                ((self.INSTRUMENT, "From scope"), (self.LOCAL, "Local"))):
            ttk.Radiobutton(self.frame, text=label, value=value,
                            variable=self.source, command=on_change)                .grid(row=0, column=column, sticky="w", padx=(0, 8))
        # MATH measures the computed trace with the same code as a channel.
        # Only meaningful for the local source: the instrument has no such
        # trace, because the arithmetic happens here.
        self.channel = labelled_combo(self.frame, "Channel",
                                      ("1", "2", self.MATH), 1, on_change)
        self.channel.set("1")

    MATH = "MATH"

    def is_local(self) -> bool:
        return self.source.get() == self.LOCAL

    def is_math(self) -> bool:
        return self.channel.get() == self.MATH

    def channel_number(self) -> Optional[int]:
        text = self.channel.get()
        return int(text) if text.isdigit() else None


class ViewPanel(Panel):
    """Which of the three views the canvas shows.

    Time, spectrum and XY are mutually exclusive, so this is one combo rather
    than a checkbox per view. All three are computed locally, so like the
    cursors this panel stays usable while disconnected.

    Each view's own controls live in a sub-frame that is shown only for that
    view; a stale window function next to an XY plot would just be noise.
    """

    title = "View"

    TIME = "Time"
    SPECTRUM = "Spectrum (FFT)"
    XY = "XY (CH1 vs CH2)"
    VIEWS = (TIME, SPECTRUM, XY)

    def __init__(self, parent, on_change):
        super().__init__(parent)
        box = self.frame
        self.view = labelled_combo(box, "Show", self.VIEWS, 0,
                                   lambda: self._changed(on_change))
        self.view.set(self.TIME)

        self.spectrum_frame = ttk.Frame(box)
        self.spectrum_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.spectrum_frame.columnconfigure(1, weight=1)
        self.window = labelled_combo(self.spectrum_frame, "Window",
                                     analysis.WINDOWS, 0, on_change)
        self.window.set(analysis.DEFAULT_WINDOW)
        self.channel = labelled_combo(self.spectrum_frame, "Channel",
                                      ("1", "2"), 1, on_change)
        self.channel.set("1")

        self.xy_frame = ttk.Frame(box)
        self.xy_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.xy_frame.columnconfigure(1, weight=1)
        self.x_channel = labelled_combo(self.xy_frame, "X axis", ("1", "2"),
                                        0, on_change)
        self.x_channel.set("1")
        self.y_channel = labelled_combo(self.xy_frame, "Y axis", ("1", "2"),
                                        1, on_change)
        self.y_channel.set("2")

        self._changed(lambda: None)

    def _changed(self, on_change) -> None:
        view = self.view.get()
        for frame, wanted in ((self.spectrum_frame, self.SPECTRUM),
                              (self.xy_frame, self.XY)):
            if view == wanted:
                frame.grid()
            else:
                frame.grid_remove()
        on_change()

    def showing(self) -> str:
        """Which view is selected, as one of ViewPanel.VIEWS."""
        return self.view.get() or self.TIME

    def channel_number(self) -> Optional[int]:
        text = self.channel.get()
        return int(text) if text.isdigit() else None

    def xy_channels(self):
        return (int(self.x_channel.get() or 1), int(self.y_channel.get() or 2))


class MathPanel(Panel):
    """Arithmetic between the two channels.

    Computed with numpy from the samples already in hand rather than through
    ``:MATH:OPER``: more flexible, no SCPI to get wrong, and it works on a
    capture loaded from a file.
    """

    title = "Math"

    def __init__(self, parent, on_change):
        super().__init__(parent)
        self.op = labelled_combo(self.frame, "Operation",
                                 (analysis.MATH_OFF,) + analysis.MATH_OPS,
                                 0, on_change)
        self.op.set(analysis.MATH_OFF)
        ttk.Label(self.frame, text="needs both channels captured",
                  foreground="#777", font=("", 8))\
            .grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def operation(self) -> str:
        return self.op.get() or analysis.MATH_OFF


class SetupPanel(Panel):
    title = "Setup"

    def __init__(self, parent, on_save, on_load):
        super().__init__(parent)
        box = self.frame
        ttk.Button(box, text="Save setup", command=on_save)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(box, text="Load setup", command=on_load)\
            .grid(row=0, column=1, sticky="ew")
