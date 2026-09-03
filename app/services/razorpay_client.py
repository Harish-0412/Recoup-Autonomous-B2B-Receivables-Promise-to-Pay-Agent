from typing import Any, cast

import razorpay

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class RazorpayClient:
    def __init__(self) -> None:
        self.client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        self.client.set_app_details({"title": "Recoup", "version": "0.1.0"})

    def create_payment_link(
        self,
        amount: int,
        currency: str = "INR",
        description: str = "",
        customer_name: str = "",
        customer_email: str = "",
        customer_phone: str = "",
        notify_sms: bool = False,
        notify_email: bool = True,
        callback_url: str = "",
        callback_method: str = "get",
        expire_by: int = 0,
        reminder_enable: bool = True,
        notes: dict[Any, Any] | None = None,
    ) -> dict:
        payload = {
            "amount": amount,
            "currency": currency,
            "description": description,
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_phone,
            },
            "notify": {"sms": notify_sms, "email": notify_email},
            "reminder_enable": reminder_enable,
            "notes": notes or {},
        }
        if callback_url:
            payload["callback_url"] = callback_url
            payload["callback_method"] = callback_method
        if expire_by:
            payload["expire_by"] = expire_by

        logger.info("Creating Razorpay payment link", amount=amount, customer_email=customer_email)
        response = cast(dict[Any, Any], self.client.payment_link.create(payload))
        logger.info("Payment link created", payment_link_id=response.get("id"))
        return response

    def fetch_payment_link(self, payment_link_id: str) -> dict:
        logger.info("Fetching payment link", payment_link_id=payment_link_id)
        return cast(dict[Any, Any], self.client.payment_link.fetch(payment_link_id))

    def verify_webhook_signature(
        self, payload: bytes, signature: str, secret: str | None = None
    ) -> bool:
        """Whether ``payload`` really came from Razorpay.

        Called through ``self.client.utility``, the *instance* the SDK builds
        for this client. ``verify_webhook_signature`` is an instance method:
        calling it on the ``razorpay.Utility`` class bound the payload to
        ``self`` and raised ``TypeError`` for a missing ``secret``, so this
        never returned a verdict at all -- every webhook 500'd. It failed
        closed, which is the right direction to fail, but it meant no webhook
        was ever processed.

        Every failure is caught and reported as False. A signature check that
        raises is a signature check that did not pass, and the caller must be
        able to treat it as a plain rejection rather than a crash.
        """

        secret = secret or settings.RAZORPAY_WEBHOOK_SECRET
        try:
            self.client.utility.verify_webhook_signature(payload.decode("utf-8"), signature, secret)
            return True
        except razorpay.errors.SignatureVerificationError:
            logger.warning("Webhook signature verification failed")
            return False
        except Exception as exc:
            # A malformed signature header, undecodable body or missing secret
            # all land here. None of them are "verified".
            logger.warning("Webhook signature could not be checked", error=str(exc))
            return False


_razorpay_client: RazorpayClient | None = None


def get_razorpay_client() -> RazorpayClient:
    global _razorpay_client
    if _razorpay_client is None:
        _razorpay_client = RazorpayClient()
    return _razorpay_client
