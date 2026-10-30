"""Ensure the pre-email Cloudflare checker detects slots without sending or consuming alerts."""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


# advance_booking_routes normally imports a configured Supabase client at module load.
# Inject a placeholder here; each test supplies its own isolated fake client.
_database_stub = types.ModuleType("database")
_database_stub.supabase = object()
with patch.dict(sys.modules, {"database": _database_stub}):
    advance_booking_routes = importlib.import_module("advance_booking_routes")


class FakeQuery:
    def __init__(self, table: str, client: "FakeSupabase"):
        self.table = table
        self.client = client
        self.update_payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def ilike(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.update_payload = payload
        self.client.updates.append((self.table, payload))
        return self

    def execute(self):
        if self.table == "advance_booking_subscriptions":
            return types.SimpleNamespace(data=self.client.subscriptions)
        if self.table == "showtimes":
            return types.SimpleNamespace(data=self.client.showtimes)
        return types.SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self):
        self.updates = []
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.subscriptions = [{
            "id": "sub-1",
            "email": "person@example.com",
            "movie_title": "Dune: Part Three",
            "cinema_names": ["CineStar DHA Raya"],
            "notified_cinemas": [],
        }]
        self.showtimes = [{
            "movie_title": "Dune: Part Three",
            "cinema_name": "CineStar DHA Raya",
            "showtime": future,
            "booking_link": "https://example.com/book",
            "ticket_price": None,
        }]

    def table(self, name):
        return FakeQuery(name, self)


class AdvanceBookingDryRunTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeSupabase()
        self.client_patch = patch.object(advance_booking_routes, "supabase", self.client)
        self.client_patch.start()

    def tearDown(self):
        self.client_patch.stop()

    def test_dry_run_detects_slots_without_sending_or_marking_notified(self):
        with patch.object(advance_booking_routes, "_send_email") as send_email:
            detected = advance_booking_routes.check_advance_booking_notifications(dry_run=True)

        self.assertEqual(detected, 1)
        send_email.assert_not_called()
        self.assertEqual(self.client.updates, [])

    def test_normal_mode_still_sends_and_marks_cinema_notified(self):
        with patch.object(advance_booking_routes, "_send_email") as send_email:
            sent = advance_booking_routes.check_advance_booking_notifications()

        self.assertEqual(sent, 1)
        send_email.assert_called_once()
        self.assertEqual(len(self.client.updates), 1)
        self.assertEqual(self.client.updates[0][1]["notified_cinemas"], ["CineStar DHA Raya"])


if __name__ == "__main__":
    unittest.main()
