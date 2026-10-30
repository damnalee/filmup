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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitor.sqlite3"
        self.logger = logging.getLogger("scraper")
        self.root_logger = logging.getLogger()
        self.original_root_level = self.root_logger.level
        self.root_logger.setLevel(logging.INFO)

    def tearDown(self):
        self.root_logger.setLevel(self.original_root_level)
        self.temp_dir.cleanup()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
