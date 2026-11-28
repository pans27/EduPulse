"""Unit tests for the mail Function. SendGrid is never called: urlopen is replaced.

Run from the repository root: uv run --with pytest pytest functions/tests
"""
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "packages" / "edupulse" / "send" / "__main__.py"
spec = importlib.util.spec_from_file_location("send_function", SOURCE)
send = importlib.util.module_from_spec(spec)
spec.loader.exec_module(send)

TOKEN = "test-token"
MAIL = {
    "message_id": "<0b6c4a52-1f7e-4c1a-9a7e-3f2f1d2c9e10@edupulse>",
    "to": "student@example.com",
    "subject": "[EduPulse] Assignment 1 handout is DOWN",
    "text": "Course: ECE1779\nState: SUSPECT -> DOWN",
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MAIL_TOKEN", TOKEN)
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
    monkeypatch.setenv("MAIL_FROM", "alerts@example.com")


class SendGrid:
    """Stands in for urlopen: records each request and answers with `status`."""

    def __init__(self, status=202, error=None):
        self.status, self.error, self.requests = status, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.status >= 400:
            raise urllib.error.HTTPError(request.full_url, self.status, "error", {}, io.BytesIO(b"{}"))
        response = io.BytesIO(b"")
        response.status = self.status
        return response


def call(monkeypatch, sendgrid, mail=MAIL, auth=f"Bearer {TOKEN}"):
    monkeypatch.setattr(send.urllib.request, "urlopen", sendgrid)
    return send.main({**mail, "http": {"headers": {"authorization": auth}, "method": "POST"}})


def test_accepted_alert_is_sent_with_its_message_id(monkeypatch):
    sendgrid = SendGrid(202)
    result = call(monkeypatch, sendgrid)

    assert result["statusCode"] == 202
    request = sendgrid.requests[0]
    assert request.full_url == send.SENDGRID_URL
    assert request.get_header("Authorization") == "Bearer SG.test"
    body = json.loads(request.data)
    assert body["headers"]["Message-ID"] == MAIL["message_id"]
    assert body["personalizations"] == [{"to": [{"email": "student@example.com"}]}]
    assert body["from"]["email"] == "alerts@example.com"
    assert body["content"] == [{"type": "text/plain", "value": MAIL["text"]}]
    assert body["tracking_settings"]["click_tracking"] == {"enable": False}


@pytest.mark.parametrize("auth", ["", "Bearer wrong", f"Basic {TOKEN}", TOKEN])
def test_wrong_or_missing_token_is_rejected_before_sendgrid(monkeypatch, auth):
    sendgrid = SendGrid()
    assert call(monkeypatch, sendgrid, auth=auth)["statusCode"] == 401
    assert sendgrid.requests == []


def test_header_name_is_matched_ignoring_case(monkeypatch):
    monkeypatch.setattr(send.urllib.request, "urlopen", SendGrid())
    result = send.main({**MAIL, "http": {"headers": {"Authorization": f"Bearer {TOKEN}"}}})
    assert result["statusCode"] == 202


def test_empty_configured_token_rejects_everything(monkeypatch):
    monkeypatch.setenv("MAIL_TOKEN", "")
    assert call(monkeypatch, SendGrid(), auth="Bearer ")["statusCode"] == 401


@pytest.mark.parametrize(
    "change",
    [
        {"to": ""},
        {"subject": None},
        {"message_id": "no-brackets@edupulse"},
        {"message_id": "<a@edupulse>\r\nBcc: x@example.com"},
        {"subject": "line\nBcc: x@example.com"},
    ],
)
def test_malformed_alert_is_permanent_and_not_sent(monkeypatch, change):
    sendgrid = SendGrid()
    assert call(monkeypatch, sendgrid, mail={**MAIL, **change})["statusCode"] == 422
    assert sendgrid.requests == []


@pytest.mark.parametrize(
    ("sendgrid_status", "reply"),
    [
        (400, 422),  # this message is bad: permanent
        (413, 422),
        (429, 429),  # rate limited: retried
        (401, 502),  # wrong key: our fault, retried once fixed
        (403, 502),  # unverified sender
        (500, 502),
        (503, 502),
    ],
)
def test_sendgrid_errors_map_to_the_dispatcher_retry_rule(monkeypatch, sendgrid_status, reply):
    assert call(monkeypatch, SendGrid(sendgrid_status))["statusCode"] == reply


def test_unreachable_sendgrid_is_retried(monkeypatch):
    sendgrid = SendGrid(error=urllib.error.URLError("no route"))
    assert call(monkeypatch, sendgrid)["statusCode"] == 502


def test_replies_agree_with_the_dispatcher(monkeypatch):
    # The dispatcher treats any 4xx except 429 as permanent (worker/message.py is_permanent).
    def permanent(code):
        return 400 <= code < 500 and code != 429

    assert not permanent(send.outcome(None))
    assert not permanent(send.outcome(401))
    assert not permanent(send.outcome(503))
    assert permanent(send.outcome(400))


def test_log_line_has_no_address_or_key(monkeypatch, capsys):
    call(monkeypatch, SendGrid(202))
    out = capsys.readouterr().out
    assert MAIL["message_id"] in out
    assert "student@example.com" not in out and "SG.test" not in out
