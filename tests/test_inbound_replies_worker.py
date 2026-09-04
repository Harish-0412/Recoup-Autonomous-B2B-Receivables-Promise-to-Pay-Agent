"""Tests for Cloudflare Email Worker inbound integration on /api/v1/replies.

Verifies:
- Svix-signature generation and verification on incoming worker payloads
- Fallback authentication via x-worker-secret header
- Rejection of forged or unsigned requests (401)
- Handling of structured MIME worker payloads with tagged reply-to addresses
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app
from app.services.reply_routing import reply_address

SECRET = "whsec_test_secret_for_inbound_email_worker_123"
ADDRESS_SECRET = "test-address-secret"
DOMAIN = "reply.recoup.xyz"


def compute_svix(body: bytes, secret: str, message_id: str, timestamp: str) -> str:
    raw = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except Exception:
        key = raw.encode()
    signed = b"%s.%s.%s" % (message_id.encode(), timestamp.encode(), body)
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def configure_secrets(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("REPLY_ADDRESS_SECRET", ADDRESS_SECRET)
    monkeypatch.setenv("REPLY_INBOUND_DOMAIN", DOMAIN)
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_unsigned_request_is_rejected(client: AsyncClient, configure_secrets):
    response = await client.post(
        "/api/v1/replies",
        json={"from": "customer@example.com", "text": "We will pay on Friday"},
    )
    assert response.status_code == 401
    assert response.json()["reason"] == "invalid_signature"


@pytest.mark.asyncio
async def test_tampered_signature_is_rejected(client: AsyncClient, configure_secrets):
    body = json.dumps({"from": "customer@example.com", "text": "We will pay"}).encode()
    headers = {
        "Content-Type": "application/json",
        "svix-id": "msg_001",
        "svix-timestamp": str(int(time.time())),
        "svix-signature": "v1,invalidBase64Signature==",
    }
    response = await client.post("/api/v1/replies", content=body, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_worker_svix_signature_passes_auth(client: AsyncClient, configure_secrets):
    message_id = f"cf_msg_{int(time.time())}"
    timestamp = str(int(time.time()))
    payload = {
        "message_id": message_id,
        "from": "customer@example.com",
        "to": ["reply+INV-UNKNOWN.0000000000000000@reply.recoup.xyz"],
        "subject": "Re: Payment",
        "text": "Hello, we got your email.",
    }
    body = json.dumps(payload).encode()
    signature = compute_svix(body, SECRET, message_id, timestamp)

    headers = {
        "Content-Type": "application/json",
        "svix-id": message_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{signature}",
    }

    response = await client.post("/api/v1/replies", content=body, headers=headers)
    # Auth passed! It proceeded to ingest, and since INV-UNKNOWN is not a valid signed tag,
    # it queued it for human review rather than rejecting as unauthorized.
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert "Could not match this reply to an invoice" in data["reason"]


@pytest.mark.asyncio
async def test_worker_fallback_secret_header_passes_auth(client: AsyncClient, configure_secrets):
    message_id = f"cf_fallback_{int(time.time())}"
    payload = {
        "message_id": message_id,
        "from": "customer@example.com",
        "to": ["reply+INV-UNKNOWN.0000000000000000@reply.recoup.xyz"],
        "subject": "Re: Payment",
        "text": "Hello, we got your email.",
    }
    body = json.dumps(payload).encode()

    headers = {
        "Content-Type": "application/json",
        "x-worker-secret": SECRET,
    }

    response = await client.post("/api/v1/replies", content=body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_duplicate_reply_is_deduplicated(client: AsyncClient, configure_secrets):
    message_id = f"cf_dedupe_{int(time.time())}"
    payload = {
        "message_id": message_id,
        "from": "customer@example.com",
        "to": ["reply+INV-UNKNOWN.0000000000000000@reply.recoup.xyz"],
        "subject": "Re: Payment",
        "text": "Hello, we got your email.",
    }
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "x-worker-secret": SECRET,
    }

    # First post
    res1 = await client.post("/api/v1/replies", content=body, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["status"] == "queued"

    # Second post with identical message_id -> duplicate detected!
    res2 = await client.post("/api/v1/replies", content=body, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_inbound_opt_out_is_auto_handled(client: AsyncClient, configure_secrets):
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models import Customer

    # Pick a real customer from DB so opt-out suppression records properly
    async with async_session_maker() as session:
        cust = (await session.scalars(select(Customer).limit(1))).first()
        from_email = cust.email if cust and cust.email else "customer@example.com"

    message_id = f"cf_optout_{int(time.time())}"
    payload = {
        "message_id": message_id,
        "from": from_email,
        "to": ["reply+INV-UNKNOWN.0000000000000000@reply.recoup.xyz"],
        "subject": "Stop emailing",
        "text": "Please stop sending these reminders, unsubscribe.",
    }
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "x-worker-secret": SECRET,
    }

    res = await client.post("/api/v1/replies", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["intent"] == "OPT_OUT"
    assert data["confidence"] == 1.0


@pytest.mark.asyncio
async def test_inbound_reply_with_tagged_address_resolves_invoice(
    client: AsyncClient, configure_secrets
):
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models import Invoice

    async with async_session_maker() as session:
        invoice = (await session.scalars(select(Invoice).limit(1))).first()
        assert invoice is not None
        invoice_id = invoice.invoice_id

    # Mint the genuine tagged address
    tagged_to = reply_address(invoice_id, secret=ADDRESS_SECRET, domain=DOMAIN)

    message_id = f"cf_tagged_{int(time.time())}"
    payload = {
        "message_id": message_id,
        "from": "customer@example.com",
        "to": [tagged_to],
        "subject": f"Re: Overdue Invoice {invoice_id}",
        "text": "We will pay this on Friday, apologies for delay.",
    }
    body = json.dumps(payload).encode()
    signature = compute_svix(body, SECRET, message_id, str(int(time.time())))
    headers = {
        "Content-Type": "application/json",
        "svix-id": message_id,
        "svix-timestamp": str(int(time.time())),
        "svix-signature": f"v1,{signature}",
    }

    res = await client.post("/api/v1/replies", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    # It must NOT be rejected as unroutable ("Could not match this reply to an invoice")
    assert "Could not match this reply to an invoice" not in data.get("reason", "")
    assert data["reply_id"] == message_id
