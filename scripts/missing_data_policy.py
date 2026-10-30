"""Pure, fail-closed data availability policy for Ver.6 A-mode.

This module does not alter forecasts, feed, or manually entered workout data.
It produces status and recommendation eligibility for callers to integrate.
"""
from datetime import datetime, timedelta
from typing import Mapping

STATES = frozenset({"confirmed", "partial", "not_published", "not_entered",
                    "fetch_failed", "stale", "invalid", "not_applicable"})
CRITICAL = ("entries", "withdrawals", "odds")


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def classify_record(record: Mapping, now: str, max_age_minutes: int = 30):
    """Return a validated state, never infer success from a populated old value."""
    state = record.get("status", "not_entered")
    if state not in STATES:
        return "invalid"
    if state != "confirmed":
        return state
    checked = _timestamp(record.get("checked_at"))
    current = _timestamp(now)
    if checked is None or current is None or max_age_minutes < 0:
        return "invalid"
    age = current - checked
    if age < timedelta(0):
        return "invalid"
    return "stale" if age > timedelta(minutes=max_age_minutes) else "confirmed"


def workout_coverage(horse_ids, workout_by_horse):
    """Missing workouts are unknown, not poor workouts or zero-score workouts."""
    horse_ids = list(dict.fromkeys(horse_ids))
    if not horse_ids:
        return {"status": "absent", "available": 0, "total": 0}
    available = sum(
        1 for horse_id in horse_ids
        if workout_by_horse.get(horse_id, {}).get("status") == "confirmed"
        and bool(workout_by_horse[horse_id].get("sessions"))
    )
    return {
        "status": "full" if available == len(horse_ids) else "partial" if available else "absent",
        "available": available, "total": len(horse_ids)
    }


def decision(records, horse_ids, workout_by_horse, now, max_age_minutes=30):
    """Prediction continues; betting advice requires independently verified essentials.

    No numeric probability or expected value is manufactured here.
    """
    states = {key: classify_record(records.get(key, {}), now, max_age_minutes)
              for key in ("entries", "withdrawals", "odds", "history", "track")}
    coverage = workout_coverage(horse_ids, workout_by_horse)
    essential = all(states[k] == "confirmed" for k in CRITICAL)
    prediction_available = states["entries"] in ("confirmed", "stale", "partial")
    return {
        "prediction_allowed": prediction_available,
        "prediction_provisional": prediction_available and (
            not essential or states["history"] != "confirmed"
            or states["track"] != "confirmed" or coverage["status"] != "full"
        ),
        "bet_recommendation_allowed": bool(prediction_available and essential),
        "expected_value_allowed": bool(prediction_available and essential),
        "workout_model": "workout_candidate" if coverage["status"] == "full"
                         else "missing_workout_baseline",
        "workout_coverage": coverage,
        "data_states": states,
        "reason": [] if essential else [k for k in CRITICAL if states[k] != "confirmed"],
    }
