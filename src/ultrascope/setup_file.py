"""Reading and writing instrument setup files.

The JSON format is shared by the GUI's Save/Load setup buttons and the CLI's
--save-setup / --load-setup, and is a published file format: see
``ScopeSettings.to_dict`` before changing any key name.
"""

from __future__ import annotations

import json

from .scope import ScopeSettings


def save_setup(path: str, settings: ScopeSettings) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings.to_dict(), fh, indent=2, ensure_ascii=False)
    return path


def load_setup(path: str) -> ScopeSettings:
    with open(path, encoding="utf-8") as fh:
        return ScopeSettings.from_dict(json.load(fh))
