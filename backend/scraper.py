"""Unified Lahore cinema scraper: showtimes, movie catalogue, and posters.

All provider scraping logic and shared source/time settings live in this file.
Supabase remains in database.py; Lahore write policy remains in location_guard.py;
FastAPI endpoints and scheduling remain in main.py.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core showtime scraper (public entry point: run_scrape_job)
# ---------------------------------------------------------------------------

import asyncio
import csv
import json
import logging
import os
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from database import upsert_showtimes, reconcile_showtimes_snapshot, audit_showtime_snapshots

# =============================================================================
# CENTRAL SCRAPER CONFIGURATION — edit URLs, branches, timeouts, and timezone here.
# The Lahore venue allowlist remains centralized in location_guard.py because
# database.py also uses it; keeping it separate avoids a circular import.
SCRAPER_CONFIG = {
    "timezone": os.getenv("TIMEZONE", "Asia/Karachi"),
    "request_timeout_seconds": int(os.getenv("SCRAPER_REQUEST_TIMEOUT", "20")),
    "max_concurrency": int(os.getenv("SCRAPER_MAX_CONCURRENCY", "6")),
    "catalog_timeout_seconds": int(os.getenv("CATALOG_REQUEST_TIMEOUT", "16")),
    "cinestar_timeout_seconds": int(os.getenv("CINESTAR_REQUEST_TIMEOUT", "18")),
    "user_agent": os.getenv("SCRAPER_USER_AGENT", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "chalo": {
        "base_url": os.getenv("CHALO_BASE_URL", "https://www.chalocinemas.com"),
        "branch_id": os.getenv("CHALO_BRANCH_ID", "1"),
        "cinema_name": "Chalo Cinema - Fortress Square Mall",
        "source": "Chalo Cinema",
    },
    "cue": {
        "source": "Cue Cinemas",
        "base_url": os.getenv("CUE_BASE_URL", "https://cuecinemas.com"),
        "cinema_name": "CUE Cinemas",
    },
    "universal": {
        "source": "Universal Cinemas",
        "base_url": os.getenv("UNIVERSAL_BASE_URL", "https://universalcinemas.com"),
        "cinema_name": "Universal Cinemas Emporium Mall, Lahore",
    },
    "cinepax": {
        "base_url": os.getenv("CINEPAX_BASE_URL", "https://web.cinepax.com"),
        "lahore_branches": {"CINEPAX AMANAH MALL", "CINEPAX PACKAGES MALL"},
    },
    "vista_listings": [
        ("/Browsing/Movies/NowShowing", "now_showing"),
        ("/Browsing/Movies/ComingSoon", "coming_soon"),
    ],
    "cinestar": {
        "base_url": os.getenv("CINESTAR_BASE_URL", "https://cinestar.pk"),
        "listings": [
            ("/Browsing/Movies/NowShowing", "now_showing"),
            ("/Browsing/Movies/ComingSoon", "coming_soon"),
        ],
        "branches": [
            {"cinema_name": "CineStar Xinhua Mall Lahore", "cinema_code": "0001", "detail_url": os.getenv("CINESTAR_BASE_URL", "https://cinestar.pk").rstrip("/") + "/Browsing/Cinemas/Details/0001"},
            {"cinema_name": "CineStar DHA Raya Lahore", "cinema_code": "0002", "detail_url": os.getenv("CINESTAR_BASE_URL", "https://cinestar.pk").rstrip("/") + "/Browsing/Cinemas/Details/0002"},
            {"cinema_name": "CineStar Township Lahore", "cinema_code": "0005", "detail_url": os.getenv("CINESTAR_BASE_URL", "https://cinestar.pk").rstrip("/") + "/Browsing/Cinemas/Details/0005"},
        ],
    },
    "dha": {
        "base_url": os.getenv("DHA_CINEMAS_BASE_URL", "https://dhacinemas.pk"),
        "showtimes_url": os.getenv("DHA_CINEMAS_SHOWTIMES_URL", "https://dhacinemas.pk/showtimes"),
    },
    "cinegold": {
        "base_url": "https://www.cinegoldplex.com",
        "showtimes_url": "https://www.cinegoldplex.com/showtimes",
        "schedule_endpoint": "https://www.cinegoldplex.com/api/get_schedules",
        "lahore_branch_id": "1",
        "lounge_ids": {"Gold Lounge": "1", "Red Lounge": "2"},
        "lookahead_days": 7,
    },
}


logger = logging.getLogger("scraper")

PK_TZ = ZoneInfo(SCRAPER_CONFIG["timezone"])
REQUEST_TIMEOUT = SCRAPER_CONFIG["request_timeout_seconds"]
MAX_CONCURRENCY = SCRAPER_CONFIG["max_concurrency"]

OUTPUT_DIR = (
    Path("/tmp/filmup/scrape_output")
    if os.getenv("VERCEL")
    else Path("scrape_output")
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_CSV = OUTPUT_DIR / "showtimes_raw.csv"
DEDUPED_CSV = OUTPUT_DIR / "showtimes_deduplicated.csv"

CINEPAX_BASE_URL = SCRAPER_CONFIG["cinepax"]["base_url"]

VISTA_SITES = [(SCRAPER_CONFIG[key]["source"], SCRAPER_CONFIG[key]["base_url"]) for key in ("cue", "universal")]

CINEPAX_LAHORE_BRANCHES = set(SCRAPER_CONFIG["cinepax"]["lahore_branches"])

LISTING_PATH = SCRAPER_CONFIG["vista_listings"][0][0]

DB_COLUMNS = [
    "movie_title",
    "cinema_name",
    "showtime",
    "ticket_price",
    "booking_link",
]

CSV_COLUMNS = [
    "scrape_run_at",
    "source",
    "movie_title",
    "cinema_name",
    "showtime",
    "raw_showtime",
    "display_time",
    "ticket_price",
    "booking_link",
    "source_url",
    "movie_detail_url",
    "film_code",
    "scheduled_film_id",
    "movie_genre",
    "movie_cast",
    "movie_duration",
    "movie_rating",
    "poster_url",
    "backdrop_url",
    "cinema_id",
    "session_id",
    "screen_name",
    "screen_number",
    "seats_available",
    "attributes",
    "business_date",
    "calendar_date",
    "late_night",
    "is_lahore_venue",
    "duplicate_key_count",
    "is_duplicate",
]

_thread_local = threading.local()


# ---------------------------------------------------------------------
# HTTP and normalization helpers
# ---------------------------------------------------------------------

def get_http_session() -> requests.Session:
    """Reuse an HTTP session within each worker thread."""
    session = getattr(_thread_local, "session", None)

    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": SCRAPER_CONFIG["user_agent"],
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/json,*/*;q=0.8"
            ),
        })
        _thread_local.session = session

    return session


def http_get(url: str, params: dict | None = None) -> requests.Response:
    response = get_http_session().get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def to_local_iso(raw_datetime: str | None) -> str | None:
    """Normalize timestamps to Pakistan time."""
    if not raw_datetime:
        return None

    try:
        dt = datetime.fromisoformat(raw_datetime.strip())
    except (ValueError, TypeError):
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PK_TZ)
    else:
        dt = dt.astimezone(PK_TZ)

    return dt.isoformat()


def is_lahore_venue(source: str, cinema_name: str) -> bool:
    """Explicitly allow only configured Lahore branches."""
    name = normalize_text(cinema_name)

    if source == "Cue Cinemas":
        # The tested Cue showtime page labels its Lahore branch "CUE Cinemas".
        return name in {
            "cue cinemas",
            "cue cinema",
            "cue cinemas lahore",
        } or ("cue" in name and ("lahore" in name or "gulberg" in name))

    if source == "Universal Cinemas":
        return "emporium" in name and "lahore" in name

    if source == "Cinepax":
        return name.upper() in CINEPAX_LAHORE_BRANCHES

    return False


def extract_film_code(url: str) -> str | None:
    match = re.search(
        r"/([hmf]-HO\d+)(?:/|$)",
        urlparse(url).path,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


# ---------------------------------------------------------------------
# Cue Cinemas and Universal Cinemas
# ---------------------------------------------------------------------

def find_vista_movies(
    html: str,
    page_url: str,
    base_url: str,
) -> list[dict]:
    """Extract unique movie detail links, independent of URL casing."""
    soup = BeautifulSoup(html, "html.parser")
    movies = []
    seen_urls = set()

    # Use a.movie first, then fall back to any matching detail link.
    anchors = list(soup.select("a.movie[href]"))

    if not any(
        "/movies/details/" in (a.get("href") or "").lower()
        for a in anchors
    ):
        anchors = list(soup.select("a[href]"))

    for anchor in anchors:
        href = (anchor.get("href") or "").strip()

        if not href or "/movies/details/" not in href.lower():
            continue

        detail_url = urljoin(page_url, href)
        parsed = urlparse(detail_url)

        if parsed.scheme not in {"http", "https"}:
            continue

        if parsed.netloc.lower() != urlparse(base_url).netloc.lower():
            continue

        # Event pages are not regular movie pages.
        if "/f-" in parsed.path.lower():
            continue

        detail_url = parsed._replace(fragment="").geturl()

        if detail_url in seen_urls:
            continue

        name_element = anchor.select_one(".name")
        title = (
            name_element.get_text(" ", strip=True)
            if name_element
            else anchor.get_text(" ", strip=True)
        )

        if not title:
            continue

        seen_urls.add(detail_url)
        movies.append({
            "title": title,
            "detail_url": detail_url,
        })

    return movies


def fetch_vista_listing(base_url: str) -> list[dict]:
    listing_url = urljoin(
        base_url.rstrip("/") + "/",
        LISTING_PATH.lstrip("/"),
    )

    response = http_get(listing_url)
    movies = find_vista_movies(
        response.text,
        response.url,
        base_url,
    )

    logger.info(
        "[%s] Found %d movie links.",
        base_url,
        len(movies),
    )

    if not movies:
        raise RuntimeError(
            f"No movie detail links found at {listing_url}; "
            "the listing may have changed."
        )

    return movies


def parse_vista_detail(
    html: str,
    movie_title: str,
    detail_url: str,
    source: str,
) -> list[dict]:
    """Extract sessions from raw HTML; no browser is required."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    film_code = extract_film_code(detail_url)

    for film_item in soup.select(".film-item"):
        title_element = film_item.select_one(".film-title")

        if not title_element:
            continue

        cinema_name = title_element.get_text(" ", strip=True)
        in_lahore = is_lahore_venue(source, cinema_name)

        for session_link in film_item.select("a.session-time"):
            time_element = session_link.select_one("time[datetime]")

            if not time_element:
                continue

            raw_datetime = time_element.get("datetime")
            showtime = to_local_iso(raw_datetime)

            if not showtime:
                logger.warning(
                    "[%s] Invalid datetime for %s: %r",
                    source,
                    movie_title,
                    raw_datetime,
                )
                continue

            href = session_link.get("href")
            booking_link = urljoin(detail_url, href) if href else None

            rows.append({
                "source": source,
                "movie_title": movie_title,
                "cinema_name": cinema_name,
                "showtime": showtime,
                "raw_showtime": raw_datetime,
                "display_time": session_link.get_text(" ", strip=True),
                "ticket_price": None,
                "booking_link": booking_link,
                "source_url": detail_url,
                "movie_detail_url": detail_url,
                "film_code": film_code,
                "scheduled_film_id": None,
                "movie_genre": None,
                "movie_cast": None,
                "movie_duration": None,
                "movie_rating": None,
                "poster_url": None,
                "backdrop_url": None,
                "cinema_id": None,
                "session_id": None,
                "screen_name": None,
                "screen_number": None,
                "seats_available": None,
                "attributes": None,
                "business_date": None,
                "calendar_date": None,
                "late_night": None,
                "is_lahore_venue": in_lahore,
            })

    return rows


async def scrape_vista_site(
    source: str,
    base_url: str,
) -> list[dict]:
    """Collect one Vista site's complete Now Showing snapshot.

    A failed detail page or an empty detail-page parse invalidates the entire
    source snapshot. That prevents a partially parsed page from being treated as
    authoritative when stale showtimes are reconciled.
    """
    movies = await asyncio.to_thread(fetch_vista_listing, base_url)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def scrape_one(movie: dict) -> list[dict]:
        async with semaphore:
            response = await asyncio.to_thread(http_get, movie["detail_url"])
        rows = parse_vista_detail(response.text, movie["title"], response.url, source)
        if not rows:
            raise RuntimeError(
                f"[{source}] Detail page parsed zero session rows for {movie['title']!r}; "
                "refusing to treat the source snapshot as complete."
            )
        logger.info(
            "[%s] %s: %d sessions, %d in Lahore.",
            source,
            movie["title"],
            len(rows),
            sum(bool(row.get("is_lahore_venue")) for row in rows),
        )
        return rows

    results = await asyncio.gather(
        *(scrape_one(movie) for movie in movies),
        return_exceptions=True,
    )
    failures = [
        (movie.get("title", "unknown"), result)
        for movie, result in zip(movies, results)
        if isinstance(result, Exception)
    ]
    if failures:
        logger.error(
            "[%s] %d/%d detail page(s) failed; discarding this partial snapshot. First failures: %s",
            source,
            len(failures),
            len(movies),
            [(title, str(error)) for title, error in failures[:5]],
        )
        raise RuntimeError(f"{source} snapshot incomplete ({len(failures)} detail page failure(s)).")

    all_rows = [row for result in results for row in result]
    if not all_rows:
        raise RuntimeError(f"[{source}] No showtime rows were parsed; snapshot is not authoritative.")

    logger.info("[%s] Complete source snapshot contains %d rows.", source, len(all_rows))
    return all_rows



# ---------------------------------------------------------------------
# Cinepax direct JSON API
# ---------------------------------------------------------------------

def fetch_cinepax_json(
    path: str,
    params: dict | None = None,
):
    url = urljoin(CINEPAX_BASE_URL + "/", path.lstrip("/"))
    response = http_get(url, params=params)
    return response.json(), response.url


async def scrape_cinepax_branch(cinema: dict) -> list[dict]:
    cinema_id = cinema["id"]
    cinema_name = cinema["name"]

    payload, request_url = await asyncio.to_thread(
        fetch_cinepax_json,
        "/api/movies",
        {"cinemaId": cinema_id},
    )

    if isinstance(payload, list):
        movies = payload
    elif isinstance(payload, dict):
        movies = payload.get("movies", [])
    else:
        raise ValueError("Unexpected Cinepax movie API response.")

    rows = []

    for movie in movies:
        title = movie.get("title") or "Unknown movie"
        screens = movie.get("screens") or {}

        for fallback_screen_name, screen in screens.items():
            if not isinstance(screen, dict):
                continue

            for session in screen.get("sessions") or []:
                raw_datetime = session.get("showtime")
                showtime = to_local_iso(raw_datetime)

                if not showtime:
                    continue

                attributes = session.get("attributes") or []
                if not isinstance(attributes, list):
                    attributes = [attributes]

                rows.append({
                    "source": "Cinepax",
                    "movie_title": title,
                    "cinema_name": cinema_name,
                    "showtime": showtime,
                    "raw_showtime": raw_datetime,
                    "display_time": session.get("displayTime"),
                    "ticket_price": None,
                    "booking_link": None,
                    "source_url": request_url,
                    "movie_detail_url": None,
                    "film_code": movie.get("filmCode") or movie.get("id"),
                    "scheduled_film_id": movie.get("scheduledFilmId"),
                    "movie_genre": movie.get("genre"),
                    "movie_cast": movie.get("cast"),
                    "movie_duration": movie.get("duration"),
                    "movie_rating": movie.get("rating"),
                    "poster_url": movie.get("poster"),
                    "backdrop_url": movie.get("backdrop"),
                    "cinema_id": cinema_id,
                    "session_id": session.get("sessionId"),
                    "screen_name": (
                        session.get("screenName") or fallback_screen_name
                    ),
                    "screen_number": session.get("screenNumber"),
                    "seats_available": session.get("seatsAvailable"),
                    "attributes": ", ".join(str(x) for x in attributes),
                    "screen_number": session.get("screenNumber"),
                    "source_language": movie.get("language") or session.get("language"),
                    "source_format": movie.get("format") or session.get("format") or session.get("movieFormat"),
                    "business_date": session.get("businessDate"),
                    "calendar_date": session.get("calendarDate"),
                    "late_night": session.get("lateNight"),
                    "is_lahore_venue": is_lahore_venue(
                        "Cinepax",
                        cinema_name,
                    ),
                })

    logger.info(
        "[Cinepax] %s: %d movies, %d sessions.",
        cinema_name,
        len(movies),
        len(rows),
    )

    return rows


