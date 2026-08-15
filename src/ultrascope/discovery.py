"""VISA resource enumeration."""

from __future__ import annotations

from typing import List, Optional

from .profile import DEFAULT_PROFILE, DeviceProfile


def list_scopes(rm=None, profile: Optional[DeviceProfile] = None) -> List[str]:
    """Return the VISA resources that look like a supported oscilloscope.

    The USB driver comes from Rigol's UltraSigma package; without it installed
    the resource simply does not show up here.
    """
    import pyvisa

    profile = profile or DEFAULT_PROFILE
    rm = rm or pyvisa.ResourceManager()
    return [r for r in rm.list_resources() if profile.is_scope_resource(r)]
