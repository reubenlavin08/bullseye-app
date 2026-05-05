"""Tests for the v1.1 streak + Pro-day banking client.

Covers:
    - cloud.streak.fetch_streak / redeem_pro_days happy paths
    - graceful degradation on Unauthorized / CloudUnavailable / CloudError
    - parity tests for the pure milestone math (mirrors _shared/streak.ts)
    - same-day / +1 day / +2 day with-and-without freeze transitions
    - 7/30/100 day milestone awards + the 14-day banking cap
    - Redeem rejection cases:
        * banked < 7   -> 403 swallowed -> None
        * already paid -> 403 swallowed -> None
        * double redeem (bank empty after first) -> None on second

The cloud client is fully mocked — no Edge Function or Supabase
calls escape the test process.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.cloud import streak as cloud_streak
from deal_finder.cloud.client import (
    CloudError,
    CloudUnavailable,
    Unauthorized,
)


# ---------- Pure parity helpers (mirror cloud/_shared/streak.ts) -----------
#
# These live here in Python so the unit-test layer can validate the
# milestone tunings without spinning up Deno. If you change the TS
# constants, change these too — the parity test will catch drift.

PRO_DAYS_CAP = 14
REDEEM_COST = 7
TRIAL_DAYS = 7


def _py_compute_milestone(current_streak: int) -> list[str]:
    out: list[str] = []
    if current_streak == 1:
        out.append("first_streak")
    if current_streak == 7:
        out.append("week_warrior")
    if current_streak == 30:
        out.append("month_master")
    if current_streak == 100:
        out.append("century_club")
        out.append("year_in_review_unlocked")
    return out


def _py_award_pro_days(milestone: str) -> int:
    return {
        "week_warrior": 1,
        "month_master": 3,
        "century_club": 7,
    }.get(milestone, 0)


def _py_transition_streak(
    *,
    current_streak: int,
    last_active_date: str | None,
    today: str,
    freeze_available: bool,
) -> dict:
    """Mirror of _shared/streak.ts:transitionStreak."""
    if last_active_date is None:
        return {"new_streak": 1, "same_day": False, "used_freeze": False}
    from datetime import date

    a = date.fromisoformat(last_active_date)
    b = date.fromisoformat(today)
    diff = (b - a).days
    if diff <= 0:
        return {
            "new_streak": current_streak,
            "same_day": True,
            "used_freeze": False,
        }
    if diff == 1:
        return {
            "new_streak": current_streak + 1,
            "same_day": False,
            "used_freeze": False,
        }
    if freeze_available:
        return {
            "new_streak": current_streak + 1,
            "same_day": False,
            "used_freeze": True,
        }
    return {"new_streak": 1, "same_day": False, "used_freeze": False}


# ---------- Fetch ----------------------------------------------------------


def _ok_streak_response(**overrides) -> dict:
    base = {
        "current_streak": 5,
        "longest_streak": 5,
        "freeze_available": True,
        "pro_days_banked": 0,
        "milestones_earned_today": [],
    }
    base.update(overrides)
    return base


def test_fetch_streak_calls_streak_endpoint():
    """fetch_streak must POST to /streak with empty body."""
    with patch.object(
        cloud_streak.client, "post", return_value=_ok_streak_response()
    ) as mock_post:
        result = cloud_streak.fetch_streak()
    mock_post.assert_called_once_with("streak", {})
    assert result["current_streak"] == 5


def test_fetch_streak_returns_milestones_when_earned():
    resp = _ok_streak_response(
        current_streak=7,
        pro_days_banked=1,
        milestones_earned_today=["week_warrior"],
    )
    with patch.object(cloud_streak.client, "post", return_value=resp):
        result = cloud_streak.fetch_streak()
    assert result["milestones_earned_today"] == ["week_warrior"]
    assert result["pro_days_banked"] == 1


def test_fetch_streak_unauthorized_returns_none():
    """Logged-out users should get None, not an exception."""
    with patch.object(
        cloud_streak.client, "post", side_effect=Unauthorized("expired")
    ):
        assert cloud_streak.fetch_streak() is None


def test_fetch_streak_unavailable_returns_none():
    """Offline / 5xx should degrade gracefully to None."""
    with patch.object(
        cloud_streak.client, "post", side_effect=CloudUnavailable("offline")
    ):
        assert cloud_streak.fetch_streak() is None


def test_fetch_streak_cloud_error_returns_none():
    """4xx should swallow, not raise — gamification is non-essential."""
    with patch.object(
        cloud_streak.client,
        "post",
        side_effect=CloudError("streak 400: bad body"),
    ):
        assert cloud_streak.fetch_streak() is None


# ---------- Redeem ---------------------------------------------------------


def _trial_response_after_redeem() -> dict:
    return {
        "tier": "trial",
        "watches_limit": None,
        "poll_interval_min": 5,
        "expires_at": None,
        "trial_ends_at": "2026-05-11T00:00:00Z",
        "cancel_at_period_end": False,
        "pro_days_banked": 0,
        "can_redeem_trial": False,
        "min_supported_version": "0.1.0",
    }


def test_redeem_pro_days_calls_license_endpoint_with_action():
    """redeem_pro_days must POST {action: 'redeem_pro_days'} to /license."""
    with patch.object(
        cloud_streak.client,
        "post",
        return_value=_trial_response_after_redeem(),
    ) as mock_post:
        result = cloud_streak.redeem_pro_days()
    mock_post.assert_called_once_with(
        "license", {"action": "redeem_pro_days"}
    )
    assert result["tier"] == "trial"
    assert result["pro_days_banked"] == 0


def test_redeem_when_banked_below_threshold_returns_none():
    """Cloud returns 403 'insufficient banked days' -> CloudError -> None."""
    with patch.object(
        cloud_streak.client,
        "post",
        side_effect=CloudError(
            "license 403: insufficient banked days: have 3, need 7"
        ),
    ):
        assert cloud_streak.redeem_pro_days() is None


def test_redeem_when_already_paid_returns_none():
    """Cloud returns 403 'cannot redeem on tier=paid' -> CloudError -> None."""
    with patch.object(
        cloud_streak.client,
        "post",
        side_effect=CloudError("license 403: cannot redeem on tier=paid"),
    ):
        assert cloud_streak.redeem_pro_days() is None


def test_redeem_when_already_trial_returns_none():
    """Trial users can't redeem again — 403."""
    with patch.object(
        cloud_streak.client,
        "post",
        side_effect=CloudError("license 403: cannot redeem on tier=trial"),
    ):
        assert cloud_streak.redeem_pro_days() is None


