"""Supabase connection and database operations for the cinema backend."""

import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import Client, create_client
from location_guard import filter_lahore_showtimes

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)
PK_TZ = ZoneInfo("Asia/Karachi")

DB_COLUMNS = (
    "movie_title",
    "cinema_name",
    "showtime",
    "ticket_price",
    "booking_link",
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
).strip()

if not SUPABASE_URL or "your-project-id" in SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing or still a placeholder.")
if not SUPABASE_KEY or "your_private_backend_key" in SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY) is missing or still a placeholder."
    )
if not SUPABASE_URL.startswith(("https://", "http://")):
    raise RuntimeError("SUPABASE_URL must be an absolute HTTP(S) URL.")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as exc:
    raise RuntimeError(f"Supabase client initialization failed: {exc}") from exc

SUPABASE_CONFIGURED = True


def _deduplicate_batch(rows: list[dict]) -> list[dict]:
    """Prevent duplicate conflict keys within a single upsert batch."""
    unique = {}

    for row in rows:
        key = (
            row["movie_title"],
            row["cinema_name"],
            row["showtime"],
        )
        unique[key] = row

    return list(unique.values())


def upsert_showtimes(data: list[dict]) -> list[dict]:
    """
    Insert or update cinema showtimes in Supabase.

    IMPORTANT: In the Supabase SQL Editor, create a UNIQUE constraint
    on (movie_title, cinema_name, showtime). The upsert's on_conflict
    target relies on this constraint to prevent duplicate database rows.
    """
    data = filter_lahore_showtimes(data, context="database.upsert_showtimes")
    if not isinstance(data, list):
        raise TypeError("upsert_showtimes expects a list of dictionaries.")

    if not data:
        logger.info("No showtimes supplied; skipping database upsert.")
        return []

    cleaned_rows = []

    for index, row in enumerate(data):
        if not isinstance(row, dict):
            logger.warning("Skipping invalid row at index %d.", index)
            continue

        # Only send columns supported by the current showtimes schema.
        clean = {
            column: row.get(column)
            for column in DB_COLUMNS
        }

        # Required fields for identifying a showtime.
        missing = [
            field
            for field in ("movie_title", "cinema_name", "showtime")
            if not clean[field]
        ]

        if missing:
            logger.warning(
                "Skipping row %d; missing required fields: %s",
                index,
                ", ".join(missing),
            )
            continue

        clean["movie_title"] = str(clean["movie_title"]).strip()
        clean["cinema_name"] = str(clean["cinema_name"]).strip()
        clean["showtime"] = str(clean["showtime"]).strip()

        if not clean["movie_title"] or not clean["cinema_name"]:
            logger.warning("Skipping row %d with empty title or cinema.", index)
            continue

        cleaned_rows.append(clean)

    if not cleaned_rows:
        logger.warning("No valid showtime rows remain after validation.")
        return []

    unique_rows = _deduplicate_batch(cleaned_rows)

    logger.info(
        "Upserting %d unique showtimes (%d valid input rows).",
        len(unique_rows),
        len(cleaned_rows),
    )

    # PostgREST uses the matching UNIQUE constraint to update an existing
    # record instead of inserting a duplicate.
    response = (
        supabase.table("showtimes")
        .upsert(
            unique_rows,
            on_conflict="movie_title,cinema_name,showtime",
        )
        .execute()
    )

    result = response.data or []
    logger.info("Supabase upsert completed; returned %d records.", len(result))
    return result


