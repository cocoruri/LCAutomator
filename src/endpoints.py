from __future__ import annotations


class Endpoints:
    """LCU REST paths, kept in one place so call sites read declaratively."""

    SUMMONER_SPELLS_ASSETS = "/lol-game-data/assets/v1/summoner-spells.json"

    PERKS_PAGES = "/lol-perks/v1/pages"

    CHAMP_SELECT_SESSION = "/lol-champ-select/v1/session"
    CHAMP_SELECT_MY_SELECTION = "/lol-champ-select/v1/session/my-selection"

    GAMEFLOW_SESSION = "/lol-gameflow/v1/session"
    GAMEFLOW_PHASE = "/lol-gameflow/v1/gameflow-phase"

    GAME_QUEUES = "/lol-game-queues/v1/queues"

    LOBBY = "/lol-lobby/v2/lobby"
    LOBBY_MEMBERS = "/lol-lobby/v2/lobby/members"
    LOBBY_POSITION_PREFERENCES = (
        "/lol-lobby/v2/lobby/members/localMember/position-preferences"
    )
    LOBBY_SEARCH = "/lol-lobby/v2/lobby/matchmaking/search"

    READY_CHECK = "/lol-matchmaking/v1/ready-check"
    READY_CHECK_ACCEPT = "/lol-matchmaking/v1/ready-check/accept"

    CURRENT_SUMMONER = "/lol-summoner/v1/current-summoner"
    # POST with a JSON list of puuids -> list of summoner DTOs (one round-trip
    # for a whole lobby, instead of one GET per member).
    SUMMONERS_BY_PUUIDS = "/lol-summoner/v2/summoners/puuid"

    @staticmethod
    def perks_page(page_id) -> str:
        return f"/lol-perks/v1/pages/{page_id}"

    @staticmethod
    def champ_select_action(action_id) -> str:
        return f"/lol-champ-select/v1/session/actions/{action_id}"