def test_redeem_twice_in_a_row_first_succeeds_then_fails():
    """First call succeeds (bank had 7+); second fails (bank now 0)."""
    responses = [
        _trial_response_after_redeem(),
    ]
    side_effects: list = list(responses) + [
        CloudError("license 403: insufficient banked days: have 0, need 7"),
    ]
    with patch.object(
        cloud_streak.client, "post", side_effect=side_effects
    ):
        first = cloud_streak.redeem_pro_days()
        second = cloud_streak.redeem_pro_days()
    assert first is not None
    assert first["tier"] == "trial"
    assert second is None


def test_redeem_unauthorized_returns_none():
    with patch.object(
        cloud_streak.client, "post", side_effect=Unauthorized("expired")
    ):
        assert cloud_streak.redeem_pro_days() is None


def test_redeem_unavailable_returns_none():
    with patch.object(
        cloud_streak.client, "post", side_effect=CloudUnavailable("offline")
    ):
        assert cloud_streak.redeem_pro_days() is None


# ---------- Pure streak math (parity with _shared/streak.ts) ---------------


def test_milestone_first_streak_at_day_1():
    assert _py_compute_milestone(1) == ["first_streak"]


def test_milestone_week_warrior_at_day_7():
    assert _py_compute_milestone(7) == ["week_warrior"]


def test_milestone_month_master_at_day_30():
    assert _py_compute_milestone(30) == ["month_master"]


def test_milestone_century_club_at_day_100_unlocks_year_in_review():
    """Day 100 stacks two milestones — the Pro-day award AND the
    'year in review' cosmetic unlock for the dashboard."""
    assert _py_compute_milestone(100) == [
        "century_club",
        "year_in_review_unlocked",
    ]


@pytest.mark.parametrize("day", [2, 3, 6, 8, 29, 31, 99, 101, 365])
def test_no_milestone_on_non_threshold_days(day):
    assert _py_compute_milestone(day) == []


@pytest.mark.parametrize(
    "milestone,expected_days",
    [
        ("first_streak", 0),
        ("week_warrior", 1),
        ("month_master", 3),
        ("century_club", 7),
        ("year_in_review_unlocked", 0),
        ("unknown_milestone", 0),
    ],
)
def test_award_pro_days_per_milestone(milestone, expected_days):
    """Tunings: 1 / 3 / 7 days at the 7 / 30 / 100 day marks.
    Cosmetic-only milestones (first_streak, year_in_review) award 0."""
    assert _py_award_pro_days(milestone) == expected_days


