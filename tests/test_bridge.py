"""Arming behavior in the GUI bridge (skipped when PySide6 isn't installed)."""

import pytest

pytest.importorskip("PySide6")

import lcu_watch  # noqa: E402
from gui import bridge  # noqa: E402
from src import state  # noqa: E402


class _FakeLcu:
    """Records coroutines submitted to the lcu loop without running them."""

    def __init__(self):
        self.submitted = []

    def submit(self, coro):
        self.submitted.append(coro)
        coro.close()  # we never await it here; close to avoid a warning


def _ap(start):
    return lcu_watch.Autopilot(mode="solo", lanes={}, lane_order=[], bans=[], start=start)


def test_arm_auto_start_while_connected_starts_queue():
    state.CONNECTION = object()
    lcu = _FakeLcu()
    bridge.arm_autopilot(lcu, _ap(start=True))
    assert state.AUTOPILOT is not None
    assert len(lcu.submitted) == 1  # setup_queue kicked onto the lcu loop now


def test_arm_watch_only_does_not_start_queue():
    state.CONNECTION = object()
    lcu = _FakeLcu()
    bridge.arm_autopilot(lcu, _ap(start=False))
    assert state.AUTOPILOT is not None
    assert lcu.submitted == []  # run_draft handles it on the next champ-select event


def test_arm_auto_start_while_disconnected_defers_to_on_ready():
    state.CONNECTION = None
    lcu = _FakeLcu()
    bridge.arm_autopilot(lcu, _ap(start=True))
    assert state.AUTOPILOT is not None  # on_ready will run setup_queue on connect
    assert lcu.submitted == []


def test_disarm_while_connected_stops_queue():
    state.CONNECTION = object()
    lcu = _FakeLcu()
    bridge.arm_autopilot(lcu, None)
    assert state.AUTOPILOT is None
    assert len(lcu.submitted) == 1  # _stop_queue (cancel matchmaking) scheduled


def test_disarm_while_disconnected_does_nothing():
    state.CONNECTION = None
    lcu = _FakeLcu()
    bridge.arm_autopilot(lcu, None)
    assert state.AUTOPILOT is None
    assert lcu.submitted == []  # nothing to stop
