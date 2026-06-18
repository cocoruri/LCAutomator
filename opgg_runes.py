#!/usr/bin/env python3
"""Fetch recommended rune pages from OP.GG for a given champion name.

Data sources (all read-only, no API key needed):
  * Riot Data Dragon ............ champion name  -> numeric champion id
  * OP.GG champion build API .... numeric id     -> recommended rune pages
  * CommunityDragon perks.json .. rune/shard id  -> human readable name

The OP.GG endpoint is their own internal build API (the same one their
website calls), so we are not scraping HTML:

    https://lol-api-champion.op.gg/api/{region}/champions/{mode}/{id}/{position}

Each entry in the response `data.runes` array already matches what the
League client needs for POST /lol-perks/v1/pages, so `RunePage.to_lcu_page()`
gives you a body you can send straight to the LCU (the original goal).

Usage:
    python opgg_runes.py "Jinx"
    python opgg_runes.py "Miss Fortune" --position adc
    python opgg_runes.py Lee Sin --position jungle --all
    python opgg_runes.py Ahri --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http import HTTPStatus
from dataclasses import dataclass
from typing import Any, Callable

USER_AGENT = "Mozilla/5.0 (opgg-runes script)"

# Reference data (champions, perks) is cached on disk so we only download it once.
PORTABLE_MARKER = ".portable"
APP_NAME = "lcu_automator"


def _app_dir() -> str:
    """Directory the app runs from: the PyInstaller exe, else this script."""
    base = sys.executable if getattr(sys, "frozen", False) else __file__
    return os.path.dirname(os.path.abspath(base))


def resolve_cache_dir() -> str:
    """Where reference data is cached.

    Portable mode: if a `.portable` marker sits next to the app, cache in a
    `.cache/` folder right there, so the whole thing is self-contained (e.g. on a
    USB stick). Otherwise cache under the user's home: ~/.cache/lcu_automator.
    The choice is deterministic -- the marker is either present or it isn't.
    """
    app_dir = _app_dir()
    if os.path.exists(os.path.join(app_dir, PORTABLE_MARKER)):
        return os.path.join(app_dir, ".cache")
    return os.path.join(os.path.expanduser("~"), ".cache", APP_NAME)


CACHE_DIR = resolve_cache_dir()
CHAMPIONS_CACHE = os.path.join(CACHE_DIR, "champions.json")
PERKS_CACHE = os.path.join(CACHE_DIR, "perks.json")
META_CACHE = os.path.join(CACHE_DIR, "meta.json")

# League's five rune trees never change ids, so a tiny static map is enough.
RUNE_TREES = {
    8000: "Precision",
    8100: "Domination",
    8200: "Sorcery",
    8300: "Inspiration",
    8400: "Resolve",
}

POSITIONS = ["top", "jungle", "mid", "adc", "support"]

# Modes without a lane. The value is the position token OP.GG's route wants
# ("none" for ARAM, empty string for Arena which takes no position segment).
POSITIONLESS_MODES = {"aram": "none", "arena": ""}

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"
LOCALE = "en_US"  # Data Dragon locale; affects champion display names
DDRAGON_VERSIONS = f"{DDRAGON_BASE}/api/versions.json"
PERKS_URL = (
    "https://raw.communitydragon.org/latest/plugins/"
    "rcp-be-lol-game-data/global/default/v1/perks.json"
)
# One response listing every champion's positions (ranked by play). Cached so
# we can pick a champion's main lane without probing all five lanes.
META_URL = "https://lol-api-champion.op.gg/api/global/champions/ranked"

HTTP_TIMEOUT = 20  # seconds, shared by every outbound request

DEFAULT_REGION = "global"  # OP.GG region segment used when none is given
DEFAULT_MODE = "ranked"  # OP.GG queue mode used when none is given


# --------------------------------------------------------------------------- #
# HTTP helper (stdlib only, so the script has no third-party dependencies)
# --------------------------------------------------------------------------- #
class DataFetchError(RuntimeError):
    """A network or decode failure while fetching JSON, with a readable message.

    HTTPError is deliberately *not* wrapped: callers (e.g. fetch_build) branch on
    its status code, so it must propagate unchanged. Only the cases that would
    otherwise surface as a raw stack trace (offline/DNS, malformed JSON) are
    re-raised as this single, message-carrying type.
    """


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError:
        raise  # status-bearing; callers inspect exc.code, so don't mask it
    except urllib.error.URLError as exc:
        raise DataFetchError(f"could not reach {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise DataFetchError(f"invalid JSON from {url}: {exc}") from exc


def _load_cache(path: str) -> object | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _cached_today(path: str) -> bool:
    """True if the cache file exists and was last written on the current date."""
    try:
        from datetime import date, datetime

        return datetime.fromtimestamp(os.path.getmtime(path)).date() == date.today()
    except OSError:
        return False


def _save_cache(path: str, data: object) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass  # a non-writable cache dir must not break the script


# --------------------------------------------------------------------------- #
# Reference data: downloaded once, then read from .cache/ on later runs.
#
# Two invalidation strategies, by how the data goes stale:
#   * champion_index / perk_names are miss-based -- champion and rune *names*
#     only change when something new is added, so we refresh only when a lookup
#     misses (a new champion/rune id we haven't seen).
#   * champion_meta is time-based (_cached_today) -- its play/pick stats and lane
#     assignments shift every patch even with no new ids, so it refreshes daily.
# --------------------------------------------------------------------------- #
_champion_index: dict[str, dict] | None = None
_perk_names: dict[int, str] | None = None
_champion_meta: dict[str, list[str]] | None = None


def _slug(name: str) -> str:
    """Normalise a champion name for fuzzy matching: 'Kai'Sa' -> 'kaisa'."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _fetch_champion_index() -> dict[str, dict]:
    """Download the champion list from Data Dragon and build the name index."""
    version = get_json(DDRAGON_VERSIONS)[0]
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{LOCALE}/champion.json"
    data = get_json(url)["data"]
    idx: dict[str, dict] = {}
    for champ in data.values():
        # A schema change at the data source must skip the bad entry, not discard
        # the entire index with an unhandled KeyError/ValueError.
        try:
            entry = {
                "id": int(champ["key"]),  # numeric id OP.GG expects
                "slug": champ["id"],  # e.g. "MissFortune"
                "name": champ["name"],  # display name
            }
        except (KeyError, ValueError, TypeError) as exc:
            print(f"(warn) skipping malformed champion entry {champ!r}: {exc}")
            continue
        idx[_slug(champ["name"])] = entry
        idx[_slug(champ["id"])] = entry
    return idx


