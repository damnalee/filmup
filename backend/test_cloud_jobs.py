"""Safety tests for scheduled cloud-job defaults."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import cloud_jobs


class CloudJobDryRunTests(unittest.TestCase):
    def test_advance_booking_check_defaults_to_dry_run(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADVANCE_BOOKING_DRY_RUN", None)
            with patch.object(cloud_jobs, "check_advance_booking_notifications", return_value=2) as check:
                self.assertEqual(cloud_jobs.run_advance_booking_check(), 2)
        check.assert_called_once_with(dry_run=True)

    def test_live_delivery_requires_explicit_opt_in_and_resend_config(self):
        with patch.dict(os.environ, {"ADVANCE_BOOKING_DRY_RUN": "false"}):
            with patch("email_service.resend_config") as validate_sender:
                with patch.object(cloud_jobs, "check_advance_booking_notifications", return_value=1) as check:
                    self.assertEqual(cloud_jobs.run_advance_booking_check(), 1)
        validate_sender.assert_called_once_with()
        check.assert_called_once_with(dry_run=False)


if __name__ == "__main__":
    unittest.main()
