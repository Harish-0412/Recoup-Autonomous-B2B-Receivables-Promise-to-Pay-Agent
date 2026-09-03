"""Tagged reply addresses and inbound webhook signatures.

Both are trust boundaries. The address decides *which invoice* a reply is
about, and the signature decides whether the request is real at all -- and the
endpoint behind them can create a promise, which silences the agent on a live
debt. A forged either one is a way to stop collection on any invoice in the
book.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.services.reply_routing import (
    WEBHOOK_TOLERANCE_SECONDS,
    reply_address,
    resolve_from_recipients,
    resolve_invoice_id,
    verify_svix_signature,
)

SECRET = "test-address-secret"
DOMAIN = "reply.recoup.test"


# --- tagged addresses -------------------------------------------------------


def test_an_address_round_trips_to_its_invoice():
    address = reply_address("INV-2026-00042", secret=SECRET, domain=DOMAIN)

    assert address.startswith("reply+INV-2026-00042.")
    assert address.endswith(f"@{DOMAIN}")
    assert resolve_invoice_id(address, secret=SECRET) == "INV-2026-00042"


def test_a_forged_address_does_not_resolve():
    """The property the signature exists for.

    Without it, anyone who worked out the format could post a reply naming any
    invoice and have a promise recorded against it.
    """

    assert resolve_invoice_id(f"reply+INV-9999.0000000000000000@{DOMAIN}", secret=SECRET) is None


def test_an_address_signed_with_another_key_does_not_resolve():
    address = reply_address("INV-1", secret="a-different-secret", domain=DOMAIN)

    assert resolve_invoice_id(address, secret=SECRET) is None


def test_the_tag_is_bound_to_the_invoice_it_names():
    """Swapping the invoice id while keeping a valid tag must fail."""

    valid = reply_address("INV-1", secret=SECRET, domain=DOMAIN)
    tag = valid.split(".")[-1].split("@")[0]

    assert resolve_invoice_id(f"reply+INV-2.{tag}@{DOMAIN}", secret=SECRET) is None


@pytest.mark.parametrize(
    "address",
    ["", "plain@example.com", "reply+INV-1@example.com", "reply+.abcdef@example.com", "garbage"],
)
def test_addresses_that_are_not_ours_resolve_to_nothing(address):
    assert resolve_invoice_id(address, secret=SECRET) is None


def test_a_display_name_wrapper_is_tolerated():
    """Mail clients send 'Name <addr>'; the address is still in there."""

    address = reply_address("INV-7", secret=SECRET, domain=DOMAIN)

    assert resolve_invoice_id(f"Recoup Billing <{address}>", secret=SECRET) == "INV-7"


def test_every_recipient_is_checked_not_just_the_first():
    """A 'reply all' can leave our address in cc rather than to."""

    address = reply_address("INV-8", secret=SECRET, domain=DOMAIN)
    recipients = ["accounts@customer.example", "someone@else.example", address]

    assert resolve_from_recipients(recipients, secret=SECRET) == "INV-8"


def test_no_recipients_resolves_to_nothing():
    assert resolve_from_recipients([], secret=SECRET) is None
    assert resolve_from_recipients(None, secret=SECRET) is None


# --- webhook signatures -----------------------------------------------------


def sign(body: bytes, secret: str, message_id: str, timestamp: str) -> str:
    raw = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except Exception:  # pragma: no cover - defensive
        key = raw.encode()
    signed = b"%s.%s.%s" % (message_id.encode(), timestamp.encode(), body)
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


@pytest.fixture
def signed():
    body = json.dumps({"text": "we will pay friday"}).encode()
    message_id = "msg_abc"
    timestamp = str(int(time.time()))
    secret = "whsec_testing"
    return {
        "body": body,
        "message_id": message_id,
        "timestamp": timestamp,
        "signature_header": f"v1,{sign(body, secret, message_id, timestamp)}",
        "secret": secret,
    }


def test_a_correctly_signed_webhook_verifies(signed):
    ok, reason = verify_svix_signature(**signed)

    assert ok is True
    assert reason == "ok"


def test_a_tampered_body_fails(signed):
    ok, _ = verify_svix_signature(**{**signed, "body": b'{"text":"different"}'})

    assert ok is False


def test_a_tampered_signature_fails(signed):
    ok, _ = verify_svix_signature(**{**signed, "signature_header": "v1,AAAAAAAA"})

    assert ok is False


def test_missing_headers_fail(signed):
    for field in ("message_id", "timestamp", "signature_header"):
        ok, reason = verify_svix_signature(**{**signed, field: ""})
        assert ok is False
        assert "missing" in reason


def test_no_configured_secret_fails_closed(signed):
    """An unset secret must reject everything, never accept everything."""

    ok, reason = verify_svix_signature(**{**signed, "secret": ""})

    assert ok is False
    assert "no webhook secret" in reason


def test_a_stale_timestamp_is_rejected(signed):
    """A signature with no freshness window is a replay waiting to happen."""

    stale = str(int(time.time()) - WEBHOOK_TOLERANCE_SECONDS - 60)
    body, secret = signed["body"], signed["secret"]
    ok, reason = verify_svix_signature(
        body=body,
        message_id=signed["message_id"],
        timestamp=stale,
        signature_header=f"v1,{sign(body, secret, signed['message_id'], stale)}",
        secret=secret,
    )

    assert ok is False
    assert "tolerance" in reason


def test_a_non_numeric_timestamp_is_rejected(signed):
    ok, reason = verify_svix_signature(**{**signed, "timestamp": "not-a-number"})

    assert ok is False
    assert "not an integer" in reason


def test_multiple_signatures_allow_key_rotation(signed):
    """Svix sends several v1 pairs while a secret is being rotated."""

    header = f"v1,AAAAinvalidAAAA {signed['signature_header'].split(',', 1)[1]}"
    ok, _ = verify_svix_signature(**{**signed, "signature_header": f"v1,{header.split(' ')[1]}"})

    assert ok is True
