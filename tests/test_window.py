"""Window-level arming guard (offscreen Qt; skipped without PySide6)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no display needed

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import config  # noqa: E402
from gui.bridge import install_sink  # noqa: E402
from gui.window import MainWindow  # noqa: E402
from src import state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, monkeypatch):
    # Isolate persistence to a temp cache so construction loads an empty config.
    monkeypatch.setattr(config, "resolve_cache_dir", lambda: str(tmp_path))
    return MainWindow(object(), install_sink())


def test_arm_blocked_when_config_incomplete(qapp, tmp_path, monkeypatch):
    win = _window(tmp_path, monkeypatch)
    win._on_arm()  # nothing configured
    assert state.AUTOPILOT is None  # not armed
    assert "position" in win._status.text().lower()  # told why


def test_arm_blocked_without_bans(qapp, tmp_path, monkeypatch):
    win = _window(tmp_path, monkeypatch)
    win._lane_choices["top"] = [(1, "A"), (2, "B")]
    win._lane_choices["jungle"] = [(3, "C"), (4, "D")]
    win._on_arm()  # two full lanes but no bans
    assert state.AUTOPILOT is None
    assert "ban" in win._status.text().lower()
