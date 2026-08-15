"""UltraScopeSimple - capture and plotting tools for the Rigol DS1102E / DS1000D-E.

Typical use::

    from ultrascope import Scope

    with Scope.connect() as scope:
        wave = scope.capture(points="normal")
"""

from __future__ import annotations

from .discovery import list_scopes
from .export import save_csv, save_png
from .profile import DS1000E, DeviceProfile
from .scope import ChannelSettings, Scope, ScopeError, ScopeSettings
from .transport import FakeTransport, PyVisaTransport, Transport
from .units import eng
from .waveform import Waveform, WaveformError, decode, parse_block, time_axis

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "Scope", "ScopeError", "ScopeSettings", "ChannelSettings",
    "Waveform", "WaveformError", "parse_block", "decode", "time_axis",
    "DeviceProfile", "DS1000E",
    "Transport", "PyVisaTransport", "FakeTransport",
    "list_scopes", "save_csv", "save_png", "eng",
]