def _matches_preferred_time(
    row: dict,
    preferred_time: str | None,
) -> bool:
    """Match broad time ranges or screenings within 60 minutes of a requested time."""
    preference = (preferred_time or "").strip().lower()

    if preference in ("", "any", "any time", "no preference"):
        return True

    try:
        value = str(row.get("showtime", ""))
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PK_TZ)

        local_dt = dt.astimezone(PK_TZ)
        hour = local_dt.hour
        actual_minutes = hour * 60 + local_dt.minute
    except (ValueError, TypeError):
        return False

    ranges = {
        "morning": lambda h: 5 <= h < 12,
        "afternoon": lambda h: 12 <= h < 17,
        "evening": lambda h: 17 <= h < 21,
        "night": lambda h: h >= 21 or h < 5,
    }

    if preference in ranges:
        return ranges[preference](hour)

    match = re.fullmatch(
        r"(?:(?:around|about|at|near|roughly|approximately)\\s+)?"
        r"(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?"
        r"(?:\\s*(?:ish|around))?",
        preference,
    )

    if match:
        requested_hour = int(match.group(1))
        requested_minute = int(match.group(2) or 0)
        meridiem = match.group(3)

        if requested_minute > 59 or requested_hour > 23:
            logger.warning(
                "Invalid time preference %r; not filtering by time.",
                preference,
            )
            return True

        if meridiem:
            if not 1 <= requested_hour <= 12:
                logger.warning(
                    "Invalid clock time %r; not filtering by time.",
                    preference,
                )
                return True

            requested_hour %= 12
            if meridiem == "pm":
                requested_hour += 12

        requested_minutes = requested_hour * 60 + requested_minute
        difference = abs(actual_minutes - requested_minutes)
        difference = min(difference, 1440 - difference)

        # "Around 8 PM" means within one hour either side.
        return difference <= 60

    logger.warning(
        "Unrecognized time preference %r; not filtering by time.",
        preference,
    )
    return True


def get_movie_showtimes(
    movie_title: str,
    preferred_time: str = "any",
    limit: int = 100,
) -> list[dict]:
    """
    Query cached schedules from Supabase.

    This function never scrapes websites. It is intended for the MCP tool
    called by the chatbot during a user request.
    """
    title = (movie_title or "").strip()

    if not title:
        return []

    safe_limit = max(1, min(int(limit), 500))

    response = (
        supabase.table("showtimes")
        .select(
            "id,movie_title,cinema_name,showtime,ticket_price,booking_link"
        )
        .ilike("movie_title", f"%{title}%")
        .order("showtime")
        .limit(safe_limit)
        .execute()
    )

    rows = response.data or []

    return [
        row
        for row in rows
        if _matches_preferred_time(row, preferred_time)
    ]


