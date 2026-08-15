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
from .panels import (AcquisitionPanel, ChannelPanel, ConnectionPanel,
                     ExportPanel, HorizontalPanel, TriggerPanel)
from .plot import PlotCanvas
from .worker import Worker

REFRESH_MS = 40           # how often the UI drains the result queue
SINGLE_TIMEOUT_S = 30.0   # GUI one-shot wait; the CLI exposes --trigger-timeout
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

        self.volt_table = st.OptionTable(DEFAULT_PROFILE.volt_scales, "V")
        self.time_table = st.OptionTable(DEFAULT_PROFILE.time_scales, "s")

        self._build()
        self._set_enabled(False)

        self.after(REFRESH_MS, self._drain)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        panel = ttk.Frame(self)
        panel.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        self.connection = ConnectionPanel(panel, self._refresh_resources,
                                          self._toggle_connect)
        self.acquisition = AcquisitionPanel(
            panel, on_run=self._run, on_stop=self._stop, on_auto=self._autoset,
            on_single=self._single, on_force=self._force,
            on_live=self._toggle_live, on_apply=self._apply_acquire)
        self.channels = {ch: ChannelPanel(panel, ch, self.volt_table,
                                          self._apply_channel)
                         for ch in CHANNELS}
        self.horizontal = HorizontalPanel(panel, self.time_table,
                                          self._apply_timebase)
        self.trigger = TriggerPanel(panel, self._apply_trigger_mode,
                                    self._apply_trigger)
        self.export_panel = ExportPanel(panel, self._save_csv, self._save_png,
                                        self._deep_capture)

        self.panels = [self.connection, self.acquisition,
                       *self.channels.values(), self.horizontal,
                       self.trigger, self.export_panel]
        for row, item in enumerate(self.panels):
            item.grid(row)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(panel, textvariable=self.status, wraplength=260,
                  foreground="#333").grid(row=len(self.panels), column=0, sticky="w")

        self.plot = PlotCanvas(self)
        self.plot.grid(row=0, column=1, sticky="nsew")

    def _set_enabled(self, on: bool) -> None:
        """Everything but the connection box follows the connection state."""
        for item in self.panels:
            if item is not self.connection:
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
        self.connection.show_disconnected()
        self._set_enabled(False)

    def _on_connect(self, idn) -> None:
        self.connected = True
        self.connection.show_connected(idn)
        self._set_enabled(True)
        self.status.set("Connected.")
        self._do(lambda s: s.snapshot(CHANNELS), "settings")

    def _on_disconnect(self, _payload) -> None:
        self.status.set("Disconnected.")

    def _on_settings(self, settings) -> None:
        """Mirror the instrument's own state onto the panel."""
        self.horizontal.timebase.set(self.time_table.label_for(settings.timebase))
        self.acquisition.acq_type.set(settings.acquire_type.upper())
        self.acquisition.average.set(str(settings.average))
        self.acquisition.memory.set(settings.memory_depth.upper())
        self.trigger.mode.set(settings.trigger_mode.upper())

        for ch, info in settings.channels.items():
            panel = self.channels[ch]
            panel.on.set(info.on)
            panel.scale.set(self.volt_table.label_for(info.volt_scale))
            panel.coupling.set(info.coupling.upper())

        if settings.trigger_source is not None:
            self.trigger.source.set(settings.trigger_source.upper())
            self.trigger.slope.set(settings.trigger_slope.upper())
            self.trigger.sweep.set(settings.trigger_sweep.upper())
            self.trigger.level.set(f"{settings.trigger_level:.4g}")

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

        def job(scope):
            got = scope.single(timeout_s=SINGLE_TIMEOUT_S)
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

        def job(scope):
            scope.set_channel_on(ch, on)
            if on:
                if scale is not None:
                    scope.set_volt_scale(ch, scale)
                if coupling:
                    scope.set_coupling(ch, coupling)

        self._do(job)

    def _apply_timebase(self) -> None:
        value = self.horizontal.seconds_per_div()
        if value is not None:
            self._do(lambda s: s.set_timebase(value))

    def _apply_acquire(self) -> None:
        atype = self.acquisition.acq_type.get() or None
        memory = self.acquisition.memory.get() or None
        average = self.acquisition.average.get()
        # Only push the average count when the type actually calls for it.
        count = int(average) if (average and atype == "AVERAGE") else None
        self._do(lambda s: s.set_acquire(atype, count, memory))

    def _apply_trigger_mode(self) -> None:
        mode = self.trigger.mode.get()

        def job(scope):
            scope.set_trigger_mode(mode)
            return scope.snapshot(CHANNELS)

        self._do(job, "settings")

    def _apply_trigger(self) -> None:
        level = self.trigger.level_volts()
        source = self.trigger.source.get() or None
        slope = self.trigger.slope.get() or None
        sweep = self.trigger.sweep.get() or None
        coupling = self.trigger.coupling.get() or None

        self._do(lambda s: s.set_trigger(source=source, slope=slope, level=level,
                                         coupling=coupling, sweep=sweep))

    # --------------------------------------------------------------- display

    def _on_trace(self, wave) -> None:
        self.last_capture = wave
        self.plot.show(wave)

    def _on_meas(self, stats) -> None:
        self.plot.show_measurements(stats)

    def _on_status(self, status) -> None:
        self.trigger.status.set(f"Status: {status}")

    def _on_cmd(self, _payload) -> None:
        self.status.set("OK.")

    def _on_noop(self, _payload) -> None:
        pass

    # ---------------------------------------------------------------- export

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
