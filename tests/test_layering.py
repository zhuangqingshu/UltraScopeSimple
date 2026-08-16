"""The layering rules, asserted instead of merely documented.

``docs/ARCHITECTURE.md`` describes a strictly one-directional dependency chain.
Nothing enforced it, so a single convenient import could quietly undo the
property the whole test suite rests on: that everything above ``transport`` can
be exercised without an instrument, a display, or a VISA backend.

These checks read the sources with :mod:`ast` rather than importing them, so a
violation is reported as a rule breach and not as an ImportError.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "ultrascope"

# The two modules allowed to touch the VISA backend. Everything else reaches
# the instrument through the Transport protocol, which FakeTransport satisfies.
VISA_MODULES = {"transport", "discovery"}

# Layers, low to high. A module may import from its own layer or below, never
# above. gui/* is the top and may reach anywhere.
LAYERS = [
    {"units", "profile"},
    {"transport", "waveform"},
    {"analysis", "discovery"},
    {"scope"},
    {"export", "setup_file"},
    {"cli"},
]


def module_files() -> Dict[str, Path]:
    """Every module in the package, keyed by the name used in imports."""
    found = {path.stem: path for path in PACKAGE.glob("*.py")
             if path.stem != "__init__"}
    found.update({f"gui.{path.stem}": path for path in (PACKAGE / "gui").glob("*.py")
                  if not path.stem.startswith("__")})
    return found


def imports_of(path: Path) -> Set[str]:
    """Names imported by a module, including imports nested inside functions.

    A lazy import is still a dependency: ``scope`` importing ``discovery``
    inside ``connect()`` is exactly the kind of edge these rules are about.
    Relative imports come back as bare module names ("waveform"), absolute ones
    as their root package ("pyvisa", "numpy").
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # from . import x / from ..units import eng
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names.update(alias.name for alias in node.names)
            else:
                names.add(node.module.split(".")[0] if node.module else "")
    return names


ALL_MODULES = module_files()


@pytest.mark.parametrize("name", sorted(ALL_MODULES))
def test_only_the_visa_modules_import_pyvisa(name):
    # This is what makes 278 offline tests possible: swap Transport for
    # FakeTransport and no other module notices.
    if name in VISA_MODULES:
        return
    assert "pyvisa" not in imports_of(ALL_MODULES[name]), (
        f"{name} imports pyvisa; only {sorted(VISA_MODULES)} may")


@pytest.mark.parametrize("name", sorted(ALL_MODULES))
def test_display_libraries_stay_out_of_the_instrument_layer(name):
    """tkinter and matplotlib belong to gui/, plus export's PNG writer."""
    if name.startswith("gui.") or name == "export":
        return
    found = imports_of(ALL_MODULES[name])
    assert "tkinter" not in found, f"{name} imports tkinter"
    assert "matplotlib" not in found, f"{name} imports matplotlib"


def layer_of(name: str) -> int:
    for index, layer in enumerate(LAYERS):
        if name in layer:
            return index
    return len(LAYERS)  # gui/* and anything new sits on top


@pytest.mark.parametrize("name", sorted(ALL_MODULES))
def test_no_module_imports_from_a_higher_layer(name):
    level = layer_of(name)
    for imported in imports_of(ALL_MODULES[name]):
        if imported == "gui":
            assert name.startswith("gui."), f"{name} imports gui"
            continue
        if imported not in ALL_MODULES:
            continue  # third-party or stdlib
        assert layer_of(imported) <= level, (
            f"{name} (layer {level}) imports {imported} "
            f"(layer {layer_of(imported)}) — dependencies point downward only")


def test_every_module_is_covered_by_a_layer():
    """A new module added to the package must be placed, not silently ignored."""
    unplaced = {name for name in ALL_MODULES
                if not name.startswith("gui.") and layer_of(name) == len(LAYERS)}
    assert not unplaced, (f"{sorted(unplaced)} are not listed in LAYERS; "
                          "add them so the direction rule applies")


def test_importing_the_package_pulls_in_no_gui_stack():
    """``import ultrascope`` must work where matplotlib and Tk are absent.

    Run in a subprocess: by the time this file executes, the GUI tests have
    long since populated sys.modules.
    """
    code = ("import sys, ultrascope; "
            "print(','.join(m for m in ('matplotlib', 'tkinter', 'pyvisa') "
            "if m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"import ultrascope loaded {result.stdout.strip()}")