async def scrape_cinepax_lahore() -> list[dict]:
    """Retrieve the configured Lahore Cinepax branches as one complete snapshot.

    If any configured branch fails, discard this run's snapshot instead of
    reconciling a partial branch set against the database.
    """
    cinemas, _ = await asyncio.to_thread(fetch_cinepax_json, "/api/cinemas")
    if not isinstance(cinemas, list):
        raise ValueError("Unexpected response from Cinepax /api/cinemas.")

    selected = [
        cinema for cinema in cinemas
        if normalize_text(cinema.get("name", "")).upper() in CINEPAX_LAHORE_BRANCHES
    ]
    if not selected:
        raise RuntimeError(
            "No configured Cinepax Lahore branches found. "
            f"Available branches: {[c.get('name') for c in cinemas]}"
        )

    logger.info("[Cinepax] Selected Lahore branches: %s", [c["name"] for c in selected])
    semaphore = asyncio.Semaphore(2)

    async def limited(cinema: dict) -> list[dict]:
        async with semaphore:
            return await scrape_cinepax_branch(cinema)

    results = await asyncio.gather(
        *(limited(cinema) for cinema in selected),
        return_exceptions=True,
    )
    failed = [
        (cinema.get("name", "unknown"), result)
        for cinema, result in zip(selected, results)
        if isinstance(result, Exception)
    ]
    if failed:
        logger.error(
            "[Cinepax] %d/%d configured branch scrape(s) failed; discarding partial snapshot: %s",
            len(failed), len(selected), [(name, str(error)) for name, error in failed],
        )
        raise RuntimeError(f"Cinepax snapshot incomplete ({len(failed)} branch failure(s)).")

    rows = [row for result in results for row in result]
    if not rows:
        raise RuntimeError("Cinepax returned zero showtime rows; snapshot is not authoritative.")
    return rows



# ---------------------------------------------------------------------
# CSV auditing and deduplication
# ---------------------------------------------------------------------

def duplicate_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("movie_title") or "").strip(),
        str(row.get("cinema_name") or "").strip(),
        str(row.get("showtime") or "").strip(),
    )


def count_duplicate_keys(rows: list[dict]) -> Counter:
    return Counter(duplicate_key(row) for row in rows)


def dedupe_showtimes(rows: list[dict]) -> list[dict]:
    unique = {}

    for row in rows:
        unique[duplicate_key(row)] = row

    return list(unique.values())


def csv_value(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, bool):
        return str(value).lower()

    return value


def write_csv(
    path: Path,
    rows: list[dict],
    run_timestamp: str,
    duplicate_counts: Counter,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # utf-8-sig makes the CSV easier to open in Excel and Numbers.
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",
            restval="",
        )
        writer.writeheader()

        for row in rows:
            output = {
                column: row.get(column)
                for column in CSV_COLUMNS
            }
            key = duplicate_key(row)
            output["scrape_run_at"] = run_timestamp
            output["duplicate_key_count"] = duplicate_counts.get(key, 1)
            output["is_duplicate"] = duplicate_counts.get(key, 1) > 1

            writer.writerow({
                column: csv_value(value)
                for column, value in output.items()
            })

    logger.info(
        "CSV written: %s (%d rows)",
        path.resolve(),
        len(rows),
    )


# ---------------------------------------------------------------------
# Background collection entry point
# ---------------------------------------------------------------------


def scrape_chalo_lahore() -> list[dict]:
    """Scrape Chalo Cinema's confirmed Fortress Square Mall listing.

    Chalo returns its schedule as JSON. Branch 4 is intentionally skipped:
    the site labels it '-' and our tests returned the same session UUIDs
    as branch 1, so its venue identity is not currently reliable.

    Chalo requires users to proceed through its own booking flow. Until a
    direct session-specific booking URL is verified, booking_link stays None.
    """
    base_url = "https://www.chalocinemas.com"
    branch_id = "1"
    branch_url = f"{base_url}/?branch={branch_id}#now-showing"
    timeout = 25

    session = requests.Session()
    session.headers.update({
        "User-Agent": SCRAPER_CONFIG["user_agent"],
        "Referer": base_url + "/",
        "X-Requested-With": "XMLHttpRequest",
    })

    try:
        page = session.get(
            base_url + "/",
            params={"branch": branch_id},
            timeout=timeout,
        )
        page.raise_for_status()
    except requests.RequestException:
        logger.exception("[Chalo Cinema] Failed to load movie listings.")
        return []

    soup = BeautifulSoup(page.text, "html.parser")
    movie_cards = soup.select(".movie-card-link[onclick]")

    if not movie_cards:
        logger.warning(
            "[Chalo Cinema] No movie cards found; page markup may have changed."
        )
        return []

    onclick_pattern = re.compile(
        r"""openMovieScheduleModal\(\s*(['"])(.*?)\1\s*,\s*(['"])([0-9a-fA-F-]{36})\3\s*\)"""
    )

    rows: list[dict] = []

    for card in movie_cards:
        onclick = card.get("onclick", "")
        match = onclick_pattern.search(onclick)

        if not match:
            title_element = card.select_one(".movie-title")
            logger.warning(
                "[Chalo Cinema] Skipping card with no movie UUID: %s",
                title_element.get_text(" ", strip=True)
                if title_element else onclick[:100],
            )
            continue

        movie_title = match.group(2).strip()
        movie_id = match.group(4)
        source_card_text = card.get_text(" ", strip=True)
        source_card_image = card.select_one("img[alt]")
        if source_card_image and source_card_image.get("alt"):
            source_card_text += " " + str(source_card_image.get("alt"))
        source_language_match = re.search(
            r"\b(English|Urdu|Punjabi|Hindi|Pashto|Arabic)\b", source_card_text, re.I
        )
        source_format_match = re.search(r"\b(2D|3D|IMAX|4DX)\b", source_card_text, re.I)

        try:
            schedule_response = session.get(
                base_url + "/home/get_movie_schedule",
                params={
                    "movieName": movie_title,
                    "movieId": movie_id,
                },
                timeout=timeout,
            )
            schedule_response.raise_for_status()
            schedule_data = schedule_response.json()
        except (requests.RequestException, ValueError):
            logger.exception(
                "[Chalo Cinema] Could not fetch schedule for %s.",
                movie_title,
            )
            continue

        if not isinstance(schedule_data, list):
            logger.warning(
                "[Chalo Cinema] Unexpected schedule format for %s.",
                movie_title,
            )
            continue

        movie_row_count = 0

        for date_entry in schedule_data:
            if not isinstance(date_entry, dict):
                continue

            date_text = str(date_entry.get("date") or "").strip()

            for show in date_entry.get("shows") or []:
                if not isinstance(show, dict):
                    continue

                time_text = str(show.get("time") or "").strip()
                session_id = str(show.get("id") or "").strip()

                if not date_text or not time_text or not session_id:
                    logger.warning(
                        "[Chalo Cinema] Skipping incomplete session for %s.",
                        movie_title,
                    )
                    continue

                try:
                    naive_dt = datetime.strptime(
                        f"{date_text} {time_text}",
                        "%m/%d/%Y %I:%M %p",
                    )
                    local_dt = naive_dt.replace(tzinfo=PK_TZ)
                except ValueError:
                    logger.warning(
                        "[Chalo Cinema] Invalid date/time for %s: %r %r",
                        movie_title,
                        date_text,
                        time_text,
                    )
                    continue

                screen_name = str(show.get("screen") or "").strip() or None
                screen_match = (
                    re.search(r"(\d+)", screen_name)
                    if screen_name else None
                )

                rows.append({
                    "source": "Chalo Cinema",
                    "movie_title": movie_title,
                    "cinema_name": "Chalo Cinema - Fortress Square Mall",
                    "showtime": local_dt.isoformat(),
                    "raw_showtime": local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "display_time": time_text,
                    "ticket_price": None,
                    # No direct session booking URL has been verified yet.
                    "booking_link": None,
                    "source_url": branch_url,
                    "movie_detail_url": branch_url,
                    "film_code": None,
                    "scheduled_film_id": movie_id,
                    "movie_genre": None,
                    "movie_cast": None,
                    "movie_duration": None,
                    "movie_rating": None,
                    "poster_url": None,
                    "backdrop_url": None,
                    "cinema_id": branch_id,
                    "session_id": session_id,
                    "screen_name": screen_name,
                    "screen_number": (
                        screen_match.group(1) if screen_match else None
                    ),
                    "seats_available": None,
                    "attributes": show.get("type"),
                    "source_language": source_language_match.group(1).title() if source_language_match else None,
                    "source_format": source_format_match.group(1).upper() if source_format_match else None,
                    "business_date": local_dt.strftime("%Y-%m-%d"),
                    "calendar_date": date_text,
                    "late_night": (
                        local_dt.hour >= 23 or local_dt.hour < 5
                    ),
                    "is_lahore_venue": True,
                })
                movie_row_count += 1

        logger.info(
            "[Chalo Cinema] %s: %d sessions.",
            movie_title,
            movie_row_count,
        )

    session.close()

    logger.info(
        "[Chalo Cinema] Collected %d sessions from branch %s.",
        len(rows),
        branch_id,
    )
    return rows


async def run_source(label: str, awaitable) -> tuple[bool, list[dict]]:
    """Return an authoritative snapshot only when a source finished with rows.

    Empty or failed runs are deliberately marked non-authoritative: they may not
    cause the database to remove sessions from a venue.
    """
    try:
        rows = await awaitable
        if not rows:
            logger.warning(
                "[%s] Returned zero rows; treating the scrape as non-authoritative. "
                "No upsert or stale-session reconciliation will use this source.",
                label,
            )
            return False, []
        logger.info("[%s] Returned %d rows.", label, len(rows))
        return True, rows
    except Exception:
        logger.exception("[%s] Source failed; preserving its existing showtimes.", label)
        return False, []





async def run_catalogue_refresh() -> None:
    """Refresh poster/catalogue metadata as part of the regular scraper job."""
    try:
        pass

        count = await asyncio.to_thread(catalog_refresh_movie_catalog)
        logger.info("Scheduled movie catalogue refresh finished (%d records upserted).", count)
    except Exception:
        # Catalogue failures must not interrupt showtime scraping.
        logger.exception("Scheduled movie catalogue refresh failed.")


async def run_scrape_job() -> None:
    """Collect source snapshots, upsert rows, then safely reconcile stale sessions."""
    await run_catalogue_refresh()

    # Refresh CineStar, DHA Cinema and CineGold on the normal hourly scrape schedule.
    # That combined refresh performs its own snapshot reconciliation and read-only audit.
    try:
        await asyncio.to_thread(cinestar_dha_refresh_new_cinema_sources)
    except Exception:
        logger.exception("CineStar/DHA/CineGold scheduled refresh failed; core source scraping continues.")
    run_timestamp = datetime.now(PK_TZ).isoformat()
    logger.info("Starting Lahore cinema scrape at %s.", run_timestamp)

    (cue_ok, cue_rows), (universal_ok, universal_rows), (cinepax_ok, cinepax_rows) = await asyncio.gather(
        run_source("Cue Cinemas", scrape_vista_site("Cue Cinemas", SCRAPER_CONFIG["cue"]["base_url"])),
        run_source("Universal Cinemas", scrape_vista_site("Universal Cinemas", SCRAPER_CONFIG["universal"]["base_url"])),
        run_source("Cinepax", scrape_cinepax_lahore()),
    )
    chalo_ok, chalo_rows = await run_source(
        "Chalo Cinema", asyncio.to_thread(scrape_chalo_lahore)
    )

    source_snapshots = [
        ("Cue Cinemas", cue_ok, cue_rows),
        ("Universal Cinemas", universal_ok, universal_rows),
        ("Cinepax", cinepax_ok, cinepax_rows),
        ("Chalo Cinema", chalo_ok, chalo_rows),
    ]
    raw_rows = [
        row
        for _label, is_complete, rows in source_snapshots
        if is_complete
        for row in rows
    ]

    for label, is_complete, rows in source_snapshots:
        logger.info(
            "[%s] Snapshot status=%s, rows=%d.",
            label,
            "complete" if is_complete else "incomplete/skipped",
            len(rows),
        )

    if not raw_rows:
        logger.error(
            "No authoritative source rows collected. Skipping CSV replacement, "
            "database updates, and reconciliation."
        )
        return

    raw_duplicates = count_duplicate_keys(raw_rows)
    write_csv(RAW_CSV, raw_rows, run_timestamp, raw_duplicates)

    lahore_rows = [row for row in raw_rows if row.get("is_lahore_venue", False)]
    unique_lahore_rows = dedupe_showtimes(lahore_rows)
    lahore_duplicates = count_duplicate_keys(lahore_rows)
    write_csv(DEDUPED_CSV, unique_lahore_rows, run_timestamp, lahore_duplicates)

    logger.info(
        "Collected %d authoritative rows; %d Lahore rows; %d unique Lahore rows.",
        len(raw_rows), len(lahore_rows), len(unique_lahore_rows),
    )
    if not unique_lahore_rows:
        logger.warning("No Lahore showtimes found. Database was not modified or reconciled.")
        audit_showtime_snapshots(source_snapshots, context="main showtime scrape (no usable rows)")
        return

    database_rows = [{column: row.get(column) for column in DB_COLUMNS} for row in unique_lahore_rows]
    try:
        result = await asyncio.to_thread(upsert_showtimes, database_rows)
        logger.info("Scrape upsert completed; Supabase response omitted.")
    except Exception:
        logger.exception("Supabase upsert failed. No stale-session reconciliation was run.")
        audit_showtime_snapshots(source_snapshots, context="main showtime scrape (upsert failed)")
        return

    # Only successful, non-empty, source-complete snapshots can retire stale rows.
    # Failed/empty source runs preserve prior data instead of implying closures.
    for label, is_complete, rows in source_snapshots:
        if not is_complete or not rows:
            continue
        venue_rows = [row for row in rows if row.get("is_lahore_venue", False)]
        if not venue_rows:
            logger.warning("[%s] No approved Lahore venue rows; skipping reconciliation.", label)
            continue
        try:
            removed = await asyncio.to_thread(
                reconcile_showtimes_snapshot, venue_rows, f"{label} snapshot"
            )
            logger.info("[%s] Safe reconciliation removed %d stale future row(s).", label, removed)
        except Exception:
            logger.exception("[%s] Reconciliation failed; retained any unverified stale rows.", label)

    audit_showtime_snapshots(source_snapshots, context="main showtime scrape")

    # Trigger opted-in advance-booking emails immediately after a successful scrape.
    try:
        from advance_booking_routes import check_advance_booking_notifications
        await asyncio.to_thread(check_advance_booking_notifications)
    except Exception:
        logger.exception("Advance-booking notification check failed after scrape.")






