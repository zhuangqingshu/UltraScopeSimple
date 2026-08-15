"""Value formatting shared by the CLI, the GUI and the option tables."""

from __future__ import annotations

from decimal import Decimal

_PREFIXES = (
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
)


def scpi_number(value: float) -> str:
    """Format a number the way the DS1000E's command parser reliably accepts.

    The scope's parser is unreliable with exponent notation: ":TIM:SCAL 5e-5"
    and ":TRIG:HOLD 5e-07" are accepted at the link level and then silently
    ignored, so the setting just appears not to work. Plain decimal notation
    has always been honoured, so never emit an exponent -- 2 ns/div goes out
    as 0.000000002.

    Verified on a DS1102E, firmware 00.04.02.01.00.
    """
    decimal = Decimal(repr(float(value))).normalize()
    text = format(decimal, "f")          # 'f' never uses an exponent
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def eng(value: float, unit: str) -> str:
    """Format a value with an SI prefix, the way the scope's own display does."""
    if value == 0:
        return f"0 {unit}"
    for factor, prefix in _PREFIXES:
        if abs(value) >= factor:
            text = f"{value / factor:.6g}"
            return f"{text} {prefix}{unit}"
    return f"{value:.3g} {unit}"
