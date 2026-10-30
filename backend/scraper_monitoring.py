"""Independent observability wrapper for FilmUp's existing cinema scraper.

This module records executions and observes existing scraper/auditor logs. It
never changes scraping, parsing, Supabase writes, or reconciliation behavior.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "scrape_output" / "monitoring.sqlite3"
DB_PATH = Path(os.getenv("SCRAPER_MONITOR_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
AUDIT_DIR = BASE_DIR / "scrape_output" / "showtime_audits"

SNAPSHOT_RE = re.compile(r"^\[(?P<label>.+?)\] Snapshot status=(?P<status>[^,]+), rows=(?P<rows>\d+)\.")
COLLECTED_RE = re.compile(
    r"Collected (?P<raw>\d+) authoritative rows; (?P<lahore>\d+) Lahore rows; (?P<unique>\d+) unique Lahore rows\."
)
CATALOG_RE = re.compile(r"Scheduled movie catalogue refresh finished \((?P<count>\d+) records upserted\)\.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _RunLogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.warning_count = 0
        self.error_count = 0
        self.sources: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, int] = {
            "authoritative_rows": 0,
            "lahore_rows": 0,
            "unique_lahore_rows": 0,
            "catalogue_rows_upserted": 0,
        }
        self.no_authoritative_rows = False
        self.critical_error = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.error_count += 1
        elif record.levelno >= logging.WARNING:
            self.warning_count += 1

        message = record.getMessage()
        snapshot = SNAPSHOT_RE.search(message)
        if snapshot:
            self.sources[snapshot.group("label")] = {
                "status": snapshot.group("status").strip(),
                "rows": int(snapshot.group("rows")),
            }
        collected = COLLECTED_RE.search(message)
        if collected:
            self.metrics["authoritative_rows"] = int(collected.group("raw"))
            self.metrics["lahore_rows"] = int(collected.group("lahore"))
            self.metrics["unique_lahore_rows"] = int(collected.group("unique"))
        catalogue = CATALOG_RE.search(message)
        if catalogue:
            self.metrics["catalogue_rows_upserted"] = int(catalogue.group("count"))
        if "No authoritative source rows collected" in message:
            self.no_authoritative_rows = True
            self.critical_error = True
        if "Supabase upsert failed" in message:
            self.critical_error = True


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_monitoring(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS scraper_runs (
                run_id TEXT PRIMARY KEY,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_seconds REAL,
                authoritative_rows INTEGER NOT NULL DEFAULT 0,
                lahore_rows INTEGER NOT NULL DEFAULT 0,
                unique_lahore_rows INTEGER NOT NULL DEFAULT 0,
                catalogue_rows_upserted INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                sources_json TEXT NOT NULL DEFAULT '{}',
                audit_json TEXT NOT NULL DEFAULT '[]'
            )"""
        )
        # A process restart during a scrape leaves a durable RUNNING row. Mark it
        # interrupted rather than silently presenting it as a healthy execution.
        stale_before = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        connection.execute(
            "UPDATE scraper_runs SET status='interrupted', finished_at=?, "
            "error_count=error_count+1 WHERE status='running' AND started_at < ?",
            (_utc_now(), stale_before),
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraper_runs_started_at "
            "ON scraper_runs(started_at DESC)"
        )


def _new_audit_files(started_epoch: float) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if not AUDIT_DIR.exists():
        return reports
    for path in sorted(AUDIT_DIR.glob("post_scrape_audit_*.json")):
        try:
            if path.stat().st_mtime < started_epoch - 1:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            summary = data.get("summary") or {}
            reports.append({
                "context": data.get("context"),
                "status": summary.get("status"),
                "venues_compared": summary.get("venues_compared", 0),
                "live_unique_sessions": summary.get("live_unique_sessions", 0),
                "stored_unique_sessions": summary.get("stored_unique_sessions", 0),
                "stored_only": summary.get("stored_only", 0),
                "live_only": summary.get("live_only", 0),
                "parser_warnings": summary.get("parser_warnings", 0),
                "non_authoritative_sources": summary.get("non_authoritative_sources", 0),
                "sources": [
                    {
                        "label": source.get("label"),
                        "status": source.get("status"),
                        "input_rows": source.get("input_rows", 0),
                        "approved_future_rows": source.get("approved_future_rows", 0),
                    }
                    for source in data.get("sources", [])
                ],
            })
        except (OSError, ValueError, TypeError):
            continue
    return reports