def _cached_reference_data(
    in_memory: Any,
    *,
    refresh: bool,
    cache_path: str,
    fetch: Callable[[], Any],
    decode: Callable[[Any], Any] | None = None,
    cache_is_fresh: Callable[[str], bool] | None = None,
) -> tuple[Any, Any]:
    """Resolve reference data from memory -> local cache -> network.

    Shared by champion_index / perk_names / champion_meta, which differ only in
    where they fetch from and how their cache is decoded or invalidated:

      * in_memory     -- the caller's current in-memory value (None if unset)
      * fetch()       -- downloads and builds the data when nothing is cached
      * decode(raw)   -- optional transform applied to a cache hit (perk_names
                         restores int keys that JSON stringified)
      * cache_is_fresh(path) -- optional gate; when it returns False the on-disk
                         cache is ignored (champion_meta refreshes daily)

    Returns (value, value_to_save): value_to_save is non-None only when the data
    was just fetched and the caller should persist it (so a cache hit isn't
    re-written).
    """
    if refresh:
        in_memory = None
    if in_memory is None and not refresh:
        if cache_is_fresh is None or cache_is_fresh(cache_path):
            raw = _load_cache(cache_path)
            if raw is not None:
                in_memory = decode(raw) if decode else raw
    if in_memory is None:
        fetched = fetch()
        return fetched, fetched
    return in_memory, None


