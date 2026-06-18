"""The single place Qt, asyncio, and lcu-driver meet.

## Async-integration decision: background thread, not qasync

lcu-driver's ``Connector.start()`` is fully synchronous and owns its event loop:
it busy-waits (``time.sleep``) for ``LeagueClientUx.exe`` to appear, then calls
``loop.run_until_complete(...)`` and finally ``loop.close()`` (see
lcu_driver/connector.py). It exposes no awaitable start that would let Qt's loop
drive it, and it closes the loop it's handed — so handing it qasync's loop would
tear down the Qt loop on disconnect. The task brief says: if lcu-driver insists
on owning its loop, fall back to a background thread. It does, so we do.

So: lcu-driver runs on its own asyncio loop in a daemon thread. The handlers it
fires emit framework-agnostic Updates to a sink (src.events). Our sink here is a
QObject; ``emit`` is called on the lcu-driver thread but only does
``signal.emit(update)`` — a queued cross-thread Qt signal — so the actual widget
work always runs on the Qt main thread. That's the only thread hand-off, and Qt
makes it safe. We never touch widgets from the lcu thread, and never block the
Qt loop on the network.

Arming the autopilot sets ``state.AUTOPILOT`` (read live by the draft handlers).
For a *watch-only* config that's all it takes -- ``run_draft`` picks it up on the
next champ-select event. But an *auto-start* config has to create the lobby and
start the queue, and ``setup_queue`` only runs in ``on_ready`` (i.e. at connect
time). When the user arms auto-start *after* already being connected, we must run
``setup_queue`` ourselves -- a Qt->lcu-loop coroutine hand-off, done via
``LcuThread.submit`` (``run_coroutine_threadsafe`` onto the lcu loop), using the
live connection captured in ``state.CONNECTION``.
"""

from __future__ import annotations

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from src import events, state
from src.autopilot import QueueNotAvailableError, setup_queue, stop_queue
from src.events import NoticeUpdate


class QtEventSink(QObject):
    """An EventSink (src.events) that forwards Updates as Qt signals.

    Implements the structural EventSink protocol (a single ``emit``). Because
    ``updated`` is a Qt signal connected across threads with the default
    AutoConnection, the slot runs queued on the receiver's (main) thread even
    though ``emit`` is invoked from the lcu-driver thread.
    """

    updated = Signal(object)  # carries an src.events Update dataclass

    def emit(self, update) -> None:  # noqa: A003 - matches EventSink protocol
        self.updated.emit(update)


class LcuThread:
    """Runs lcu-driver's blocking connector on its own loop in a daemon thread.

    Keeps the loop off the Qt thread so the network never blocks the UI. The
    handlers it fires emit Updates (forwarded to the Qt sink); the GUI itself
    issues no coroutines onto this loop.
    """

    def __init__(self, connector) -> None:
        self._connector = connector
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        """Launch the connector thread. Returns immediately."""
        self._thread = threading.Thread(
            target=self._run, name="lcu-driver", daemon=True
        )
        self._thread.start()
        # Wait until the connector's loop is installed before returning.
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        # A fresh loop owned entirely by this thread. lcu-driver will
        # run_until_complete on it and close it on disconnect; that's fine
        # because nothing else shares it.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._connector.loop = loop  # connector was built before this loop existed
        self._loop = loop
        self._ready.set()
        try:
            self._connector.start()  # blocks: discover client -> run_until_complete
        except Exception as exc:  # surface, don't silently die
            import sys

            print(f"(error) lcu-driver thread stopped: {exc}", file=sys.stderr)

    def submit(self, coro) -> None:
        """Schedule a coroutine on the lcu-driver loop from the Qt thread.

        The connection's HTTP session lives on that loop, so client requests made
        after connect must run there, not on the Qt thread.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("lcu-driver loop is not running yet.")
        asyncio.run_coroutine_threadsafe(coro, loop)


def install_sink() -> QtEventSink:
    """Create the Qt sink and register it as src.events' active sink."""
    sink = QtEventSink()
    events.set_sink(sink)
    return sink


async def _start_queue(connection, autopilot) -> None:
    """Run setup_queue and surface a failed queue to the GUI status line."""
    try:
        await setup_queue(connection, autopilot)
    except QueueNotAvailableError as exc:
        events.emit(NoticeUpdate(text=str(exc), level="error"))
    except Exception as exc:  # don't let it vanish into the coroutine's Future
        events.emit(NoticeUpdate(text=f"Could not start queue: {exc!r}", level="error"))


async def _stop_queue(connection) -> None:
    """Cancel matchmaking on disarm and report if there was nothing to stop."""
    try:
        if await stop_queue(connection):
            events.emit(NoticeUpdate(text="Queue stopped.", level="info"))
        else:
            events.emit(NoticeUpdate(text="No active queue to stop.", level="warn"))
    except Exception as exc:
        events.emit(NoticeUpdate(text=f"Could not stop queue: {exc!r}", level="error"))


def arm_autopilot(lcu: LcuThread, autopilot) -> None:
    """Set the live autopilot config (or None to disarm) and act on it now.

    Watch-only (start=False) just records the config; run_draft reads it on the
    next champ-select event. Auto-start (start=True) also needs the queue created
    -- if we're already connected, kick setup_queue onto the lcu loop now (on_ready
    only does that at connect time); if not connected yet, on_ready runs it when it
    sees state.AUTOPILOT. Disarm (None) stops drafting and, if connected, cancels
    any matchmaking search the autopilot started.
    """
    state.AUTOPILOT = autopilot
    if state.CONNECTION is None:
        return
    if autopilot is None:
        lcu.submit(_stop_queue(state.CONNECTION))
    elif autopilot.start:
        lcu.submit(_start_queue(state.CONNECTION, autopilot))
