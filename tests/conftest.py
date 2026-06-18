"""Shared test fixtures.

The two modules under test live at the repo root, so make sure it's importable
regardless of how pytest is invoked. We also provide a fake LCU connection (the
real one needs a running League client) and reset the watcher's module-level
champ-select state between tests.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lcu_watch  # noqa: E402  (after sys.path tweak)


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data

    async def json(self):
        return self._data


class FakeConnection:
    """Stand-in for lcu_driver's connection.

    Records every request as (method, endpoint, body). A handler callback may
    return a FakeResponse for specific endpoints; otherwise a 200/{} is returned.
    """

    def __init__(self, handler=None):
        self.calls = []
        self._handler = handler

    async def request(self, method, endpoint, **kwargs):
        body = kwargs.get("data")
        self.calls.append((method, endpoint, body))
        if self._handler is not None:
            resp = self._handler(method, endpoint, body)
            if resp is not None:
                return resp
        return FakeResponse(200, {})

    # Convenience views over recorded calls.
    def patches(self):
        return [(endpoint, body) for (m, endpoint, body) in self.calls if m == "patch"]

    def puts(self):
        return [body for (m, endpoint, body) in self.calls if m == "put"]

    def posts(self):
        return [endpoint for (m, endpoint, body) in self.calls if m == "post"]


@pytest.fixture
def run():
    """Run a coroutine to completion (avoids a pytest-asyncio dependency)."""
    return asyncio.run


@pytest.fixture(autouse=True)
def reset_lcu_state():
    """Clear the watcher's per-session state and caches before each test."""
    lcu_watch.STATE.reset()
    lcu_watch._champ_names.clear()
    lcu_watch._spell_names.clear()
    lcu_watch.AUTOPILOT = None
    lcu_watch.CONNECTION = None
    yield
    # Tests that install a custom event sink should leave the default one behind.
    from src import events

    events.reset_sink()
