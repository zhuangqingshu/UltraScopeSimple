"""The sidebar layout.

Fourteen panels in one column ran off the bottom of the window, so they are
grouped into notebook tabs and each tab scrolls when it has to. The tests worth
having here are the ones that catch a panel going missing rather than merely
being hard to reach: a panel left out of ``App.TABS`` is built but never
gridded, which looks exactly like a panel that does not exist.

Heights are deliberately not asserted against fixed numbers -- they depend on
the platform's fonts, and a test that fails on CI for that reason teaches
nothing. What is asserted is the mechanism: content that does not fit gets a
scrollbar.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from ultrascope.gui.app import App
from ultrascope.gui.panels import Panel, ScrollableColumn

# ``root`` comes from conftest: only one Tk root may exist per process.


@pytest.fixture
def app(root):
    """A fully built App. The worker thread is stopped again afterwards.

    App expects a toplevel: it registers a WM_DELETE_WINDOW handler.
    """
    window = tk.Toplevel(root)
    window.withdraw()
    instance = App(window)
    window.update_idletasks()
    yield instance
    instance.worker.shutdown()
    window.destroy()


def panel_attributes(app):
    """Every Panel the App holds, by attribute name."""
    found = {name: value for name, value in vars(app).items()
             if isinstance(value, Panel)}
    # The channel panels live in a dict rather than as plain attributes.
    found.update({f"channel{ch}": panel for ch, panel in app.channels.items()})
    return found


# --- nothing gets lost ------------------------------------------------------

def test_every_panel_the_app_builds_is_placed_on_a_tab(app):
    # A panel missing from TABS is constructed and then never gridded, which is
    # indistinguishable from a panel that was never written.
    placed = set(app.panels)
    orphans = sorted(name for name, panel in panel_attributes(app).items()
                     if panel not in placed)
    assert not orphans, f"{orphans} are built but not listed in App.TABS"


def test_every_name_in_the_tab_table_exists(app):
    for label, names in App.TABS:
        for name in names:
            assert isinstance(getattr(app, name, None), Panel), \
                f"App.TABS lists {name!r} on {label!r}, but it is not a panel"


def test_no_panel_is_placed_on_two_tabs():
    listed = [name for _label, names in App.TABS for name in names]
    assert len(listed) == len(set(listed))


def test_the_always_enabled_list_names_real_panels(app):
    for name in App.ALWAYS_ENABLED:
        assert isinstance(getattr(app, name, None), Panel)


def test_there_is_one_scrollable_column_per_tab(app):
    assert len(app.columns) == len(App.TABS)
    assert app.tabs.index("end") == len(App.TABS)


# --- what belongs where -----------------------------------------------------

def test_analysis_panels_stay_usable_while_disconnected(app):
    # Everything on the Analysis tab reads last_capture and nothing else, so
    # none of it may follow the connection state.
    analysis = dict(App.TABS)["Analysis"]
    assert set(analysis) <= set(App.ALWAYS_ENABLED)


def test_the_status_line_is_not_inside_the_notebook(app):
    # A message about a failed command must not be hidden on the tab you just
    # switched away from.
    labels = [child for child in app.tabs.master.winfo_children()
              if isinstance(child, tk.ttk.Label)]
    assert labels, "the status label should be a sibling of the notebook"


def test_switching_tabs_keeps_every_panel_alive(app):
    for label in app.columns:
        app.tabs.select(app.columns[label].outer)
        app.master.update_idletasks()
    assert all(panel.frame.winfo_exists() for panel in app.panels)


# --- scrolling --------------------------------------------------------------

@pytest.fixture
def column(root):
    frame = tk.Frame(root, width=200, height=120)
    frame.grid()
    made = ScrollableColumn(frame)
    made.grid(row=0, column=0, sticky="nsew")
    yield made
    frame.destroy()


def fill(column, count, height=40):
    for _ in range(count):
        tk.Frame(column.inner, height=height, width=150).pack(fill="x")
    column.canvas.update_idletasks()
    column.canvas.event_generate("<Configure>")
    column.canvas.update_idletasks()


def test_content_that_fits_shows_no_scrollbar(column):
    column.canvas.configure(height=400)
    fill(column, 2)
    column.canvas.update()
    assert not column.scrollbar.winfo_manager()


def test_content_that_overflows_gets_a_scrollbar(column):
    column.canvas.configure(height=100)
    fill(column, 20)
    column.canvas.update()
    assert column.scrollbar.winfo_manager()


def test_the_scroll_region_follows_the_content(column):
    fill(column, 5, height=30)
    column.canvas.update()
    _x0, _y0, _x1, y1 = [float(v) for v in
                         column.canvas.cget("scrollregion").split()]
    assert y1 >= column.inner.winfo_reqheight() - 1


def test_the_wheel_is_ignored_when_the_pointer_is_elsewhere(column, root):
    # Every column binds the wheel globally, because that is the only way to
    # see a notch over a child widget. Each must therefore ignore the ones that
    # belong to somebody else -- notably the plot, where the wheel nudges the
    # trigger level.
    column.canvas.configure(height=60)
    fill(column, 20)
    column.canvas.update()
    before = column.canvas.yview()

    outside = tk.Frame(root)
    column._contains_pointer = lambda: False
    column._on_wheel(type("Event", (), {"delta": -120, "num": None})())
    assert column.canvas.yview() == before
    outside.destroy()


def test_the_wheel_scrolls_when_the_pointer_is_over_the_column(column):
    column.canvas.configure(height=60)
    fill(column, 20)
    column.canvas.update()
    before = column.canvas.yview()

    column._contains_pointer = lambda: True
    column._on_wheel(type("Event", (), {"delta": -120, "num": None})())
    assert column.canvas.yview()[0] > before[0]


def test_the_wheel_scrolls_back_up(column):
    column.canvas.configure(height=60)
    fill(column, 20)
    column.canvas.update()
    column._contains_pointer = lambda: True
    down = type("Event", (), {"delta": -120, "num": None})()
    up = type("Event", (), {"delta": 120, "num": None})()
    column._on_wheel(down)
    moved = column.canvas.yview()[0]
    column._on_wheel(up)
    assert column.canvas.yview()[0] < moved


@pytest.mark.parametrize("number, expect_down", [(5, True), (4, False)])
def test_x11_wheel_buttons_scroll_too(column, number, expect_down):
    # X11 has no delta; it sends button 4 and 5. CI runs under xvfb, so this is
    # the path that actually gets exercised there.
    column.canvas.configure(height=60)
    fill(column, 20)
    column.canvas.update()
    column._contains_pointer = lambda: True
    if not expect_down:
        column._on_wheel(type("Event", (), {"delta": 0, "num": 5})())
    before = column.canvas.yview()[0]
    column._on_wheel(type("Event", (), {"delta": 0, "num": number})())
    after = column.canvas.yview()[0]
    assert (after > before) if expect_down else (after < before)
