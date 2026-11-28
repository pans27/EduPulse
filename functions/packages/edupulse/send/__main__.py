"""EduPulse mail Function: the dispatcher's mail endpoint on DigitalOcean Functions.

The dispatcher POSTs one alert as JSON, {message_id, to, subject, text}, with
`Authorization: Bearer <MAIL_TOKEN>`; this Function sends it through SendGrid. The SendGrid
key lives only here (a Function environment variable), never in the Swarm cluster.

Status codes follow the dispatcher's retry rule (worker/message.py `is_permanent`): a 4xx other
than 429 is permanent and the alert is marked failed, so only a problem with this one message
returns 4xx. A problem on our side or SendGrid's (wrong key, unverified sender, outage) returns
502, and the dispatcher retries until it is fixed.

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
    if not authorized(header(event, "authorization"), os.environ["MAIL_TOKEN"]):
        return reply(401, "unauthorized")

    mail = {name: event.get(name) for name in FIELDS}
    problem = invalid(mail)
    if problem:
        return reply(422, problem)

    status = post(sendgrid_payload(mail), os.environ["SENDGRID_API_KEY"])
    code = outcome(status)
    # The outbox id identifies the alert; the address and the key are never logged here.
    print(json.dumps({"message_id": mail["message_id"], "sendgrid_status": status, "reply": code}))
    return reply(code, "sent" if code == 202 else f"sendgrid status {status}")


def header(event: dict, name: str) -> str:
    # Web functions receive the request headers under `http` (DigitalOcean lowercases the names;
    # matched ignoring case anyway).
    headers = (event.get("http") or {}).get("headers") or {}
    return next((value for key, value in headers.items() if key.lower() == name), "")


def authorized(value: str, token: str) -> bool:
    scheme, _, presented = value.partition(" ")
    # compare_digest takes the same time wherever the strings differ, so timing reveals nothing.
    return scheme.lower() == "bearer" and bool(token) and hmac.compare_digest(presented.encode(), token.encode())


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


def post(payload: dict, api_key: str) -> int | None:
    """SendGrid's HTTP status, or None if it could not be reached."""
    request = urllib.request.Request(
        SENDGRID_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SENDGRID_TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def outcome(status: int | None) -> int:
    if status is not None and 200 <= status < 300:
        return 202  # SendGrid accepted it
    if status in (400, 413):
        return 422  # this message is malformed or too large: retrying will not help
    if status == 429:
        return 429  # rate limited: the dispatcher retries later
    return 502      # unreachable, wrong key, unverified sender, outage: fixable, so retry


def reply(code: int, detail: str) -> dict:
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": {"detail": detail}}
