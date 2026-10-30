
import logging
import re

_logger = logging.getLogger("location_guard")

def _venue_key(name):
    if not isinstance(name, str) or not name.strip():
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))

_ALLOWED_LAHORE = {
    _venue_key("CINEPAX AMANAH MALL"),
    _venue_key("Raiha CineGold Plex, Lahore"),
    _venue_key("Raiha CineGold Plex - Gold Lounge, Lahore"),
    _venue_key("Raiha CineGold Plex - Red Lounge, Lahore"),
    _venue_key("CINEPAX PACKAGES MALL"),
    _venue_key("CUE Cinemas"),
    _venue_key("Chalo Cinema - Fortress Square Mall"),
    _venue_key("CineStar Lahore"),
    _venue_key("CineStar DHA Raya Lahore"),
    _venue_key("CineStar Township Lahore"),
    _venue_key("CineStar Xinhua Mall Lahore"),
    _venue_key("Universal Cinemas Emporium Mall, Lahore"),
}

_OTHER_CITIES = re.compile(
    r"\b(?:multan|karachi|islamabad|rawalpindi|faisalabad|gujranwala|"
    r"sialkot|peshawar|quetta|hyderabad|bahawalpur|sargodha|sukkur|"
    r"abbottabad|gujrat|sheikhupura|jhelum|mirpur|mianwali|sahiwal|"
    r"okara|kasur|murree|nowshera|kohat|bannu|chiniot|jhang)\b",
    re.IGNORECASE,
)

def is_lahore_venue(cinema_name):
    key = _venue_key(cinema_name)
    if not key or _OTHER_CITIES.search(key):
        return False
    if key in _ALLOWED_LAHORE:
        return True
    # DHA may use a more specific branch label; require Lahore in its name.
    return "dha cinema" in key and "lahore" in key

def _filter_rows(rows, context):
    accepted = []
    rejected = {}
    for row in rows or []:
        if is_lahore_venue(row.get("cinema_name")):
            accepted.append(row)
        else:
            name = row.get("cinema_name") or "<missing cinema_name>"
            rejected[name] = rejected.get(name, 0) + 1
    if rejected:
        _logger.warning(
            "%s: blocked %d non-Lahore/unverified row(s): %s",
            context, sum(rejected.values()), rejected
        )
    return accepted

def filter_lahore_showtimes(rows, context="showtimes"):
    return _filter_rows(rows, context)

def filter_lahore_catalogue_rows(rows, context="movie_catalog"):
    return _filter_rows(rows, context)
