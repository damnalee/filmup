import re
import unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from database import supabase

router = APIRouter()
LAHORE = ZoneInfo("Asia/Karachi")

CATALOG_FIELDS = (
    "id,source,cinema_name,source_movie_id,movie_title,"
    "listing_status,release_date,source_release_label,"
    "poster_url,detail_url,trailer_url,language,movie_format,"
    "last_seen_at"
)

ALLOWED_STATUSES = {
    "now_showing",
    "coming_soon",
    "released_no_sessions",
    "unknown",
}


def title_key(title: str) -> str:
    """Normalize movie titles so format/language suffixes do not block matches."""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(
        r"\b(?:18\s*\+|16\s*\+|13\s*\+|2d|3d|4dx|imax|"
        r"urdu|english|hindi|punjabi|dubbed|subtitled)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())


def database_rows(query):
    try:
        if supabase is None:
            raise HTTPException(
                status_code=503,
                detail="Backend database credentials are not configured.",
            )
        response = query.execute()
        return response.data or []
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The movie database could not be queried.",
        ) from exc


def load_upcoming_showtimes():
    now = datetime.now(timezone.utc)

    rows = database_rows(
        supabase.table("showtimes")
        .select("movie_title,cinema_name,showtime,booking_link,ticket_price")
        .gte("showtime", now.isoformat())
        .order("showtime")
        .limit(2000)
    )

    grouped = {}

    for row in rows:
        cinema_name = row.get("cinema_name") or ""
        if any(city in cinema_name.casefold() for city in ("multan", "sargodha", "karachi", "islamabad", "rawalpindi", "faisalabad", "gujranwala", "gujrat", "hyderabad", "peshawar", "quetta", "sialkot", "bahawalpur", "abbottabad")):
            continue
        movie_title = row.get("movie_title") or ""
        key = title_key(movie_title)
        raw_time = row.get("showtime")

        if not key or not raw_time:
            continue

        try:
            parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed <= now:
                continue
        except (ValueError, TypeError):
            continue

        showtime = {
            "movie_title": movie_title,
            "cinema_name": row.get("cinema_name"),
            "showtime": parsed.isoformat(),
            "showtime_lahore": parsed.astimezone(LAHORE).strftime(
                "%a, %d %b at %I:%M %p"
            ),
            "booking_link": row.get("booking_link"),
            "ticket_price": row.get("ticket_price"),
        }

        grouped.setdefault(key, []).append(showtime)

    return grouped


def effective_status(movie, showtimes):
    """Actual future sessions take priority over a cinema's catalogue label."""
    if showtimes:
        return "now_showing"

    release_date = movie.get("release_date")
    if release_date:
        try:
            parsed_date = date.fromisoformat(str(release_date)[:10])
            if parsed_date < datetime.now(LAHORE).date():
                return "released_no_sessions"
            return "coming_soon"
        except ValueError:
            pass

    source_status = movie.get("listing_status", "unknown")

    if source_status == "now_showing":
        # A source listing alone is not proof of an available future session.
        return "unknown"

    return source_status if source_status in ALLOWED_STATUSES else "unknown"


def enrich_movie(movie, grouped_showtimes):
    result = dict(movie)
    key = title_key(result.get("movie_title") or "")
    showtimes = grouped_showtimes.get(key, [])

    result["showtimes"] = showtimes
    result["has_upcoming_showtimes"] = bool(showtimes)
    result["display_status"] = effective_status(result, showtimes)

    release_date = result.get("release_date")

    if showtimes:
        result["availability_message"] = "Upcoming showtimes are available."
    elif result["display_status"] == "coming_soon":
        if release_date:
            result["availability_message"] = (
                f"Release date: {release_date}. "
                "Showtimes have not been posted yet."
            )
        else:
            result["availability_message"] = (
                "Coming soon. The release date or showtimes have not been announced."
            )
    elif result["display_status"] == "released_no_sessions":
        result["availability_message"] = (
            "Already released, but no upcoming showtimes are currently listed."
        )
    else:
        result["availability_message"] = (
            "No upcoming showtimes are currently confirmed."
        )

    return result


@router.get("/api/movies")
def list_movies(
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    """List catalogue entries, independently enriched with actual future sessions."""
    if status is not None and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Use one of: {', '.join(sorted(ALLOWED_STATUSES))}",
        )

    catalog = database_rows(
        supabase.table("movie_catalog")
        .select(CATALOG_FIELDS)
        .order("movie_title")
        .limit(1000)
    )

    grouped_showtimes = load_upcoming_showtimes()
    movies = [enrich_movie(movie, grouped_showtimes) for movie in catalog]

    if q:
        term = q.strip().casefold()
        movies = [
            movie for movie in movies
            if term in (movie.get("movie_title") or "").casefold()
        ]

    if status:
        movies = [
            movie for movie in movies
            if movie["display_status"] == status
        ]

    status_order = {
        "now_showing": 0,
        "coming_soon": 1,
        "unknown": 2,
        "released_no_sessions": 3,
    }
    movies.sort(
        key=lambda movie: (
            status_order.get(movie["display_status"], 9),
            (movie.get("movie_title") or "").casefold(),
        )
    )

    return {"count": min(len(movies), limit), "movies": movies[:limit]}


@router.get("/api/showtimes")
def list_showtimes(
    limit: int = Query(default=2000, ge=1, le=5000),
):
    """Return future source showtime rows for the shared browser-side data adapter."""
    now = datetime.now(timezone.utc)
    rows = database_rows(
        supabase.table("showtimes")
        .select("movie_title,cinema_name,showtime,booking_link,ticket_price")
        .gte("showtime", now.isoformat())
        .order("showtime")
        .limit(limit)
    )
    return {"count": len(rows), "showtimes": rows}


@router.get("/api/movies/{movie_id}")
def get_movie(movie_id: str):
    """Get one catalogue entry, its release information, and confirmed future sessions."""
    catalog = database_rows(
        supabase.table("movie_catalog")
        .select(CATALOG_FIELDS)
        .eq("id", movie_id)
        .limit(1)
    )

    if not catalog:
        raise HTTPException(status_code=404, detail="Movie not found.")

    grouped_showtimes = load_upcoming_showtimes()
    return enrich_movie(catalog[0], grouped_showtimes)
