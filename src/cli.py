from __future__ import annotations

import argparse
import asyncio
import sys

from src import state
from src.autopilot import UnknownLaneError, make_autopilot
from src.display import opgg_runes
from src.handlers import connector
from src.state import Autopilot


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_champion(name: str) -> tuple[int, str]:
    """Champion name -> (id, display name), via opgg_runes' Data Dragon cache."""
    if opgg_runes is None:
        raise SystemExit("opgg_runes is required to resolve champion names.")
    champ = opgg_runes.resolve_champion(name)
    return champ["id"], champ["name"]


def build_autopilot(args) -> Autopilot:
    # Reuse the headless builder; translate its lane error into the CLI's
    # SystemExit so argument-validation behavior is unchanged.
    lanes = [(position, [champ1, champ2]) for position, champ1, champ2 in (args.lane or [])]
    try:
        return make_autopilot(
            args.mode,
            lanes=lanes,
            bans=list(args.ban or []),
            start=not args.no_start,
            resolve=_resolve_champion,
        )
    except UnknownLaneError as exc:
        raise SystemExit(str(exc)) from exc


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Watch the League client; optionally auto-queue, draft, and set runes."
    )
    p.add_argument(
        "--mode",
        choices=["solo", "flex", "aram"],
        help="Queue to start (solo/flex = ranked, aram = ARAM Mayhem). Omit to only watch.",
    )
    p.add_argument(
        "--lane",
        nargs=3,
        action="append",
        metavar=("POSITION", "CHAMP1", "CHAMP2"),
        help="A preferred lane and its two champions. Repeatable (max 2). Ranked only.",
    )
    p.add_argument(
        "--ban",
        nargs=2,
        metavar=("CHAMP1", "CHAMP2"),
        help="Two champions to ban (2nd only used if the 1st is already banned).",
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help="Don't create a lobby or start the queue (e.g. you're a non-owner "
        "party member); just set roles and auto-draft.",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.no_start and not args.mode:
        print("note: --no-start has no effect without --mode (nothing to configure).")
    if args.mode:
        if args.lane and len(args.lane) > 2:
            raise SystemExit("At most two --lane options are supported.")
        state.AUTOPILOT = build_autopilot(args)
        summary = f"Autopilot armed: mode={args.mode}"
        if not state.AUTOPILOT.start:
            summary += " (no-start: roles only)"
        if state.AUTOPILOT.lane_order:
            lanes = "; ".join(
                f"{pos} -> {', '.join(state.AUTOPILOT.lane_names[pos])}"
                for pos in state.AUTOPILOT.lane_order
            )
            summary += f" | lanes: {lanes}"
        if state.AUTOPILOT.ban_names:
            summary += f" | bans: {', '.join(state.AUTOPILOT.ban_names)}"
        print(summary)

    # On Windows the selector loop avoids noisy ProactorEventLoop teardown
    # errors from aiohttp when you Ctrl+C out of the watcher.
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print("Waiting for the League client... (start it if it isn't running)")
    try:
        connector.start()
    except KeyboardInterrupt:
        print("\nStopped.")
