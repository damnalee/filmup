"""Transactional email delivery through Resend's HTTPS API."""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)
RESEND_SEND_URL = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 20.0


def resend_config() -> tuple[str, str, str]:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender_email = os.getenv("MAIL_FROM_EMAIL", "").strip()
    sender_name = os.getenv("MAIL_FROM_NAME", "FilmUp Notifications").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY must be configured in the server environment.")
    if not sender_email:
        raise RuntimeError("MAIL_FROM_EMAIL must be configured with an allowed Resend sender.")
    return api_key, sender_email, sender_name


def send_resend_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html_body: str,
) -> str | None:
    """Send one transactional email and return Resend's email id when supplied."""
    api_key, sender_email, sender_name = resend_config()
    payload = {
        "from": f"{sender_name} <{sender_email}>",
        "to": [to_email],
        "subject": subject,
        "text": plain_text,
        "html": html_body,
    }
    try:
        response = httpx.post(
            RESEND_SEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("Resend transactional email request failed (%s).", type(exc).__name__)
        raise RuntimeError("The transactional email service could not be reached.") from exc

    if not response.is_success:
        # Provider response bodies can contain account details; never expose them.
        logger.error("Resend rejected a transactional email (HTTP %s).", response.status_code)
        raise RuntimeError(f"Resend rejected the email request (HTTP {response.status_code}).")

    try:
        result = response.json()
    except ValueError:
        result = {}
    email_id = result.get("id") if isinstance(result, dict) else None
    logger.info("Resend accepted transactional email (id present: %s).", bool(email_id))
    return str(email_id) if email_id else None
