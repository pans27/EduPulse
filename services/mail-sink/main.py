"""Mock mail endpoint: stands in for the DigitalOcean Function + SendGrid during tests and demos.

It accepts the same JSON the dispatcher will send to the Function, keeps every message in
memory, and drops a resend with a Message-ID it has already delivered, so the crash test can
count duplicates. (Real mailboxes do not do this: Gmail showed both copies.) Admin endpoints let a test make it fail on purpose to exercise retries.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="mail-sink")


class Mail(BaseModel):
    message_id: str
    to: str
    subject: str
    text: str


delivered: dict[str, Mail] = {}  # message_id -> first copy
received = 0                     # every accepted POST, duplicates included
duplicates: dict[str, int] = {}  # message_id -> extra copies dropped
fail_next = 0                    # answer this many sends with 503


@app.post("/send")
async def send(mail: Mail) -> dict:
    global received, fail_next
    if fail_next > 0:
        fail_next -= 1
        raise HTTPException(503, "injected failure")
    received += 1
    if mail.message_id in delivered:
        duplicates[mail.message_id] = duplicates.get(mail.message_id, 0) + 1
        return {"accepted": True, "duplicate": True}
    delivered[mail.message_id] = mail
    return {"accepted": True, "duplicate": False}


@app.get("/messages")
async def messages() -> list[Mail]:
    return list(delivered.values())


@app.get("/stats")
async def stats() -> dict:
    return {"received": received, "delivered": len(delivered), "duplicates_dropped": sum(duplicates.values()),
            "duplicate_ids": duplicates}


@app.post("/admin/fail")
async def fail(count: int = 1) -> dict:
    global fail_next
    fail_next = count
    return {"fail_next": fail_next}


@app.post("/admin/reset")
async def reset() -> dict:
    global received, fail_next
    delivered.clear(); duplicates.clear(); received = 0; fail_next = 0
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
