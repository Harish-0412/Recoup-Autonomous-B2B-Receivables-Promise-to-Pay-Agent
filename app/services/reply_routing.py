"""Routing an inbound reply back to the invoice it belongs to, and proving it.

Two independent problems, both solved here because both are about trusting
something that arrived from outside.

**Which invoice is this reply about?** Not the subject line. Subjects get
edited, forwarded, translated and truncated, and a customer replying about the
wrong thing would silently attach a promise to the wrong debt. Instead each
outbound message sets a tagged ``Reply-To`` (Wave 1 tenanted form):

    reply+acme.INV-2026-00042.a1b2c3d4e5f6@reply.example.com

The ``<business>.<invoice>`` pair is carried in the address, and the suffix is
an HMAC over that pair. The signature is what makes the address *unforgeable*:
without it, anyone who worked out the format could post a reply naming any
invoice in the book and have a promise recorded against it. With it, an
address that was not minted by this service does not resolve -- and a valid
address from tenant A cannot record a promise in tenant B, because the tag is
bound to the business as well as the invoice.

Legacy single-tenant addresses (``reply+<invoice>.<tag>@``) still resolve to
``("default", invoice)`` so in-flight mail from before Wave 1 is not dropped.
Every new address minted uses the tenanted form.

**Did this webhook really come from Resend?** Resend signs inbound webhooks
with the Svix scheme: an id, a timestamp, and an HMAC over
``{id}.{timestamp}.{body}``. Verified here rather than pulling in the ``svix``
package, because the scheme is twenty lines and the dependency is not free.
The timestamp is checked too -- a signature with no freshness window is a
replay waiting to happen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

from app.core.logging import get_logger

logger = get_logger(__name__)

#: How far out of date a webhook timestamp may be before it is rejected.
#: Svix's own recommendation; wide enough for clock skew, narrow enough that a
#: captured request is not replayable tomorrow.
WEBHOOK_TOLERANCE_SECONDS = 5 * 60

#: Length of the address signature. Eight bytes of HMAC is 64 bits, which is
#: ample against forgery here and keeps the address readable.
_TAG_BYTES = 8

#: Tenant ids are restricted so they survive inside an email local-part.
_BUSINESS_PATTERN = r"[A-Za-z0-9_-]{1,64}"

_ADDRESS_RE = re.compile(
    r"^reply\+(?P<business>" + _BUSINESS_PATTERN + r")\."
    r"(?P<invoice_id>[A-Za-z0-9][A-Za-z0-9._-]*)\.(?P<tag>[0-9a-f]{16})@",
    re.IGNORECASE,
)

#: Pre-Wave-1 form: reply+<invoice>.<tag>@. Resolves to ("default", invoice).
_LEGACY_ADDRESS_RE = re.compile(
    r"^reply\+(?P<invoice_id>[A-Za-z0-9][A-Za-z0-9._-]*)\.(?P<tag>[0-9a-f]{16})@",
    re.IGNORECASE,
)


def _tag(business_id: str, invoice_id: str, secret: str) -> str:
    scope = f"{business_id}.{invoice_id}"
    digest = hmac.new(secret.encode("utf-8"), scope.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[: _TAG_BYTES * 2]


def _legacy_tag(invoice_id: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), invoice_id.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[: _TAG_BYTES * 2]


def reply_address(
    invoice_id: str, *, secret: str, domain: str, business_id: str = "default"
) -> str:
    """The tagged Reply-To address for one invoice in one business."""

    return f"reply+{business_id}.{invoice_id}.{_tag(business_id, invoice_id, secret)}@{domain}"


def resolve_tenant_invoice(address: str, *, secret: str) -> tuple[str, str] | None:
    """Recover (business_id, invoice_id) from a tagged address, or ``None``.

    Returns ``None`` for anything that does not carry a valid signature: a
    plain address, a malformed one, or a forged one -- including a valid tag
    for tenant A presented as tenant B. The caller treats that as "queue this
    for a human", never as "guess".
    """

    if not address:
        return None

    # Mail clients send "Name <addr@host>"; take the angle-bracketed part.
    if "<" in address and ">" in address:
        address = address[address.index("<") + 1 : address.index(">")]

    cleaned = address.strip()
    match = _ADDRESS_RE.match(cleaned)
    if match is not None:
        business_id = match.group("business")
        invoice_id = match.group("invoice_id")
        if hmac.compare_digest(match.group("tag").lower(), _tag(business_id, invoice_id, secret)):
            return business_id, invoice_id
        logger.warning("Reply address failed its signature check", address=address)
        return None

    # Legacy single-tenant form -> default business only.
    legacy = _LEGACY_ADDRESS_RE.match(cleaned)
    if legacy is not None:
        invoice_id = legacy.group("invoice_id")
        # A tenanted business id never contains a dot, so a legacy address
        # cannot be confused with a tenanted one; verify against "default".
        if hmac.compare_digest(legacy.group("tag").lower(), _legacy_tag(invoice_id, secret)):
            return "default", invoice_id
        logger.warning("Legacy reply address failed its signature check", address=address)
        return None
    return None


def resolve_invoice_id(
    address: str, *, secret: str, expected_business_id: str | None = None
) -> str | None:
    """Recover the invoice id, optionally requiring a business match.

    When ``expected_business_id`` is given, an address minted for any other
    business resolves to ``None`` -- this is the cross-tenant forgery check.
    Without it, the invoice id is returned regardless of tenant (legacy
    callers; prefer :func:`resolve_tenant_invoice` in new code).
    """

    resolved = resolve_tenant_invoice(address, secret=secret)
    if resolved is None:
        return None
    business_id, invoice_id = resolved
    if expected_business_id is not None and business_id != expected_business_id:
        logger.warning(
            "Reply address business mismatch",
            address=address,
            expected=expected_business_id,
            actual=business_id,
        )
        return None
    return invoice_id


def resolve_from_recipients(addresses: list[str] | None, *, secret: str) -> tuple[str, str] | None:
    """First valid tagged (business, invoice) among recipients.

    A reply usually lands with our address in ``to``, but a customer who used
    "reply all", or a mail system that rewrote headers, can leave it in ``cc``
    instead -- so every recipient is checked rather than just the first.

    Note: returns a tuple in Wave 1 (was invoice_id). Callers that need the
    legacy string form should use :func:`resolve_invoice_from_recipients`.
    """

    for address in addresses or []:
        resolved = resolve_tenant_invoice(address, secret=secret)
        if resolved is not None:
            return resolved
    return None


def resolve_invoice_from_recipients(
    addresses: list[str] | None, *, secret: str, expected_business_id: str | None = None
) -> str | None:
    """Legacy helper: first valid invoice id among recipients."""

    for address in addresses or []:
        invoice_id = resolve_invoice_id(
            address, secret=secret, expected_business_id=expected_business_id
        )
        if invoice_id is not None:
            return invoice_id
    return None


def verify_svix_signature(
    *,
    body: bytes,
    message_id: str,
    timestamp: str,
    signature_header: str,
    secret: str,
    tolerance_seconds: int = WEBHOOK_TOLERANCE_SECONDS,
    now: float | None = None,
) -> tuple[bool, str]:
    """Verify a Resend/Svix webhook signature. Returns (ok, reason).

    Returns a reason rather than raising so the caller can log *why* a delivery
    was rejected -- "invalid signature" and "clock skew" need different fixes,
    and a webhook that silently 401s is very hard to debug from the outside.
    """

    if not secret:
        return False, "no webhook secret configured"
    if not message_id or not timestamp or not signature_header:
        return False, "missing svix-id, svix-timestamp or svix-signature header"

    try:
        sent_at = int(timestamp)
    except ValueError:
        return False, "svix-timestamp is not an integer"

    drift = abs((now if now is not None else time.time()) - sent_at)
    if drift > tolerance_seconds:
        return False, f"timestamp is {drift:.0f}s out of tolerance"

    # Svix secrets are "whsec_<base64>"; the raw key is what gets HMAC'd with.
    raw_secret = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except Exception:
        # A non-base64 secret is used verbatim, which is what a local test
        # harness will hand us.
        key = raw_secret.encode("utf-8")

    signed = b"%s.%s.%s" % (message_id.encode("utf-8"), timestamp.encode("utf-8"), body)
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    # The header carries one or more space-separated "v1,<signature>" pairs, so
    # a secret can be rotated without dropping deliveries mid-flight.
    for candidate in signature_header.split():
        _, _, value = candidate.partition(",")
        if value and hmac.compare_digest(value, expected):
            return True, "ok"

    return False, "no signature in the header matched"