async def run_monitored_scrape(
    scrape_callable: Callable[[], Awaitable[Any]],
    *,
    trigger: str = "scheduled",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Run the original scraper unchanged while recording operational telemetry."""
    run_id = str(uuid.uuid4())
    started_at = _utc_now()
    started_epoch = time.time()
    started_clock = time.monotonic()
    capture = _RunLogCapture()
    try:
        initialize_monitoring(db_path)
        with _connect(db_path) as connection:
            connection.execute(
                "INSERT INTO scraper_runs (run_id, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
                (run_id, trigger, started_at),
            )
    except Exception:
        # Monitoring is deliberately fail-open: a telemetry/storage problem
        # must never prevent the established scraper from doing its work.
        logging.getLogger(__name__).exception(
            "Scraper monitoring storage is unavailable; running scraper without telemetry."
        )
        await scrape_callable()
        return {"run_id": None, "status": "monitoring_unavailable", "duration_seconds": round(time.monotonic() - started_clock, 3)}

    monitored_loggers = [
        logging.getLogger(name)
        for name in (
            "scraper", "catalog_scraper", "poster_fallback",
            "catalog_sources", "cinestar_dha_sources", "database",
        )
    ]
    for monitored_logger in monitored_loggers:
        monitored_logger.addHandler(capture)

    raised_error: str | None = None
    try:
        await scrape_callable()
    except BaseException as exc:  # persist cancellation/interruption before re-raising
        raised_error = type(exc).__name__
        raise
    finally:
        for monitored_logger in monitored_loggers:
            monitored_logger.removeHandler(capture)
        duration = round(time.monotonic() - started_clock, 3)
        finished_at = _utc_now()
        audit_reports = _new_audit_files(started_epoch)

        if raised_error or capture.critical_error or capture.no_authoritative_rows:
            status = "failed"
        elif capture.warning_count or capture.error_count or any(
            report.get("status") not in (None, "PASS") for report in audit_reports
        ):
            status = "completed_with_warnings"
        else:
            status = "success"

        try:
            with _connect(db_path) as connection:
                connection.execute(
                    """UPDATE scraper_runs SET status=?, finished_at=?, duration_seconds=?,
                       authoritative_rows=?, lahore_rows=?, unique_lahore_rows=?,
                       catalogue_rows_upserted=?, warning_count=?, error_count=?,
                       sources_json=?, audit_json=? WHERE run_id=?""",
                    (
                        status, finished_at, duration,
                        capture.metrics["authoritative_rows"],
                        capture.metrics["lahore_rows"],
                        capture.metrics["unique_lahore_rows"],
                        capture.metrics["catalogue_rows_upserted"],
                        capture.warning_count,
                        capture.error_count + (1 if raised_error else 0),
                        json.dumps(capture.sources, ensure_ascii=False),
                        json.dumps(audit_reports, ensure_ascii=False),
                        run_id,
                    ),
                )
        except Exception:
            # Do not let a monitoring write failure mask the scraper's result.
            logging.getLogger(__name__).exception("Could not persist scraper monitoring result for run %s.", run_id)

    return {"run_id": run_id, "status": status, "duration_seconds": duration}


def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["sources"] = json.loads(result.pop("sources_json") or "{}")
    result["audits"] = json.loads(result.pop("audit_json") or "[]")
    return result


def get_monitoring_snapshot(limit: int = 20, db_path: Path = DB_PATH) -> dict[str, Any]:
    initialize_monitoring(db_path)
    limit = max(1, min(int(limit), 100))
    with _connect(db_path) as connection:
        latest_row = connection.execute(
            "SELECT * FROM scraper_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM scraper_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        totals = connection.execute(
            """SELECT COUNT(*) AS total_runs,
               COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) AS successful_runs,
               COALESCE(SUM(CASE WHEN status='completed_with_warnings' THEN 1 ELSE 0 END), 0) AS warning_runs,
               COALESCE(SUM(CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END), 0) AS failed_runs
               FROM scraper_runs"""
        ).fetchone()
        last_success = connection.execute(
            "SELECT finished_at FROM scraper_runs WHERE status='success' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return {
        "latest": _decode_run(latest_row) if latest_row else None,
        "runs": [_decode_run(row) for row in rows],
        "totals": dict(totals) if totals else {},
        "last_success_at": last_success["finished_at"] if last_success else None,
        "database": "sqlite",
    }