def champion_index(refresh: bool = False) -> dict[str, dict]:
    """Name index, served from memory -> local cache -> network (in that order)."""
    global _champion_index
    _champion_index, to_save = _cached_reference_data(
        _champion_index,
        refresh=refresh,
        cache_path=CHAMPIONS_CACHE,
        fetch=_fetch_champion_index,
    )
    if to_save is not None:
        _save_cache(CHAMPIONS_CACHE, to_save)
    return _champion_index


def perk_names(refresh: bool = False) -> dict[int, str]:
    """Perk/shard id -> name, served from memory -> local cache -> network."""
    global _perk_names
    _perk_names, to_save = _cached_reference_data(
        _perk_names,
        refresh=refresh,
        cache_path=PERKS_CACHE,
        fetch=lambda: {p["id"]: p["name"] for p in get_json(PERKS_URL)},
        decode=lambda raw: {int(k): v for k, v in raw.items()},  # JSON keys are str
    )
    if to_save is not None:
        _save_cache(PERKS_CACHE, to_save)
    return _perk_names


def perk_name(perk_id: int) -> str:
    """Look up a perk name, refreshing the cache once if the id is unknown."""
    names = perk_names()
    if perk_id not in names:
        names = perk_names(refresh=True)
    return names.get(perk_id, f"#{perk_id}")


def _lookup(name: str, idx: dict[str, dict]) -> dict | None:
    key = _slug(name)
    if key in idx:
        return idx[key]
    # Forgiving prefix match ("kog" -> "Kog'Maw"). Dict iteration order must not
    # decide an ambiguous prefix, so sort candidate slugs by (len, alpha): the
    # shortest slug wins, ties broken alphabetically, making the result stable.
    candidate_slugs = sorted(
        (k for k in idx if k.startswith(key)), key=lambda slug: (len(slug), slug)
    )
    if not candidate_slugs:
        return None
    return idx[candidate_slugs[0]]


def resolve_champion(name: str) -> dict:
    champ = _lookup(name, champion_index())
    if champ is None:
        # Cache may predate a newly released champion: refresh once, then retry.
        champ = _lookup(name, champion_index(refresh=True))
    if champ is None:
        raise ValueError(f"Unknown champion: {name!r}")
    return champ


def _fetch_champion_meta() -> dict[str, list[str]]:
    """Map champion id -> lanes (lowercased, most played first) for every champ."""
    data = get_json(META_URL)["data"]
    meta: dict[str, list[str]] = {}
    for champ in data:
        # A schema change at the data source must skip the bad entry, not discard
        # the entire index with an unhandled KeyError/TypeError.
        try:
            positions = sorted(
                champ.get("positions", []),
                key=lambda p: p.get("stats", {}).get("play", 0),  # tolerate missing stats
                reverse=True,
            )
            meta[str(champ["id"])] = [p["name"].lower() for p in positions]
        except (KeyError, AttributeError, TypeError) as exc:
            print(f"(warn) skipping malformed meta entry {champ!r}: {exc}")
            continue
    return meta


def champion_meta(refresh: bool = False) -> dict[str, list[str]]:
    """Position index, served from memory -> local cache -> network.

    The on-disk cache is refreshed once per day so position/play data tracks
    new patches without a manual cache wipe.
    """
    global _champion_meta
    _champion_meta, to_save = _cached_reference_data(
        _champion_meta,
        refresh=refresh,
        cache_path=META_CACHE,
        fetch=_fetch_champion_meta,
        cache_is_fresh=_cached_today,
    )
    if to_save is not None:
        _save_cache(META_CACHE, to_save)
    return _champion_meta


def champion_positions(champion_id: int) -> list[str]:
    """Lanes a champion is played in (most played first); [] if unknown."""
    meta = champion_meta()
    positions = meta.get(str(champion_id))
    if positions is None:
        # Cache may predate a new champion: refresh once.
        positions = champion_meta(refresh=True).get(str(champion_id))
    return positions or []


