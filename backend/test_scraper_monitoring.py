"""Unit tests for the independent scraper monitoring wrapper."""
import asyncio
import json
import logging
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path

import scraper_monitoring
from scraper_monitoring import get_monitoring_snapshot, initialize_monitoring, run_monitored_scrape


class ScraperMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.storage_env = patch.dict("os.environ", {"SCRAPER_MONITOR_STORAGE": "sqlite"})
        self.storage_env.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitor.sqlite3"
        self.logger = logging.getLogger("scraper")
        self.root_logger = logging.getLogger()
        self.original_root_level = self.root_logger.level
        self.root_logger.setLevel(logging.INFO)

    def tearDown(self):
        self.root_logger.setLevel(self.original_root_level)
        self.temp_dir.cleanup()
        self.storage_env.stop()

    def test_success_records_run_and_metrics(self):
        async def fake_scrape():
            self.logger.info("[Test Cinema] Snapshot status=complete, rows=12.")
            self.logger.info("Collected 12 authoritative rows; 10 Lahore rows; 9 unique Lahore rows.")
            self.logger.info("Scheduled movie catalogue refresh finished (4 records upserted).")

        result = asyncio.run(run_monitored_scrape(fake_scrape, trigger="test", db_path=self.db_path))
        snapshot = get_monitoring_snapshot(db_path=self.db_path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(snapshot["latest"]["unique_lahore_rows"], 9)
        self.assertEqual(snapshot["latest"]["catalogue_rows_upserted"], 4)
        self.assertEqual(snapshot["latest"]["sources"]["Test Cinema"]["rows"], 12)
        self.assertEqual(snapshot["totals"]["total_runs"], 1)

    def test_warning_marks_run_as_completed_with_warnings(self):
        async def fake_scrape():
            self.logger.warning("A source was unavailable; retained previous data.")

        asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        snapshot = get_monitoring_snapshot(db_path=self.db_path)
        self.assertEqual(snapshot["latest"]["status"], "completed_with_warnings")
        self.assertEqual(snapshot["latest"]["warning_count"], 1)

    def test_exception_is_recorded_and_reraised(self):
        async def fake_scrape():
            raise RuntimeError("expected test failure")

        with self.assertRaisesRegex(RuntimeError, "expected test failure"):
            asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        snapshot = get_monitoring_snapshot(db_path=self.db_path)
        self.assertEqual(snapshot["latest"]["status"], "failed")
        self.assertEqual(snapshot["latest"]["error_count"], 1)

    def test_no_authoritative_rows_marks_failed(self):
        async def fake_scrape():
            self.logger.error("No authoritative source rows collected. Skipping writes.")

        asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        snapshot = get_monitoring_snapshot(db_path=self.db_path)
        self.assertEqual(snapshot["latest"]["status"], "failed")

    def test_audit_summary_is_attached_and_marks_warning(self):
        audit_dir = Path(self.temp_dir.name) / "audits"
        audit_dir.mkdir()
        report = {
            "context": "test audit",
            "summary": {
                "status": "MATCH_WITH_WARNINGS_OR_INCOMPLETE_SOURCES",
                "venues_compared": 2,
                "live_unique_sessions": 18,
                "stored_unique_sessions": 17,
                "stored_only": 0,
                "live_only": 1,
                "parser_warnings": 1,
                "non_authoritative_sources": 1,
            },
            "sources": [{"label": "Test Cinema", "status": "SNAPSHOT_WARNINGS", "input_rows": 18, "approved_future_rows": 18}],
        }
        async def fake_scrape():
            path = audit_dir / "post_scrape_audit_test.json"
            path.write_text(json.dumps(report), encoding="utf-8")

        with patch.object(scraper_monitoring, "AUDIT_DIR", audit_dir):
            asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        latest = get_monitoring_snapshot(db_path=self.db_path)["latest"]
        self.assertEqual(latest["status"], "completed_with_warnings")
        self.assertEqual(latest["audits"][0]["live_unique_sessions"], 18)
        self.assertEqual(latest["audits"][0]["sources"][0]["label"], "Test Cinema")

    def test_stale_running_job_is_marked_interrupted(self):
        initialize_monitoring(self.db_path)
        stale_start = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO scraper_runs (run_id, trigger, status, started_at) VALUES (?, ?, ?, ?)",
                ("stale-run", "scheduled", "running", stale_start),
            )
        latest = get_monitoring_snapshot(db_path=self.db_path)["latest"]
        self.assertEqual(latest["run_id"], "stale-run")
        self.assertEqual(latest["status"], "interrupted")

    def test_cancelled_job_is_recorded_before_cancellation_propagates(self):
        async def fake_scrape():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        latest = get_monitoring_snapshot(db_path=self.db_path)["latest"]
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["error_count"], 1)

    def test_monitoring_storage_failure_does_not_block_scraper(self):
        called = []
        async def fake_scrape():
            called.append(True)

        with patch.object(scraper_monitoring, "initialize_monitoring", side_effect=OSError("disk unavailable")), \
             patch.object(logging.getLogger(scraper_monitoring.__name__), "exception"):
            result = asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        self.assertEqual(called, [True])
        self.assertEqual(result["status"], "monitoring_unavailable")

    def test_supabase_mode_writes_run_and_metrics_to_shared_table(self):
        class FakeQuery:
            def __init__(self, calls):
                self.calls = calls
            def insert(self, payload):
                self.calls.append(("insert", payload))
                return self
            def update(self, payload):
                self.calls.append(("update", payload))
                return self
            def eq(self, key, value):
                self.calls.append(("eq", key, value))
                return self
            def lt(self, key, value):
                self.calls.append(("lt", key, value))
                return self
            def execute(self):
                return type("Response", (), {"data": [], "count": 0})()
        class FakeClient:
            def __init__(self):
                self.calls = []
            def table(self, name):
                self.calls.append(("table", name))
                return FakeQuery(self.calls)
        fake = FakeClient()
        async def fake_scrape():
            self.logger.info("Collected 20 authoritative rows; 18 Lahore rows; 17 unique Lahore rows.")
        with patch.dict("os.environ", {"SCRAPER_MONITOR_STORAGE": "supabase"}), \
             patch.object(scraper_monitoring, "_supabase_client", return_value=fake):
            result = asyncio.run(run_monitored_scrape(fake_scrape, db_path=self.db_path))
        self.assertEqual(result["storage"], "supabase")
        inserts = [call for call in fake.calls if call[0] == "insert"]
        updates = [call for call in fake.calls if call[0] == "update"]
        completed_updates = [call for call in updates if call[1].get("status") == "success"]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1]["status"], "running")
        self.assertEqual(len(completed_updates), 1)
        self.assertEqual(completed_updates[0][1]["unique_lahore_rows"], 17)

    def test_supabase_snapshot_reads_history_and_exact_totals(self):
        class FakeQuery:
            def __init__(self, client):
                self.client = client
                self.filters = []
                self.selection = None
            def select(self, selection, **kwargs):
                self.selection = selection
                self.count = kwargs.get("count")
                return self
            def order(self, *args, **kwargs):
                return self
            def limit(self, amount):
                self.limit_value = amount
                return self
            def eq(self, key, value):
                self.filters.append((key, value))
                return self
            def lt(self, key, value):
                return self
            def update(self, payload):
                return self
            def execute(self):
                if self.selection == "*":
                    return type("Response", (), {"data": [self.client.run], "count": None})()
                if self.selection == "finished_at":
                    return type("Response", (), {"data": [{"finished_at": "2026-10-30T10:00:00+00:00"}], "count": None})()
                status = dict(self.filters).get("status")
                count = self.client.counts.get(status, 3 if status is None else 1)
                return type("Response", (), {"data": [], "count": count})()
        class FakeClient:
            run = {"run_id": "cloud-run", "status": "success", "sources": {"Cinema": {"rows": 4}}, "audits": []}
            counts = {None: 3, "success": 2, "completed_with_warnings": 1, "failed": 0, "interrupted": 0}
            def table(self, name):
                assert name == "scraper_runs"
                return FakeQuery(self)
        with patch.dict("os.environ", {"SCRAPER_MONITOR_STORAGE": "supabase"}), \
             patch.object(scraper_monitoring, "_supabase_client", return_value=FakeClient()):
            snapshot = get_monitoring_snapshot(limit=10, db_path=self.db_path)
        self.assertEqual(snapshot["database"], "supabase")
        self.assertEqual(snapshot["latest"]["run_id"], "cloud-run")
        self.assertEqual(snapshot["totals"]["total_runs"], 3)
        self.assertEqual(snapshot["totals"]["successful_runs"], 2)
        self.assertEqual(snapshot["last_success_at"], "2026-10-30T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