# ===========================================================================
# Chalo cinema catalogue parser
# ===========================================================================
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

chalo_BASE_URL = SCRAPER_CONFIG["chalo"]["base_url"]
chalo_BRANCH_ID = SCRAPER_CONFIG["chalo"]["branch_id"]
chalo_CINEMA_NAME = SCRAPER_CONFIG["chalo"]["cinema_name"]
chalo_SOURCE = SCRAPER_CONFIG["chalo"]["source"]
chalo_TIMEZONE = ZoneInfo(SCRAPER_CONFIG["timezone"])

chalo_logger = logging.getLogger("catalog_scraper")


def chalo_normalize_title(title: str) -> str:
    """Normalize title/format variants for matching existing showtimes."""
    text = re.sub(r"\([^)]*\)", " ", title or "")
    text = re.sub(r"\b(?:2d|3d|imax|urdu|english|hindi)\b", " ", text, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def chalo_parse_release_date(card_text: str) -> tuple[date | None, str | None]:
    """Extract an explicitly labelled release date; never guess a date."""
    match = re.search(
        r"\bRelease\s*:\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
        card_text,
        flags=re.I,
    )
    if not match:
        return None, None

    label = match.group(1).strip()

    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(label, fmt).date(), label
        except ValueError:
            continue

    chalo_logger.warning("Could not parse release date %r", label)
    return None, label


def chalo_extract_movie_id(card) -> str | None:
    onclick = card.get("onclick", "")
    match = re.search(
        r"""openMovieScheduleModal\(\s*(['"])(.*?)\1\s*,\s*(['"])([0-9a-fA-F-]{36})\3""",
        onclick,
    )
    if match:
        return match.group(4)

    # Also inspect descendants in case the event is attached to a child.
    for element in card.select("[onclick]"):
        match = re.search(
            r"""openMovieScheduleModal\(\s*(['"])(.*?)\1\s*,\s*(['"])([0-9a-fA-F-]{36})\3""",
            element.get("onclick", ""),
        )
        if match:
            return match.group(4)

    return None


def chalo_extract_trailer_url(card) -> str | None:
    candidates = []

    if card.name == "a" and card.get("href"):
        candidates.append(card.get("href"))

    candidates.extend(link.get("href") for link in card.select("a[href]"))

    for href in candidates:
        if not href:
            continue

        url = urljoin(chalo_BASE_URL, href)
        host = (urlparse(url).hostname or "").lower()

        if host == "youtu.be" or host.endswith("youtube.com"):
            return url

    return None


def chalo_extract_detail_url(card) -> str | None:
    """Keep only an actual first-party detail link, not a trailer/homepage."""
    candidates = []

    if card.name == "a" and card.get("href"):
        candidates.append(card.get("href"))

    candidates.extend(link.get("href") for link in card.select("a[href]"))

    for href in candidates:
        if not href or href.strip().startswith(("#", "javascript:")):
            continue

        url = urljoin(chalo_BASE_URL, href)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if host not in {"chalocinemas.com", "www.chalocinemas.com"}:
            continue

        if parsed.path not in {"", "/"}:
            return url

    return None


def chalo_source_section_status(section_heading: str) -> str:
    heading = (section_heading or "").strip().lower()

    if "coming soon" in heading or "upcoming" in heading:
        return "coming_soon"

    if "now showing" in heading:
        return "now_showing"

    return "unknown"


def chalo_get_future_showtime_titles() -> set[str]:
    """Read actual future sessions so stale Coming Soon cards can be reconciled."""
    try:
        # database.py loads .env and creates the existing Supabase client.
        from database import supabase

        response = (
            supabase.table("showtimes")
            .select("movie_title,cinema_name,showtime")
            .ilike("cinema_name", "%Chalo Cinema - Fortress Square Mall%")
            .order("showtime")
            .limit(1000)
            .execute()
        )

        now = datetime.now(chalo_TIMEZONE)
        titles = set()

        for row in response.data or []:
            raw = row.get("showtime")
            title = row.get("movie_title")

            if not raw or not title:
                continue

            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

                # Supabase timestamptz values are normally offset-aware.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                if dt.astimezone(chalo_TIMEZONE) >= now:
                    titles.add(chalo_normalize_title(title))
            except (ValueError, TypeError):
                chalo_logger.warning("Invalid stored showtime: %r", raw)

        return titles

    except Exception:
        chalo_logger.exception(
            "Could not read stored showtimes; catalogue classification "
            "will rely on Chalo's page and release dates."
        )
        return set()


def chalo_classify_status(
    section_status: str,
    release_date: date | None,
    has_future_showtime: bool,
    today: date,
) -> str:
    """Distinguish listed sessions, future releases, and expired stale cards."""
    if has_future_showtime:
        return "now_showing"

    if section_status == "now_showing":
        return "now_showing"

    if section_status == "coming_soon":
        if release_date and release_date < today:
            return "released_no_sessions"
        return "coming_soon"

    if release_date and release_date > today:
        return "coming_soon"

    return "unknown"


def chalo_scrape_chalo_catalog() -> list[dict]:
    """Collect Chalo movie catalogue entries without writing to the database."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": SCRAPER_CONFIG["user_agent"],
        "Referer": chalo_BASE_URL + "/",
    })

    response = session.get(
        chalo_BASE_URL + "/",
        params={"branch": chalo_BRANCH_ID},
        timeout=25,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    today = datetime.now(chalo_TIMEZONE).date()
    active_titles = chalo_get_future_showtime_titles()

    found = {}

    for section in soup.select("section"):
        grid = section.select_one(".movie-grid")
        if grid is None:
            continue

        heading_element = section.select_one(".section-header h2")
        heading = heading_element.get_text(" ", strip=True) if heading_element else ""
        section_status = chalo_source_section_status(heading)

        if section_status == "unknown":
            chalo_logger.info("Skipping unclassified movie section: %r", heading)
            continue

        for card in grid.select(".movie-card-link"):
            title_element = card.select_one(".movie-title")
            if title_element is None:
                continue

            title = title_element.get_text(" ", strip=True).strip()
            if not title:
                continue

            card_text = card.get_text(" ", strip=True)
            release_date, release_label = chalo_parse_release_date(card_text)

            genres_element = card.select_one(".movie-genres")
            genre_text = (
                genres_element.get_text(" ", strip=True)
                if genres_element else ""
            )

            if re.search(r"\bRelease\s*:", genre_text, re.I):
                language = None
                movie_format = None
            else:
                parts = [
                    part.strip()
                    for part in re.split(r"[|,]", genre_text)
                    if part.strip()
                ]
                movie_format = next(
                    (part for part in parts if re.search(r"\b[23]D\b|\bIMAX\b", part, re.I)),
                    None,
                )
                language = next(
                    (
                        part for part in parts
                        if part != movie_format
                        and re.fullmatch(
                            r"Urdu|English|Hindi|Punjabi|Arabic",
                            part,
                            re.I,
                        )
                    ),
                    None,
                )

            image = card.select_one("img")
            poster_url = None
            if image:
                image_src = image.get("src") or image.get("data-src")
                if image_src:
                    poster_url = urljoin(chalo_BASE_URL + "/", image_src)

            has_future_showtime = (
                chalo_normalize_title(title) in active_titles
            )

            listing_status = chalo_classify_status(
                section_status,
                release_date,
                has_future_showtime,
                today,
            )

            movie_id = chalo_extract_movie_id(card)

            row = {
                "source": chalo_SOURCE,
                "cinema_name": chalo_CINEMA_NAME,
                "source_movie_id": movie_id,
                "movie_title": title,
                "listing_status": listing_status,
                "release_date": (
                    release_date.isoformat() if release_date else None
                ),
                "source_release_label": release_label,
                "poster_url": poster_url,
                "detail_url": chalo_extract_detail_url(card),
                "trailer_url": chalo_extract_trailer_url(card),
                "language": language,
                "movie_format": movie_format,
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            key = chalo_normalize_title(title)
            old = found.get(key)

            # A movie can appear in more than one section. Prefer a record
            # reconciled to actual future screenings, preserving rich metadata.
            if old is None:
                found[key] = row
            else:
                status_priority = {
                    "now_showing": 4,
                    "coming_soon": 3,
                    "released_no_sessions": 2,
                    "unknown": 1,
                }
                chosen = (
                    row
                    if status_priority[row["listing_status"]]
                    > status_priority[old["listing_status"]]
                    else old
                )

                for field, value in old.items():
                    if chosen.get(field) in (None, "") and value not in (None, ""):
                        chosen[field] = value

                for field, value in row.items():
                    if chosen.get(field) in (None, "") and value not in (None, ""):
                        chosen[field] = value

                found[key] = chosen

    session.close()
    return sorted(found.values(), key=lambda row: row["movie_title"].casefold())


def chalo_print_preview(rows: list[dict]) -> None:
    print("\n=== CHALO MOVIE CATALOGUE PREVIEW ===")
    print("Movies extracted:", len(rows))

    counts = {}
    for row in rows:
        status = row["listing_status"]
        counts[status] = counts.get(status, 0) + 1

    print("\nBy status:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print("\nEntries:")
    for row in rows:
        print(
            f'- {row["movie_title"]} | '
            f'status={row["listing_status"]} | '
            f'release={row["release_date"] or "not stated"} | '
            f'format={row["movie_format"] or "not stated"} | '
            f'language={row["language"] or "not stated"}'
        )
        print(f'  poster: {row["poster_url"] or "not listed"}')
        print(f'  trailer: {row["trailer_url"] or "not listed"}')
        print(f'  detail: {row["detail_url"] or "not listed"}')

    print("\nDRY RUN: no movie_catalog records were written.")


def chalo_save_to_supabase(rows: list[dict]) -> None:
    if not rows:
        print("No catalogue entries to save.")
        return

    from database import supabase

    response = (
        supabase.table("movie_catalog")
        .upsert(
            rows,
            on_conflict="source,cinema_name,movie_title",
        )
        .execute()
    )

    print(
        f"Saved/upserted {len(response.data or [])} movie catalogue "
        "record(s) to Supabase."
    )





# ===========================================================================
# Cross-provider poster fallback (poster_url only)
# ===========================================================================

import logging
import re
import unicodedata
from urllib.parse import urlparse

poster_logger = logging.getLogger("poster_fallback")

poster_FORMAT_LANGUAGE_TAGS = {
    "2d", "3d", "4dx", "imax",
    "urdu", "english", "hindi", "punjabi",
    "dubbed", "subtitled",
}

poster_GENERIC_TITLES = {
    "more details", "book now", "buy tickets", "showtimes",
    "coming soon", "now showing", "view all",
}

poster_SOURCE_PRIORITY = {
    "cinestar": 0,
    "cinepax": 1,
    "cue": 2,
    "universal": 3,
    "chalo": 4,
    "dha": 5,
}


def poster__title_key(value: str | None) -> str:
    if not value:
        return ""

    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"^\s*(?:18|16|15|13)\s*\+\s*", "", text)

    def remove_metadata_parentheses(match):
        words = re.findall(r"[a-z0-9]+", match.group(1).casefold())
        if words and all(word in poster_FORMAT_LANGUAGE_TAGS for word in words):
            return " "
        return match.group(0)

    text = re.sub(r"\(([^()]*)\)", remove_metadata_parentheses, text)

    words = re.findall(r"[a-z0-9]+", text)

    # Strip only trailing format/language tags, preserving meaningful title words.
    while len(words) > 1 and words[-1] in poster_FORMAT_LANGUAGE_TAGS:
        words.pop()

    key = " ".join(words)
    return "" if key in poster_GENERIC_TITLES else key


def poster__compact_key(value: str | None) -> str:
    return poster__title_key(value).replace(" ", "")


def poster__valid_poster(value) -> str | None:
    if not isinstance(value, str):
        return None

    url = value.strip()
    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    lowered = url.casefold()
    if any(marker in lowered for marker in (
        "placeholder", "no-image", "noimage", "default-poster",
        "loading.gif", "ratingicongraphic",
    )):
        return None

    return url


def poster__source(row: dict) -> str:
    return str(row.get("source") or "").strip().casefold()


def poster__priority(row: dict) -> int:
    source = poster__source(row)
    for name, rank in poster_SOURCE_PRIORITY.items():
        if name in source:
            return rank
    return 99


def poster_fill_missing_posters(
    target_rows: list[dict],
    donor_rows: list[dict],
    *,
    context: str = "catalogue",
) -> list[dict]:
    """
    Fill missing poster_url values by matching movie titles across sources.

    All other fields remain unchanged. Posters from the same source are not
    used as cross-website fallbacks.
    """
    output = [dict(row) for row in target_rows]
    candidates = list(donor_rows) + output

    by_exact_title: dict[str, list[dict]] = {}
    by_compact_title: dict[str, list[dict]] = {}

    for donor in candidates:
        poster = poster__valid_poster(donor.get("poster_url"))
        title = poster__title_key(donor.get("movie_title"))
        compact = poster__compact_key(donor.get("movie_title"))

        if not poster or not title or not compact:
            continue

        entry = {
            "source": poster__source(donor),
            "title": title,
            "poster_url": poster,
            "priority": poster__priority(donor),
        }
        by_exact_title.setdefault(title, []).append(entry)
        by_compact_title.setdefault(compact, []).append(entry)

    filled = 0

    for row in output:
        # Never replace a poster already supplied for this record.
        if poster__valid_poster(row.get("poster_url")):
            continue

        target_source = poster__source(row)
        title = poster__title_key(row.get("movie_title"))
        compact = poster__compact_key(row.get("movie_title"))

        if not title or not compact:
            continue

        donors = [
            item for item in by_exact_title.get(title, [])
            if item["source"] and item["source"] != target_source
        ]

        # Allows spacing differences such as "Mango Jutt"/"MangoJutt".
        if not donors:
            donors = [
                item for item in by_compact_title.get(compact, [])
                if item["source"] and item["source"] != target_source
            ]

        if not donors:
            continue

        donors.sort(key=lambda item: (item["priority"], item["poster_url"]))
        row["poster_url"] = donors[0]["poster_url"]
        filled += 1

    if filled:
        poster_logger.info(
            "Poster-only fallback: filled %d missing poster URL(s) for %s.",
            filled, context,
        )

    return output



# ===========================================================================
# Multi-provider movie catalogue refresh
# ===========================================================================

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from database import supabase
pass
from location_guard import filter_lahore_catalogue_rows

catalog_logger = logging.getLogger("catalog_sources")
catalog_PK_TZ = ZoneInfo(SCRAPER_CONFIG["timezone"])
catalog_HTTP_TIMEOUT = SCRAPER_CONFIG["catalog_timeout_seconds"]
catalog_USER_AGENT = SCRAPER_CONFIG["user_agent"]

catalog_VISTA_SOURCES = [dict(SCRAPER_CONFIG[key]) for key in ("cue", "universal")]
catalog_VISTA_LISTINGS = list(SCRAPER_CONFIG["vista_listings"])
catalog_CINEPAX_BASE_URL = SCRAPER_CONFIG["cinepax"]["base_url"]
catalog_CINEPAX_LAHORE_BRANCHES = set(SCRAPER_CONFIG["cinepax"]["lahore_branches"])

catalog_CATALOG_FIELDS = (
    "source",
    "cinema_name",
    "source_movie_id",
    "movie_title",
    "listing_status",
    "release_date",
    "source_release_label",
    "poster_url",
    "detail_url",
    "trailer_url",
    "language",
    "movie_format",
    "last_seen_at",
    "updated_at",
)
catalog_PRESERVE_IF_EMPTY = (
    "source_movie_id",
    "release_date",
    "source_release_label",
    "poster_url",
    "detail_url",
    "trailer_url",
    "language",
    "movie_format",
)

catalog__http = requests.Session()
catalog__http.headers.update(
    {
        "User-Agent": catalog_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    }
)


def catalog__get(url: str, params: dict | None = None) -> requests.Response:
    response = catalog__http.get(url, params=params, timeout=catalog_HTTP_TIMEOUT)
    response.raise_for_status()
    return response


def catalog__absolute_url(base_url: str, value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith(("data:", "javascript:", "#")):
        return None
    result = urljoin(base_url, value)
    if urlparse(result).scheme not in {"http", "https"}:
        return None
    return result


def catalog__usable_image_url(url: str | None) -> str | None:
    if not url:
        return None
    lowered = url.casefold()
    if any(
        token in lowered
        for token in (
            "placeholder",
            "no-image",
            "noimage",
            "loading.gif",
            "spinner",
            "logo",
            "rating",
            "avatar",
        )
    ):
        return None
    if lowered.startswith("data:") or lowered.endswith(".svg"):
        return None
    return url


def catalog__image_candidate(img) -> str | None:
    if img is None:
        return None
    # Prefer lazy-loaded/full-size URLs over placeholder src values.
    for attr in ("data-src", "data-original", "data-lazy-src", "data-original-src", "src"):
        value = img.get(attr)
        if value:
            if attr == "src" and value.startswith("data:"):
                continue
            return value
    srcset = img.get("data-srcset") or img.get("srcset")
    if srcset:
        # The last candidate is usually the largest size in a srcset.
        candidate = srcset.split(",")[-1].strip().split()[0]
        return candidate
    return None


def catalog__poster_from_element(element, base_url: str) -> str | None:
    if element is None:
        return None
    candidates: list[str] = []
    img = element if getattr(element, "name", None) == "img" else element.select_one("img")
    if img is not None:
        candidate = catalog__image_candidate(img)
        if candidate:
            candidates.append(candidate)
    for source in element.select("picture source[srcset], source[data-srcset]"):
        srcset = source.get("data-srcset") or source.get("srcset")
        if srcset:
            candidates.append(srcset.split(",")[-1].strip().split()[0])
    style = element.get("style", "")
    if not style and hasattr(element, "select_one"):
        styled = element.select_one('[style*="background-image"]')
        if styled is not None:
            style = styled.get("style", "")
    if style:
        match = re.search(r"background-image\s*:\s*url\(['\"]?([^)'\"]+)", style, re.I)
        if match:
            candidates.append(match.group(1))

    for candidate in candidates:
        absolute = catalog__absolute_url(base_url, candidate)
        absolute = catalog__usable_image_url(absolute)
        if absolute:
            return absolute
    return None


def catalog__poster_from_soup(soup: BeautifulSoup, base_url: str) -> str | None:
    for selector in (
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ):
        meta = soup.select_one(selector)
        if meta and meta.get("content"):
            candidate = catalog__usable_image_url(catalog__absolute_url(base_url, meta.get("content")))
            if candidate:
                return candidate

    # Prefer a poster/fill-specific container before falling back to the first
    # reasonably sized non-logo image on the detail page.
    for selector in (
        ".movie-poster img",
        ".film-poster img",
        ".poster img",
        ".movie-image img",
        "img[alt*='poster' i]",
        "main img",
    ):
        for element in soup.select(selector):
            candidate = catalog__poster_from_element(element, base_url)
            if candidate:
                return candidate
    return None


def catalog__date_from_text(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    text = " ".join(text.split())
    months = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )
    patterns = [
        rf"\b(\d{{1,2}})\s+({months})\s+(\d{{4}})\b",
        rf"\b({months})\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
    ]
    for pattern_index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        try:
            if pattern_index == 0:
                parsed = datetime.strptime(" ".join(match.groups()), "%d %B %Y").date()
            elif pattern_index == 1:
                parsed = datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
            elif pattern_index == 2:
                parsed = date.fromisoformat(match.group(0))
            else:
                # Treat slash dates as day/month/year, the common local format.
                parsed = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            return parsed.isoformat(), match.group(0)
        except (ValueError, TypeError):
            continue
    return None, None


def catalog__format_and_language(title: str) -> tuple[str | None, str | None]:
    format_match = re.search(r"\b(IMAX|4DX|3D|2D)\b", title, re.I)
    language_match = re.search(r"\b(Urdu|English|Punjabi|Hindi)\b", title, re.I)
    return (
        format_match.group(1).upper() if format_match else None,
        language_match.group(1).title() if language_match else None,
    )


def catalog__title_from_anchor(anchor) -> str:
    for selector in (".name", ".movie-title", ".title", "h2", "h3"):
        node = anchor.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    image = anchor.select_one("img[alt]")
    if image and image.get("alt", "").strip():
        return image["alt"].strip()
    return anchor.get_text(" ", strip=True)


def catalog__vista_detail_id(detail_url: str) -> str | None:
    """Return the movie identifier from a Vista detail URL, case-insensitively."""
    path = urlparse(detail_url).path
    match = re.search(r"/browsing/movies/details/([^/]+)", path, re.I)
    if not match:
        return None
    identifier = match.group(1).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,100}", identifier):
        return None
    return identifier


def catalog__same_site(url: str, base_url: str) -> bool:
    """Treat www and non-www aliases of the same cinema as one site."""
    got = (urlparse(url).hostname or "").lower().removeprefix("www.")
    expected = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    return bool(got and expected and got == expected)


def catalog__matching_card(anchor, detail_url: str, soup: BeautifulSoup):
    # Vista sites may place the movie-title link and poster link in different
    # siblings. Climb to a card containing an image and one or two detail links.
    current = anchor
    for _ in range(7):
        current = current.parent
        if current is None or getattr(current, "name", None) in {"body", "html", "main"}:
            break
        if getattr(current, "select", None):
            detail_links = [
                item for item in current.select("a[href]")
                if "/browsing/movies/details/" in (item.get("href") or "").lower()
            ]
            has_image = (
                current.select_one("img") is not None
                or current.select_one('[style*="background-image"]') is not None
                or current.select_one('a[href*="FilmPosterGraphic"]') is not None
            )
            if has_image and 1 <= len(detail_links) <= 3:
                return current
    for other in soup.select("a[href]"):
        if catalog__absolute_url(detail_url, other.get("href")) == detail_url:
            if other.select_one("img") or "filmpostergraphic" in (other.get("href") or "").lower():
                return other
    return anchor


def catalog__title_from_card(card, anchor) -> str:
    """Prefer a real heading/title over poster link labels such as 'Image'."""
    title = catalog__title_from_anchor(anchor)
    if title and title.strip().casefold() not in {"image", "poster", "more details", "details", "play trailer"}:
        return " ".join(title.split())
    if card is not None and getattr(card, "select_one", None):
        for selector in (
            ".name", ".movie-title", ".movieName", ".title", "h2", "h3",
            "[itemprop='name']", "[data-movie-title]"
        ):
            node = card.select_one(selector)
            if node:
                candidate = (
                    node.get("content") or node.get("data-movie-title")
                    or node.get_text(" ", strip=True)
                )
                if candidate and candidate.strip().casefold() not in {"image", "poster", "more details", "details"}:
                    return " ".join(candidate.split())
    if card is not None and getattr(card, "select_one", None):
        image = card.select_one("img[alt]")
        if image and image.get("alt", "").strip().casefold() not in {"image", "poster"}:
            return image.get("alt", "").strip()
    return title.strip() if title else ""


def catalog__vista_poster_url(detail_url: str) -> str | None:
    """Build Vista's official FilmPosterGraphic endpoint from a detail URL.

    The poster is often exposed as a linked CDN entity rather than an img[src],
    so looking only for <img> misses legitimate posters on Cue and Universal.
    """
    identifier = catalog__vista_detail_id(detail_url)
    if not identifier:
        return None
    parsed = urlparse(detail_url)
    path = (
        f"/CDN/media/entity/get/FilmPosterGraphic/{identifier}"
        "?allowPlaceHolder=true&height=600&referenceScheme=HeadOffice&width=400"
    )
    return catalog__absolute_url(f"{parsed.scheme}://{parsed.netloc}", path)


def catalog__extract_vista_cards(html: str, page_url: str, base_url: str, status: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Do not use a case-sensitive CSS attribute selector here: Cue uses
    # /Browsing/... while Universal commonly returns /browsing/... URLs.
    anchors = [
        anchor for anchor in soup.select("a[href]")
        if "/browsing/movies/details/" in (anchor.get("href") or "").lower()
    ]

    found: dict[str, dict] = {}
    for anchor in anchors:
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        detail_url = catalog__absolute_url(page_url, href)
        if not detail_url or not catalog__same_site(detail_url, base_url):
            continue
        card = catalog__matching_card(anchor, detail_url, soup)
        title = catalog__title_from_card(card, anchor)
        title = " ".join(title.split())
        if not title or len(title) > 160:
            continue

        card_text = card.get_text(" ", strip=True) if hasattr(card, "get_text") else title
        release_date, release_label = catalog__date_from_text(card_text)
        trailer = None
        for link in card.select("a[href]") if hasattr(card, "select") else []:
            href_link = link.get("href", "")
            if "youtube.com" in href_link.lower() or "youtu.be" in href_link.lower():
                trailer = catalog__absolute_url(detail_url, href_link)
                break

        poster_url = catalog__poster_from_element(card, page_url)
        if not poster_url:
            # Confirmed Vista image route used by Cue's movie cards. It works
            # even when the HTML only links to the CDN image endpoint.
            poster_url = catalog__vista_poster_url(detail_url)

        movie_id = catalog__vista_detail_id(detail_url)
        movie_format, language = catalog__format_and_language(title)
        row = {
            "source": None,
            "cinema_name": None,
            "source_movie_id": movie_id,
            "movie_title": title,
            "listing_status": status,
            "release_date": release_date,
            "source_release_label": release_label,
            "poster_url": poster_url,
            "detail_url": detail_url,
            "trailer_url": trailer,
            "language": language,
            "movie_format": movie_format,
        }

        existing = found.get(detail_url)
        if existing is None:
            found[detail_url] = row
        else:
            # Prefer meaningful title text and keep any nonempty metadata.
            merged = dict(existing)
            for key, value in row.items():
                if not merged.get(key) and value:
                    merged[key] = value
            if row.get("poster_url"):
                merged["poster_url"] = row["poster_url"]
            if row.get("listing_status") == "now_showing":
                merged["listing_status"] = "now_showing"
            found[detail_url] = merged
    return list(found.values())

def catalog__enrich_vista_detail(row: dict) -> dict:
    """Fill missing metadata from a movie detail page without inventing it."""
    if row.get("poster_url") and row.get("release_date") and row.get("trailer_url"):
        return row
    try:
        response = catalog__get(row["detail_url"])
    except Exception as exc:
        catalog_logger.debug("Detail page unavailable for %s: %s", row.get("movie_title"), exc)
        return row

    soup = BeautifulSoup(response.text, "html.parser")
    if not row.get("poster_url"):
        row["poster_url"] = catalog__poster_from_soup(soup, response.url)
    if not row.get("poster_url"):
        row["poster_url"] = catalog__vista_poster_url(response.url)
    if not row.get("trailer_url"):
        for link in soup.select('a[href]'):
            href = link.get("href", "")
            if "youtube.com" in href.lower() or "youtu.be" in href.lower():
                row["trailer_url"] = catalog__absolute_url(response.url, href)
                if row["trailer_url"]:
                    break
    if not row.get("release_date"):
        release_node = soup.select_one(
            ".release-date, .releaseDate, [itemprop='datePublished'], [data-release-date]"
        )
        release_text = ""
        if release_node:
            release_text = (
                release_node.get("content")
                or release_node.get("data-release-date")
                or release_node.get_text(" ", strip=True)
            )
        if not release_text:
            for node in soup.select("p, li, span, div"):
                text = node.get_text(" ", strip=True)
                if re.search(r"release\s*date|release", text, re.I) and len(text) < 180:
                    release_text = text
                    break
        release_date, release_label = catalog__date_from_text(release_text)
        if release_date:
            row["release_date"] = release_date
            row["source_release_label"] = release_label or release_text
    return row


def catalog__scrape_vista_source(config: dict) -> list[dict]:
    base_url = config["base_url"]
    candidates: list[dict] = []
    for route, status in catalog_VISTA_LISTINGS:
        url = urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
        try:
            response = catalog__get(url)
        except Exception as exc:
            catalog_logger.warning("[%s] Catalogue listing failed (%s): %s", config["source"], route, exc)
            continue
        page_rows = catalog__extract_vista_cards(response.text, response.url, base_url, status)
        catalog_logger.info("[%s] %s catalogue page produced %d records.", config["source"], status, len(page_rows))
        for row in page_rows:
            row["source"] = config["source"]
            row["cinema_name"] = config["cinema_name"]
            candidates.append(row)

    # Some Vista deployments (notably Universal) render usable movie links on
    # /Browsing/ but return a sparse response from the explicit listing routes.
    if not candidates:
        for fallback_route in ("/Browsing/", "/"):
            fallback_url = urljoin(base_url.rstrip("/") + "/", fallback_route.lstrip("/"))
            try:
                fallback_response = catalog__get(fallback_url)
                fallback_rows = catalog__extract_vista_cards(
                    fallback_response.text, fallback_response.url, base_url, "unknown"
                )
                catalog_logger.info(
                    "[%s] fallback %s catalogue page produced %d records.",
                    config["source"], fallback_route, len(fallback_rows),
                )
                if fallback_rows:
                    for row in fallback_rows:
                        row["source"] = config["source"]
                        row["cinema_name"] = config["cinema_name"]
                    candidates.extend(fallback_rows)
                    break
            except Exception as exc:
                catalog_logger.warning(
                    "[%s] fallback catalogue page failed (%s): %s",
                    config["source"], fallback_route, exc,
                )

    # Detail-page requests run concurrently, but one failed card cannot cancel
    # all other catalogue entries.
    enriched: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(catalog__enrich_vista_detail, row) for row in candidates]
        for future in as_completed(futures):
            try:
                enriched.append(future.result())
            except Exception:
                catalog_logger.exception("[%s] Failed to enrich a movie detail page.", config["source"])
    # A title can appear on both pages. Prefer now_showing only when it has a
    # listing and preserve the richer poster/date fields.
    unique: dict[tuple[str, str, str], dict] = {}
    for row in enriched:
        key = (row["source"], row["cinema_name"], row["movie_title"].casefold())
        old = unique.get(key)
        if old is None:
            unique[key] = row
            continue
        merged = dict(old)
        for field in catalog_PRESERVE_IF_EMPTY:
            if not merged.get(field) and row.get(field):
                merged[field] = row[field]
        if row.get("listing_status") == "now_showing":
            merged["listing_status"] = "now_showing"
        unique[key] = merged
    catalog_logger.info(
        "[%s] Catalogue records=%d, posters=%d.",
        config["source"],
        len(unique),
        sum(bool(row.get("poster_url")) for row in unique.values()),
    )
    return list(unique.values())


def catalog__cinepax_date(movie: dict) -> tuple[str | None, str | None]:
    for key in ("releaseDate", "release_date", "releaseDateText", "releaseDateLabel"):
        value = movie.get(key)
        if value:
            if isinstance(value, (int, float)):
                continue
            try:
                parsed = date.fromisoformat(str(value)[:10])
                return parsed.isoformat(), str(value)
            except ValueError:
                parsed, label = catalog__date_from_text(str(value))
                if parsed:
                    return parsed, label or str(value)
    return None, None


def catalog__cinepax_movie_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("movies", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def catalog__scrape_cinepax_catalogue() -> list[dict]:
    try:
        cinemas_response = catalog__get(urljoin(catalog_CINEPAX_BASE_URL + "/", "api/cinemas"))
        cinemas = cinemas_response.json()
    except Exception:
        catalog_logger.exception("[Cinepax] Could not load cinema list for catalogue refresh.")
        return []
    if not isinstance(cinemas, list):
        catalog_logger.error("[Cinepax] Unexpected cinema-list payload; skipping catalogue source.")
        return []

    selected = [
        item for item in cinemas
        if str(item.get("name", "")).strip().upper() in catalog_CINEPAX_LAHORE_BRANCHES
    ]
    if not selected:
        catalog_logger.warning("[Cinepax] No configured Lahore branches returned by API.")
        return []

    rows: list[dict] = []
    for cinema in selected:
        cinema_id = cinema.get("id")
        cinema_name = str(cinema.get("name") or "Cinepax Lahore")
        if not cinema_id:
            continue
        try:
            response = catalog__get(
                urljoin(catalog_CINEPAX_BASE_URL + "/", "api/movies"),
                params={"cinemaId": cinema_id},
            )
            movies = catalog__cinepax_movie_list(response.json())
        except Exception:
            catalog_logger.exception("[Cinepax] Movie catalogue request failed for %s.", cinema_name)
            continue

        for movie in movies:
            title = str(movie.get("title") or movie.get("name") or "").strip()
            if not title:
                continue
            release_date, release_label = catalog__cinepax_date(movie)
            screens = movie.get("screens") or {}
            has_sessions = any(
                isinstance(screen, dict) and bool(screen.get("sessions"))
                for screen in screens.values()
            ) if isinstance(screens, dict) else False
            if has_sessions:
                status = "now_showing"
            elif release_date:
                status = "coming_soon" if release_date >= datetime.now(catalog_PK_TZ).date().isoformat() else "released_no_sessions"
            else:
                status = "unknown"
            poster = movie.get("poster") or movie.get("posterUrl") or movie.get("poster_url")
            poster = catalog__usable_image_url(catalog__absolute_url(catalog_CINEPAX_BASE_URL, str(poster))) if poster else None
            trailer = movie.get("trailer") or movie.get("trailerUrl") or movie.get("trailer_url")
            trailer = catalog__absolute_url(catalog_CINEPAX_BASE_URL, str(trailer)) if trailer else None
            detail = movie.get("detailUrl") or movie.get("detail_url") or movie.get("url")
            detail = catalog__absolute_url(catalog_CINEPAX_BASE_URL, str(detail)) if detail else None
            movie_format, language = catalog__format_and_language(title)
            rows.append(
                {
                    "source": "Cinepax",
                    "cinema_name": cinema_name,
                    "source_movie_id": str(movie.get("filmCode") or movie.get("id") or "") or None,
                    "movie_title": title,
                    "listing_status": status,
                    "release_date": release_date,
                    "source_release_label": release_label,
                    "poster_url": poster,
                    "detail_url": detail,
                    "trailer_url": trailer,
                    "language": language or movie.get("language"),
                    "movie_format": movie_format or movie.get("format"),
                }
            )

    catalog_logger.info(
        "[Cinepax] Catalogue records=%d, posters=%d.",
        len(rows),
        sum(bool(row.get("poster_url")) for row in rows),
    )
    return rows


def catalog__scrape_chalo_catalogue() -> list[dict]:
    """Use the existing Chalo parser rather than duplicating its tested logic."""
    try:
        pass
    except Exception:
        catalog_logger.exception(
            "[Chalo] Could not import catalog_scraper.scrape_chalo_catalog; "
            "Chalo catalogue was skipped."
        )
        return []
    try:
        rows = chalo_scrape_chalo_catalog()
    except Exception:
        catalog_logger.exception("[Chalo] Catalogue extraction failed.")
        return []
    if not isinstance(rows, list):
        catalog_logger.error("[Chalo] Catalogue extractor returned an unexpected result.")
        return []
    catalog_logger.info("[Chalo] Catalogue records=%d, posters=%d.", len(rows), sum(bool(r.get("poster_url")) for r in rows))
    return rows


def catalog__catalog_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("source") or "").strip(),
        str(row.get("cinema_name") or "").strip(),
        str(row.get("movie_title") or "").strip(),
    )


def catalog__row_to_schema(row: dict, now: str) -> dict | None:
    source, cinema_name, movie_title = catalog__catalog_key(row)
    if not source or not cinema_name or not movie_title:
        return None
    out = {field: row.get(field) for field in catalog_CATALOG_FIELDS}
    out["source"] = source
    out["cinema_name"] = cinema_name
    out["movie_title"] = movie_title
    out["listing_status"] = out.get("listing_status") or "unknown"
    if out["listing_status"] not in {
        "now_showing", "coming_soon", "released_no_sessions", "unknown"
    }:
        out["listing_status"] = "unknown"
    out["last_seen_at"] = now
    out["updated_at"] = now
    # Normalize blank strings to null so merge logic can preserve known values.
    for field in catalog_CATALOG_FIELDS:
        if isinstance(out.get(field), str) and not out[field].strip():
            out[field] = None
    return out


def catalog__merge_with_existing(incoming: dict, existing: dict | None) -> dict:
    merged = dict(incoming)
    if existing:
        for field in catalog_PRESERVE_IF_EMPTY:
            if not merged.get(field) and existing.get(field):
                merged[field] = existing[field]
        # Keep a previously known release date if today's page omits it.
        if not merged.get("release_date") and existing.get("release_date"):
            merged["release_date"] = existing["release_date"]
        if not merged.get("source_release_label") and existing.get("source_release_label"):
            merged["source_release_label"] = existing["source_release_label"]
        # Do not retain a made-up/old status: use the latest source page status.
    return merged


def catalog_refresh_movie_catalog() -> int:
    """Scrape every source and upsert catalog rows, preserving existing posters.

    A failed source never causes its existing rows to be deleted. If the read of
    current catalogue state fails, the write is aborted rather than risk replacing
    stored poster URLs with null values.
    """
    run_at = datetime.now(catalog_PK_TZ).isoformat()
    catalog_logger.info("Starting multi-source movie catalogue refresh at %s.", run_at)

    incoming: list[dict] = []
    incoming.extend(catalog__scrape_chalo_catalogue())
    for config in catalog_VISTA_SOURCES:
        try:
            incoming.extend(catalog__scrape_vista_source(config))
        except Exception:
            catalog_logger.exception("[%s] Catalogue scrape failed; retaining existing rows.", config["source"])
    try:
        incoming.extend(catalog__scrape_cinepax_catalogue())
    except Exception:
        catalog_logger.exception("[Cinepax] Catalogue scrape failed; retaining existing rows.")

    now = datetime.now(timezone.utc).isoformat()
    rows_by_key: dict[tuple[str, str, str], dict] = {}
    for row in incoming:
        normalized = catalog__row_to_schema(row, now)
        if not normalized:
            continue
        key = catalog__catalog_key(normalized)
        existing = rows_by_key.get(key)
        if existing is None:
            rows_by_key[key] = normalized
        else:
            # Merge repeated cards for a source/cinema/title, retaining metadata.
            rows_by_key[key] = catalog__merge_with_existing(normalized, existing)

    rows = list(rows_by_key.values())
    if not rows:
        catalog_logger.warning("Catalogue refresh produced no rows; database was not modified.")
        return 0

    try:
        existing_response = (
            supabase.table("movie_catalog")
            .select(",".join(catalog_CATALOG_FIELDS))
            .limit(5000)
            .execute()
        )
        existing_rows = existing_response.data or []
    except Exception:
        catalog_logger.exception(
            "Could not read the existing movie_catalog. Aborting catalogue write "
            "to protect stored poster URLs."
        )
        return 0

    existing_by_key = {catalog__catalog_key(row): row for row in existing_rows}
    merged_rows = [
        catalog__merge_with_existing(row, existing_by_key.get(catalog__catalog_key(row)))
        for row in rows
    ]

    # Ignore UI labels mistakenly parsed as movie titles.
    merged_rows = [
        row for row in merged_rows
        if (row.get("movie_title") or "").strip().casefold() != "more details"
    ]

    # Fill poster_url only, using matching titles from other providers.
    merged_rows = poster_fill_missing_posters(
        merged_rows, existing_rows + merged_rows, context="catalogue sources"
    )

    try:
        response = (
            supabase.table("movie_catalog")
            .upsert(
                filter_lahore_catalogue_rows(merged_rows, context="catalog_sources.py.movie_catalog"),
                on_conflict="source,cinema_name,movie_title",
            )
            .execute()
        )
    except Exception:
        catalog_logger.exception("Supabase movie_catalog upsert failed.")
        return 0

    catalog_logger.info(
        "Movie catalogue upsert completed: sent=%d, posters=%d, release_dates=%d.",
        len(merged_rows),
        sum(bool(row.get("poster_url")) for row in merged_rows),
        sum(bool(row.get("release_date")) for row in merged_rows),
    )
    extra_catalogue_count = 0
    try:
        pass
        extra_catalogue_count = cinestar_dha_refresh_new_cinema_sources()
    except Exception:
        catalog_logger.exception("CineStar/DHA catalogue and showtime refresh failed; existing rows retained.")
    return len(merged_rows) + extra_catalogue_count



# ===========================================================================
# CineStar and DHA catalogue/showtime scraper
# ===========================================================================

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from database import supabase
pass
from location_guard import filter_lahore_catalogue_rows
from location_guard import filter_lahore_showtimes

cinestar_dha_logger = logging.getLogger("cinestar_dha_sources")
cinestar_dha_PK_TZ = ZoneInfo(SCRAPER_CONFIG["timezone"])
cinestar_dha_UTC = timezone.utc
cinestar_dha_TIMEOUT = SCRAPER_CONFIG["cinestar_timeout_seconds"]
cinestar_dha_CINESTAR_BASE = SCRAPER_CONFIG["cinestar"]["base_url"]
cinestar_dha_DHA_SHOWTIMES_URL = SCRAPER_CONFIG["dha"]["showtimes_url"]
cinestar_dha_DHA_BASE = SCRAPER_CONFIG["dha"]["base_url"]
cinestar_dha_USER_AGENT = SCRAPER_CONFIG["user_agent"]

# Official CineStar details pages. The cinema codes are confirmed by the
# booking links present on CineStar's official showtime pages.
cinestar_dha_CINESTAR_BRANCHES = [dict(row) for row in SCRAPER_CONFIG["cinestar"]["branches"]]
cinestar_dha_CINESTAR_LISTINGS = list(SCRAPER_CONFIG["cinestar"]["listings"])
cinestar_dha_CATALOG_COLUMNS = (
    "source,cinema_name,source_movie_id,movie_title,listing_status,release_date,"
    "source_release_label,poster_url,detail_url,trailer_url,language,movie_format,"
    "last_seen_at,updated_at"
)
cinestar_dha_PRESERVE_FIELDS = (
    "source_movie_id", "release_date", "source_release_label", "poster_url",
    "detail_url", "trailer_url", "language", "movie_format",
)

cinestar_dha__session = requests.Session()
cinestar_dha__session.headers.update({
    "User-Agent": cinestar_dha_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
})


def cinestar_dha__get(url: str) -> requests.Response:
    response = cinestar_dha__session.get(url, timeout=cinestar_dha_TIMEOUT)
    response.raise_for_status()
    return response


def cinestar_dha__absolute(base: str, value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value or value.startswith(("#", "data:", "javascript:")):
        return None
    result = urljoin(base, value)
    if urlparse(result).scheme not in {"http", "https"}:
        return None
    return result


def cinestar_dha__valid_poster(value: str | None) -> str | None:
    if not value:
        return None
    lower = value.casefold()
    if any(x in lower for x in ("placeholder", "no-image", "noimage", "spinner", "loading.gif", "logo", "avatar")):
        return None
    if lower.startswith("data:") or lower.endswith(".svg"):
        return None
    return value


def cinestar_dha__poster_from_element(element, base_url):
    """Extract poster URLs from image elements and CineStar poster links."""
    if element is None or not hasattr(element, "select"):
        return None

    nodes = [element]
    try:
        nodes.extend(list(element.select("*")))
    except Exception:
        pass

    direct_posters = []
    other_candidates = []

    for node in nodes:
        name = getattr(node, "name", None)
        if not hasattr(node, "get"):
            continue

        if name == "a":
            href = node.get("href")
            if href:
                value = str(href).strip()
                if "FilmPosterGraphic" in value:
                    direct_posters.append(value)
                elif re.search(r"\.(?:jpe?g|png|webp)(?:[?#]|$)", value, re.I):
                    other_candidates.append(value)

        if name in {"img", "source"}:
            for attr in (
                "data-src", "data-original", "data-lazy-src",
                "data-original-src", "src", "content",
            ):
                value = node.get(attr)
                if value and not str(value).startswith("data:"):
                    value = str(value).strip()
                    if "FilmPosterGraphic" in value:
                        direct_posters.append(value)
                    else:
                        other_candidates.append(value)

            srcset = node.get("data-srcset") or node.get("srcset")
            if srcset:
                candidate = str(srcset).split(",")[-1].strip().split()[0]
                other_candidates.append(candidate)

        if name == "meta" and node.get("content"):
            if node.get("property") == "og:image" or node.get("name") == "twitter:image":
                other_candidates.append(str(node["content"]))

        style = str(node.get("style", ""))
        match = re.search(r"background-image\s*:\s*url\((.*?)\)", style, re.I)
        if match:
            other_candidates.append(match.group(1).strip().strip("'\""))

    for candidate in direct_posters + other_candidates:
        absolute = cinestar_dha__absolute(base_url, candidate)
        if not absolute:
            continue
        lowered = absolute.casefold()

        # Don't mistake rating icons and site logos for film posters.
        if any(bad in lowered for bad in (
            "ratingicongraphic", "placeholder", "no-image",
            "noimage", "spinner", "loading.gif", "avatar",
        )):
            continue

        if absolute.lower().endswith(".svg"):
            continue

        return absolute

    return None


def cinestar_dha__title_from_anchor(anchor) -> str:
    if anchor is None:
        return ""
    for selector in (".movie-title", ".title", ".name", "h2", "h3", "h4"):
        node = anchor.select_one(selector)
        if node and node.get_text(" ", strip=True):
            raw = node.get_text(" ", strip=True)
            return cinestar_dha__clean_title(raw)
    image = anchor.select_one("img[alt]")
    if image and image.get("alt"):
        return cinestar_dha__clean_title(str(image["alt"]))
    return cinestar_dha__clean_title(anchor.get_text(" ", strip=True))


def cinestar_dha__clean_title(raw: str) -> str:
    text = " ".join((raw or "").split())
    text = re.sub(r"\b(?:Hide Future Dates|Show Future Dates|View Show Times|See What's Playing|Play Trailer)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -–|:;,.\n\t")
    if text.isupper() and len(text) > 2:
        text = text.title()
    return text[:180].strip()


def cinestar_dha__title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def cinestar_dha__parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = " ".join(str(value).split())
    for pattern, fmt in (
        (r"\b\d{4}-\d{2}-\d{2}\b", "%Y-%m-%d"),
        (r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b", "%d %B %Y"),
        (r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b", "%B %d %Y"),
        (r"\b\d{1,2}/\d{1,2}/\d{4}\b", "%d/%m/%Y"),
        (r"\b\d{1,2}-\d{1,2}-\d{4}\b", "%d-%m-%Y"),
    ):
        match = re.search(pattern, value, re.I)
        if not match:
            continue
        candidate = match.group(0).replace(",", "")
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            # Try abbreviated month variants when a site uses Oct rather than October.
            for alt_fmt in ("%d %b %Y", "%b %d %Y"):
                try:
                    return datetime.strptime(candidate, alt_fmt).date()
                except ValueError:
                    pass
    if re.search(r"\btoday\b", value, re.I):
        return datetime.now(cinestar_dha_PK_TZ).date()
    if re.search(r"\btomorrow\b", value, re.I):
        return datetime.now(cinestar_dha_PK_TZ).date() + timedelta(days=1)
    return None


def cinestar_dha__date_in_text(text: str) -> date | None:
    # Try each line independently to avoid accidentally parsing a different
    # date embedded in a long card's poster alt text or metadata.
    for line in reversed([s.strip() for s in str(text).splitlines() if s.strip()]):
        parsed = cinestar_dha__parse_date(line)
        if parsed:
            return parsed
    return cinestar_dha__parse_date(text)


def cinestar_dha__time_in_text(text: str) -> str | None:
    value = " ".join(str(text or "").split())
    match = re.search(r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AP]\.?M\.?)\b", value, re.I)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(3).upper().startswith("P"):
            hour += 12
        return f"{hour:02d}:{match.group(2)}"
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", value)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return None


def cinestar_dha__nearest_date_before(element) -> date | None:
    # Use the closest preceding visible text node that looks like a full date.
    for text_node in element.find_all_previous(string=True, limit=180):
        parent = getattr(text_node, "parent", None)
        if parent is not None and getattr(parent, "name", "") in {"script", "style", "noscript", "svg"}:
            continue
        value = " ".join(str(text_node).split())
        parsed = cinestar_dha__parse_date(value)
        if parsed:
            return parsed
    return None


def cinestar_dha__source_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    session_id = (qs.get("txtSessionId") or qs.get("sessionId") or [None])[0]
    if session_id:
        return str(session_id)
    path = parsed.path.rstrip("/").split("/")
    return path[-1] if path and path[-1] else None


def cinestar_dha__is_future(local_date: date, local_time: str) -> bool:
    hour, minute = map(int, local_time.split(":"))
    local_dt = datetime.combine(local_date, time(hour, minute), tzinfo=cinestar_dha_PK_TZ)
    return local_dt > datetime.now(cinestar_dha_PK_TZ)


def cinestar_dha__to_utc_iso(local_date: date, local_time: str) -> str:
    hour, minute = map(int, local_time.split(":"))
    local_dt = datetime.combine(local_date, time(hour, minute), tzinfo=cinestar_dha_PK_TZ)
    return local_dt.astimezone(cinestar_dha_UTC).isoformat()


def cinestar_dha__nearest_card(anchor):
    current = anchor
    for _ in range(8):
        current = getattr(current, "parent", None)
        if current is None or getattr(current, "name", None) in {"body", "html"}:
            break
        if not hasattr(current, "select"):
            continue
        # Don't jump to a large page container with many movie cards.
        title_links = current.select('a[href*="/Browsing/Movies/Details/"]')
        has_image = current.select_one("img") is not None or current.select_one('[style*="background-image"]') is not None
        has_time = current.select_one('a[href*="visSelectTickets.aspx"]') is not None
        if len(title_links) <= 2 and (has_image or has_time):
            return current
    return anchor


def cinestar_dha__poster_near_anchor(anchor, base_url: str) -> str | None:
    """Find the film poster, including CineStar's separate sibling poster link."""
    card = cinestar_dha__nearest_card(anchor)

    for root in (card, anchor):
        poster = cinestar_dha__poster_from_element(root, base_url)
        if poster:
            return poster

    # CineStar's listing places an <a href="...FilmPosterGraphic...">
    # before the movie-details anchor, outside the nearest title container.
    for link in anchor.find_all_previous("a", href=True, limit=20):
        href = str(link.get("href") or "")
        if "FilmPosterGraphic" in href:
            candidate = cinestar_dha__valid_poster(cinestar_dha__absolute(base_url, href))
            if candidate:
                return candidate

        # Do not cross the previous film's details link and grab its poster.
        if "/browsing/movies/details/" in href.lower():
            break

    # Also support templates where the poster link follows the title link.
    for link in anchor.find_all_next("a", href=True, limit=12):
        href = str(link.get("href") or "")
        if "FilmPosterGraphic" in href:
            candidate = cinestar_dha__valid_poster(cinestar_dha__absolute(base_url, href))
            if candidate:
                return candidate
        if "/browsing/movies/details/" in href.lower():
            break

    return None


def cinestar_dha__extract_global_cinestar_catalogue() -> dict[str, dict]:
    catalogue: dict[str, dict] = {}
    for route, status in cinestar_dha_CINESTAR_LISTINGS:
        url = urljoin(cinestar_dha_CINESTAR_BASE + "/", route.lstrip("/"))
        try:
            response = cinestar_dha__get(url)
        except Exception as exc:
            cinestar_dha_logger.warning("[CineStar] Catalogue page failed (%s): %s", route, exc)
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        count = 0
        for anchor in soup.select('a[href*="/Browsing/Movies/Details/"]'):
            title = cinestar_dha__title_from_anchor(anchor)
            if not title or len(title) < 2:
                continue
            href = cinestar_dha__absolute(response.url, anchor.get("href"))
            card = cinestar_dha__nearest_card(anchor)
            card_text = card.get_text(" ", strip=True) if hasattr(card, "get_text") else anchor.get_text(" ", strip=True)
            poster = cinestar_dha__poster_near_anchor(anchor, response.url)
            trailer = None
            if hasattr(card, "select"):
                for link in card.select("a[href]"):
                    u = str(link.get("href") or "")
                    if "youtube.com" in u.lower() or "youtu.be" in u.lower():
                        trailer = cinestar_dha__absolute(response.url, u)
                        break
            release = None
            if re.search(r"release\s*date|release", card_text, re.I):
                release = cinestar_dha__date_in_text(card_text)
            release_label = release.isoformat() if release else None
            key = cinestar_dha__title_key(title)
            existing = catalogue.get(key)
            row = {
                "source": "CineStar",
                "cinema_name": None,
                "source_movie_id": cinestar_dha__source_id(href),
                "movie_title": title,
                "listing_status": status,
                "release_date": release.isoformat() if release else None,
                "source_release_label": release_label,
                "poster_url": poster,
                "detail_url": href,
                "trailer_url": trailer,
                "language": None,
                "movie_format": None,
            }
            if existing is None:
                catalogue[key] = row
            else:
                for field in cinestar_dha_PRESERVE_FIELDS:
                    if not existing.get(field) and row.get(field):
                        existing[field] = row[field]
                if status == "now_showing":
                    existing["listing_status"] = status
            count += 1
        cinestar_dha_logger.info("[CineStar] %s catalogue page produced %d entries.", status, count)
    return catalogue


def cinestar_dha__scrape_cinestar_branch(branch: dict, global_catalogue: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    url = branch["detail_url"]
    response = cinestar_dha__get(url)
    soup = BeautifulSoup(response.text, "html.parser")
    branch_name = branch["cinema_name"]
    # Titles visible on this branch detail page are real branch records. Sessions
    # below are extracted from official Ticketing/visSelectTickets.aspx links.
    title_anchors = []
    seen_titles = set()
    for anchor in soup.select('a[href*="/Browsing/Movies/Details/"]'):
        title = cinestar_dha__title_from_anchor(anchor)
        key = cinestar_dha__title_key(title)
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        title_anchors.append((anchor, title, key, cinestar_dha__absolute(response.url, anchor.get("href"))))

    sessions: list[dict] = []
    sessions_by_title: dict[str, int] = {}
    for link in soup.select('a[href*="visSelectTickets.aspx"]'):
        booking_url = cinestar_dha__absolute(response.url, link.get("href"))
        local_time = cinestar_dha__time_in_text(link.get_text(" ", strip=True))
        if not booking_url or not local_time:
            continue
        qs = parse_qs(urlparse(booking_url).query)
        source_cinema_code = (qs.get("cinemacode") or [""])[0].lstrip("0") or "0"
        expected_code = branch["cinema_code"].lstrip("0") or "0"
        # Ignore ticket links for other CineStar branches if a global booking
        # widget happens to be present on the detail page.
        if source_cinema_code != expected_code:
            continue
        show_date = cinestar_dha__nearest_date_before(link)
        if not show_date or not cinestar_dha__is_future(show_date, local_time):
            continue
        title_anchor = next(
            (a for a in link.find_all_previous("a", href=True)
             if "/browsing/movies/details/" in str(a.get("href", "")).lower()),
            None,
        )
        title = cinestar_dha__title_from_anchor(title_anchor) if title_anchor else ""
        if not title:
            # Some page templates use plain title links without the standard
            # route; use the nearest preceding recognized heading as a fallback.
            for node in link.find_all_previous(["h2", "h3", "h4", "h5"]):
                candidate = cinestar_dha__clean_title(node.get_text(" ", strip=True))
                if candidate and not cinestar_dha__time_in_text(candidate) and not cinestar_dha__parse_date(candidate):
                    title = candidate
                    break
        if not title:
            cinestar_dha_logger.debug("[CineStar] Cannot associate session with a title: %s", booking_url)
            continue
        title = cinestar_dha__clean_title(title)
        key = cinestar_dha__title_key(title)
        sessions.append({
            "movie_title": title,
            "cinema_name": branch_name,
            "showtime": cinestar_dha__to_utc_iso(show_date, local_time),
            # Preserve the official label parsed above so independent source
            # certification can compare display text as well as normalized time.
            "display_time": " ".join(link.get_text(" ", strip=True).split()),
            "booking_link": booking_url,
            "ticket_price": None,
        })
        sessions_by_title[key] = sessions_by_title.get(key, 0) + 1

    catalog_rows: list[dict] = []
    for anchor, title, key, detail_url in title_anchors:
        global_row = global_catalogue.get(key, {})
        poster = cinestar_dha__poster_near_anchor(anchor, response.url) or global_row.get("poster_url")
        status = global_row.get("listing_status") or "unknown"
        future_sessions = sessions_by_title.get(key, 0)
        if future_sessions:
            status = "now_showing"
        elif status == "now_showing":
            # The global chain page alone does not prove this specific branch
            # has a session; avoid asserting it is playing at this branch.
            status = "unknown"
        catalog_rows.append({
            "source": "CineStar",
            "cinema_name": branch_name,
            "source_movie_id": cinestar_dha__source_id(detail_url) or global_row.get("source_movie_id"),
            "movie_title": title,
            "listing_status": status,
            "release_date": global_row.get("release_date"),
            "source_release_label": global_row.get("source_release_label"),
            "poster_url": poster,
            "detail_url": detail_url or global_row.get("detail_url"),
            "trailer_url": global_row.get("trailer_url"),
            "language": None,
            "movie_format": None,
        })
    cinestar_dha_logger.info(
        "[CineStar] %s: catalogue records=%d posters=%d future sessions=%d.",
        branch_name, len(catalog_rows), sum(bool(r.get("poster_url")) for r in catalog_rows), len(sessions),
    )
    return catalog_rows, sessions


def cinestar_dha__element_date(element, card_text: str, active_date: date | None) -> date | None:
    node = element
    for _ in range(5):
        if node is None or not hasattr(node, "get"):
            break
        for attr in ("data-date", "data-show-date", "datetime", "date"):
            value = node.get(attr)
            if value:
                parsed = cinestar_dha__parse_date(str(value))
                if parsed:
                    return parsed
        node = getattr(node, "parent", None)
    # Only treat a date visible on a session card as a screening date; a
    # movie's labelled release date must never be repurposed as a showtime date.
    lines = [line.strip() for line in str(card_text).splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"release\s*date|release date|premiere", line, re.I):
            continue
        parsed = cinestar_dha__parse_date(line)
        if parsed:
            return parsed
    return active_date


def cinestar_dha__dha_active_date(soup: BeautifulSoup) -> date | None:
    selectors = [
        '[aria-current="date"]', '[aria-selected="true"]',
        '[data-date].active', '[data-date].selected',
        'button.active', 'button.selected', '[role="tab"][aria-selected="true"]',
    ]
    for selector in selectors:
        for node in soup.select(selector):
            text = " ".join(node.get_text(" ", strip=True).split())
            for attr in ("data-date", "datetime", "date"):
                if node.get(attr):
                    parsed = cinestar_dha__parse_date(str(node.get(attr)))
                    if parsed:
                        return parsed
            if re.search(r"\btoday\b|\btomorrow\b|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}", text, re.I):
                parsed = cinestar_dha__parse_date(text)
                if parsed:
                    return parsed
    return None


def cinestar_dha__movie_title_from_card(card) -> str:
    if hasattr(card, "get") and card.get("data-movie-title"):
        own_title = cinestar_dha__clean_title(str(card.get("data-movie-title")))
        if own_title and len(own_title) < 130:
            return own_title
    for selector in (
        "[data-movie-title]", ".movie-title", ".movie-name", ".film-title",
        ".title", "h2", "h3", "h4", "h5",
    ):
        node = card.select_one(selector)
        if node:
            title = cinestar_dha__clean_title(str(node.get("data-movie-title") or node.get_text(" ", strip=True)))
            if title and len(title) < 130:
                return title
    img = card.select_one("img[alt]")
    if img:
        title = cinestar_dha__clean_title(str(img.get("alt", "")))
        if title and len(title) < 130:
            return title
    return ""


# BEGIN CINEGOLD LAHORE SOURCE

def cinegold__catalogue_and_sessions() -> tuple[list[dict], list[dict], dict[str, bool]]:
    """Fetch Lahore-only CineGold Plex schedules through its public date API.

    Returns catalogue records, future session records, and per-lounge completeness.
    Every scheduled date is checked independently for both Lahore lounges. Only an
    explicit, understood "No Schedule Available" response counts as a valid empty
    date. HTTP failures, unknown response states, malformed cards, and duplicate
    session keys mark that lounge snapshot non-authoritative.
    """
    import json
    import re
    from datetime import date, datetime, timedelta
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup
    import requests

    cfg = SCRAPER_CONFIG["cinegold"]
    base_url = str(cfg["base_url"]).rstrip("/") + "/"
    showtimes_url = str(cfg["showtimes_url"])
    endpoint = str(cfg["schedule_endpoint"])
    branch_id = str(cfg["lahore_branch_id"])
    lounges = {str(k): str(v) for k, v in cfg["lounge_ids"].items()}
    lounge_labels = {
        "Gold Lounge": "Raiha CineGold Plex - Gold Lounge, Lahore",
        "Red Lounge": "Raiha CineGold Plex - Red Lounge, Lahore",
    }
    logger_obj = cinestar_dha_logger
    today = datetime.now(cinestar_dha_PK_TZ).date()
    session = requests.Session()
    session.headers.update({
        "User-Agent": cinestar_dha_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": base_url.rstrip("/"),
        "Referer": showtimes_url,
        "X-Requested-With": "XMLHttpRequest",
    })

    catalogue_by_title: dict[str, dict] = {}
    # A single title can appear in more than one lounge/date. Keep only a value
    # that is consistent across the source cards; conflicting metadata remains blank.
    catalogue_metadata_by_title: dict[str, dict[str, set[str]]] = {}
    session_rows: list[dict] = []
    complete_by_lounge = {label: True for label in lounge_labels.values()}
    rows_by_lounge: dict[str, list[dict]] = {label: [] for label in lounge_labels.values()}
    date_failures_by_lounge = {label: [] for label in lounge_labels.values()}
    seen_by_lounge: dict[str, set[tuple[str, str, str]]] = {
        label: set() for label in lounge_labels.values()
    }
    parsed_response_count = {label: 0 for label in lounge_labels.values()}
    schedule_request_count = {label: 0 for label in lounge_labels.values()}

    try:
        page = session.get(showtimes_url, timeout=cinestar_dha_TIMEOUT)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")

        # Validate the website's explicit branch/lounges before treating any results
        # as Lahore data. The documented IDs are never inferred from default selection.
        branch_option = soup.select_one(f"#branchSel option[value='{branch_id}']")
        if not branch_option or "lahore" not in branch_option.get_text(" ", strip=True).casefold():
            for label in complete_by_lounge:
                complete_by_lounge[label] = False
                date_failures_by_lounge[label].append("Lahore branch selector was not verified in the page")
        weekly_lounge_options = {
            str(option.get("value"))
            for option in soup.select(f".lng-{branch_id} option[value]")
        }
        if not set(lounges.values()).issubset(weekly_lounge_options):
            for label in complete_by_lounge:
                complete_by_lounge[label] = False
                date_failures_by_lounge[label].append(
                    f"Expected Lahore lounge IDs {sorted(lounges.values())}; saw {sorted(weekly_lounge_options)}"
                )

        # Prefer the exact date tabs exposed by the weekly schedule widget. If the
        # site changes markup, still retrieve a bounded 7-day sample but explicitly
        # mark it non-authoritative so it cannot reconcile stored sessions.
        date_values = []
        for node in soup.select(".date_section li a[data-date]"):
            raw = str(node.get("data-date") or "").strip()
            try:
                parsed = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if parsed >= today and parsed not in date_values:
                date_values.append(parsed)
        date_values = sorted(date_values)[:max(1, min(14, int(cfg.get("lookahead_days", 7))))]
        if not date_values or today not in date_values:
            input_node = soup.select_one("#dateInp")
            fallback_value = str(input_node.get("value") or "").strip() if input_node else ""
            try:
                fallback_today = date.fromisoformat(fallback_value[:10])
            except ValueError:
                fallback_today = today
            date_values = [fallback_today + timedelta(days=i) for i in range(max(1, min(14, int(cfg.get("lookahead_days", 7)))))]
            for label in complete_by_lounge:
                complete_by_lounge[label] = False
                date_failures_by_lounge[label].append(
                    "Could not verify the weekly schedule date tabs; bounded fallback dates used"
                )

        if len(date_values) < 2:
            for label in complete_by_lounge:
                complete_by_lounge[label] = False
                date_failures_by_lounge[label].append("Fewer than two future dates were exposed by the source")

        for day in date_values:
            date_text = day.isoformat()
            for lounge_name, lounge_id in lounges.items():
                venue = lounge_labels.get(lounge_name)
                if not venue:
                    continue
                schedule_request_count[venue] += 1
                try:
                    response = session.post(
                        endpoint,
                        data={"branchId": branch_id, "loungeId": lounge_id, "showDate": date_text},
                        timeout=cinestar_dha_TIMEOUT,
                    )
                    response.raise_for_status()
                    try:
                        payload = response.json()
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise RuntimeError(f"response was not JSON: {exc}") from exc
                    if not isinstance(payload, dict):
                        raise RuntimeError("API response was not a JSON object")

                    api_status = str(payload.get("status") or "").strip().casefold()
                    message = payload.get("message")
                    if not isinstance(message, str):
                        raise RuntimeError("API message field was not HTML/text")

                    if api_status == "warning" and re.search(r"no\s+schedule\s+available", BeautifulSoup(message, "html.parser").get_text(" ", strip=True), re.I):
                        parsed_response_count[venue] += 1
                        continue
                    if api_status != "success":
                        raise RuntimeError(f"unrecognized API status {api_status!r}: {message[:180]!r}")

                    fragment = BeautifulSoup(message, "html.parser")
                    cards = fragment.select(".movie-section")
                    if not cards:
                        raise RuntimeError("API said success but no .movie-section movie cards were found")

                    for card in cards:
                        title_node = card.select_one(".show-popup-title p")
                        if not title_node:
                            title_node = card.select_one(".show-popup-title, .popup-title-main p")
                        if not title_node:
                            raise RuntimeError(f"movie card has no title on {date_text} / {lounge_name}")
                        # The movie title is the direct text before the nested language/format span.
                        direct_parts = [str(part).strip() for part in title_node.contents if isinstance(part, str) and str(part).strip()]
                        title = " ".join(direct_parts).strip()
                        if not title:
                            title = title_node.get_text(" ", strip=True)
                            title = re.sub(r"\s+(?:2D|3D|IMAX|English|Urdu)\b.*$", "", title, flags=re.I).strip()
                        title = cinestar_dha__clean_title(title)
                        if not title or len(title) > 160:
                            raise RuntimeError(f"movie card has an invalid title on {date_text} / {lounge_name}")

                        metadata_node = title_node.select_one(".watch-lang") or card.select_one(".watch-lang")
                        metadata_text = metadata_node.get_text(" ", strip=True) if metadata_node else ""
                        language_match = re.search(
                            r"\b(English|Urdu|Punjabi|Hindi|Pashto|Arabic)\b", metadata_text, re.I
                        )
                        format_match = re.search(r"\b(2D|3D|IMAX|4DX)\b", metadata_text, re.I)
                        language_value = language_match.group(1).title() if language_match else None
                        format_value = format_match.group(1).upper() if format_match else None

                        title_key = title.casefold()
                        metadata_values = catalogue_metadata_by_title.setdefault(
                            title_key, {"languages": set(), "formats": set()}
                        )
                        if language_value:
                            metadata_values["languages"].add(language_value)
                        if format_value:
                            metadata_values["formats"].add(format_value)

                        if title_key not in catalogue_by_title:
                            slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or title_key
                            catalogue_by_title[title_key] = {
                                "source": "Raiha CineGold Plex",
                                "cinema_name": "Raiha CineGold Plex, Lahore",
                                "source_movie_id": slug,
                                "movie_title": title,
                                "listing_status": "now_showing",
                                "release_date": None,
                                "source_release_label": None,
                                "poster_url": None,
                                "detail_url": showtimes_url,
                                "trailer_url": None,
                                "language": None,
                                "movie_format": None,
                            }

                        timing_links = card.select(".showtime-timings li a")
                        if not timing_links:
                            raise RuntimeError(f"{title!r} card has no time links on {date_text} / {lounge_name}")
                        valid_times_in_card = 0
                        for anchor in timing_links:
                            display = anchor.get_text(" ", strip=True)
                            match = re.fullmatch(r"\s*(\d{1,2}):([0-5]\d)\s*([ap])\.?m\.?\s*", display, re.I)
                            if not match:
                                complete_by_lounge[venue] = False
                                date_failures_by_lounge[venue].append(
                                    f"Unrecognized time string {display!r} for {title!r} on {date_text}"
                                )
                                continue
                            hour = int(match.group(1))
                            minute = int(match.group(2))
                            meridiem = match.group(3).casefold()
                            if not 1 <= hour <= 12:
                                complete_by_lounge[venue] = False
                                date_failures_by_lounge[venue].append(
                                    f"Invalid 12-hour clock value {display!r} for {title!r} on {date_text}"
                                )
                                continue
                            hour24 = hour % 12 + (12 if meridiem == "p" else 0)
                            local_time_text = f"{hour24:02d}:{minute:02d}"
                            local_time_obj = datetime.strptime(local_time_text, "%H:%M").time()
                            valid_times_in_card += 1

                            # The source date is retained as the calendar date. We do
                            # not guess a midnight rollover; the visible source time
                            # and the selected source date define the session.
                            if not cinestar_dha__is_future(day, local_time_text):
                                continue
                            local_dt = datetime.combine(day, local_time_obj, tzinfo=cinestar_dha_PK_TZ)
                            stamp = cinestar_dha__to_utc_iso(day, local_time_text)
                            if not stamp:
                                complete_by_lounge[venue] = False
                                date_failures_by_lounge[venue].append(
                                    f"Could not convert {date_text} {display!r} for {title!r}"
                                )
                                continue
                            key = (title.casefold(), venue.casefold(), str(stamp))
                            if key in seen_by_lounge[venue]:
                                complete_by_lounge[venue] = False
                                date_failures_by_lounge[venue].append(
                                    f"Duplicate source session {title!r} {date_text} {display}"
                                )
                                continue
                            seen_by_lounge[venue].add(key)
                            booking_href = str(anchor.get("href") or "").strip()
                            booking_link = urljoin(base_url, booking_href) if booking_href and booking_href not in {"#", "javascript:;", "javascript:void(0);"} else showtimes_url
                            row = {
                                "source": "Raiha CineGold Plex",
                                "movie_title": title,
                                "cinema_name": venue,
                                "showtime": stamp,
                                "raw_showtime": local_dt.isoformat(),
                                "display_time": display,
                                "ticket_price": None,
                                "booking_link": booking_link,
                                "source_url": showtimes_url,
                                "movie_detail_url": showtimes_url,
                                "film_code": None,
                                "scheduled_film_id": None,
                                "movie_genre": None,
                                "movie_cast": None,
                                "movie_duration": None,
                                "movie_rating": None,
                                "poster_url": None,
                                "backdrop_url": None,
                                "cinema_id": branch_id,
                                "session_id": None,
                                "screen_name": lounge_name,
                                "screen_number": None,
                                "seats_available": None,
                                "attributes": f"Lounge: {lounge_name}; branch_id={branch_id}; lounge_id={lounge_id}",
                                "business_date": day.isoformat(),
                                "calendar_date": day.isoformat(),
                                "late_night": local_dt.hour >= 23 or local_dt.hour < 5,
                                "source_language": language_value,
                                "source_format": format_value,
                                "is_lahore_venue": True,
                            }
                            rows_by_lounge[venue].append(row)
                            session_rows.append(row)
                        if valid_times_in_card == 0:
                            complete_by_lounge[venue] = False
                            date_failures_by_lounge[venue].append(
                                f"No valid time strings parsed for {title!r} on {date_text}"
                            )

                    parsed_response_count[venue] += 1
                except Exception as exc:
                    complete_by_lounge[venue] = False
                    date_failures_by_lounge[venue].append(
                        f"{date_text}: {type(exc).__name__}: {exc}"
                    )
                    logger_obj.warning(
                        "[Raiha CineGold Plex] %s schedule request failed for %s: %s",
                        lounge_name, date_text, exc,
                    )

    finally:
        session.close()

    for title_key, catalogue_row in catalogue_by_title.items():
        values = catalogue_metadata_by_title.get(title_key, {})
        languages = values.get("languages", set())
        formats = values.get("formats", set())
        catalogue_row["language"] = next(iter(languages)) if len(languages) == 1 else None
        catalogue_row["movie_format"] = next(iter(formats)) if len(formats) == 1 else None

    catalogue_rows = list(catalogue_by_title.values())
    for venue, complete in complete_by_lounge.items():
        failures = date_failures_by_lounge[venue]
        logger_obj.info(
            "[Raiha CineGold Plex] %s: rows=%d dates_checked=%d valid_responses=%d complete=%s failures=%d.",
            venue,
            len(rows_by_lounge[venue]),
            len(date_values) if "date_values" in locals() else 0,
            parsed_response_count[venue],
            complete and bool(rows_by_lounge[venue]),
            len(failures),
        )
        for failure in failures[:8]:
            logger_obj.warning("[Raiha CineGold Plex] %s source warning: %s", venue, failure)
        if "date_values" not in locals() or schedule_request_count[venue] != len(date_values):
            complete_by_lounge[venue] = False
        if schedule_request_count[venue] == 0 or parsed_response_count[venue] != schedule_request_count[venue]:
            complete_by_lounge[venue] = False

    return catalogue_rows, session_rows, complete_by_lounge

# END CINEGOLD LAHORE SOURCE


def cinestar_dha__dha_catalogue_and_sessions() -> tuple[list[dict], list[dict]]:
    """Fetch DHA Cinema Lahore movies and sessions from its BookKaru JSON feed.

    This is a source-only function: it performs HTTP GET requests and returns
    normalized catalogue/session dictionaries. It never writes to Supabase.
    If the response shape or venue identity is unexpected, raise so the caller
    marks this source non-authoritative and does not reconcile stale rows.
    """
    import requests
    from datetime import date, datetime, timedelta

    source_page = "https://dhacinemas.pk/showtimes"
    api_url = "https://app.bookkaru.com/api/web/movie/list"
    expected_cinema_id = "77900f8c-1ad9-497b-838f-4988342deee4"
    today = datetime.now(cinestar_dha_PK_TZ).date()
    params = {
        "cinema_id": expected_cinema_id,
        "from_date": today.isoformat(),
        "to_date": (today + timedelta(days=7)).isoformat(),
        "coming_soon": "false",
    }

    client = requests.Session()
    client.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        # Visiting the public page first establishes the ordinary browser session
        # required by the API edge; no login or private credentials are used.
        page_response = client.get(source_page, timeout=25)
        page_response.raise_for_status()
        response = client.get(
            api_url,
            params=params,
            headers={
                "Origin": "https://dhacinemas.pk",
                "Referer": source_page,
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"DHA BookKaru request failed: {type(exc).__name__}: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("Status") not in (200, "200"):
        raise RuntimeError("DHA BookKaru response did not contain Status=200.")
    movies = payload.get("message")
    if not isinstance(movies, list):
        raise RuntimeError("DHA BookKaru response has no list-valued 'message' collection.")
    if not movies:
        cinestar_dha_logger.warning(
            "[DHA Cinema] BookKaru returned an empty movie list; source is not authoritative."
        )
        return [], []

    catalogue_rows: list[dict] = []
    showtime_rows: list[dict] = []
    source_movie_count = 0
    source_schedule_count = 0
    skipped_inactive = 0

    for movie in movies:
        if not isinstance(movie, dict):
            raise RuntimeError("DHA BookKaru movie collection contains a non-object record.")

        movie_cinema_id = str(movie.get("cinema_id") or "")
        cinema = movie.get("cinema")
        if not isinstance(cinema, dict):
            raise RuntimeError("DHA BookKaru movie record has no nested cinema object.")
        nested_cinema_id = str(cinema.get("id") or "")
        city = str(cinema.get("city") or "").strip().casefold()
        venue_name = str(cinema.get("name") or "").strip()
        compact_venue = re.sub(r"[^a-z0-9]+", "", venue_name.casefold())
        if (
            movie_cinema_id != expected_cinema_id
            or nested_cinema_id != expected_cinema_id
            or city != "lahore"
            or "dhacinema" not in compact_venue
            or "lahore" not in venue_name.casefold()
        ):
            raise RuntimeError(
                "DHA BookKaru response venue validation failed; refusing to treat it as authoritative."
            )

        title = cinestar_dha__clean_title(str(movie.get("name") or ""))
        movie_id = str(movie.get("id") or "").strip()
        if not title or not movie_id:
            raise RuntimeError("DHA BookKaru movie record is missing an ID or title.")

        is_active = movie.get("is_active")
        if is_active is False:
            skipped_inactive += 1
            continue

        schedules = movie.get("schedules", [])
        if schedules is None:
            schedules = []
        if not isinstance(schedules, list):
            raise RuntimeError(f"DHA BookKaru schedules for {title!r} are not a list.")
        movie_future_count = 0
        source_movie_count += 1

        for schedule in schedules:
            if not isinstance(schedule, dict):
                raise RuntimeError(f"DHA BookKaru schedule for {title!r} is not an object.")
            raw_date = str(schedule.get("schedule_date") or "").strip()
            raw_time = str(schedule.get("start_time") or "").strip()
            if not raw_date or not raw_time:
                raise RuntimeError(f"DHA BookKaru returned a schedule without date/time for {title!r}.")
            try:
                show_date = date.fromisoformat(raw_date[:10])
                try:
                    local_time = datetime.strptime(raw_time, "%H:%M:%S").strftime("%H:%M")
                except ValueError:
                    local_time = datetime.strptime(raw_time, "%H:%M").strftime("%H:%M")
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"DHA BookKaru returned an invalid schedule date/time for {title!r}: "
                    f"{raw_date!r} {raw_time!r}"
                ) from exc

            source_schedule_count += 1
            if not cinestar_dha__is_future(show_date, local_time):
                continue

            showtime_iso = cinestar_dha__to_utc_iso(show_date, local_time)
            if not showtime_iso:
                raise RuntimeError(f"Could not convert DHA showtime for {title!r} to UTC.")
            # Use the cinema's own showtimes page as a verified booking fallback.
            pricing = schedule.get("pricing")
            showtime_rows.append({
                "movie_title": title,
                "cinema_name": "DHA Cinema, Lahore",
                "showtime": showtime_iso,
                "raw_showtime": f"{show_date.isoformat()}T{raw_time}",
                "display_time": raw_time,
                "session_id": schedule.get("id") or schedule.get("session_id") or schedule.get("schedule_id"),
                "screen_number": schedule.get("screen_number"),
                "pricing": pricing if isinstance(pricing, list) else [],
                "source_language": movie.get("language") or schedule.get("language"),
                "source_format": movie.get("format") or schedule.get("format"),
                "business_date": str(schedule.get("business_date") or raw_date),
                "calendar_date": str(schedule.get("calendar_date") or raw_date),
                "booking_link": source_page,
                # BookKaru may expose multiple seat-type prices. Keep the structured
                # pricing array for source auditing rather than collapsing it to one price.
                "ticket_price": None,
            })
            movie_future_count += 1

        raw_poster = movie.get("image_url")
        if not raw_poster:
            banners = movie.get("banner_urls") or []
            if isinstance(banners, list) and banners:
                raw_poster = banners[0]
        poster_url = (
            cinestar_dha__absolute("https://dhacinemas.pk/", str(raw_poster))
            if raw_poster else None
        )
        release_date = None
        raw_release = movie.get("release_date")
        if raw_release:
            parsed_release = cinestar_dha__parse_date(str(raw_release))
            if parsed_release:
                release_date = parsed_release.isoformat()

        if movie_future_count:
            status = "now_showing"
        elif movie.get("coming_soon"):
            status = "coming_soon"
        else:
            status = "released_no_sessions"

        catalogue_rows.append({
            "source": "DHA Cinemas",
            "cinema_name": "DHA Cinema, Lahore",
            "source_movie_id": movie_id,
            "movie_title": title,
            "listing_status": status,
            "release_date": release_date,
            "source_release_label": str(raw_release) if raw_release else None,
            "poster_url": poster_url,
            "detail_url": source_page,
            "trailer_url": movie.get("trailer_url") or None,
            "language": movie.get("language") or None,
            "movie_format": None,
        })

    # An empty post-filter result is never considered a complete schedule snapshot.
    if not catalogue_rows or not showtime_rows:
        cinestar_dha_logger.warning(
            "[DHA Cinema] API was structurally valid but produced no usable future sessions "
            "(movies=%d schedules=%d inactive=%d); source is not authoritative.",
            len(catalogue_rows), source_schedule_count, skipped_inactive,
        )
        return catalogue_rows, []

    # De-duplicate at the same natural key used by the database table.
    unique_sessions: dict[tuple[str, str, str], dict] = {}
    for row in showtime_rows:
        key = (
            str(row["movie_title"]).casefold(),
            str(row["cinema_name"]).casefold(),
            str(row["showtime"]),
        )
        unique_sessions[key] = row
    showtime_rows = list(unique_sessions.values())

    cinestar_dha_logger.info(
        "[DHA Cinema] BookKaru API validated: movies=%d source schedules=%d "
        "future unique sessions=%d inactive skipped=%d date range=%s..%s.",
        source_movie_count, source_schedule_count, len(showtime_rows), skipped_inactive,
        params["from_date"], params["to_date"],
    )
    return catalogue_rows, showtime_rows



def cinestar_dha__merge_catalog_row(incoming: dict, existing: dict | None, now: str) -> dict:
    merged = dict(incoming)
    merged["last_seen_at"] = now
    merged["updated_at"] = now
    if existing:
        for field in cinestar_dha_PRESERVE_FIELDS:
            if not merged.get(field) and existing.get(field):
                merged[field] = existing[field]
    return {key: merged.get(key) for key in (
        "source", "cinema_name", "source_movie_id", "movie_title", "listing_status",
        "release_date", "source_release_label", "poster_url", "detail_url", "trailer_url",
        "language", "movie_format", "last_seen_at", "updated_at",
    )}


def cinestar_dha__upsert_catalogue(rows: list[dict]) -> int:
    dedup: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        source = str(row.get("source") or "").strip()
        cinema = str(row.get("cinema_name") or "").strip()
        title = cinestar_dha__clean_title(str(row.get("movie_title") or ""))
        if not (source and cinema and title):
            continue
        row["movie_title"] = title
        row["source"] = source
        row["cinema_name"] = cinema
        if row.get("listing_status") not in {"now_showing", "coming_soon", "released_no_sessions", "unknown"}:
            row["listing_status"] = "unknown"
        dedup[(source, cinema, title.casefold())] = row
    if not dedup:
        cinestar_dha_logger.info("[CineStar/DHA] No catalogue records to upsert.")
        return 0

    now = datetime.now(cinestar_dha_UTC).isoformat()
    try:
        existing_response = supabase.table("movie_catalog").select(cinestar_dha_CATALOG_COLUMNS).limit(5000).execute()
        existing_rows = existing_response.data or []
    except Exception:
        cinestar_dha_logger.exception("[CineStar/DHA] Could not read movie_catalog; skipping catalogue write to preserve current posters.")
        return 0
    existing_by_key = {
        (str(r.get("source") or "").strip(), str(r.get("cinema_name") or "").strip(), str(r.get("movie_title") or "").strip().casefold()): r
        for r in existing_rows
    }
    values = [
        cinestar_dha__merge_catalog_row(row, existing_by_key.get((key[0], key[1], key[2])), now)
        for key, row in dedup.items()
    ]

    # Ignore UI labels mistakenly parsed as movie titles.
    values = [
        row for row in values
        if (row.get("movie_title") or "").strip().casefold() != "more details"
    ]

    # Fill poster_url only, using matching titles from other providers.
    values = poster_fill_missing_posters(
        values, existing_rows + values, context="CineStar/DHA sources"
    )

    try:
        result = supabase.table("movie_catalog").upsert(
            filter_lahore_catalogue_rows(values, context="cinestar_dha_sources.py.movie_catalog"), on_conflict="source,cinema_name,movie_title"
        ).execute()
        cinestar_dha_logger.info(
            "[CineStar/DHA] Catalogue upsert sent=%d posters=%d; Supabase returned %d rows.",
            len(values), sum(bool(r.get("poster_url")) for r in values), len(result.data or []),
        )
        return len(values)
    except Exception:
        cinestar_dha_logger.exception("[CineStar/DHA] movie_catalog upsert failed.")
        return 0


def cinestar_dha__upsert_showtimes(rows: list[dict]) -> int:
    dedup: dict[tuple[str, str, str], dict] = {}
    now = datetime.now(cinestar_dha_UTC)
    for row in rows:
        title = cinestar_dha__clean_title(str(row.get("movie_title") or ""))
        cinema = str(row.get("cinema_name") or "").strip()
        raw = row.get("showtime")
        if not title or not cinema or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=cinestar_dha_UTC)
            parsed = parsed.astimezone(cinestar_dha_UTC)
        except (ValueError, TypeError):
            continue
        if parsed <= now:
            continue
        normalized = {
            "movie_title": title,
            "cinema_name": cinema,
            "showtime": parsed.isoformat(),
            "booking_link": row.get("booking_link"),
            "ticket_price": row.get("ticket_price"),
        }
        dedup[(title.casefold(), cinema.casefold(), parsed.isoformat())] = normalized
    if not dedup:
        cinestar_dha_logger.info("[CineStar/DHA] No future showtime slots parsed; no showtime rows written.")
        return 0
    try:
        result = supabase.table("showtimes").upsert(
            filter_lahore_showtimes(list(dedup.values()), context="cinestar_dha_sources.py.showtimes"), on_conflict="movie_title,cinema_name,showtime"
        ).execute()
        cinestar_dha_logger.info("[CineStar/DHA] Showtimes upsert sent=%d, returned=%d.", len(dedup), len(result.data or []))
        return len(dedup)
    except Exception:
        cinestar_dha_logger.exception("[CineStar/DHA] showtimes upsert failed.")
        return 0


def cinestar_dha_refresh_new_cinema_sources() -> int:
    """Scrape CineStar + DHA metadata and sessions; return catalogue rows upserted."""
    started = datetime.now(cinestar_dha_PK_TZ).isoformat()
    cinestar_dha_logger.info("Starting CineStar/DHA source refresh at %s.", started)
    catalogue_rows: list[dict] = []
    showtime_rows: list[dict] = []
    successful_snapshots: list[tuple[str, list[dict]]] = []
    audit_snapshots: list[tuple[str, bool, list[dict]]] = []

    global_cinestar = cinestar_dha__extract_global_cinestar_catalogue()
    # Keep the official site-wide listings (especially Coming Soon films that
    # have no branch schedule yet) as catalogue-only records. Their venue name
    # is intentionally not one of the mapped cinema branches, so they cannot
    # create a false map pin or imply branch-level availability.
    for global_row in global_cinestar.values():
        catalogue_only = dict(global_row)
        catalogue_only["cinema_name"] = "CineStar Lahore"
        catalogue_rows.append(catalogue_only)

    for branch in cinestar_dha_CINESTAR_BRANCHES:
        try:
            branch_catalogue, branch_sessions = cinestar_dha__scrape_cinestar_branch(branch, global_cinestar)
            catalogue_rows.extend(branch_catalogue)
            showtime_rows.extend(branch_sessions)
            if branch_sessions:
                successful_snapshots.append((branch["cinema_name"], list(branch_sessions)))
                audit_snapshots.append((branch["cinema_name"], True, list(branch_sessions)))
            else:
                audit_snapshots.append((branch["cinema_name"], False, []))
        except Exception as exc:
            cinestar_dha_logger.warning("[CineStar] Could not scrape %s: %s", branch["cinema_name"], exc)
            audit_snapshots.append((branch["cinema_name"], False, []))

    try:
        dha_catalogue, dha_sessions = cinestar_dha__dha_catalogue_and_sessions()
        catalogue_rows.extend(dha_catalogue)
        showtime_rows.extend(dha_sessions)
        if dha_sessions:
            successful_snapshots.append(("DHA Cinema, Lahore", list(dha_sessions)))
            audit_snapshots.append(("DHA Cinema, Lahore", True, list(dha_sessions)))
        else:
            audit_snapshots.append(("DHA Cinema, Lahore", False, []))
    except Exception as exc:
        cinestar_dha_logger.warning("[DHA Cinemas] Source unavailable or parser could not read it: %s", exc)
        audit_snapshots.append(("DHA Cinema, Lahore", False, []))


    # CineGold Plex Lahore is part of the same branch-level catalogue, showtime,
    # reconciliation, and read-only post-scrape audit pipeline.
    cinegold_labels = (
        "Raiha CineGold Plex - Gold Lounge, Lahore",
        "Raiha CineGold Plex - Red Lounge, Lahore",
    )
    try:
        cinegold_catalogue, cinegold_sessions, cinegold_complete = cinegold__catalogue_and_sessions()
        catalogue_rows.extend(cinegold_catalogue)
        showtime_rows.extend(cinegold_sessions)
        for source_label in cinegold_labels:
            source_rows = [row for row in cinegold_sessions if row.get("cinema_name") == source_label]
            is_complete = bool(cinegold_complete.get(source_label, False))
            audit_snapshots.append((source_label, is_complete, list(source_rows)))
            if is_complete and source_rows:
                successful_snapshots.append((source_label, list(source_rows)))
    except Exception as exc:
        cinestar_dha_logger.warning("[Raiha CineGold Plex] Source unavailable or parser failed: %s", exc)
        for source_label in cinegold_labels:
            audit_snapshots.append((source_label, False, []))

    # Save the catalogue first, preserving good data from earlier successful runs.
    catalogue_count = cinestar_dha__upsert_catalogue(catalogue_rows)
    showtime_count = cinestar_dha__upsert_showtimes(showtime_rows)
    if showtime_count > 0:
        for snapshot_label, snapshot_rows in successful_snapshots:
            try:
                removed = reconcile_showtimes_snapshot(snapshot_rows, f"{snapshot_label} snapshot")
                logger.info("[CineStar/DHA] %s reconciliation removed %d stale row(s).", snapshot_label, removed)
            except Exception:
                logger.exception("[CineStar/DHA] Reconciliation failed for %s; retaining unverified stale rows.", snapshot_label)

    audit_showtime_snapshots(audit_snapshots, context="CineStar/DHA refresh")
    cinestar_dha_logger.info(
        "CineStar/DHA refresh finished: catalog records=%d, future showtimes=%d.",
        catalogue_count, showtime_count,
    )
    return catalogue_count




# Public aliases for maintenance/manual use.
scrape_chalo_catalog = chalo_scrape_chalo_catalog
refresh_movie_catalog = catalog_refresh_movie_catalog
refresh_new_cinema_sources = cinestar_dha_refresh_new_cinema_sources

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from scraper_monitoring import run_monitored_scrape
    asyncio.run(run_monitored_scrape(run_scrape_job, trigger=os.getenv("SCRAPER_TRIGGER", "manual_cli")))
