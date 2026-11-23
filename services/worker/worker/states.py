"""Health state machine: (current state, outcome) -> next state, incident action, next check.

Pure function, so the transition table is unit-tested directly. The worker applies the result
in one transaction together with the check result, the incident change and the NOTIFY.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .classify import Outcome

State = Literal["HEALTHY", "DEGRADED", "SUSPECT", "DOWN", "AUTH_REQUIRED", "MISSING"]
IncidentAction = Literal["open", "update", "resolve"] | None

# Consecutive failures before a resource is confirmed DOWN (the proposal's N, tunable).
FAILURE_THRESHOLD = 3
# While SUSPECT, recheck sooner than the normal interval so a real outage is confirmed in
# about 2 x 15 s instead of 2 full intervals.
SUSPECT_RECHECK_SECONDS = 15

# States that mean "staff should know": entering one opens an incident, leaving one resolves it.
ALERT_STATES: frozenset[str] = frozenset({"DOWN", "MISSING", "AUTH_REQUIRED"})


@dataclass(frozen=True)
class Resource:
    state: State | None  # None until the first check
    consecutive_failures: int
    check_interval_seconds: int
    release_at: datetime | None = None


@dataclass(frozen=True)
class Transition:
    state: State | None
    consecutive_failures: int
    next_check_at: datetime
    incident: IncidentAction

    def changed(self, before: State | None) -> bool:
        return self.state != before


def _incident_action(before: State | None, after: State | None) -> IncidentAction:
    was_alert, is_alert = before in ALERT_STATES, after in ALERT_STATES
    if is_alert and not was_alert:
        return "open"
    if was_alert and not is_alert:
        return "resolve"
    if was_alert and is_alert and before != after:
        return "update"  # e.g. DOWN -> AUTH_REQUIRED: same incident, new state
    return None


def transition(resource: Resource, outcome: Outcome, now: datetime) -> Transition:
    interval = timedelta(seconds=resource.check_interval_seconds)
    released = resource.release_at is None or now >= resource.release_at

    if outcome == "not_found" and not released:
        # Before the scheduled release a 404 is expected, not a failure. Nothing changes;
        # look again at the release time.
        return Transition(resource.state, 0, resource.release_at, None)

    failures = 0
    if outcome == "ok":
        state: State = "HEALTHY"
    elif outcome == "slow":
        state = "DEGRADED"
    elif outcome == "auth":
        # Deterministic, not transient: no need to wait for repeated failures.
        state = "AUTH_REQUIRED"
    else:  # "error" or "not_found" after release
        failures = resource.consecutive_failures + 1
        if failures < FAILURE_THRESHOLD:
            state = "SUSPECT"
        elif outcome == "not_found" and resource.release_at is not None:
            state = "MISSING"  # released on schedule, but the material is not there
        else:
            state = "DOWN"

    if state == "SUSPECT":
        next_check_at = now + min(interval, timedelta(seconds=SUSPECT_RECHECK_SECONDS))
    else:
        next_check_at = now + interval
    return Transition(state, failures, next_check_at, _incident_action(resource.state, state))
