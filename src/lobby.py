from __future__ import annotations

from src.constants import QUEUE_AVAILABLE
from src.endpoints import Endpoints
from src.http import ok


# --------------------------------------------------------------------------- #
# Lobby / queue read queries
#
# Plain reads over the pre-game lobby and queue state. They carry no autopilot
# or draft policy, so they live apart from the features that happen to call them
# today (autopilot's role/queue setup) and can serve a future GUI just as well.
# --------------------------------------------------------------------------- #
async def fetch_available_queues(connection) -> list[dict]:
    """All queues the client reports as currently available on this platform.

    Returns the raw queue dicts from the LCU endpoint, filtered to
    queueAvailability == "Available". Intended as the single source of truth
    for what can be queued into right now — used by queue_candidates for CLI
    mode matching and will populate the GUI queue-selector dropdown.
    """
    resp = await connection.request("get", Endpoints.GAME_QUEUES)
    if not ok(resp.status):
        return []
    return [q for q in await resp.json() if q.get("queueAvailability") == QUEUE_AVAILABLE]


async def get_lobby_members(connection) -> list[dict]:
    """The current lobby's members, straight from the members endpoint.

    Returns the raw member dicts (puuid, summonerId, isLeader, position
    preferences, ...). On a non-2xx response we return [] -- an honest "no
    members reported" rather than a guessed party size. Serves party-size
    (len), leader detection, and per-player puuids for a future GUI too.
    """
    resp = await connection.request("get", Endpoints.LOBBY_MEMBERS)
    if not ok(resp.status):
        return []
    return await resp.json()
