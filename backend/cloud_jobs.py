"""Entrypoints for the Cloudflare Cron -> GitHub Actions scheduled jobs."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

from advance_booking_routes import check_advance_booking_notifications
from scraper import run_scrape_job
from scraper_monitoring import run_monitored_scrape


def run_scrape() -> dict:
    os.environ.setdefault("SCRAPER_MONITOR_STORAGE", "supabase")
    result = asyncio.run(
        run_monitored_scrape(
            run_scrape_job,
            trigger="cloudflare_cron_github_actions",
        )
    )
    print(json.dumps(result, default=str))
    if result.get("status") == "failed":
        raise SystemExit("FilmUp scraper run failed; see logs and scraper_runs history.")
    if result.get("status") == "monitoring_unavailable":
        logging.error("Scraper completed without a Supabase monitoring record.")
    return result


def run_advance_booking_check() -> int:
    """Check booking availability; live email delivery requires explicit opt-in."""
    dry_run_value = os.getenv("ADVANCE_BOOKING_DRY_RUN", "true").strip().lower()
    dry_run = dry_run_value not in {"0", "false", "no", "off"}
    if not dry_run:
        # Validate sender configuration before any scan can send real notifications.
        from email_service import resend_config

        resend_config()
    result = check_advance_booking_notifications(dry_run=dry_run)
    print(json.dumps({
        "job": "advance_booking_check",
        "dry_run": dry_run,
        "subscriptions_with_new_slots" if dry_run else "subscriptions_alerted": result,
    }))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a FilmUp scheduled cloud job.")
    parser.add_argument("job", choices=("scrape", "advance_booking_check"))
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

    if args.job == "scrape":
        run_scrape()
    else:
        run_advance_booking_check()


if __name__ == "__main__":
    main()