def test_connection() -> None:
    """Verify credentials and confirm that the showtimes table is accessible."""
    response = (
        supabase.table("showtimes")
        .select("id")
        .limit(1)
        .execute()
    )

    print("Supabase connection successful.")
    print("The showtimes table is accessible.")
    print(f"Test query returned {len(response.data or [])} row(s).")
    print(
        "Reminder: verify the UNIQUE constraint on "
        "(movie_title, cinema_name, showtime) in Supabase."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        test_connection()
    except Exception as exc:
        logger.exception(
            "Supabase connection test failed. Check the .env credentials, "
            "network access, table name, and table permissions. Error: %s",
            exc,
        )
        raise SystemExit(1)

# BEGIN SAFE SHOWTIME RECONCILIATION

def reconcile_showtimes_snapshot(rows: list[dict], context: str = "source snapshot") -> int:
    """Retire stale future sessions only inside dates covered by a complete snapshot.

    Call only after a non-empty source scrape and successful upsert. This function
    rechecks the Lahore allowlist, backs up candidate rows before deletion, refuses
    suspiciously large deletions, and verifies the resulting database state.
    """
    import json
    from datetime import date, time, timedelta, timezone
    from pathlib import Path

    if not isinstance(rows, list) or not rows:
        logger.warning("[%s] Empty snapshot; skipping showtime reconciliation.", context)
        return 0

    approved = filter_lahore_showtimes(rows, context=f"{context} reconciliation")
    if len(approved) != len(rows) or not approved:
        logger.error(
            "[%s] Snapshot included unapproved/invalid rows; skipping reconciliation (%d/%d approved).",
            context, len(approved), len(rows),
        )
        return 0

    now = datetime.now(timezone.utc)

    def parse_utc(raw_value: object) -> datetime | None:
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=PK_TZ)
            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None

    def key_for(row: dict) -> tuple[str, str, str] | None:
        stamp = parse_utc(row.get("showtime"))
        title = str(row.get("movie_title") or "").strip()
        venue = str(row.get("cinema_name") or "").strip()
        if not stamp or not title or not venue:
            return None
        return title, venue, stamp.isoformat()

    # One independent date window per venue. A session's local calendar date is
    # used, not its UTC date, to avoid accidentally shifting midnight screenings.
    observed_by_venue: dict[str, dict[str, set]] = {}
    for row in approved:
        key = key_for(row)
        if not key:
            logger.error("[%s] Invalid row in snapshot; refusing reconciliation.", context)
            return 0
        stamp = parse_utc(row.get("showtime"))
        if stamp is None or stamp <= now:
            continue
        venue = key[1]
        state = observed_by_venue.setdefault(venue, {"keys": set(), "dates": set()})
        state["keys"].add(key)
        state["dates"].add(stamp.astimezone(PK_TZ).date())

    if not observed_by_venue:
        logger.warning("[%s] Snapshot contains no future sessions; no rows deleted.", context)
        return 0

    def fetch_window(venue: str, lower_iso: str, upper_iso: str) -> list[dict] | None:
        collected: list[dict] = []
        page_size = 500
        offset = 0
        maximum = 5000
        while offset < maximum:
            response = (
                supabase.table("showtimes")
                .select("movie_title,cinema_name,showtime,booking_link,ticket_price")
                .eq("cinema_name", venue)
                .gte("showtime", lower_iso)
                .lt("showtime", upper_iso)
                .order("showtime")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            page = response.data or []
            collected.extend(page)
            if len(page) < page_size:
                return collected
            offset += page_size
        logger.error(
            "[%s] Venue %s exceeded %d rows in its coverage window; refusing reconciliation.",
            context, venue, maximum,
        )
        return None

    total_removed = 0
    for venue, state in observed_by_venue.items():
        dates = sorted(state["dates"])
        first_day = dates[0]
        last_day = dates[-1]
        start_local = datetime.combine(first_day, time.min, tzinfo=PK_TZ)
        end_local = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=PK_TZ)
        lower = max(start_local.astimezone(timezone.utc), now).isoformat()
        upper = end_local.astimezone(timezone.utc).isoformat()
        if lower >= upper:
            continue

        existing = fetch_window(venue, lower, upper)
        if existing is None:
            continue

        observed = state["keys"]
        stale = []
        for row in existing:
            key = key_for(row)
            if key is None:
                logger.error("[%s] Existing row has invalid fields; refusing venue reconciliation: %r", context, row)
                stale = []
                existing = None
                break
            if key not in observed:
                stale.append(row)
        if existing is None:
            continue
        if not stale:
            logger.info("[%s] %s: no stale future rows found in %s..%s.", context, venue, first_day, last_day)
            continue

        # Safety rails: never erase the venue's entire known window in one run,
        # and pause if more than half of a sufficiently large window is stale.
        if len(stale) == len(existing) or len(stale) > max(10, len(existing) // 2):
            logger.error(
                "[%s] Refusing suspicious reconciliation for %s: %d/%d rows would be removed. "
                "Source snapshot needs manual review.",
                context, venue, len(stale), len(existing),
            )
            continue

        backup_dir = Path(__file__).resolve().parent / "scrape_output" / "reconciliation_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (
            "stale_showtimes_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + ".json"
        )
        backup_payload = {
            "context": context,
            "venue": venue,
            "coverage_start_local": first_day.isoformat(),
            "coverage_end_local": last_day.isoformat(),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": stale,
        }
        backup_path.write_text(json.dumps(backup_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.warning(
            "[%s] %s: backing up %d stale future row(s) to %s before deletion.",
            context, venue, len(stale), backup_path,
        )

        for row in stale:
            try:
                (
                    supabase.table("showtimes")
                    .delete()
                    .eq("movie_title", str(row["movie_title"]))
                    .eq("cinema_name", str(row["cinema_name"]))
                    .eq("showtime", str(row["showtime"]))
                    .execute()
                )
            except Exception:
                logger.exception(
                    "[%s] Delete request failed for %r; stopping this venue's cleanup.", context, key_for(row)
                )
                break

        remaining = fetch_window(venue, lower, upper)
        if remaining is None:
            logger.error("[%s] Could not verify reconciliation for %s; inspect backup %s.", context, venue, backup_path)
            continue
        remaining_keys = {key_for(row) for row in remaining}
        unresolved = [row for row in stale if key_for(row) in remaining_keys]
        removed = len(stale) - len(unresolved)
        total_removed += removed
        if unresolved:
            logger.error(
                "[%s] %s: reconciliation verification found %d stale row(s) still present.",
                context, venue, len(unresolved),
            )
        else:
            logger.info(
                "[%s] %s: reconciliation verified; removed %d stale future row(s).",
                context, venue, removed,
            )

    return total_removed

# END SAFE SHOWTIME RECONCILIATION

# BEGIN POST-SCRAPE SHOWTIME AUDITOR

def audit_showtime_snapshots(snapshots, context="post-scrape") -> dict:
    """Read-only consistency audit of fresh scraper snapshots vs Supabase.

    snapshots is an iterable of (source_label, complete, rows). Incomplete or
    empty source runs are reported but never treated as evidence that sessions
    should be removed. This function does not mutate Supabase.

    This verifies database/snapshot consistency and selected raw/display time
    invariants; it is not an independent guarantee that the website's own HTML
    was interpreted correctly.
    """
    import re
    from collections import Counter
    from datetime import date, datetime, time, timedelta, timezone
    from pathlib import Path

    now = datetime.now(timezone.utc)
    report = {
        "created_at_utc": now.isoformat(),
        "context": context,
        "audit_scope": (
            "read-only DB-vs-snapshot consistency plus raw/display-time sanity checks; "
            "not an independent certification of source website truth"
        ),
        "sources": [],
        "venues": [],
        "summary": {},
    }
    venue_states = {}
    total_parser_warnings = 0
    total_snapshot_duplicates = 0
    total_live_keys = 0
    total_stored_keys = 0
    total_stored_only = 0
    total_live_only = 0

    def parse_stamp(raw):
        if raw is None or str(raw).strip() == "":
            return None
        try:
            dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=PK_TZ)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None

    def venue_key(value):
        return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

    def session_key(title, venue, stamp):
        # Database/UI identity is one displayed slot: movie + venue + timestamp.
        return (str(title or "").strip().casefold(), venue_key(venue), stamp.isoformat())

    def source_session_id(row):
        """Return a source ticket/session ID when one is explicitly available."""
        direct = str(row.get("session_id") or "").strip()
        if direct:
            return direct
        link = str(row.get("booking_link") or "")
        match = re.search(r"[?&](?:txtSessionId|sessionId)=([^&#]+)", link, re.I)
        return match.group(1).strip() if match else ""

    def parse_display_minutes(value):
        text = str(value or "").strip()
        match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?", text, re.I)
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        meridiem = (match.group(3) or "").replace(".", "").replace(" ", "").casefold()
        if minute > 59 or hour > 24 or hour == 0 and meridiem or hour > 12 and meridiem:
            return None
        if meridiem:
            if hour < 1 or hour > 12:
                return None
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif hour == 24:
            if minute:
                return None
            hour = 0
        return hour * 60 + minute

    # Build the live expected key-set and check parser-internal time consistency.
    for snapshot in snapshots or []:
        try:
            label, complete, rows = snapshot
        except Exception:
            report["sources"].append({
                "label": "<invalid snapshot descriptor>",
                "status": "NON_AUTHORITATIVE",
                "reason": "Snapshot must be (label, complete, rows).",
            })
            continue

        rows = list(rows or [])
        source_doc = {
            "label": str(label),
            "authoritative": bool(complete and rows),
            "input_rows": len(rows),
            "approved_future_rows": 0,
            "venue_count": 0,
            "reason": None,
        }
        if not complete or not rows:
            source_doc["status"] = "NON_AUTHORITATIVE"
            source_doc["reason"] = "source failed, was empty, or was marked incomplete; no comparison used"
            report["sources"].append(source_doc)
            logger.warning(
                "[Post-scrape audit] %s: NON-AUTHORITATIVE (complete=%s rows=%d); "
                "no deletion or absence inference permitted.",
                label, bool(complete), len(rows),
            )
            continue

        # Vista listings can include non-Lahore branches in the HTML. Their
        # explicit false flag means they are outside this Lahore product.
        eligible = [row for row in rows if row.get("is_lahore_venue") is not False]
        approved = filter_lahore_showtimes(eligible, context=f"audit {label}")
        if not approved:
            source_doc["authoritative"] = False
            source_doc["status"] = "NON_AUTHORITATIVE"
            source_doc["reason"] = "no approved rows remained after Lahore allowlist filtering"
            report["sources"].append(source_doc)
            logger.warning("[Post-scrape audit] %s: NON-AUTHORITATIVE (no approved Lahore rows).", label)
            continue

        future_rows = 0
        venues_for_source = set()
        invalid_rows = 0
        source_dupes = 0
        # Count exact source-record repeats separately from distinct ticketing
        # session IDs that happen to share one movie/venue/start time.
        source_seen_identities = Counter()
        source_ids_by_slot = {}
        for row in approved:
            stamp = parse_stamp(row.get("showtime"))
            title = str(row.get("movie_title") or "").strip()
            venue = str(row.get("cinema_name") or "").strip()
            if stamp is None or not title or not venue:
                invalid_rows += 1
                continue
            if stamp <= now:
                continue
            future_rows += 1
            venues_for_source.add(venue_key(venue))
            state = venue_states.setdefault(venue_key(venue), {
                "canonical_venue": venue,
                "venue_labels": set(),
                "source_labels": set(),
                "live_counts": Counter(),
                "live_rows": {},
                "dates": set(),
                "parser_warnings": [],
                "snapshot_duplicate_count": 0,
                "invalid_rows": 0,
            })
            state["venue_labels"].add(venue)
            state["source_labels"].add(str(label))
            # The DB/UI represent one displayed slot as movie + venue + time.
            # Some cinemas can expose multiple ticketing session IDs at that
            # exact time. Those are not duplicate source records; repeated
            # occurrences of the SAME source session ID still are duplicates.
            key = session_key(title, venue, stamp)
            source_id = source_session_id(row)
            source_identity = (*key, source_id.casefold()) if source_id else key
            source_seen_identities[source_identity] += 1
            if source_id:
                source_ids_by_slot.setdefault(key, set()).add(source_id.casefold())

            state["live_counts"][key] += 1
            state["live_rows"].setdefault(key, row)
            state["dates"].add(stamp.astimezone(PK_TZ).date())
            if source_seen_identities[source_identity] > 1:
                state["snapshot_duplicate_count"] += 1
                source_dupes += 1

            raw = row.get("raw_showtime")
            display = row.get("display_time")
            if raw and display:
                raw_stamp = parse_stamp(raw)
                if raw_stamp is not None and raw_stamp.replace(second=0, microsecond=0) != stamp.replace(second=0, microsecond=0):
                    state["parser_warnings"].append({
                        "kind": "raw_timestamp_mismatch",
                        "movie_title": title,
                        "cinema_name": venue,
                        "raw_showtime": str(raw),
                        "parsed_showtime": stamp.isoformat(),
                        "display_time": str(display),
                    })
                display_minutes = parse_display_minutes(display)
                local_stamp = stamp.astimezone(PK_TZ)
                if display_minutes is not None and display_minutes != local_stamp.hour * 60 + local_stamp.minute:
                    state["parser_warnings"].append({
                        "kind": "visible_time_mismatch",
                        "movie_title": title,
                        "cinema_name": venue,
                        "raw_showtime": str(raw),
                        "parsed_showtime": stamp.isoformat(),
                        "display_time": str(display),
                        "parsed_local_time": local_stamp.strftime("%I:%M %p").lstrip("0"),
                    })

        source_doc["approved_future_rows"] = future_rows
        source_doc["venue_count"] = len(venues_for_source)
        source_doc["invalid_rows"] = invalid_rows
        source_doc["duplicate_snapshot_keys"] = source_dupes
        parallel_id_groups = [ids for ids in source_ids_by_slot.values() if len(ids) > 1]
        source_doc["same_time_distinct_session_id_groups"] = len(parallel_id_groups)
        source_doc["same_time_distinct_session_id_extras"] = sum(len(ids) - 1 for ids in parallel_id_groups)
        if future_rows == 0:
            source_doc["authoritative"] = False
            source_doc["status"] = "NON_AUTHORITATIVE"
            source_doc["reason"] = "no valid future Lahore sessions parsed; safe to audit, not to infer closure"
        elif invalid_rows or source_dupes:
            source_doc["status"] = "SNAPSHOT_WARNINGS"
            source_doc["authoritative"] = False
            source_doc["reason"] = "invalid rows or duplicate keys detected; flag for review"
        else:
            source_doc["status"] = "SNAPSHOT_OK"
        report["sources"].append(source_doc)
        logger.info(
            "[Post-scrape audit] %s: %s, %d future approved rows across %d venue(s), "
            "invalid=%d duplicate_keys=%d.",
            label, source_doc["status"], future_rows, len(venues_for_source), invalid_rows, source_dupes,
        )

    if not venue_states:
        report["summary"] = {
            "venues_compared": 0,
            "live_unique_sessions": 0,
            "stored_unique_sessions": 0,
            "stored_only": 0,
            "live_only": 0,
            "parser_warnings": 0,
            "status": "NO_AUTHORITATIVE_SNAPSHOTS",
        }
        logger.warning("[Post-scrape audit] No authoritative future snapshots available; no DB reconciliation inferred.")
    else:
        # Query the union of all observed windows once, paging every result.
        all_dates = [day for state in venue_states.values() for day in state["dates"]]
        first_day, last_day = min(all_dates), max(all_dates)
        lower_local = datetime.combine(first_day, time.min, tzinfo=PK_TZ)
        lower_utc = max(lower_local.astimezone(timezone.utc), now)
        upper_utc = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=PK_TZ).astimezone(timezone.utc)
        database_rows = []
        query_error = None
        try:
            offset = 0
            page_size = 500
            max_rows = 20000
            while offset < max_rows:
                response = (
                    supabase.table("showtimes")
                    .select("movie_title,cinema_name,showtime,booking_link,ticket_price")
                    .gte("showtime", lower_utc.isoformat())
                    .lt("showtime", upper_utc.isoformat())
                    .order("showtime")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                page = response.data or []
                database_rows.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
            else:
                query_error = f"Query exceeded safety cap of {max_rows} rows."
        except Exception as exc:
            query_error = f"{type(exc).__name__}: {exc}"

        if query_error:
            report["database_query_error"] = query_error
            logger.error("[Post-scrape audit] Database window query failed or exceeded cap: %s", query_error)

        for state in venue_states.values():
            venue = state["canonical_venue"]
            norm_venue = venue_key(venue)
            dates = sorted(state["dates"])
            first, last = dates[0], dates[-1]
            expected = set(state["live_counts"].keys())
            db_counts = Counter()
            db_rows_for_venue = []
            label_variants = set()

            if not query_error:
                for row in database_rows:
                    stored_venue = str(row.get("cinema_name") or "").strip()
                    if venue_key(stored_venue) != norm_venue:
                        continue
                    stamp = parse_stamp(row.get("showtime"))
                    if stamp is None or stamp <= now:
                        continue
                    local_day = stamp.astimezone(PK_TZ).date()
                    if local_day < first or local_day > last:
                        continue
                    title = str(row.get("movie_title") or "").strip()
                    key = session_key(title, stored_venue, stamp)
                    db_counts[key] += 1
                    db_rows_for_venue.append(row)
                    if stored_venue != venue:
                        label_variants.add(stored_venue)

            stored = set(db_counts.keys())
            stored_only_keys = sorted(stored - expected)
            live_only_keys = sorted(expected - stored)
            snapshot_dupes = int(state["snapshot_duplicate_count"])
            db_dupe_keys = [key for key, count in db_counts.items() if count > 1]
            parser_warnings = state["parser_warnings"]
            total_parser_warnings += len(parser_warnings)
            total_snapshot_duplicates += snapshot_dupes
            total_live_keys += len(expected)
            total_stored_keys += len(stored)
            total_stored_only += len(stored_only_keys)
            total_live_only += len(live_only_keys)

            if query_error:
                status = "DATABASE_CHECK_FAILED"
            elif stored_only_keys or live_only_keys or db_dupe_keys:
                status = "MISMATCH"
            elif parser_warnings or snapshot_dupes or label_variants:
                status = "MATCH_WITH_WARNINGS"
            else:
                status = "MATCH"

            def key_detail(key):
                stamp = datetime.fromisoformat(key[2]).astimezone(PK_TZ)
                return {
                    "movie_title_key": key[0],
                    "cinema_key": key[1],
                    "showtime_utc": key[2],
                    "showtime_lahore": stamp.strftime("%Y-%m-%d %I:%M %p").lstrip("0"),
                }

            venue_doc = {
                "venue": venue,
                "source_labels": sorted(state["source_labels"]),
                "coverage_start_local": first.isoformat(),
                "coverage_end_local": last.isoformat(),
                "live_unique_sessions": len(expected),
                "stored_unique_sessions": len(stored),
                "stored_database_rows": len(db_rows_for_venue),
                "stored_only_count": len(stored_only_keys),
                "live_only_count": len(live_only_keys),
                "duplicate_snapshot_keys": snapshot_dupes,
                "duplicate_database_keys": len(db_dupe_keys),
                "database_cinema_label_variants": sorted(label_variants),
                "parser_consistency_warnings": parser_warnings[:100],
                "stored_only_samples": [key_detail(key) for key in stored_only_keys[:25]],
                "live_only_samples": [key_detail(key) for key in live_only_keys[:25]],
                "status": status,
            }
            report["venues"].append(venue_doc)
            log_method = logger.info if status == "MATCH" else logger.warning
            log_method(
                "[Post-scrape audit] %s | %s..%s | live=%d stored=%d "
                "stored_only=%d live_only=%d duplicate_db=%d parser_warnings=%d label_variants=%d | %s",
                venue, first, last, len(expected), len(stored), len(stored_only_keys), len(live_only_keys),
                len(db_dupe_keys), len(parser_warnings), len(label_variants), status,
            )
            for sample in venue_doc["stored_only_samples"][:5]:
                logger.warning("[Post-scrape audit] STORED-ONLY candidate: %s", sample)
            for sample in venue_doc["live_only_samples"][:5]:
                logger.warning("[Post-scrape audit] LIVE-ONLY session: %s", sample)
            for warning in parser_warnings[:5]:
                logger.warning("[Post-scrape audit] PARSER CONSISTENCY WARNING: %s", warning)
            for variant in sorted(label_variants):
                logger.warning("[Post-scrape audit] Cinema label variant for %s: %r", venue, variant)

        all_statuses = [venue["status"] for venue in report["venues"]]
        if any(status in {"MISMATCH", "DATABASE_CHECK_FAILED"} for status in all_statuses):
            overall = "ISSUES_FOUND"
        elif any(status == "MATCH_WITH_WARNINGS" for status in all_statuses) or any(
            source.get("status") != "SNAPSHOT_OK" for source in report["sources"]
        ):
            overall = "MATCH_WITH_WARNINGS_OR_INCOMPLETE_SOURCES"
        else:
            overall = "PASS"
        report["summary"] = {
            "venues_compared": len(report["venues"]),
            "live_unique_sessions": total_live_keys,
            "stored_unique_sessions": total_stored_keys,
            "stored_only": total_stored_only,
            "live_only": total_live_only,
            "parser_warnings": total_parser_warnings,
            "duplicate_snapshot_keys": total_snapshot_duplicates,
            "non_authoritative_sources": sum(source.get("status") != "SNAPSHOT_OK" for source in report["sources"]),
            "status": overall,
        }
        logger.info(
            "[Post-scrape audit] SUMMARY: status=%s venues=%d live=%d stored=%d "
            "stored_only=%d live_only=%d parser_warnings=%d non_authoritative_sources=%d.",
            overall, report["summary"]["venues_compared"], total_live_keys, total_stored_keys,
            total_stored_only, total_live_only, total_parser_warnings,
            report["summary"]["non_authoritative_sources"],
        )

    try:
        output_dir = Path(__file__).resolve().parent / "scrape_output" / "showtime_audits"
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", str(context).casefold()).strip("_") or "scrape"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        path = output_dir / f"post_scrape_audit_{timestamp}_{slug}.json"
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info("[Post-scrape audit] Local JSON report written: %s", path)
    except Exception:
        logger.exception("[Post-scrape audit] Could not write the local report; source/database data were not changed.")

    return report

# END POST-SCRAPE SHOWTIME AUDITOR
