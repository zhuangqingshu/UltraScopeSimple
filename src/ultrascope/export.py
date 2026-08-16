"""Writing captures to disk.

Shared by the CLI and the GUI so the two produce identical files.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .profile import DEFAULT_PROFILE, DeviceProfile
from .waveform import Waveform

CSV_FORMAT = "%.9g"


def save_csv(path: str, wave: Waveform) -> str:
    """Write ``Time(s),CH1(V),CH2(V)`` for the channels present in the capture."""
    order = wave.channel_ids
    cols = [wave.t] + [wave.channels[ch] for ch in order]
    header = "Time(s)," + ",".join(f"CH{ch}(V)" for ch in order)
    np.savetxt(path, np.column_stack(cols), delimiter=",",
               header=header, comments="", fmt=CSV_FORMAT)
    return path


def load_csv(path: str, profile: Optional[DeviceProfile] = None) -> Waveform:
    """Read back a CSV written by :func:`save_csv`.

    The file carries no acquisition settings, so the timebase and offset are
    reconstructed from the time column on the same assumption the capture was
    built with: the record spans the profile's horizontal divisions.
    """
    profile = profile or DEFAULT_PROFILE
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
    if len(header) < 2 or not header[0].lower().startswith("time"):
        raise ValueError(f"{path}: not a waveform CSV (header {header!r})")

    data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if data.shape[1] != len(header):
        raise ValueError(f"{path}: {len(header)} columns declared, "
                         f"{data.shape[1]} found")

    t = data[:, 0]
    channels = {}
    for column, name in enumerate(header[1:], start=1):
        digits = "".join(ch for ch in name if ch.isdigit())
        if not digits:
            raise ValueError(f"{path}: cannot tell which channel {name!r} is")
        channels[int(digits)] = data[:, column]

    span = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    return Waveform(t=t, channels=channels,
                    timebase=span / profile.h_divisions,
                    time_offset=float((t[0] + t[-1]) / 2) if len(t) else 0.0)


def save_png(path: str, wave: Waveform, dpi: int = 150) -> str:
    """Render the capture to a PNG. Requires the ``gui`` extra (matplotlib)."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    for ch in wave.channel_ids:
        ax.plot(wave.t, wave.channels[ch], linewidth=0.8, label=f"CH{ch}")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Voltage (V)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def png_path_for(csv_path: str) -> str:
    """The PNG name --plot derives from the CSV output path."""
    return csv_path.rsplit(".", 1)[0] + ".png"