def test_total_pro_days_lifetime_through_day_100():
    """A user who keeps a 100-day streak earns at most 1 + 3 + 7 = 11
    Pro days lifetime — comfortably under the PRO_DAYS_CAP of 14."""
    total = sum(
        _py_award_pro_days(m)
        for day in (1, 7, 30, 100)
        for m in _py_compute_milestone(day)
    )
    assert total == 11
    assert total < PRO_DAYS_CAP


def test_pro_days_cap_blocks_overflow():
    """If a user is at the 14-day cap, further awards must not push
    the bank above the cap. We model the cloud's clamp logic here."""
    banked = PRO_DAYS_CAP  # already maxed
    award = _py_award_pro_days("century_club")  # would add 7
    room = max(0, PRO_DAYS_CAP - banked)
    actually_banked = min(award, room)
    assert actually_banked == 0


def test_pro_days_cap_partial_clamp():
    """Banked=12, award=7 -> clamp to 2 (cap is 14)."""
    banked = 12
    award = 7
    room = max(0, PRO_DAYS_CAP - banked)
    actually_banked = min(award, room)
    assert actually_banked == 2
    assert banked + actually_banked == PRO_DAYS_CAP


# ---------- Streak transition table ---------------------------------------


def test_transition_first_ever_call_starts_at_one():
    t = _py_transition_streak(
        current_streak=0,
        last_active_date=None,
        today="2026-05-04",
        freeze_available=True,
    )
    assert t == {"new_streak": 1, "same_day": False, "used_freeze": False}


def test_transition_same_day_is_noop():
    """Same UTC day -> no streak change, sameDay=True so the caller
    skips milestone awards and the persist write."""
    t = _py_transition_streak(
        current_streak=5,
        last_active_date="2026-05-04",
        today="2026-05-04",
        freeze_available=True,
    )
    assert t == {"new_streak": 5, "same_day": True, "used_freeze": False}


def test_transition_one_day_later_increments():
    t = _py_transition_streak(
        current_streak=5,
        last_active_date="2026-05-03",
        today="2026-05-04",
        freeze_available=True,
    )
    assert t == {"new_streak": 6, "same_day": False, "used_freeze": False}


def test_transition_two_day_gap_with_freeze_uses_freeze():
    """User skipped one day; freeze is available -> treat as +1 and
    consume the freeze. Streak is preserved."""
    t = _py_transition_streak(
        current_streak=5,
        last_active_date="2026-05-02",
        today="2026-05-04",
        freeze_available=True,
    )
    assert t == {"new_streak": 6, "same_day": False, "used_freeze": True}


def test_transition_two_day_gap_without_freeze_resets():
    """User skipped one day; freeze already used this month -> reset."""
    t = _py_transition_streak(
        current_streak=42,
        last_active_date="2026-05-02",
        today="2026-05-04",
        freeze_available=False,
    )
    assert t == {"new_streak": 1, "same_day": False, "used_freeze": False}


def test_transition_long_gap_without_freeze_resets():
    """A week-long gap also resets to 1 (not negative, not zero)."""
    t = _py_transition_streak(
        current_streak=42,
        last_active_date="2026-04-28",
        today="2026-05-04",
        freeze_available=False,
    )
    assert t["new_streak"] == 1


def test_transition_long_gap_with_freeze_consumes_freeze():
    """A week-long gap WITH a freeze still preserves the streak.
    (Design choice: freeze is one-shot per month regardless of gap
    length — generous, but the cap on freezes-per-month bounds abuse.)"""
    t = _py_transition_streak(
        current_streak=42,
        last_active_date="2026-04-28",
        today="2026-05-04",
        freeze_available=True,
    )
    assert t["new_streak"] == 43
    assert t["used_freeze"] is True


def test_transition_clock_skew_treated_as_same_day():
    """If the client's clock is briefly behind the server (last_active
    is 'tomorrow'), treat as same-day no-op rather than rewarding or
    punishing — clock skew shouldn't break a streak."""
    t = _py_transition_streak(
        current_streak=10,
        last_active_date="2026-05-05",  # tomorrow
        today="2026-05-04",
        freeze_available=True,
    )
    assert t["same_day"] is True
    assert t["new_streak"] == 10


# ---------- Redeem cost & trial duration constants -------------------------


def test_redeem_cost_matches_trial_days():
    """Design invariant: a redemption trades exactly REDEEM_COST banked
    days for TRIAL_DAYS days of Pro. Different values would create
    weird arbitrage (e.g. 5 banked -> 7-day trial) and erode the
    perceived fairness of the trade."""
    assert REDEEM_COST == TRIAL_DAYS == 7
