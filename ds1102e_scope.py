"""
A small stand-in for Rigol's UltraScope, for the DS1102E / DS1000D-E.

Live waveform display plus panel controls for the vertical, horizontal,
trigger and acquisition subsystems, and one-click export to CSV/PNG.

    python ds1102e_scope.py

All instrument I/O happens on a single worker thread; the Tk main thread
only ever touches widgets. Requests are funnelled through a queue so the
USBTMC link is never used from two places at once.
"""

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import ds1102e as rig

REFRESH_MS = 40          # how often the UI drains the result queue
MEAS_EVERY = 6           # refresh measurements once every N live frames


class Worker(threading.Thread):
    """Owns the Scope object and serialises every command onto one thread."""

    def __init__(self, ui_queue):
        super().__init__(daemon=True)
        self.ui = ui_queue
        self.jobs = queue.Queue()
        self.scope = None
        self.streaming = threading.Event()
        self.abort = threading.Event()
        self._stop = threading.Event()
        self._frame = 0

    # --- called from the UI thread ---
    def submit(self, func, tag=None):
        """Queue func(scope) -> result; the result is posted back as (tag, ...)."""
        self.jobs.put((func, tag))

    def shutdown(self):
        self._stop.set()
        self.jobs.put((None, None))

    # --- worker thread ---
    def run(self):
        while not self._stop.is_set():
            try:
                func, tag = self.jobs.get(timeout=0.02)
            except queue.Empty:
                if self.streaming.is_set() and self.scope is not None:
                    self._live_frame()
                continue

            if func is None:
                break
            try:
                result = func(self.scope)
                self.ui.put((tag, "ok", result))
            except Exception as exc:
                self.streaming.clear()
                self.ui.put((tag, "error", exc))

        if self.scope is not None:
            self.scope.close()

    def _live_frame(self):
        try:
            t, traces = self.scope.capture(points="normal")
            self.ui.put(("trace", "ok", (t, traces)))

            self._frame += 1
            if self._frame % MEAS_EVERY == 0:
                stats = {ch: self.scope.measure(ch) for ch in traces}
                self.ui.put(("meas", "ok", stats))
                self.ui.put(("status", "ok", self.scope.trigger_status()))
        except Exception as exc:
            self.streaming.clear()
            self.ui.put(("trace", "error", exc))


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.ui_queue = queue.Queue()
        self.worker = Worker(self.ui_queue)
        self.worker.start()

        self.connected = False
        self.last_capture = None      # (t, traces) of whatever is on screen
        self.lines = {}

        self._build_controls()
        self._build_plot()
        self._set_enabled(False)

        self.after(REFRESH_MS, self._drain)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build_controls(self):
        panel = ttk.Frame(self)
        panel.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        self.panel = panel
        row = 0

        # --- connection ---
        box = ttk.LabelFrame(panel, text="Connection", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        self.resource_var = tk.StringVar()
        self.resource_cb = ttk.Combobox(box, textvariable=self.resource_var, width=34)
        self.resource_cb.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(box, text="Refresh", command=self._refresh_resources)\
            .grid(row=1, column=0, sticky="ew", padx=(0, 3))
        self.connect_btn = ttk.Button(box, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=1, column=1, sticky="ew")
        self.idn_var = tk.StringVar(value="not connected")
        ttk.Label(box, textvariable=self.idn_var, wraplength=250,
                  foreground="#555").grid(row=2, column=0, columnspan=2,
                                          sticky="w", pady=(4, 0))

        # --- acquisition run control ---
        box = ttk.LabelFrame(panel, text="Acquisition", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        for i, (label, cmd) in enumerate([("Run", self._run), ("Stop", self._stop),
                                          ("Auto", self._autoset)]):
            ttk.Button(box, text=label, command=cmd, width=8)\
                .grid(row=0, column=i, padx=1, sticky="ew")
        ttk.Button(box, text="Single", command=self._single, width=8)\
            .grid(row=1, column=0, padx=1, pady=(3, 0), sticky="ew")
        ttk.Button(box, text="Force", command=self._force, width=8)\
            .grid(row=1, column=1, padx=1, pady=(3, 0), sticky="ew")
        self.live_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Live", variable=self.live_var,
                        command=self._toggle_live)\
            .grid(row=1, column=2, padx=1, pady=(3, 0))

        self.acq_type = self._combo(box, "Type", ["NORMAL", "AVERAGE", "PEAKDETECT"],
                                    2, self._apply_acquire)
        self.acq_avg = self._combo(box, "Averages",
                                   ["2", "4", "8", "16", "32", "64", "128", "256"],
                                   3, self._apply_acquire)
        self.acq_mem = self._combo(box, "Memory", ["NORMAL", "LONG"],
                                   4, self._apply_acquire)

        # --- vertical ---
        self.active_ch = tk.IntVar(value=1)
        self.ch_on, self.ch_scale, self.ch_coup = {}, {}, {}
        self.ch_probe, self.ch_offset = {}, {}
        for ch in (1, 2):
            box = ttk.LabelFrame(panel, text=f"Channel {ch}", padding=6)
            box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
            self.ch_on[ch] = tk.BooleanVar(value=True)
            ttk.Checkbutton(box, text="Display", variable=self.ch_on[ch],
                            command=lambda c=ch: self._apply_channel(c))\
                .grid(row=0, column=0, sticky="w")
            # The active channel is the one vertical dragging and the wheel move.
            ttk.Radiobutton(box, text="Active", value=ch, variable=self.active_ch)\
                .grid(row=0, column=1, sticky="w")
            self.ch_probe[ch] = self._combo(
                box, "Probe", [f"{int(r)}X" for r in rig.PROBE_RATIOS],
                1, lambda c=ch: self._apply_channel(c))
            self.ch_scale[ch] = self._combo(
                box, "V/div", [rig.eng(v, "V") for v in rig.VOLT_SCALES],
                2, lambda c=ch: self._apply_channel(c))
            self.ch_coup[ch] = self._combo(box, "Coupling", ["DC", "AC", "GND"],
                                           3, lambda c=ch: self._apply_channel(c))
            self.ch_offset[ch] = self._entry(box, "Offset (V)", 4,
                                             lambda c=ch: self._apply_channel(c))

        # --- horizontal ---
        box = ttk.LabelFrame(panel, text="Horizontal", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        self.timebase = self._combo(box, "s/div",
                                    [rig.eng(v, "s") for v in rig.TIME_SCALES],
                                    0, self._apply_timebase)
        self.time_offset = self._entry(box, "Position (s)", 1, self._apply_timebase)

        # --- trigger ---
        box = ttk.LabelFrame(panel, text="Trigger", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        self.trig_mode = self._combo(box, "Mode",
                                     ["EDGE", "PULSE", "VIDEO", "SLOPE",
                                      "PATTERN", "DURATION", "ALTERNATION"],
                                     0, self._apply_trigger_mode)
        self.trig_src = self._combo(box, "Source", ["CHAN1", "CHAN2", "EXT", "ACLINE"],
                                    1, self._apply_trigger)
        self.trig_slope = self._combo(box, "Slope", ["POSITIVE", "NEGATIVE"],
                                      2, self._apply_trigger)
        self.trig_sweep = self._combo(box, "Sweep", ["AUTO", "NORMAL", "SINGLE"],
                                      3, self._apply_trigger)
        self.trig_coup = self._combo(box, "Coupling", ["DC", "AC", "HF", "LF"],
                                     4, self._apply_trigger)

        ttk.Label(box, text="Level (V)").grid(row=5, column=0, sticky="w", pady=(3, 0))
        self.trig_level = tk.StringVar(value="0")
        entry = ttk.Entry(box, textvariable=self.trig_level, width=12)
        entry.grid(row=5, column=1, sticky="ew", pady=(3, 0))
        entry.bind("<Return>", lambda _e: self._apply_trigger())
        ttk.Button(box, text="Set", command=self._apply_trigger)\
            .grid(row=6, column=0, sticky="ew", pady=(3, 0), padx=(0, 3))
        ttk.Button(box, text="50%", command=self._level_50)\
            .grid(row=6, column=1, sticky="ew", pady=(3, 0))
        ttk.Label(box, text="drag the red line, or scroll over the plot",
                  foreground="#777", font=("", 8))\
            .grid(row=7, column=0, columnspan=2, sticky="w")
        self.trig_holdoff = self._entry(box, "Holdoff (s)", 8, self._apply_trigger)

        self.trig_status = tk.StringVar(value="--")
        ttk.Label(box, textvariable=self.trig_status, foreground="#0a6")\
            .grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- export ---
        box = ttk.LabelFrame(panel, text="Export", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Button(box, text="Save CSV", command=self._save_csv)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(box, text="Save PNG", command=self._save_png)\
            .grid(row=0, column=1, sticky="ew")
        ttk.Button(box, text="Deep memory capture (RAW)", command=self._deep_capture)\
            .grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))

        box = ttk.LabelFrame(panel, text="Setup", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Button(box, text="Save setup", command=self._save_setup)\
            .grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(box, text="Load setup", command=self._load_setup)\
            .grid(row=0, column=1, sticky="ew")

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(panel, textvariable=self.status, wraplength=260, foreground="#333")\
            .grid(row=row, column=0, sticky="w")

    def _combo(self, parent, label, values, row, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        var = tk.StringVar()
        cb = ttk.Combobox(parent, textvariable=var, values=values,
                          state="readonly", width=13)
        cb.grid(row=row, column=1, columnspan=2, sticky="ew", pady=1)
        cb.bind("<<ComboboxSelected>>", lambda _e: command())
        return var

    def _entry(self, parent, label, row, command):
        """A numeric entry that applies on Enter or when focus leaves."""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        var = tk.StringVar()
        widget = ttk.Entry(parent, textvariable=var, width=13)
        widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=1)
        widget.bind("<Return>", lambda _e: command())
        widget.bind("<FocusOut>", lambda _e: command())
        return var

    def _build_plot(self):
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.fig = Figure(figsize=(9, 5.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#101010")
        self.ax.grid(True, color="#404040", linewidth=0.5)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Voltage (V)")
        self.fig.tight_layout()

        # The draggable trigger-level marker. Dragging only moves this artist;
        # the SCPI write happens once, on mouse release, so a drag can't flood
        # the USBTMC link with dozens of commands.
        self.level_line = self.ax.axhline(0.0, color="#ff5555", linewidth=1.2,
                                          linestyle="--", visible=False)
        self.level_tag = self.ax.annotate(
            "T", xy=(1.0, 0.0), xycoords=("axes fraction", "data"),
            xytext=(3, 0), textcoords="offset points",
            color="#ff5555", fontsize=8, va="center", visible=False)

        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        self.dragging_level = False
        self.pan = None                       # in-progress drag-to-pan state
        self.vscales = {1: 1.0, 2: 1.0}
        self.voffsets = {1: 0.0, 2: 0.0}
        self.time_off = 0.0
        self.canvas.mpl_connect("button_press_event", self._on_plot_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_plot_motion)
        self.canvas.mpl_connect("button_release_event", self._on_plot_release)
        self.canvas.mpl_connect("scroll_event", self._on_plot_scroll)

        self.meas_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.meas_var, font=("Consolas", 9),
                  justify="left").grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _set_enabled(self, on):
        state = "normal" if on else "disabled"
        for frame in self.panel.winfo_children():
            if not isinstance(frame, ttk.LabelFrame) or frame.cget("text") == "Connection":
                continue
            for child in frame.winfo_children():
                try:
                    child.configure(state="readonly" if
                                    (on and isinstance(child, ttk.Combobox)) else state)
                except tk.TclError:
                    pass

    # ------------------------------------------------------- worker plumbing

    def _drain(self):
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

    def _do(self, func, tag="cmd", restream=True):
        """Run a command on the worker, pausing live capture around it."""
        if not self.connected:
            return
        was_live = self.worker.streaming.is_set()
        self.worker.streaming.clear()
        self.worker.submit(func, tag)
        if restream and was_live and self.live_var.get():
            self.worker.submit(lambda s: self.worker.streaming.set(), "noop")

    # -------------------------------------------------------------- actions

    def _refresh_resources(self):
        try:
            found = rig.list_scopes()
        except Exception as exc:
            messagebox.showerror("VISA error", str(exc))
            return
        self.resource_cb["values"] = found
        if found and not self.resource_var.get():
            self.resource_var.set(found[0])
        self.status.set(f"{len(found)} instrument(s) found.")

    def _toggle_connect(self):
        if self.connected:
            self.worker.streaming.clear()
            self.worker.submit(lambda s: s.close() if s else None, "disconnect")
            self.worker.scope = None
            self._set_disconnected()
            return

        resource = self.resource_var.get() or None
        self.status.set("Connecting...")

        def job(_scope):
            scope = rig.Scope(resource)
            self.worker.scope = scope
            return scope.idn

        self.worker.submit(job, "connect")

    def _set_disconnected(self):
        self.connected = False
        self.idn_var.set("not connected")
        self.connect_btn.configure(text="Connect")
        self._set_enabled(False)

    def _on_connect(self, idn):
        self.connected = True
        self.idn_var.set(idn)
        self.connect_btn.configure(text="Disconnect")
        self._set_enabled(True)
        self.status.set("Connected.")
        self._do(self._read_settings_job, "settings")

    def _on_disconnect(self, _payload):
        self.status.set("Disconnected.")

    @staticmethod
    def _read_settings_job(scope):
        """Mirror the instrument's current state onto the panel."""
        return scope.snapshot()

    def _on_settings(self, st):
        self.timebase.set(rig.eng(st["timebase"], "s"))
        self.time_offset.set(f"{st['time_offset']:.6g}")
        self.time_off = st["time_offset"]
        self.acq_type.set(st["acq_type"])
        self.acq_avg.set(str(st["acq_average"]))
        self.acq_mem.set(st["acq_memdepth"])

        for ch_key, info in st["channels"].items():
            ch = int(ch_key)
            self.ch_on[ch].set(info["on"])
            self.ch_probe[ch].set(f"{int(info['probe'])}X")
            self.ch_scale[ch].set(rig.eng(info["scale"], "V"))
            self.ch_coup[ch].set(info["coupling"])
            self.ch_offset[ch].set(f"{info['offset']:.6g}")
            self.vscales[ch] = info["scale"]
            self.voffsets[ch] = info["offset"]

        trig = st["trigger"]
        self.trig_mode.set(trig["mode"])
        for var, key in ((self.trig_src, "source"), (self.trig_slope, "slope"),
                         (self.trig_sweep, "sweep")):
            if key in trig:
                var.set(trig[key])
        if "holdoff" in trig:
            self.trig_holdoff.set(f"{trig['holdoff']:.6g}")
        if "level" in trig:
            self._show_level(trig["level"])

        if self.live_var.get():
            self.worker.streaming.set()

    def _toggle_live(self):
        if self.live_var.get() and self.connected:
            self.worker.streaming.set()
        else:
            self.worker.streaming.clear()

    def _run(self):
        self._do(lambda s: s.run(), "cmd")

    def _stop(self):
        self.worker.streaming.clear()
        self.live_var.set(False)
        self._do(lambda s: s.stop(), "cmd", restream=False)

    def _autoset(self):
        self._do(lambda s: s.auto(), "cmd")
        self.after(2000, lambda: self._do(self._read_settings_job, "settings"))

    def _force(self):
        self._do(lambda s: s.force_trigger(), "cmd")

    def _single(self):
        self.worker.streaming.clear()
        self.live_var.set(False)
        self.status.set("Armed, waiting for trigger...")

        def job(scope):
            got = scope.single(timeout_s=30.0)
            t, traces = scope.capture(points="normal")
            return got, t, traces

        self._do(job, "single", restream=False)

    def _on_single(self, payload):
        got, t, traces = payload
        self.trig_sweep.set("SINGLE")
        self.status.set("Triggered." if got else "Trigger timed out; showing last data.")
        self._on_trace((t, traces))

    def _apply_channel(self, ch):
        on = self.ch_on[ch].get()
        scale = self._parse_combo(self.ch_scale[ch].get(), rig.VOLT_SCALES, "V")
        coup = self.ch_coup[ch].get()
        probe = self.ch_probe[ch].get()
        probe = float(probe.rstrip("Xx")) if probe else None
        offset = self._parse_float(self.ch_offset[ch].get())

        if scale is not None:
            self.vscales[ch] = scale  # keeps the level step and clamp in sync
        if offset is not None:
            self.voffsets[ch] = offset

        def job(scope):
            scope.set_channel_on(ch, on)
            if not on:
                return
            # Probe first: changing it rescales volts/div and the offset.
            if probe is not None:
                scope.set_probe(ch, probe)
            if coup:
                scope.set_coupling(ch, coup)
            if scale is not None:
                scope.set_volt_scale(ch, scale)
            if offset is not None:
                scope.set_volt_offset(ch, offset)

        self._do(job, "cmd")

    def _apply_timebase(self):
        scale = self._parse_combo(self.timebase.get(), rig.TIME_SCALES, "s")
        position = self._parse_float(self.time_offset.get())
        if position is not None:
            self.time_off = position

        def job(scope):
            if scale is not None:
                scope.set_timebase(scale)
            if position is not None:
                scope.set_time_offset(position)

        self._do(job, "cmd")

    def _apply_acquire(self):
        atype = self.acq_type.get() or None
        avg = int(self.acq_avg.get()) if self.acq_avg.get() else None
        mem = self.acq_mem.get() or None
        # Only push the average count when the type actually calls for it.
        avg = avg if atype == "AVERAGE" else None
        self._do(lambda s: s.set_acquire(atype, avg, mem), "cmd")

    def _apply_trigger_mode(self):
        mode = self.trig_mode.get()

        def job(scope):
            scope.set_trigger_mode(mode)
            return self._read_settings_job(scope)

        self._do(job, "settings")

    def _apply_trigger(self):
        try:
            level = float(self.trig_level.get())
        except ValueError:
            level = None

        src = self.trig_src.get() or None
        slope = self.trig_slope.get() or None
        sweep = self.trig_sweep.get() or None
        coup = self.trig_coup.get() or None
        holdoff = self._parse_float(self.trig_holdoff.get())

        if level is not None:
            level = self._clamp_level(level)
            self._show_level(level)

        self._do(lambda s: s.set_trigger(source=src, slope=slope, level=level,
                                         coupling=coup, sweep=sweep,
                                         holdoff=holdoff), "cmd")

    # ----------------------------------------------------------- level input

    def _trigger_channel(self):
        """Which channel's volts/div governs the level, or None for EXT/ACLINE."""
        src = self.trig_src.get().upper()
        if "2" in src:
            return 2
        if "1" in src:
            return 1
        return None

    def _level_span(self):
        """The scope only accepts a trigger level within +/-6 divisions."""
        ch = self._trigger_channel()
        return 6.0 * self.vscales.get(ch, 1.0) if ch else float("inf")

    def _clamp_level(self, level):
        limit = self._level_span()
        clamped = max(-limit, min(limit, level))
        if clamped != level:
            self.status.set(f"Level clamped to +/-6 div ({rig.eng(limit, 'V')}).")
        return clamped

    def _show_level(self, level):
        """Update the entry box and the marker without touching the instrument."""
        self.trig_level.set(f"{level:.4g}")
        self.level_line.set_ydata([level, level])
        self.level_line.set_visible(True)
        self.level_tag.set_visible(True)
        self.level_tag.xy = (1.0, level)
        self.canvas.draw_idle()

    def _push_level(self, level):
        level = self._clamp_level(level)
        self._show_level(level)
        self._do(lambda s: s.set_trigger(level=level), "cmd")

    def _level_50(self):
        def job(scope):
            return scope.trigger_level_50()

        self._do(job, "level50")

    def _on_level50(self, level):
        self._show_level(level)
        self.status.set(f"Level set to 50% ({rig.eng(level, 'V')}).")

    def _near_level(self, event):
        """True if the cursor is within a few pixels of the marker line."""
        if event.ydata is None or not self.level_line.get_visible():
            return False
        low, high = self.ax.get_ylim()
        tolerance = abs(high - low) * 0.03
        return abs(event.ydata - self.level_line.get_ydata()[0]) < tolerance

    def _on_plot_press(self, event):
        if not self.connected or event.inaxes is not self.ax or event.button != 1:
            return
        if self._near_level(event):
            self.dragging_level = True
        elif event.dblclick:
            # Double-clicking anywhere drops the level right there.
            self._push_level(event.ydata)
        else:
            self._pan_start(event)

    def _on_plot_motion(self, event):
        if self.pan is not None:
            self._pan_move(event)
            return
        if event.inaxes is not self.ax or event.ydata is None:
            return
        if self.dragging_level:
            self._show_level(event.ydata)  # local only; the write waits for release
        else:
            cursor = "sb_v_double_arrow" if self._near_level(event) else "fleur"
            try:
                self.canvas.get_tk_widget().configure(cursor=cursor)
            except tk.TclError:
                pass

    def _on_plot_release(self, event):
        if self.pan is not None:
            self._pan_finish(event)
            return
        if not self.dragging_level:
            return
        self.dragging_level = False
        level = event.ydata
        if level is None:
            level = self.level_line.get_ydata()[0]
        self._push_level(level)

    # -------------------------------------------------------- drag-to-pan

    # Dragging shifts the *view* while the mouse is down and writes the new
    # offsets once, on release. Doing it live would put a pair of SCPI writes
    # on every mouse-move event and stall the USBTMC link.

    def _pan_start(self, event):
        self.pan = {
            "px": (event.x, event.y),
            "xlim": self.ax.get_xlim(),
            "ylim": self.ax.get_ylim(),
            "time_off": self.time_off,
            "ch": self.active_ch.get(),
        }
        self.pan["voffset"] = self.voffsets.get(self.pan["ch"], 0.0)

    def _pan_delta(self, pan, event):
        """Convert the pixel drag into data units against the pre-drag limits."""
        box = self.ax.get_window_extent()
        x0, y0 = pan["px"]
        xlim, ylim = pan["xlim"], pan["ylim"]
        dx = (event.x - x0) / box.width * (xlim[1] - xlim[0])
        dy = (event.y - y0) / box.height * (ylim[1] - ylim[0])
        return dx, dy

    def _pan_move(self, event):
        if event.x is None or event.y is None:
            return
        dx, dy = self._pan_delta(self.pan, event)
        xlim, ylim = self.pan["xlim"], self.pan["ylim"]
        self.ax.set_xlim(xlim[0] - dx, xlim[1] - dx)
        self.ax.set_ylim(ylim[0] - dy, ylim[1] - dy)
        self.canvas.draw_idle()

    def _pan_finish(self, event):
        pan, self.pan = self.pan, None
        if event.x is None or event.y is None:
            return

        dx, dy = self._pan_delta(pan, event)
        if abs(dx) < 1e-15 and abs(dy) < 1e-15:
            return              # a plain click, not a drag

        ch = pan["ch"]
        # Displayed volts are raw volts minus the channel offset, so moving the
        # trace up by dy means decreasing the offset by the same amount.
        time_off = pan["time_off"] - dx
        voffset = pan["voffset"] - dy

        self.time_off = time_off
        self.voffsets[ch] = voffset
        self.time_offset.set(f"{time_off:.6g}")
        self.ch_offset[ch].set(f"{voffset:.6g}")

        def job(scope):
            scope.set_time_offset(time_off)
            scope.set_volt_offset(ch, voffset)

        self._do(job, "cmd")

    def _on_plot_scroll(self, event):
        """Fine adjustment: one notch moves the level a fifth of a division."""
        if not self.connected or event.inaxes is not self.ax:
            return
        ch = self._trigger_channel()
        step = self.vscales.get(ch, 1.0) / 5.0 if ch else 0.05
        self._push_level(self.level_line.get_ydata()[0] + event.step * step)

    @staticmethod
    def _parse_float(text):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_combo(text, table, unit):
        """Map a formatted combobox entry ('20 mV') back to the nearest real value."""
        if not text:
            return None
        for value in table:
            if rig.eng(value, unit) == text:
                return value
        return None

    # --------------------------------------------------------------- display

    def _on_trace(self, payload):
        t, traces = payload
        self.last_capture = (t, traces)

        for ch, volts in sorted(traces.items()):
            colour = "#f0d000" if ch == 1 else "#00d0f0"
            line = self.lines.get(ch)
            if line is None:
                (line,) = self.ax.plot(t, volts, color=colour,
                                       linewidth=1.0, label=f"CH{ch}")
                self.lines[ch] = line
                self.ax.legend(loc="upper right", fontsize=8)
            else:
                line.set_data(t, volts)
                line.set_visible(True)

        for ch, line in self.lines.items():
            if ch not in traces:
                line.set_visible(False)

        span = max(np.max(np.abs(v)) for v in traces.values()) if traces else 1.0
        if self.level_line.get_visible():
            # Keep the marker on screen even when it sits outside the signal.
            span = max(span, abs(self.level_line.get_ydata()[0]))
        span = span * 1.15 or 1.0
        if self.pan is None:      # a drag in progress owns the view; don't fight it
            self.ax.set_xlim(t[0], t[-1])
            self.ax.set_ylim(-span, span)
        self.canvas.draw_idle()

    def _on_meas(self, stats):
        lines = []
        for ch, values in sorted(stats.items()):
            parts = []
            for label, value in values.items():
                unit = "Hz" if label == "Freq" else ("s" if label == "Period" else "V")
                parts.append(f"{label}={rig.eng(value, unit) if value is not None else '--':>12}")
            lines.append(f"CH{ch}  " + "  ".join(parts))
        self.meas_var.set("\n".join(lines))

    def _on_status(self, status):
        self.trig_status.set(f"Status: {status}")

    def _on_cmd(self, _payload):
        self.status.set("OK.")

    def _on_noop(self, _payload):
        pass

    # ---------------------------------------------------------------- export

    def _save_csv(self):
        if self.last_capture is None:
            messagebox.showinfo("Nothing to save", "Capture a waveform first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")],
                                            initialfile="waveform.csv")
        if not path:
            return
        t, traces = self.last_capture
        rig.save_csv(path, t, traces)
        self.status.set(f"Wrote {path}")

    def _save_png(self):
        if self.last_capture is None:
            messagebox.showinfo("Nothing to save", "Capture a waveform first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")],
                                            initialfile="waveform.png")
        if not path:
            return
        self.fig.savefig(path, dpi=150, bbox_inches="tight")
        self.status.set(f"Wrote {path}")

    def _save_setup(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Setup", "*.json")],
                                            initialfile="setup.json")
        if not path:
            return
        self._do(lambda s: (path, s.snapshot()), "savesetup")

    def _on_savesetup(self, payload):
        path, state = payload
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
        self.status.set(f"Wrote {path}")

    def _load_setup(self):
        path = filedialog.askopenfilename(filetypes=[("Setup", "*.json"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot read setup", str(exc))
            return

        saved_idn = state.get("idn")
        if saved_idn and self.idn_var.get() not in ("", "not connected") \
                and saved_idn != self.idn_var.get():
            if not messagebox.askyesno(
                    "Different instrument",
                    f"This setup was saved from:\n{saved_idn}\n\n"
                    f"Currently connected:\n{self.idn_var.get()}\n\nApply anyway?"):
                return

        def job(scope):
            warnings = scope.restore(state)
            return warnings, scope.snapshot()

        self._do(job, "loadsetup")

    def _on_loadsetup(self, payload):
        warnings, state = payload
        self._on_settings(state)
        if warnings:
            self.status.set(f"Restored with {len(warnings)} problem(s).")
            messagebox.showwarning("Setup restored with warnings",
                                   "\n".join(warnings))
        else:
            self.status.set("Setup restored.")

    def _deep_capture(self):
        """Stop and pull the full acquisition memory instead of the 600 screen points."""
        self.worker.streaming.clear()
        self.live_var.set(False)
        self.status.set("Reading deep memory, this takes a while...")
        self._do(lambda s: s.capture(points="raw"), "deep", restream=False)

    def _on_deep(self, payload):
        t, traces = payload
        npts = len(t)
        self.status.set(f"Deep capture: {npts} points per channel.")
        self._on_trace(payload)

    # ----------------------------------------------------------------- close

    def _on_close(self):
        self.worker.streaming.clear()
        self.worker.shutdown()
        self.master.destroy()


def main():
    root = tk.Tk()
    root.title("DS1102E Scope")
    root.geometry("1250x720")
    app = App(root)
    app._refresh_resources()
    root.mainloop()


if __name__ == "__main__":
    main()
