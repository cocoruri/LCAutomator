"""PySide6 desktop GUI for the LCU automator.

A second front end over the headless ``src/`` library (the CLI is the first). The
Qt-free parts (``gui.viewmodel``) are importable and unit-tested without a
display; ``gui.app``/``gui.window``/``gui.bridge`` need PySide6 at runtime.
"""

from __future__ import annotations
