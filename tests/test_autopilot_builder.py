"""Tests for the headless make_autopilot builder (shared by CLI + GUI)."""

from __future__ import annotations

import pytest

from src.autopilot import UnknownLaneError, make_autopilot

# A resolve() that maps a name to a fixed (id, display) without touching opgg.
_IDS = {"ahri": (103, "Ahri"), "garen": (86, "Garen"), "zed": (238, "Zed")}


def _resolve(name):
    return _IDS[name.lower()]


def test_watch_only_when_start_false():
    ap = make_autopilot("flex", start=False, resolve=_resolve)
    assert ap.mode == "flex" and ap.start is False  # watch + pick/ban


def test_auto_start_default_true():
    ap = make_autopilot("solo", resolve=_resolve)
    assert ap.start is True


def test_lanes_and_bans_resolved():
    ap = make_autopilot(
        "flex",
        lanes=[("mid", ["ahri", "zed"])],
        bans=["garen"],
        resolve=_resolve,
    )
    assert ap.lanes == {"MIDDLE": [103, 238]}
    assert ap.lane_names == {"MIDDLE": ["Ahri", "Zed"]}
    assert ap.lane_order == ["MIDDLE"]
    assert ap.bans == [86] and ap.ban_names == ["Garen"]


def test_unknown_lane_raises():
    with pytest.raises(UnknownLaneError):
        make_autopilot("flex", lanes=[("bananas", ["ahri"])], resolve=_resolve)


def test_duplicate_lane_raises():
    with pytest.raises(UnknownLaneError):
        make_autopilot(
            "flex",
            lanes=[("mid", ["ahri"]), ("middle", ["zed"])],
            resolve=_resolve,
        )


def test_multiple_lanes_and_bans_preserve_order():
    # Mirrors the GUI arming a full per-lane config + ordered ban list.
    ap = make_autopilot(
        "solo",
        lanes=[("top", ["garen"]), ("mid", ["ahri", "zed"])],
        bans=["zed", "garen"],
        resolve=_resolve,
    )
    assert ap.lane_order == ["TOP", "MIDDLE"]
    assert ap.lanes == {"TOP": [86], "MIDDLE": [103, 238]}
    assert ap.bans == [238, 86] and ap.ban_names == ["Zed", "Garen"]


def test_no_lanes_or_bans_arms_empty_config():
    # Arming with nothing configured (e.g. watch-only ARAM) is valid: the
    # autopilot just has nothing to draft.
    ap = make_autopilot("aram", resolve=_resolve)
    assert ap.lanes == {} and ap.bans == [] and ap.lane_order == []
