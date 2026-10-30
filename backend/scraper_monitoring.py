"""Independent observability wrapper for FilmUp's existing cinema scraper.

Monitoring is stored in Supabase Postgres when available (shared cloud history).
SQLite remains a local/offline fallback. Scraping and showtime writes are not
changed by this module; monitoring storage failures never block the scraper.
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
from typing import Any, Awaitable, Callable

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "scrape_output" / "monitoring.sqlite3"
DB_PATH = Path(os.getenv("SCRAPER_MONITOR_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
AUDIT_DIR = BASE_DIR / "scrape_output" / "showtime_audits"
logger = logging.getLogger(__name__)

SNAPSHOT_RE = re.compile(r"^\[(?P<label>.+?)\] Snapshot status=(?P<status>[^,]+), rows=(?P<rows>\d+)\.")
COLLECTED_RE = re.compile(
    r"Collected (?P<raw>\d+) authoritative rows; (?P<lahore>\d+) Lahore rows; (?P<unique>\d+) unique Lahore rows\."
)
CATALOG_RE = re.compile(r"Scheduled movie catalogue refresh finished \((?P<count>\d+) records upserted\)\.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_mode() -> str:
    mode = os.getenv("SCRAPER_MONITOR_STORAGE", "auto").strip().lower()
    if mode not in {"auto", "supabase", "sqlite"}:
        logger.warning("Unknown SCRAPER_MONITOR_STORAGE=%r; using auto.", mode)
        return "auto"
    return mode


def _supabase_client():
    # Reuse the backend's configured server-side client. It is never imported
    # by the browser and uses the same private backend credentials as DB writes.
    from database import supabase
    return supabase


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
            logger.warning("Could not read scraper audit report %s.", path.name)
    return reports


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


def _run_payload(run_id: str, trigger: str, started_at: str) -> dict[str, Any]:
    return {"run_id": run_id, "trigger": trigger, "status": "running", "started_at": started_at}


def _mark_stale_supabase_runs(client) -> None:
    stale_before = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    client.table("scraper_runs").update({
        "status": "interrupted",
        "finished_at": _utc_now(),
        "error_count": 1,
    }).eq("status", "running").lt("started_at", stale_before).execute()


def _start_run(run_id: str, trigger: str, started_at: str, db_path: Path) -> str:
    mode = _storage_mode()
    if mode in {"auto", "supabase"}:
        try:
            client = _supabase_client()
            _mark_stale_supabase_runs(client)
            client.table("scraper_runs").insert(
                _run_payload(run_id, trigger, started_at)
            ).execute()
            return "supabase"
        except Exception:
            if mode == "supabase":
                raise
            logger.warning(
                "Supabase scraper_runs unavailable; using local SQLite fallback. "
                "Apply backend/supabase_scraper_monitoring.sql to enable shared history.",
                exc_info=True,
            )
    initialize_monitoring(db_path)
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO scraper_runs (run_id, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
            (run_id, trigger, started_at),
        )
    return "sqlite"


def _finish_run(storage: str, run_id: str, update: dict[str, Any], db_path: Path) -> None:
    if storage == "supabase":
        _supabase_client().table("scraper_runs").update(update).eq("run_id", run_id).execute()
        return
    with _connect(db_path) as connection:
        connection.execute(
            """UPDATE scraper_runs SET status=?, finished_at=?, duration_seconds=?,
               authoritative_rows=?, lahore_rows=?, unique_lahore_rows=?,
               catalogue_rows_upserted=?, warning_count=?, error_count=?,
               sources_json=?, audit_json=? WHERE run_id=?""",
            (
                update["status"], update["finished_at"], update["duration_seconds"],
                update["authoritative_rows"], update["lahore_rows"],
                update["unique_lahore_rows"], update["catalogue_rows_upserted"],
                update["warning_count"], update["error_count"],
                json.dumps(update["sources"], ensure_ascii=False),
                json.dumps(update["audits"], ensure_ascii=False), run_id,
            ),
        )


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
    try:
        storage = _start_run(run_id, trigger, started_at, db_path)
    except Exception:
        logger.exception("Scraper monitoring could not start; executing scraper without telemetry.")
        await scrape_callable()
        return {"run_id": None, "status": "monitoring_unavailable", "duration_seconds": round(time.monotonic() - started_clock, 3)}

    capture = _RunLogCapture()
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
    except BaseException as exc:
        raised_error = type(exc).__name__
        raise
    finally:
        for monitored_logger in monitored_loggers:
            monitored_logger.removeHandler(capture)
        duration = round(time.monotonic() - started_clock, 3)
        audit_reports = _new_audit_files(started_epoch)
        if raised_error or capture.critical_error or capture.no_authoritative_rows:
            status = "failed"
        elif capture.warning_count or capture.error_count or any(
            report.get("status") not in (None, "PASS") for report in audit_reports
        ):
            status = "completed_with_warnings"
        else:
            status = "success"
        update = {
            "status": status,
            "finished_at": _utc_now(),
            "duration_seconds": duration,
            **capture.metrics,
            "warning_count": capture.warning_count,
            "error_count": capture.error_count + (1 if raised_error else 0),
            "sources": capture.sources,
            "audits": audit_reports,
        }
        try:
            _finish_run(storage, run_id, update, db_path)
        except Exception:
            # Telemetry failures must never mask the original scraper result.
            logger.exception("Could not persist scraper monitoring result for run %s.", run_id)

    return {"run_id": run_id, "status": status, "duration_seconds": duration, "storage": storage}


def _decode_sqlite_run(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["sources"] = json.loads(result.pop("sources_json") or "{}")
    result["audits"] = json.loads(result.pop("audit_json") or "[]")
    return result


def _sqlite_snapshot(limit: int, db_path: Path) -> dict[str, Any]:
    initialize_monitoring(db_path)
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
        "latest": _decode_sqlite_run(latest_row) if latest_row else None,
        "runs": [_decode_sqlite_run(row) for row in rows],
        "totals": dict(totals) if totals else {},
        "last_success_at": last_success["finished_at"] if last_success else None,
        "database": "sqlite",
    }


def _supabase_snapshot(limit: int) -> dict[str, Any]:
    client = _supabase_client()
    _mark_stale_supabase_runs(client)
    response = client.table("scraper_runs").select("*").order(
        "started_at", desc=True
    ).limit(limit).execute()
    rows = response.data or []

    def exact_count(status: str | None = None) -> int:
        query = client.table("scraper_runs").select("run_id", count="exact").limit(0)
        if status:
            query = query.eq("status", status)
        result = query.execute()
        return int(result.count or 0)

    totals = {
        "total_runs": exact_count(),
        "successful_runs": exact_count("success"),
        "warning_runs": exact_count("completed_with_warnings"),
        "failed_runs": exact_count("failed") + exact_count("interrupted"),
    }
    last = client.table("scraper_runs").select("finished_at").eq(
        "status", "success"
    ).order("started_at", desc=True).limit(1).execute()
    return {
        "latest": rows[0] if rows else None,
        "runs": rows,
        "totals": totals,
        "last_success_at": (last.data or [{}])[0].get("finished_at"),
        "database": "supabase",
    }


def get_monitoring_snapshot(limit: int = 20, db_path: Path = DB_PATH) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    mode = _storage_mode()
    if mode in {"auto", "supabase"}:
        try:
            return _supabase_snapshot(limit)
        except Exception:
            if mode == "supabase":
                raise
            logger.warning("Supabase monitoring read unavailable; returning local SQLite history.", exc_info=True)
    return _sqlite_snapshot(limit, db_path)
