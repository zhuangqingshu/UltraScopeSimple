"""Control panel widgets.

Each panel owns its widgets and Tk variables and reports changes through
callbacks. No panel knows about Scope, the worker, or VISA.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

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


class ExportPanel(Panel):
    title = "Export"

    def __init__(self, parent, on_csv, on_png, on_deep):
        super().__init__(parent)
        box = self.frame
        ttk.Button(box, text="Save CSV", command=on_csv)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(box, text="Save PNG", command=on_png)\
            .grid(row=0, column=1, sticky="ew")
        ttk.Button(box, text="Deep memory capture (RAW)", command=on_deep)\
            .grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))


class SetupPanel(Panel):
    title = "Setup"

    def __init__(self, parent, on_save, on_load):
        super().__init__(parent)
        box = self.frame
        ttk.Button(box, text="Save setup", command=on_save)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(box, text="Load setup", command=on_load)\
            .grid(row=0, column=1, sticky="ew")
