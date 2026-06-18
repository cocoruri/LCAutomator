"""GUI bootstrap: wire the Qt window to the lcu-driver background thread.

Run with ``python -m gui`` (or ``python gui_app.py``). Imports are static so a
PyInstaller spell can follow them.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Import Qt lazily inside main so importing the package (e.g. for the headless
    # unit tests of viewmodel) doesn't require a display / PySide6 at import time
    # for the parts that don't need it.
    from PySide6.QtWidgets import QApplication

    from src.display import load_static  # noqa: F401 - ensures src import graph loads
    from src.handlers import connector  # the lcu-driver Connector with handlers attached

    from gui.bridge import LcuThread, install_sink
    from gui.window import MainWindow

    app = QApplication(sys.argv)

    sink = install_sink()  # route src.events Updates -> Qt signals
    lcu = LcuThread(connector)  # lcu-driver runs off the Qt thread (see bridge docstring)

    window = MainWindow(lcu, sink)
    window.show()

    lcu.start()  # begins discovering/serving the League client

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
