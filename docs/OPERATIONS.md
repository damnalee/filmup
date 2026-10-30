# Operations runbook

## Health checks

- API health: `GET /health`.
- Catalogue: `GET /api/movies?limit=10`.
- Interactive OpenAPI: `/docs`.

`/health` reports whether APScheduler is running and the next scheduled scrape. It does not indicate that every upstream cinema collector is healthy.

## Scheduled work

When `SCRAPER_ENABLED=true`, FastAPI starts an APScheduler instance during application lifespan:

- Main cinema refresh: cron schedule configured by `SCRAPER_SCHEDULE_MINUTE` and `SCRAPER_INTERVAL_HOURS` (defaults to minute 5, every hour).
- Advance-booking checker: every five minutes.

The scraper also calls the advance-booking checker after a successful refresh. This is important because the checker needs newly ingested showtime rows to detect booking openings.

## Manual scrape

Run from `backend/`:

```bash
source .venv/bin/activate
python scraper.py
```

Run only one scraper process at a time. A scrape can modify live Supabase catalogue/showtime records. Inspect the logs and generated local audit files before making further data corrections.

## Advance-booking email lifecycle

1. User submits an email and selected cinemas.
2. Backend sends a confirmation email and stores a pending record.
3. User opens the tokenized confirmation URL before expiry.
4. The backend changes the subscription to confirmed.
5. When a selected cinema has a matching future showtime, FilmUp sends an alert and records that cinema in `notified_cinemas`.

If a user reports no alert, check in order:

- Is the subscription confirmed and not expired?
- Is the movie title normalized/matched correctly?
- Does Supabase contain a future showtime for the selected cinema?
- Did the scraper successfully refresh that source?
- Did the notification checker run?
- Did SMTP/email provider accept the message? Check provider logs and recipient spam folder.

A successful SMTP handoff is not proof that the recipient's mailbox delivered the email.

## Operational data

Local `scrape_output/` data, reconciliation backups, CSV exports and logs are intentionally excluded from the Git repository. Back up important operational evidence separately and protect it because it can contain source data and identifiers.

## Incident response

- API unavailable: inspect process logs, environment configuration and Supabase connectivity.
- Catalogue stale: inspect scheduler state, source collectors and last audit artifacts.
- Email failure: verify SMTP/provider settings and host outbound network policy.
- Duplicate alerts: inspect subscription status and `notified_cinemas` before editing records.
- Incorrect cinema mapping: verify `location_guard.py`, `src/data/cinemas.json` and the source cinema name normalization before changing data.

Never expose service-role keys, SMTP passwords, confirmation tokens or subscriber email addresses in issues, screenshots or logs attached to public discussions.
