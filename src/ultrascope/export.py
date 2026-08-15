"""Writing captures to disk.

Shared by the CLI and the GUI so the two produce identical files.
"""

from __future__ import annotations

import numpy as np

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