# --------------------------------------------------------------------------- #
# Rune page model
# --------------------------------------------------------------------------- #
@dataclass
class RunePage:
    primary_style: int
    sub_style: int
    primary_rune_ids: list[int]
    secondary_rune_ids: list[int]
    stat_mod_ids: list[int]
    play: int = 0
    win: int = 0
    pick_rate: float = 0.0

    @classmethod
    def from_opgg(cls, raw: dict) -> "RunePage":
        return cls(
            primary_style=raw["primary_page_id"],
            sub_style=raw["secondary_page_id"],
            primary_rune_ids=raw["primary_rune_ids"],
            secondary_rune_ids=raw["secondary_rune_ids"],
            stat_mod_ids=raw["stat_mod_ids"],
            play=raw.get("play", 0),
            win=raw.get("win", 0),
            pick_rate=raw.get("pick_rate", 0.0),
        )

    @property
    def winrate(self) -> float:
        return self.win / self.play if self.play else 0.0

    @property
    def selected_perk_ids(self) -> list[int]:
        """Flat ordered id list (4 primary + 2 secondary + 3 shards)."""
        return self.primary_rune_ids + self.secondary_rune_ids + self.stat_mod_ids

    def to_lcu_page(self, name: str) -> dict:
        """A rune-page body for the League client (lol-perks).

        lcu_watch PUTs this onto your currently-active page, editing it in place,
        so `name` renames that page (the `"current"` flag is then redundant but
        harmless). Still shaped to also work as a POST body to create a page.
        """
        return {
            "name": name,
            "primaryStyleId": self.primary_style,
            "subStyleId": self.sub_style,
            "selectedPerkIds": self.selected_perk_ids,
            "current": True,
        }

    def describe(self) -> str:
        perk_names()  # warm the cache once before the per-id lookups below
        n = perk_name

        def tree(style_id: int) -> str:
            return RUNE_TREES.get(style_id, f"#{style_id}")

        lines = [
            f"  Primary   [{tree(self.primary_style)}]: "
            + ", ".join(n(i) for i in self.primary_rune_ids),
            f"  Secondary [{tree(self.sub_style)}]: "
            + ", ".join(n(i) for i in self.secondary_rune_ids),
            "  Shards            : " + ", ".join(n(i) for i in self.stat_mod_ids),
            f"  Games {self.play:>7,}  |  Winrate {self.winrate:6.1%}"
            f"  |  Pick {self.pick_rate:5.1%}",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# OP.GG fetch
# --------------------------------------------------------------------------- #
@dataclass
class Build:
    """Everything OP.GG recommends for one champion+lane."""

    position: str
    runes: list[RunePage]
    # Summoner-spell pairs [spell1Id, spell2Id], most played first.
    spells: list[list[int]]

    @property
    def best_runes(self) -> RunePage:
        return self.runes[0]

    @property
    def best_spells(self) -> list[int]:
        return self.spells[0] if self.spells else []


def _build_url(region: str, mode: str, champion_id: int, position: str) -> str:
    base = f"https://lol-api-champion.op.gg/api/{region}/champions/{mode}/{champion_id}"
    if mode in POSITIONLESS_MODES:
        token = POSITIONLESS_MODES[mode]      # the passed position is ignored here
        return f"{base}/{token}" if token else base
    return f"{base}/{position}"


def fetch_build(
    champion_id: int,
    position: str,
    region: str = DEFAULT_REGION,
    mode: str = DEFAULT_MODE,
) -> Build | None:
    """Runes + summoner spells for one lane, or None if there's no usable build.

    For positionless modes (ARAM, Arena) the `position` argument is ignored and
    the correct route token is used instead.
    """
    url = _build_url(region, mode, champion_id, position)
    try:
        data = get_json(url).get("data", {})
    except urllib.error.HTTPError as exc:
        if exc.code in (HTTPStatus.NOT_FOUND, HTTPStatus.UNPROCESSABLE_ENTITY):
            return None  # champion is not played in this position
        raise

    pages = [RunePage.from_opgg(r) for r in (data.get("runes") or [])]
    pages.sort(key=lambda p: p.play, reverse=True)
    if not pages:
        return None  # e.g. Arena builds have no runes at all

    raw_spells = sorted(
        data.get("summoner_spells") or [], key=lambda s: s.get("play", 0), reverse=True
    )
    spells = [s["ids"] for s in raw_spells if s.get("ids")]
    label = mode if mode in POSITIONLESS_MODES else position
    return Build(position=label, runes=pages, spells=spells)


def fetch_runes(
    champion_id: int,
    position: str,
    region: str = DEFAULT_REGION,
    mode: str = DEFAULT_MODE,
) -> list[RunePage]:
    """Convenience wrapper returning just the rune pages for a lane."""
    build = fetch_build(champion_id, position, region, mode)
    return build.runes if build else []


def best_build(
    champion_id: int,
    region: str = DEFAULT_REGION,
    mode: str = DEFAULT_MODE,
    preferred: str | None = None,
) -> Build:
    """Build for the champion's lane.

    If `preferred` is given (e.g. the lane the client assigned) it's tried
    first; otherwise the champion's most-played lane from the cached position
    index is used. Falls back to probing every lane if nothing else works.
    """
    if mode in POSITIONLESS_MODES:
        build = fetch_build(champion_id, "", region, mode)
        if build is None:
            raise RuntimeError(f"No {mode} build data for champion {champion_id}.")
        return build

    tried: list[str] = []
    candidates = ([preferred] if preferred else []) + champion_positions(champion_id)
    for pos in candidates:
        if pos in tried:
            continue
        tried.append(pos)
        build = fetch_build(champion_id, pos, region, mode)
        if build:
            return build
    # Fallback: probe every lane and keep the most played one.
    best: Build | None = None
    best_play = -1
    for pos in POSITIONS:
        if pos in tried:
            continue
        build = fetch_build(champion_id, pos, region, mode)
        if build:
            play = sum(p.play for p in build.runes)
            if play > best_play:
                best_play, best = play, build
    if best is None:
        raise RuntimeError("No build data found in any position.")
    return best


def best_position(
    champion_id: int, region: str, mode: str
) -> tuple[str, list[RunePage]]:
    """Champion's main lane and its rune pages (used by the CLI)."""
    build = best_build(champion_id, region, mode)
    return build.position, build.runes


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch OP.GG runes for a champion.")
    parser.add_argument(
        "champion", nargs="+", help="Champion name, e.g. 'Miss Fortune'"
    )
    parser.add_argument(
        "--position", choices=POSITIONS, help="Lane (auto-detect if omitted)"
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"OP.GG region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--mode", default=DEFAULT_MODE, help=f"game mode (default: {DEFAULT_MODE})"
    )
    parser.add_argument(
        "--all", action="store_true", help="Show all rune pages, not just the top one"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print LCU-ready JSON instead of text"
    )
    args = parser.parse_args(argv)

    name = " ".join(args.champion)
    try:
        champ = resolve_champion(name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.position:
        position = args.position
        pages = fetch_runes(champ["id"], position, args.region, args.mode)
        if not pages:
            print(f"No rune data for {champ['name']} ({position}).", file=sys.stderr)
            return 1
    else:
        position, pages = best_position(champ["id"], args.region, args.mode)

    if args.json:
        top = pages if args.all else pages[:1]
        out = [p.to_lcu_page(f"OP.GG {champ['name']} {position}") for p in top]
        print(json.dumps(out if args.all else out[0], indent=2))
        return 0

    print(
        f"\n{champ['name']}  (id {champ['id']})  -  {position.upper()}  [OP.GG {args.mode}]\n"
    )
    shown = pages if args.all else pages[:1]
    for i, page in enumerate(shown, 1):
        if args.all:
            print(f"Page {i}:")
        print(page.describe())
        print(f"  LCU selectedPerkIds: {page.selected_perk_ids}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
