"""Pure helpers for identifying unseen FilmUp cinema showtimes."""

import re
from datetime import datetime, timezone


def _cinema_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _slot_key(row: dict) -> str | None:
    """Return a stable UTC-microsecond key for one cinema showtime."""
    raw = str(row.get("showtime") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return str(
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )
    except (ValueError, TypeError, OverflowError):
        return None


def _new_slots_by_cinema(
    rows: list[dict],
    selected_cinemas: list[str],
    notified_slots: dict,
) -> dict[str, list[dict]]:
    """Keep only selected-cinema showtimes not previously included in an alert."""
    selected_keys = {_cinema_key(name): name for name in selected_cinemas}
    newly_available: dict[str, list[dict]] = {}
    for row in rows:
        cinema_key = _cinema_key(str(row.get("cinema_name") or "").strip())
        selected_name = selected_keys.get(cinema_key)
        slot_key = _slot_key(row)
        if not selected_name or slot_key is None:
            continue
        seen = set(notified_slots.get(cinema_key) or [])
        if slot_key in seen:
            continue
        newly_available.setdefault(selected_name, []).append(row)
    return newly_available
