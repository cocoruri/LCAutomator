#!/usr/bin/env python3
"""Static entry point for the GUI (PyInstaller-friendly: `pyinstaller gui_app.py`).

Equivalent to `python -m gui`.
"""

from __future__ import annotations

from gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
