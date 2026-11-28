"""EduPulse mail Function: the dispatcher's mail endpoint on DigitalOcean Functions.

The dispatcher POSTs one alert as JSON, {message_id, to, subject, text}, with
`Authorization: Bearer <MAIL_TOKEN>`; this Function sends it through SendGrid. MAIL_TOKEN may list
two tokens separated by a comma, new first, so a rotation never rejects a dispatcher still on the
old one (functions/README.md). The SendGrid
key lives only here (a Function environment variable), never in the Swarm cluster.

Status codes follow the dispatcher's retry rule (worker/message.py `is_permanent`): a 4xx other
than 429, 401 and 403 is permanent and the alert is marked failed, so only a problem with this one
message returns 422. A problem on our side or SendGrid's (wrong key, unverified or malformed
sender, outage) returns 502, and the dispatcher retries until it is fixed.

A resend after a dispatcher crash carries the same message_id, which becomes the email's
Message-ID header, so the receiving mailbox can recognise the duplicate. This Function keeps no
state between calls.

Standard library only, so the Function deploys without a build step.
"""
import hmac
import json
import os
import re
import urllib.error
import urllib.request

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
# The dispatcher waits at most 10 s per send; leave room for a cold start.
SENDGRID_TIMEOUT_SECONDS = 6
FIELDS = ("message_id", "to", "subject", "text")
# `<outbox-id@edupulse>`: one token, no spaces or line breaks, so it is safe as a header value.
MESSAGE_ID = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")


def main(event: dict, context=None) -> dict:
    # DigitalOcean passes the parsed JSON body as top-level keys of `event`, plus the request under
    # `http`; `context` (time left, memory) is not needed.
    if not authorized(header(event, "authorization"), os.environ["MAIL_TOKEN"].split(",")):
        return reply(401, "unauthorized")

    mail = {name: event.get(name) for name in FIELDS}
    problem = invalid(mail)
    if problem:
        return reply(422, problem)

    status, fields = post(sendgrid_payload(mail), os.environ["SENDGRID_API_KEY"])
    code = outcome(status, fields)
    # The outbox id identifies the alert; the address and the key are never logged here.
    print(json.dumps({"message_id": mail["message_id"], "sendgrid_status": status, "reply": code}))
    return reply(code, "sent" if code == 202 else f"sendgrid status {status}")


def header(event: dict, name: str) -> str:
    # Web functions receive the request headers under `http` (DigitalOcean lowercases the names;
    # matched ignoring case anyway).
    headers = (event.get("http") or {}).get("headers") or {}
    return next((value for key, value in headers.items() if key.lower() == name), "")


def authorized(value: str, tokens: list[str]) -> bool:
    scheme, _, presented = value.partition(" ")
    # compare_digest takes the same time wherever the strings differ, so timing reveals nothing.
    # Every configured token is compared, so the time does not depend on which one matched.
    matches = [hmac.compare_digest(presented.encode(), token.strip().encode()) for token in tokens if token.strip()]
    return scheme.lower() == "bearer" and any(matches)


def invalid(mail: dict) -> str | None:
    missing = [name for name, value in mail.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        return f"missing {', '.join(missing)}"
    if not MESSAGE_ID.fullmatch(mail["message_id"]):
        return "bad message_id"
    if "\n" in mail["subject"] or "\r" in mail["subject"]:
        return "bad subject"
    return None


def sendgrid_payload(mail: dict) -> dict:
    return {
        "personalizations": [{"to": [{"email": mail["to"]}]}],
        "from": {"email": os.environ["MAIL_FROM"], "name": os.environ.get("MAIL_FROM_NAME", "EduPulse")},
        "subject": mail["subject"],
        "content": [{"type": "text/plain", "value": mail["text"]}],
        "headers": {"Message-ID": mail["message_id"]},
        "custom_args": {"message_id": mail["message_id"]},
        # Keep the resource URL in the email as it is, not rewritten through sendgrid.net.
        "tracking_settings": {"click_tracking": {"enable": False}, "open_tracking": {"enable": False}},
    }


def post(payload: dict, api_key: str) -> tuple[int | None, list[str]]:
    """SendGrid's HTTP status (None if it could not be reached) and the fields its errors name."""
    request = urllib.request.Request(
        SENDGRID_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SENDGRID_TIMEOUT_SECONDS) as response:
            return response.status, []
    except urllib.error.HTTPError as exc:
        return exc.code, error_fields(exc)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, []


def error_fields(exc: urllib.error.HTTPError) -> list[str]:
    # SendGrid explains a 400 as {"errors": [{"field": "from.email", "message": ...}, ...]}.
    try:
        errors = json.loads(exc.read()).get("errors") or []
        return [error["field"] for error in errors if isinstance(error, dict) and isinstance(error.get("field"), str)]
    except (ValueError, AttributeError, OSError):
        return []


def outcome(status: int | None, fields: list[str] = ()) -> int:
    if status is not None and 200 <= status < 300:
        return 202  # SendGrid accepted it
    if status == 400 and any(field.split(".")[0] == "from" for field in fields):
        return 502  # MAIL_FROM is malformed: our configuration, so retry once it is fixed
    if status in (400, 413):
        return 422  # this message is malformed or too large: retrying will not help
    if status == 429:
        return 429  # rate limited: the dispatcher retries later
    return 502      # unreachable, wrong key, unverified sender, outage: fixable, so retry


def reply(code: int, detail: str) -> dict:
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": {"detail": detail}}
