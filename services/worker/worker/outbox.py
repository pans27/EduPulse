"""What goes into a notification_outbox row. Pure, so the message content is unit-tested.

The worker inserts one row per eligible recipient in the same transaction as the state change; the
dispatcher only formats and sends what is stored here, so an email always describes the state
that actually committed.
"""
import json
from datetime import datetime

from .states import IncidentAction

# NOTIFY channel that wakes the dispatcher; it also polls, so a missed NOTIFY only adds delay.
OUTBOX_CHANNEL = "outbox_new"

KINDS: dict[str, str] = {
    "open": "incident_opened",
    "update": "incident_updated",
    "resolve": "incident_resolved",
}

# Recipients: active course staff and active students who opted in.
INSERT_SQL = """
INSERT INTO notification_outbox (resource_id, incident_id, recipient_email, kind, payload)
SELECT $1, $2, u.email, $3, $4::jsonb
FROM course_members m JOIN users u ON u.id = m.user_id
WHERE m.course_id = $5 AND u.is_active
  AND (m.role = 'staff' OR (m.role = 'student' AND m.email_opt_in))
"""


def kind_for(action: IncidentAction) -> str | None:
    return KINDS.get(action) if action else None


def payload(
    *, course_id: str, resource_name: str, url: str, before: str | None, after: str | None,
    detail: str, at: datetime,
) -> str:
    return json.dumps({
        "course_id": course_id,
        "resource": resource_name,
        "url": url,
        "previous_state": before,
        "state": after,
        "detail": detail,
        "at": at.isoformat(),
    })
