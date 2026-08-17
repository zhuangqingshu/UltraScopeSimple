"""Shared fixtures.

The Tk root lives here because there may only be one per process: a second
``tk.Tk()`` — even after the first has been destroyed — fails with a TclError
that reads like a broken installation. One session-scoped root keeps every GUI
test module working off the same interpreter.
"""

from __future__ import annotations

import os
import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def root():
    """One hidden Tk root for the whole run."""
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless CI
        # On a developer machine a missing display is a reason to skip. In CI
        # it means the coverage silently vanished, so ULTRASCOPE_REQUIRE_TK
        # turns the skip into a failure.
        if os.environ.get("ULTRASCOPE_REQUIRE_TK"):
            pytest.fail(f"ULTRASCOPE_REQUIRE_TK set but Tk is unusable: {exc}")
        pytest.skip(f"no display for Tk: {exc}")
    window.withdraw()
    yield window
    window.destroy()
