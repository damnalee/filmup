import json
import logging
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from database import get_movie_showtimes

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)
LAHORE_TZ = ZoneInfo("Asia/Karachi")

mcp = MCPServer("Lahore Cinema Showtimes")


def normalize_movie_title(query: str) -> str:
    """Remove conversational filler without changing the core movie title."""
    title = (query or "").strip()

    prefixes = (
        r"please\s+",
        r"show\s+me\s+",
        r"find\s+me\s+",
        r"find\s+",
        r"search\s+for\s+",
        r"look\s+for\s+",
        r"what\s+are\s+",
        r"what\s+is\s+",
        r"do\s+you\s+have\s+",
        r"can\s+you\s+show\s+me\s+",
        r"any\s+",
    )

    for prefix in prefixes:
        title = re.sub(r"^" + prefix, "", title, flags=re.IGNORECASE)

    # Remove words describing the requested information rather than the film.
    title = re.sub(
        r"\b(showtimes?|screenings?|sessions?|movies?|films?|titles?)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )

    # Remove a locality suffix, but preserve phrases within actual movie titles.
    title = re.sub(
        r"\s+(?:at|in)\s+Lahore(?:\s+cinemas?)?\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # If time wording was accidentally included with the title, remove it.
    title = re.sub(
        r"\b(?:around|about|roughly|approximately|near)\s+"
        r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(r"\s+", " ", title).strip(" \t\r\n,;:-")

    # Remove leading conversational articles after other filler is removed.
    title = re.sub(r"^(?:the|for)\s+", "", title, flags=re.IGNORECASE)

    return title.strip()


def is_lahore_venue(cinema_name: str) -> bool:
    """Keep known Lahore venues and reject branches known to be outside Lahore."""
    name = (cinema_name or "").strip().lower()

    if "multan" in name:
        return False

    if "lahore" in name:
        return True

    # The Cue listing configured for this project is the Lahore venue.
    if "cue" in name:
        return True

    # Universal Cinemas' Lahore branch in this dataset is Emporium Mall.
    if "universal" in name and "emporium" in name:
        return True

    # These are the two Lahore Cinepax branches configured in this project.
    if "cinepax" in name and (
        "amanah" in name or "packages" in name
    ):
        return True

    return False


def parse_lahore_time(row: dict) -> datetime | None:
    raw = row.get("showtime")

    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

        # Database timestamps returned without an offset are treated as UTC.
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(LAHORE_TZ)

    except (ValueError, TypeError):
        return None


def add_lahore_time(row: dict) -> dict:
    result = dict(row)
    dt = parse_lahore_time(result)

    if dt is not None:
        result["showtime_lahore"] = dt.strftime(
            "%a, %d %b %Y · %I:%M %p"
        )

    return result


def requested_clock_minutes(preference: str) -> int | None:
    """Interpret a specific clock preference, including 'around 8 PM'."""
    text = (preference or "").strip().lower()

    match = re.search(
        r"\b(\d{1,2})(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        match = re.fullmatch(
            r"\s*(\d{1,2})(?::([0-5]\d))?\s*",
            text,
        )

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "").lower()

    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour %= 12
        if meridiem == "pm":
            hour += 12
    elif hour > 23:
        return None

    return hour * 60 + minute


def clock_distance(dt: datetime, target_minutes: int) -> int:
    actual = dt.hour * 60 + dt.minute
    difference = abs(actual - target_minutes)
    return min(difference, 1440 - difference)


def matches_preference(dt: datetime, preference: str) -> bool:
    text = (preference or "").strip().lower()

    if text in ("", "any", "any time", "no preference"):
        return True

    if "morning" in text:
        return 5 <= dt.hour < 12

    if "afternoon" in text:
        return 12 <= dt.hour < 17

    if "evening" in text:
        # Include screenings from 5 PM up to, but not including, 10 PM.
        return 17 <= dt.hour < 22

    if "night" in text:
        return dt.hour >= 21 or dt.hour < 5

    target = requested_clock_minutes(text)

    if target is not None:
        # Treat a specified clock time as a preference, not an exact-minute
        # requirement. For example, 7:45 PM is a match for around 8 PM.
        return clock_distance(dt, target) <= 60

    # Unknown wording should not make valid showtimes disappear.
    logger.warning(
        "Unrecognized time preference %r; returning upcoming showtimes.",
        preference,
    )
    return True


@mcp.tool()
def search_showtimes(
    movie_title: str,
    preferred_time: str = "any",
    limit: int = 50,
) -> str:
    """Search upcoming Lahore showtimes stored in Supabase.

    Args:
        movie_title: The movie title or a partial title. Use the core title,
            such as 'Spider-Man', without words like 'showtimes' or 'movies'.
        preferred_time: 'any', 'morning', 'afternoon', 'evening', 'night',
            or a clock time such as '8 PM' or 'around 8 PM'.
            Specific clock times match screenings within 60 minutes.
        limit: Maximum number of results to return, from 1 to 100.

    This tool only searches Supabase; it never runs the scraper.
    """
    title = normalize_movie_title(movie_title)

    if not title:
        return json.dumps({
            "match_status": "invalid_query",
            "count": 0,
            "results": [],
            "message": "Please provide a movie title.",
        })

    safe_limit = max(1, min(int(limit), 100))

    try:
        # Always fetch the title's stored rows first. Apply the time preference
        # here so a narrow database time filter cannot hide nearby screenings.
        rows = get_movie_showtimes(
            movie_title=title,
            preferred_time="any",
            limit=500,
        )

        current_time = datetime.now(LAHORE_TZ)
        upcoming_rows = []

        for row in rows:
            if not is_lahore_venue(row.get("cinema_name", "")):
                continue

            local_time = parse_lahore_time(row)
            if local_time is None or local_time < current_time:
                continue

            upcoming_rows.append((local_time, row))

        upcoming_rows.sort(key=lambda pair: pair[0])

        if not upcoming_rows:
            return json.dumps({
                "movie_title": title,
                "requested_time": preferred_time,
                "match_status": "no_upcoming_showtimes",
                "count": 0,
                "time_match_count": 0,
                "results": [],
                "message": (
                    "No future showtimes for this title were found in the "
                    "currently stored Lahore cinema listings."
                ),
            }, ensure_ascii=False)

        text = (preferred_time or "").strip().lower()
        target = requested_clock_minutes(text)
        broad_preference = any(
            word in text
            for word in ("morning", "afternoon", "evening", "night")
        )

        matched = [
            pair for pair in upcoming_rows
            if matches_preference(pair[0], text)
        ]

        if matched:
            # Spread short lists across cinema venues instead of returning
            # several consecutive screenings from the same cinema.
            grouped = {}

            for pair in matched:
                venue = " ".join(
                    str(pair[1].get("cinema_name") or "Unknown cinema").split()
                )
                grouped.setdefault(venue.casefold(), []).append(pair)

            venue_groups = sorted(
                grouped.values(),
                key=lambda group: group[0][0],
            )

            selected = []
            round_index = 0

            while len(selected) < safe_limit:
                added_any = False

                for group in venue_groups:
                    if round_index < len(group):
                        selected.append(group[round_index])
                        added_any = True

                        if len(selected) >= safe_limit:
                            break

                if not added_any:
                    break

                round_index += 1

            status = "matches_found"
            message = (
                f"Found {len(matched)} upcoming Lahore showtime(s) "
                f"across {len(venue_groups)} cinema venue(s). "
                "Results prioritize different cinema venues before "
                "showing additional screenings from a venue. "
                "The selected list is diversified, not strictly chronological."
            )
        elif text not in ("", "any", "any time", "no preference"):
            # No time match does not mean no showtimes. Return the closest
            # alternatives with an explicit status for the chatbot.
            if target is not None:
                alternatives = sorted(
                    upcoming_rows,
                    key=lambda pair: (
                        clock_distance(pair[0], target),
                        pair[0],
                    ),
                )
            elif broad_preference:
                alternatives = upcoming_rows
            else:
                alternatives = upcoming_rows

            selected = alternatives[:min(safe_limit, 5)]
            status = "no_time_match"
            message = (
                "The requested time preference had no matches. "
                "These are alternative upcoming Lahore showtimes; "
                "do not describe them as matches for the requested time."
            )
        else:
            selected = upcoming_rows[:safe_limit]
            status = "matches_found"
            message = f"Found {len(upcoming_rows)} upcoming Lahore showtime(s)."

        results = [
            add_lahore_time(row)
            for _dt, row in selected
        ]

        return json.dumps(
            {
                "movie_title": title,
                "requested_time": preferred_time,
                "match_status": status,
                "count": len(results),
                "time_match_count": len(matched),
                "message": message,
                "results": results,
            },
            ensure_ascii=False,
            default=str,
        )

    except Exception:
        logger.exception("Showtime search failed.")
        return json.dumps({
            "match_status": "search_error",
            "count": 0,
            "results": [],
            "message": (
                "The database search failed. Do not claim that the movie "
                "has no showtimes; explain that the search is unavailable."
            ),
        })


if __name__ == "__main__":
    mcp.run()
