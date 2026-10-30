"""Unit tests for Resend transactional email delivery without real sends."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from email_service import RESEND_SEND_URL, resend_config, send_resend_email


class ResendEmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            "os.environ",
            {
                "RESEND_API_KEY": "re_test-api-key",
                "MAIL_FROM_EMAIL": "onboarding@resend.dev",
                "MAIL_FROM_NAME": "FilmUp Tests",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_sender_configuration_requires_api_key_and_from_address(self):
        with patch.dict("os.environ", {"RESEND_API_KEY": "", "MAIL_FROM_EMAIL": ""}):
            with self.assertRaisesRegex(RuntimeError, "RESEND_API_KEY"):
                resend_config()
        with patch.dict("os.environ", {"RESEND_API_KEY": "re_configured", "MAIL_FROM_EMAIL": ""}):
            with self.assertRaisesRegex(RuntimeError, "MAIL_FROM_EMAIL"):
                resend_config()

    def test_sends_html_and_plaintext_through_resend_api(self):
        response = httpx.Response(200, json={"id": "resend-email-123"})
        with patch("email_service.httpx.post", return_value=response) as send:
            email_id = send_resend_email(
                "recipient@example.com",
                "FilmUp confirmation",
                "Please confirm.",
                "<p>Please confirm.</p>",
            )

        self.assertEqual(email_id, "resend-email-123")
        args, kwargs = send.call_args
        self.assertEqual(args[0], RESEND_SEND_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_test-api-key")
        self.assertEqual(kwargs["json"]["from"], "FilmUp Tests <onboarding@resend.dev>")
        self.assertEqual(kwargs["json"]["to"], ["recipient@example.com"])
        self.assertEqual(kwargs["json"]["text"], "Please confirm.")
        self.assertEqual(kwargs["json"]["html"], "<p>Please confirm.</p>")

    def test_provider_rejection_returns_a_sanitized_error(self):
        response = httpx.Response(403, json={"message": "not allowed", "name": "validation_error"})
        with patch("email_service.httpx.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                send_resend_email("recipient@example.com", "Subject", "Plain text", "<p>HTML</p>")


if __name__ == "__main__":
    unittest.main()
