from typing import Any, cast

import resend

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class ResendClient:
    def __init__(self) -> None:
        resend.api_key = settings.RESEND_API_KEY
        self.from_email = settings.RESEND_FROM_EMAIL

    def send_email(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
    ) -> dict:
        if isinstance(to, str):
            to = [to]

        params: dict[str, Any] = {
            "from": self.from_email,
            "to": to,
            "subject": subject,
            "html": html,
        }
        if text:
            params["text"] = text

        logger.info("Sending email via Resend", to=to, subject=subject)
        response = cast(dict[Any, Any], resend.Emails.send(cast(Any, params)))
        logger.info("Email sent", email_id=response.get("id"))
        return response


_resend_client: ResendClient | None = None


def get_resend_client() -> ResendClient:
    global _resend_client
    if _resend_client is None:
        _resend_client = ResendClient()
    return _resend_client
