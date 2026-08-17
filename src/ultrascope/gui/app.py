"""Window assembly and event dispatch.

The Tk thread lives entirely in this file: it builds panels, posts jobs to the
worker, and drains the result queue. Results arrive tagged, and each tag is
dispatched to the matching ``_on_<tag>`` handler.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import export
from ..discovery import list_scopes
from ..profile import DEFAULT_PROFILE
from . import state as st
from ..setup_file import load_setup, save_setup
from ..units import eng
from .panels import (AcquisitionPanel, ChannelPanel, ConnectionPanel,
                     CursorPanel, FilePanel, HorizontalPanel, MathPanel,
                     MeasurePanel, PersistencePanel, ReferencePanel,
                     ScrollableColumn, SetupPanel, TriggerPanel, ViewPanel)
from .plot import SPECTRUM_DOMAIN, TIME_DOMAIN, XY_DOMAIN, PlotCanvas
from .worker import Worker

REFRESH_MS = 40           # how often the UI drains the result queue
AUTOSET_SETTLE_MS = 2000  # :AUTO takes a while before the new state reads back

CHANNELS = (1, 2)


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.ui_queue: "queue.Queue" = queue.Queue()
        self.worker = Worker(self.ui_queue)
        self.worker.start()

        self.connected = False
        self.last_capture = None
        self.idn = ""
        self.active_channel = tk.IntVar(value=1)

        self.volt_table = st.OptionTable(DEFAULT_PROFILE.volt_scales, "V")
        self.time_table = st.OptionTable(DEFAULT_PROFILE.time_scales, "s")

        self._build()
        self._set_enabled(False)

        self.after(REFRESH_MS, self._drain)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    # The sidebar's tabs, in order, each as (label, panel attribute names).
    # Fourteen panels in one column ran off the bottom of the window; grouping
    # them by the question being asked keeps every tab short enough to read at
    # a glance without hunting.
    TABS = (
        ("Capture", ("connection", "acquisition", "horizontal", "file_panel",
                     "setup_panel")),
        ("Channels", ("channel1", "channel2")),
        ("Trigger", ("trigger",)),
        ("Analysis", ("cursor_panel", "view_panel", "math_panel",
                      "measure_panel", "reference_panel", "persistence_panel")),
    )

    def _build(self) -> None:
        sidebar = ttk.Frame(self)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        sidebar.rowconfigure(0, weight=1)

        self.tabs = ttk.Notebook(sidebar)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        # One scrollable column per tab, built before the panels because each
        # panel needs its column's inner frame as its parent.
        self.columns = {}
        for label, _names in self.TABS:
            column = ScrollableColumn(self.tabs)
            self.columns[label] = column
            self.tabs.add(column.outer, text=label)

        def home(label):
            return self.columns[label].inner

        self.connection = ConnectionPanel(home("Capture"),
                                          self._refresh_resources,
                                          self._toggle_connect)
        self.acquisition = AcquisitionPanel(
            home("Capture"), on_run=self._run, on_stop=self._stop,
            on_auto=self._autoset, on_single=self._single, on_force=self._force,
            on_live=self._toggle_live, on_apply=self._apply_acquire)
        self.horizontal = HorizontalPanel(home("Capture"), self.time_table,
                                          self._apply_timebase)
        self.file_panel = FilePanel(home("Capture"), self._open_capture,
                                    self._save_csv, self._save_png,
                                    self._deep_capture)
        self.setup_panel = SetupPanel(home("Capture"), self._save_setup,
                                      self._load_setup)

        self.channels = {ch: ChannelPanel(home("Channels"), ch, self.volt_table,
                                          self.active_channel,
                                          self._apply_channel)
                         for ch in CHANNELS}
        self.channel1, self.channel2 = self.channels[1], self.channels[2]

        self.trigger = TriggerPanel(home("Trigger"), self._apply_trigger_mode,
                                    self._apply_trigger, self._level_50)

        self.cursor_panel = CursorPanel(home("Analysis"), self._apply_cursor_mode)
        self.view_panel = ViewPanel(home("Analysis"), self._apply_view)
        self.math_panel = MathPanel(home("Analysis"), self._apply_math)
        self.measure_panel = MeasurePanel(home("Analysis"),
                                          self._apply_measure_source)
        self.reference_panel = ReferencePanel(
            home("Analysis"), self._store_reference, self._load_reference,
            self._clear_reference)
        self.persistence_panel = PersistencePanel(
            home("Analysis"), self._apply_persistence, self._clear_persistence)

        self.panels = []
        for label, names in self.TABS:
            for row, name in enumerate(names):
                item = getattr(self, name)
                item.grid(row)
                self.panels.append(item)

        # Outside the notebook: a message about a failed command must not be
        # hidden on the tab you have just switched away from.
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(sidebar, textvariable=self.status, wraplength=260,
                  foreground="#333").grid(row=1, column=0, sticky="w",
                                          pady=(6, 0))

        self.plot = PlotCanvas(self)
        self.plot.grid(row=0, column=1, sticky="nsew")
        # The canvas handles the gestures; committing them to the instrument
        # stays here, because only the App may talk to the worker.
        self.plot.on_level_commit = self._push_level
        self.plot.on_pan_commit = self._commit_pan
        self.plot.on_status = self.status.set
        self.plot.active_channel = self.active_channel.get
        self.plot.trigger_channel = self.trigger.channel
        self.plot.enabled = lambda: self.connected

    # Panels that do not talk to the instrument stay live while disconnected.
    # Everything downstream of a capture belongs here: with a waveform loaded
    # from a file there is nothing an analysis panel needs a connection for.
    ALWAYS_ENABLED = ("connection", "cursor_panel", "view_panel",
                      "math_panel", "measure_panel", "reference_panel",
                      "persistence_panel")

    def _set_enabled(self, on: bool) -> None:
        """Everything that needs a connection follows the connection state.

        FilePanel is not listed as always-enabled but gates itself: opening and
        saving files work offline, only the deep-memory read does not.
        """
        always = {getattr(self, name) for name in self.ALWAYS_ENABLED}
        for item in self.panels:
            if item not in always:
                item.set_enabled(on)

    # ------------------------------------------------------- worker plumbing

    def _drain(self) -> None:
        """Pull worker results on the Tk thread and update widgets."""
        while True:
            try:
                tag, kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "error":
                self.status.set(f"{tag}: {payload}")
                if tag == "connect":
                    self._set_disconnected()
                continue

            handler = getattr(self, f"_on_{tag}", None)
            if handler is not None:
                handler(payload)

        self.after(REFRESH_MS, self._drain)

    def _do(self, func, tag: str = "cmd", restream: bool = True) -> None:
        """Run a command on the worker, pausing live capture around it."""
        if not self.connected:
            return
        was_live = self.worker.streaming.is_set()
        self.worker.streaming.clear()
        self.worker.submit(func, tag)
        if restream and was_live and self.acquisition.live.get():
            self.worker.submit(lambda _s: self.worker.streaming.set(), "noop")

    # -------------------------------------------------------------- actions

    def _refresh_resources(self) -> None:
        try:
            found = list_scopes()
        except Exception as exc:
            messagebox.showerror("VISA error", str(exc))
            return
        self.connection.set_resources(found)
        self.status.set(f"{len(found)} instrument(s) found.")

    def _toggle_connect(self) -> None:
        if self.connected:
            self.worker.disconnect()
            self._set_disconnected()
            return
        self.status.set("Connecting...")
        self.worker.connect(self.connection.resource.get() or None)

    def _set_disconnected(self) -> None:
        self.connected = False
        self.idn = ""
        self.connection.show_disconnected()
        self._set_enabled(False)

    def _on_connect(self, idn) -> None:
        self.connected = True
        self.idn = idn
        self.connection.show_connected(idn)
        self._set_enabled(True)
        self.status.set("Connected.")
        self._do(lambda s: s.snapshot(CHANNELS), "settings")

    def _on_disconnect(self, _payload) -> None:
        self.status.set("Disconnected.")

    def _on_settings(self, settings) -> None:
        """Mirror the instrument's own state onto the panel."""
        self.horizontal.timebase.set(self.time_table.label_for(settings.timebase))
        self.horizontal.position.set(f"{settings.time_offset:.6g}")
        self.plot.time_offset = settings.time_offset
        self.acquisition.acq_type.set(settings.acquire_type.upper())
        self.acquisition.average.set(str(settings.average))
        self.acquisition.memory.set(settings.memory_depth.upper())
        self.trigger.mode.set(settings.trigger_mode.upper())

        for ch, info in settings.channels.items():
            panel = self.channels[ch]
            panel.on.set(info.on)
            panel.scale.set(self.volt_table.label_for(info.volt_scale))
            panel.coupling.set(info.coupling.upper())
            if info.probe is not None:
                panel.probe.set(f"{int(info.probe)}X")
            if info.volt_offset is not None:
                panel.offset.set(f"{info.volt_offset:.6g}")
                self.plot.volt_offsets[ch] = info.volt_offset
            # The plot needs volts/div to clamp the level and size the wheel step.
            self.plot.volt_scales[ch] = info.volt_scale

        if settings.trigger_source is not None:
            self.trigger.source.set(
                st.normalise_trigger_source(settings.trigger_source))
        for var, value in ((self.trigger.slope, settings.trigger_slope),
                           (self.trigger.sweep, settings.trigger_sweep)):
            if value is not None:
                var.set(value.upper())
        if settings.trigger_holdoff is not None:
            self.trigger.holdoff.set(f"{settings.trigger_holdoff:.6g}")

        mode = settings.trigger_mode.upper()
        self.trigger.show_conditions(st.condition_labels(mode))
        if settings.trigger_condition is not None:
            self.trigger.condition.set(
                st.condition_label_for(mode, settings.trigger_condition))
        if settings.trigger_condition_time is not None:
            self.trigger.width.set(f"{settings.trigger_condition_time:.6g}")
        if settings.trigger_level is not None:
            self._show_level(settings.trigger_level)

        if self.acquisition.live.get():
            self.worker.streaming.set()

    def _toggle_live(self) -> None:
        if self.acquisition.live.get() and self.connected:
            self.worker.streaming.set()
        else:
            self.worker.streaming.clear()

    def _run(self) -> None:
        self._do(lambda s: s.run())

    def _stop(self) -> None:
        self._pause_live()
        self._do(lambda s: s.stop(), restream=False)

    def _autoset(self) -> None:
        self._do(lambda s: s.auto())
        self.after(AUTOSET_SETTLE_MS,
                   lambda: self._do(lambda s: s.snapshot(CHANNELS), "settings"))

    def _force(self) -> None:
        self._do(lambda s: s.force_trigger())

    def _pause_live(self) -> None:
        self.worker.streaming.clear()
        self.acquisition.live.set(False)

    def _single(self) -> None:
        self._pause_live()
        self.status.set("Armed, waiting for trigger...")

        timeout = self.acquisition.single_timeout_seconds()

        def job(scope):
            got = scope.single(timeout_s=timeout)
            return got, scope.capture(points="normal")

        self._do(job, "single", restream=False)

    def _on_single(self, payload) -> None:
        got, wave = payload
        self.trigger.sweep.set("SINGLE")
        self.status.set("Triggered." if got else
                        "Trigger timed out; showing last data.")
        self._on_trace(wave)

    def _apply_channel(self, ch: int) -> None:
        panel = self.channels[ch]
        on = panel.on.get()
        scale = panel.volt_scale()
        coupling = panel.coupling.get()
        probe = panel.probe_ratio()
        offset = panel.volt_offset()

        # Keep the plot's mirror current so the level clamp and the wheel step
        # stay right without querying the instrument mid-gesture.
        if scale is not None:
            self.plot.volt_scales[ch] = scale
        if offset is not None:
            self.plot.volt_offsets[ch] = offset

        def job(scope):
            scope.set_channel_on(ch, on)
            if not on:
                return
            # Probe first: changing it rescales volts/div and the offset.
            if probe is not None:
                scope.set_probe(ch, probe)
            if coupling:
                scope.set_coupling(ch, coupling)
            if scale is not None:
                scope.set_volt_scale(ch, scale)
            if offset is not None:
                scope.set_volt_offset(ch, offset)

        self._do(job)

    def _apply_timebase(self) -> None:
        scale = self.horizontal.seconds_per_div()
        position = self.horizontal.time_offset()
        if position is not None:
            self.plot.time_offset = position

        def job(scope):
            if scale is not None:
                scope.set_timebase(scale)
            if position is not None:
                scope.set_time_offset(position)

        self._do(job)

    def _apply_acquire(self) -> None:
        atype = self.acquisition.acq_type.get() or None
        memory = self.acquisition.memory.get() or None
        average = self.acquisition.average.get()
        # Only push the average count when the type actually calls for it.
        count = int(average) if (average and atype == "AVERAGE") else None
        self._do(lambda s: s.set_acquire(atype, count, memory))

    def _apply_trigger_mode(self) -> None:
        mode = self.trigger.mode.get()
        # Show the right controls straight away; the snapshot that comes back
        # then fills in whatever the instrument reports for them.
        self.trigger.show_conditions(st.condition_labels(mode))

        def job(scope):
            scope.set_trigger_mode(mode)
            return scope.snapshot(CHANNELS)

        self._do(job, "settings")

    def _apply_trigger(self) -> None:
        level = self.trigger.level_volts()
        if level is not None:
            level = self.plot.clamp_level(level)
            self._show_level(level)
        source = self.trigger.source.get() or None
        slope = self.trigger.slope.get() or None
        sweep = self.trigger.sweep.get() or None
        coupling = self.trigger.coupling.get() or None
        holdoff = self.trigger.holdoff_seconds()

        condition = self.trigger.condition.get() or None
        width = self.trigger.width_seconds()
        timed = self.trigger.has_conditions()

        def job(scope):
            scope.set_trigger(source=source, slope=slope, level=level,
                              coupling=coupling, sweep=sweep, holdoff=holdoff)
            if timed and (condition is not None or width is not None):
                scope.set_trigger_condition(condition=condition, seconds=width)

        self._do(job)

    def _apply_cursor_mode(self) -> None:
        self.plot.set_cursor_mode(self.cursor_panel.mode.get())

    # -------------------------------------------------- reference / trail

    def _store_reference(self) -> None:
        if self.last_capture is None:
            messagebox.showinfo("Nothing to store", "Capture a waveform first.")
            return
        self.plot.set_reference(self.last_capture)
        self.reference_panel.status.set(
            f"stored: {self.last_capture.npoints} pts, "
            f"CH{','.join(str(c) for c in self.last_capture.channel_ids)}")

    def _load_reference(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Waveform CSV", "*.csv"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            wave = export.load_csv(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot read waveform", str(exc))
            return
        self.plot.set_reference(wave)
        self.reference_panel.status.set(f"loaded: {path}")

    def _clear_reference(self) -> None:
        self.plot.set_reference(None)
        self.reference_panel.status.set("none stored")

    def _apply_persistence(self) -> None:
        self.plot.set_persistence(self.persistence_panel.frames())

    def _clear_persistence(self) -> None:
        self.plot.clear_persistence()

    def _apply_measure_source(self) -> None:
        if self.measure_panel.is_local():
            self._show_local_readings(self.last_capture)
        else:
            self.plot.measurements.set("")

    def _show_local_readings(self, wave) -> None:
        if self.measure_panel.is_math():
            self.plot.show_math_measurements(wave)
        else:
            self.plot.show_local_measurements(
                wave, self.measure_panel.channel_number())

    # A view name from the panel maps to a canvas domain.
    DOMAIN_FOR_VIEW = {ViewPanel.TIME: TIME_DOMAIN,
                       ViewPanel.SPECTRUM: SPECTRUM_DOMAIN,
                       ViewPanel.XY: XY_DOMAIN}

    def _apply_view(self) -> None:
        domain = self.DOMAIN_FOR_VIEW.get(self.view_panel.showing(), TIME_DOMAIN)
        self.plot.set_spectrum_span(self.view_panel.spectrum_span())
        self.plot.set_spectrum_scale(self.view_panel.spectrum_scale())
        self.plot.set_domain(domain, window=self.view_panel.window.get())
        # Redraw straight away from the capture already on screen, so the view
        # does not stay blank until the next live frame.
        if self.last_capture is not None:
            self._draw(self.last_capture)

    def _apply_math(self) -> None:
        self.plot.set_math(self.math_panel.operation())
        if self.last_capture is not None:
            self._draw(self.last_capture)

    # ---------------------------------------------------------- trigger level

    def _show_level(self, level: float) -> None:
        """Update the entry box and the marker without touching the instrument."""
        self.trigger.level.set(f"{level:.4g}")
        self.plot.show_level(level)

    def _push_level(self, level: float) -> None:
        """Commit a level the canvas produced (drag, double-click, wheel)."""
        self.trigger.level.set(f"{level:.4g}")
        self._do(lambda s: s.set_trigger(level=level))

    def _level_50(self) -> None:
        self._do(lambda s: s.trigger_level_50(), "level50")

    def _on_level50(self, level) -> None:
        self._show_level(level)
        self.status.set(f"Level set to 50% ({eng(level, 'V')}).")

    def _commit_pan(self, time_offset: float, ch: int, volt_offset: float) -> None:
        """Write the result of a drag-to-pan gesture, once, on release."""
        self.horizontal.position.set(f"{time_offset:.6g}")
        self.channels[ch].offset.set(f"{volt_offset:.6g}")

        def job(scope):
            scope.set_time_offset(time_offset)
            scope.set_volt_offset(ch, volt_offset)

        self._do(job)

    # --------------------------------------------------------------- display

    def _on_trace(self, wave) -> None:
        self.last_capture = wave
        # Whatever was on screen is no longer the loaded file.
        self.file_panel.source.set("")
        self._draw(wave)

    def _draw(self, wave) -> None:
        if self.measure_panel.is_local():
            self._show_local_readings(wave)
        view = self.view_panel.showing()
        if view == ViewPanel.SPECTRUM:
            self.plot.show_spectrum(wave, self.view_panel.channel_number())
        elif view == ViewPanel.XY:
            self.plot.show_xy(wave, *self.view_panel.xy_channels())
        else:
            self.plot.show(wave)

    def _on_meas(self, stats) -> None:
        # The worker keeps polling the instrument; ignore it when the readout
        # has been switched to the locally computed parameters.
        if not self.measure_panel.is_local():
            self.plot.show_measurements(stats)

    def _on_status(self, status) -> None:
        self.trigger.status.set(f"Status: {status}")

    def _on_cmd(self, _payload) -> None:
        self.status.set("OK.")

    def _on_noop(self, _payload) -> None:
        pass

    # ---------------------------------------------------------------- export

    def _open_capture(self) -> None:
        """Load a CSV written earlier and treat it as the current capture.

        Everything downstream reads ``last_capture``, so once it is loaded the
        cursors, spectrum, XY, math and local measurements all apply to the
        archived waveform with no further plumbing. Live capture is paused
        first, or the next frame would overwrite the file a moment later.
        """
        path = filedialog.askopenfilename(filetypes=[("Waveform CSV", "*.csv"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            wave = export.load_csv(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot read waveform", str(exc))
            return

        self._pause_live()
        self.plot.clear_persistence()
        self._on_trace(wave)
        # After _on_trace: a capture arriving from the instrument clears this
        # label, and the load is the one case where it should stay.
        self.file_panel.source.set(f"showing: {path}")
        self.status.set(f"Loaded {wave.npoints} points from {path}")

    def _ask_path(self, extension: str, label: str):
        if self.last_capture is None:
            messagebox.showinfo("Nothing to save", "Capture a waveform first.")
            return None
        return filedialog.asksaveasfilename(
            defaultextension=extension, filetypes=[(label, f"*{extension}")],
            initialfile=f"waveform{extension}") or None

    def _save_csv(self) -> None:
        path = self._ask_path(".csv", "CSV")
        if path:
            export.save_csv(path, self.last_capture)
            self.status.set(f"Wrote {path}")

    def _save_png(self) -> None:
        path = self._ask_path(".png", "PNG")
        if path:
            self.plot.savefig(path)
            self.status.set(f"Wrote {path}")

    # ----------------------------------------------------------- setup files

    def _save_setup(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Setup", "*.json")],
                                            initialfile="setup.json")
        if path:
            self._do(lambda s: (path, s.snapshot(CHANNELS)), "savesetup")

    def _on_savesetup(self, payload) -> None:
        path, settings = payload
        save_setup(path, settings)
        self.status.set(f"Wrote {path}")

    def _load_setup(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Setup", "*.json"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            settings = load_setup(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot read setup", str(exc))
            return

        if settings.idn and self.idn and settings.idn != self.idn:
            if not messagebox.askyesno(
                    "Different instrument",
                    f"This setup was saved from:\n{settings.idn}\n\n"
                    f"Currently connected:\n{self.idn}\n\nApply anyway?"):
                return

        def job(scope):
            return scope.restore(settings), scope.snapshot(CHANNELS)

        self._do(job, "loadsetup")

    def _on_loadsetup(self, payload) -> None:
        warnings, settings = payload
        self._on_settings(settings)
        if warnings:
            self.status.set(f"Restored with {len(warnings)} problem(s).")
            messagebox.showwarning("Setup restored with warnings",
                                   "\n".join(warnings))
        else:
            self.status.set("Setup restored.")

    def _deep_capture(self) -> None:
        """Stop and pull the full acquisition memory instead of the screen points."""
        self._pause_live()
        self.status.set("Reading deep memory, this takes a while...")
        self._do(lambda s: s.capture(points="raw"), "deep", restream=False)

    def _on_deep(self, wave) -> None:
        self.status.set(f"Deep capture: {wave.npoints} points per channel.")
        self._on_trace(wave)

    # ----------------------------------------------------------------- close

    def _on_close(self) -> None:
        self.worker.shutdown()
        self.master.destroy()


def main() -> None:
    root = tk.Tk()
    root.title("DS1102E Scope")
    root.geometry("1250x720")
    app = App(root)
    app._refresh_resources()
    root.mainloop()
