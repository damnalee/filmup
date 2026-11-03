"""Run FilmUp's confirmed advance-booking notification check outside the API server."""
from __future__ import annotations

import logging

from advance_booking_routes import check_advance_booking_notifications


def main() -> int:
    # Keep Actions logs free of recipient addresses and subscription details.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sent_count = check_advance_booking_notifications()
    print(f"Advance-booking check completed. Alerts sent: {sent_count}.")
    return sent_count


if __name__ == "__main__":
    main()
