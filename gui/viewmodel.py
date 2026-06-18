"""Framework-agnostic view-model transforms for the GUI.

These turn the src.events Update dataclasses into plain strings/rows the window
renders. They import nothing from Qt, so they're unit-testable without a display
(the GUI requirement: the session->view-model logic must run headless). The Qt
window calls these and drops the results into labels/lists.
"""

from __future__ import annotations

from src.events import ChampSelectView, PlayerView, SummonerInfo


def summoner_text(summoner: SummonerInfo | None) -> str:
    """One-line label for the logged-in user box."""
    if summoner is None:
        return "Not logged in"
    level = f" - level {summoner.level}" if summoner.level is not None else ""
    return f"{summoner.name}{level}"


def phase_text(phase: str | None) -> str:
    """Persistent phase label. None (unknown) renders explicitly, not blank."""
    return f"Phase: {phase if phase else 'Unknown'}"


def player_row(player: PlayerView) -> str:
    """A single champ-select row: '<pos>  <champ> (locked|hovering)[ <- you]'."""
    if player.champion:
        state = "locked" if player.locked else "hovering"
        pick = f"{player.champion} ({state})"
    else:
        pick = "-"
    me = "  <- you" if player.is_me else ""
    return f"{player.position:<8} {pick}{me}"


def autopilot_lanes(
    lane_choices: dict[str, list[tuple[int, str]]],
) -> list[tuple[str, list[str]]]:
    """Turn the GUI's per-lane picks into make_autopilot's `lanes` argument.

    `lane_choices` maps a lane alias (e.g. "mid") to the ordered (id, name)
    champions the user selected for that role. We hand make_autopilot the names
    (it re-resolves via the same `resolve` callback the search used), and drop
    lanes with no champions so an empty role isn't armed. Order within a lane is
    preserved so the 1st choice stays first; lane iteration order is the dict's
    insertion order (the UI's role order). Qt-free and unit-testable.
    """
    return [
        (lane, [name for _id, name in choices])
        for lane, choices in lane_choices.items()
        if choices
    ]


def champ_select_lines(view: ChampSelectView) -> dict[str, list[str]]:
    """Sectioned, render-ready text for the champ-select panel.

    Returns a dict the window maps onto its widgets, so the section layout lives
    here (testable) rather than tangled into widget code.
    """
    your = (
        f"Your pick: {view.your_pick} "
        f"({'LOCKED IN' if view.your_pick_locked else 'hovering'})"
        if view.your_pick
        else "Your pick: (none yet)"
    )
    return {
        "summary": [your],
        "team": [player_row(p) for p in view.my_team],
        "enemy": list(view.enemy_champions),
        "my_bans": list(view.my_bans),
        "their_bans": list(view.their_bans),
    }
