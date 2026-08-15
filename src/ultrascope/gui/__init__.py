"""Tkinter front end. Requires the ``gui`` extra (matplotlib)."""

from __future__ import annotations


def main() -> None:
    """Entry point for ``ultrascope-gui`` and ``python -m ultrascope.gui``."""
    from .app import main as run

    run()


if __name__ == "__main__":
    main()
