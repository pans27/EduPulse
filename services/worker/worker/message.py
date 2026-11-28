"""Alert email content and retry policy for the dispatcher. Pure, so both are unit-tested."""
from datetime import timedelta

# Retry schedule: 10 s, 20 s, 40 s ... capped at 15 min, then give up after MAX_ATTEMPTS.
BASE_DELAY_SECONDS = 10
MAX_DELAY_SECONDS = 15 * 60
MAX_ATTEMPTS = 8

SUBJECTS = {
    "incident_opened": "[EduPulse] {resource} is {state}",
    "incident_updated": "[EduPulse] {resource} is now {state}",
    "incident_resolved": "[EduPulse] {resource} is back ({state})",
}


def message_id(outbox_id: str) -> str:
    # The outbox row id doubles as the email Message-ID. A resend after a crash carries the same
    # id, so the duplicate can be recognised. The mock sink drops it; Gmail does not (tested
    # Nov 27 through the Function: both copies were shown).
    return f"<{outbox_id}@edupulse>"


def render(outbox_id: str, recipient: str, kind: str, payload: dict) -> dict:
    subject = SUBJECTS[kind].format(resource=payload["resource"], state=payload["state"])
    lines = [
        f"Course: {payload['course_id']}",
        f"Resource: {payload['resource']}",
        f"URL: {payload['url']}",
        f"State: {payload['previous_state'] or 'unchecked'} -> {payload['state']}",
        f"Detail: {payload['detail']}",
        f"At: {payload['at']}",
    ]
    return {"message_id": message_id(outbox_id), "to": recipient, "subject": subject, "text": "\n".join(lines)}


def retry_delay(attempts: int) -> timedelta:
    """Delay before the next try after `attempts` failed tries (attempts >= 1)."""
    return timedelta(seconds=min(BASE_DELAY_SECONDS * 2 ** (attempts - 1), MAX_DELAY_SECONDS))


def is_permanent(status: int) -> bool:
    # A 4xx means the request itself is wrong (bad address, rejected payload): retrying will not
    # help. 429 and 5xx are the provider's problem and are retried.
    return 400 <= status < 500 and status != 429
