"""Value formatting shared by the CLI, the GUI and the option tables."""

from __future__ import annotations

_PREFIXES = (
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
)


def eng(value: float, unit: str) -> str:
    """Format a value with an SI prefix, the way the scope's own display does."""
    if value == 0:
        return f"0 {unit}"
    for factor, prefix in _PREFIXES:
        if abs(value) >= factor:
            text = f"{value / factor:.6g}"
            return f"{text} {prefix}{unit}"
    return f"{value:.3g} {unit}"
