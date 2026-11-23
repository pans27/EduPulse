from datetime import datetime, timedelta, timezone
from unittest import TestCase

from worker.states import ALERT_STATES, FAILURE_THRESHOLD, Resource, transition

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def resource(state=None, failures=0, interval=60, release_at=None, incident="same"):
    # By default a resource in an alert state has an open incident recording that state.
    open_incident = (state if state in ALERT_STATES else None) if incident == "same" else incident
    return Resource(state, failures, interval, release_at, open_incident)


def run(outcomes, start=None, **kwargs):
    # Apply outcomes in order, tracking the open incident the way the worker's transaction does.
    current, steps = start or resource(**kwargs), []
    for outcome in outcomes:
        step = transition(current, outcome, NOW)
        steps.append(step)
        incident = current.open_incident
        if step.incident in ("open", "update"):
            incident = step.state
        elif step.incident == "resolve":
            incident = None
        current = Resource(step.state, step.consecutive_failures, current.check_interval_seconds,
                           current.release_at, incident)
    return steps


class TransitionTests(TestCase):
    def test_first_check_ok(self):
        step = transition(resource(), "ok", NOW)
        self.assertEqual((step.state, step.consecutive_failures, step.incident), ("HEALTHY", 0, None))
        self.assertEqual(step.next_check_at, NOW + timedelta(seconds=60))

    def test_down_only_after_three_consecutive_failures(self):
        steps = run(["error"] * 4, start=resource("HEALTHY"))
        self.assertEqual([s.state for s in steps], ["SUSPECT", "SUSPECT", "DOWN", "DOWN"])
        self.assertEqual([s.consecutive_failures for s in steps], [1, 2, 3, 4])
        self.assertEqual([s.incident for s in steps], [None, None, "open", None])
        self.assertEqual(FAILURE_THRESHOLD, 3)

    def test_suspect_rechecks_sooner_but_never_later_than_interval(self):
        self.assertEqual(transition(resource("HEALTHY"), "error", NOW).next_check_at, NOW + timedelta(seconds=15))
        short = transition(resource("HEALTHY", interval=5), "error", NOW)
        self.assertEqual(short.next_check_at, NOW + timedelta(seconds=5))
        down = run(["error"] * 3, start=resource("HEALTHY"))[-1]
        self.assertEqual(down.next_check_at, NOW + timedelta(seconds=60))

    def test_transient_failures_between_successes_never_alert(self):
        # The fake site's fail_every_k mode: never two failures in a row.
        steps = run(["ok", "ok", "error", "ok", "error", "ok", "error", "ok"])
        self.assertNotIn("DOWN", [s.state for s in steps])
        self.assertTrue(all(s.incident is None for s in steps))
        self.assertEqual(steps[-1].state, "HEALTHY")

    def test_recovery_resolves_the_incident(self):
        steps = run(["error", "error", "error", "ok"], start=resource("HEALTHY"))
        self.assertEqual(steps[-1].state, "HEALTHY")
        self.assertEqual(steps[-1].consecutive_failures, 0)
        self.assertEqual(steps[-1].incident, "resolve")

    def test_slow_is_degraded_without_incident(self):
        step = transition(resource("HEALTHY"), "slow", NOW)
        self.assertEqual((step.state, step.incident), ("DEGRADED", None))
        self.assertEqual(transition(resource("DOWN", 3), "slow", NOW).incident, "resolve")

    def test_auth_required_is_immediate(self):
        step = transition(resource("HEALTHY"), "auth", NOW)
        self.assertEqual((step.state, step.consecutive_failures, step.incident), ("AUTH_REQUIRED", 0, "open"))
        self.assertEqual(transition(resource("DOWN", 3), "auth", NOW).incident, "update")
        self.assertIsNone(transition(resource("AUTH_REQUIRED"), "auth", NOW).incident)

    def test_404_without_release_time_is_down(self):
        steps = run(["not_found"] * 3, start=resource("HEALTHY"))
        self.assertEqual(steps[-1].state, "DOWN")

    def test_404_after_release_is_missing(self):
        released = NOW - timedelta(minutes=5)
        steps = run(["not_found"] * 3, start=resource("HEALTHY", release_at=released))
        self.assertEqual([s.state for s in steps], ["SUSPECT", "SUSPECT", "MISSING"])
        self.assertEqual(steps[-1].incident, "open")

    def test_errors_after_release_are_down_not_missing(self):
        released = NOW - timedelta(minutes=5)
        steps = run(["error"] * 3, start=resource("HEALTHY", release_at=released))
        self.assertEqual(steps[-1].state, "DOWN")

    def test_404_before_release_is_expected(self):
        release = NOW + timedelta(hours=2)
        step = transition(resource(None, release_at=release), "not_found", NOW)
        self.assertEqual((step.state, step.consecutive_failures, step.incident), (None, 0, None))
        self.assertEqual(step.next_check_at, release)

    def test_early_publish_is_healthy(self):
        step = transition(resource(None, release_at=NOW + timedelta(hours=2)), "ok", NOW)
        self.assertEqual(step.state, "HEALTHY")

    def test_changed(self):
        step = transition(resource("HEALTHY"), "ok", NOW)
        self.assertFalse(step.changed("HEALTHY"))
        self.assertTrue(transition(resource(None), "ok", NOW).changed(None))

    def test_suspect_after_an_alert_keeps_the_incident_open(self):
        # AUTH_REQUIRED, then a timeout: still failing, so no recovery email.
        steps = run(["error"], start=resource("AUTH_REQUIRED"))
        self.assertEqual((steps[0].state, steps[0].incident), ("SUSPECT", None))

    def test_incident_resolves_only_on_confirmed_recovery(self):
        steps = run(["auth", "error", "error", "ok"], start=resource("HEALTHY"))
        self.assertEqual([s.state for s in steps], ["AUTH_REQUIRED", "SUSPECT", "SUSPECT", "HEALTHY"])
        self.assertEqual([s.incident for s in steps], ["open", None, None, "resolve"])

    def test_failing_on_after_an_alert_updates_the_same_incident(self):
        # AUTH_REQUIRED, then 3 errors: the open incident moves to DOWN, no second "opened" email.
        steps = run(["auth", "error", "error", "error"], start=resource("HEALTHY"))
        self.assertEqual([s.incident for s in steps], ["open", None, None, "update"])
        self.assertEqual(steps[-1].state, "DOWN")

    def test_returning_to_the_incident_state_is_not_an_update(self):
        steps = run(["error", "auth"], start=resource("AUTH_REQUIRED"))
        self.assertEqual([s.incident for s in steps], [None, None])

    def test_suspect_from_healthy_never_resolves_anything(self):
        steps = run(["error", "ok"], start=resource("HEALTHY"))
        self.assertEqual([s.incident for s in steps], [None, None])
