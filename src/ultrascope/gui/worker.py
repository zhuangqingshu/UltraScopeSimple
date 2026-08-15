"""The one thread allowed to talk to the instrument.

Scope is not thread-safe, so every command is funnelled through this worker's
job queue. The Tk thread only ever posts jobs and drains results; it never
holds a Scope reference of its own.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Optional

from ..scope import Scope

# How many live frames pass between measurement refreshes. Measurements are
# seven queries per channel, far too slow to run on every frame.
MEAS_EVERY = 6

JOB_POLL_S = 0.02


class Worker(threading.Thread):
    """Owns the Scope object and serialises every command onto one thread.

    Results are posted to ``ui_queue`` as ``(tag, "ok" | "error", payload)``.
    """

    def __init__(self, ui_queue: "queue.Queue"):
        super().__init__(daemon=True)
        self.ui = ui_queue
        self.jobs: "queue.Queue" = queue.Queue()
        self.streaming = threading.Event()
        self.abort = threading.Event()
        self._scope: Optional[Scope] = None
        self._stop = threading.Event()
        self._frame = 0

    # --- called from the UI thread ---

    def submit(self, func: Callable[[Optional[Scope]], object], tag: str = "cmd") -> None:
        """Queue func(scope) -> result; the result is posted back under ``tag``."""
        self.jobs.put((func, tag))

    def connect(self, resource: Optional[str]) -> None:
        """Open a session on the worker thread and keep it here.

        The UI never constructs the Scope itself, which is what keeps the
        transport layer out of the Tk thread entirely.
        """
        def job(_scope):
            scope = Scope.connect(resource)
            self._scope = scope
            return scope.idn

        self.submit(job, "connect")

    def disconnect(self) -> None:
        self.streaming.clear()

        def job(scope):
            if scope is not None:
                scope.close()
            self._scope = None

        self.submit(job, "disconnect")

    def shutdown(self) -> None:
        self.streaming.clear()
        self._stop.set()
        self.jobs.put((None, None))

    @property
    def connected(self) -> bool:
        return self._scope is not None

    # --- worker thread ---

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                func, tag = self.jobs.get(timeout=JOB_POLL_S)
            except queue.Empty:
                # Idle with streaming on is what "live" mode actually is.
                if self.streaming.is_set() and self._scope is not None:
                    self._live_frame()
                continue

            if func is None:
                break
            try:
                self.ui.put((tag, "ok", func(self._scope)))
            except Exception as exc:
                self.streaming.clear()
                self.ui.put((tag, "error", exc))

        if self._scope is not None:
            self._scope.close()
            self._scope = None

    def _live_frame(self) -> None:
        scope = self._scope
        try:
            wave = scope.capture(points="normal")
            self.ui.put(("trace", "ok", wave))

            self._frame += 1
            if self._frame % MEAS_EVERY == 0:
                stats = {ch: scope.measure(ch) for ch in wave.channel_ids}
                self.ui.put(("meas", "ok", stats))
                self.ui.put(("status", "ok", scope.trigger_status()))
        except Exception as exc:
            self.streaming.clear()
            self.ui.put(("trace", "error", exc))
