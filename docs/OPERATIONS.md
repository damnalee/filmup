# Operations runbook

## Health checks

- Worker health: `GET /health`.
- Catalogue: `GET /api/movies`.
- Future showtimes: `GET /api/showtimes`.
- Scraper history: `GET /api/monitoring/scraper` with the administrator bearer token.
- Local FastAPI health: `GET /health`; interactive OpenAPI is available at `/docs`.

The Cloudflare Worker health response confirms the edge is responding; it does not prove that Supabase, GitHub Actions, every cinema collector or Resend is healthy.

## Scheduled work

The deployed Worker owns two Cron Triggers and dispatches jobs to GitHub Actions:

- `5 * * * *` (UTC): hourly Python cinema refresh.
- `*/5 * * * *` (UTC): advance-booking availability check.

The booking-check workflow defaults to `ADVANCE_BOOKING_DRY_RUN=true`. It logs matching slots but does not send booking alerts or update `notified_cinemas`. Live delivery must be explicitly enabled in `.github/workflows/cloudflare-jobs.yml` after the Resend sender and recipient behavior have been tested.

The full FastAPI backend can also start an in-process APScheduler when `SCRAPER_ENABLED=true`. Do not run that scheduler at the same time as the Cloudflare/GitHub schedule.

## Manual scrape

Run from `backend/`:

```bash
source .venv/bin/activate
python scraper.py
```

Run only one scraper process at a time. A scrape can modify live Supabase catalogue/showtime records. Inspect the logs and generated local audit files before making further data corrections.

## Advance-booking email lifecycle

1. User submits an email and selected cinemas.
2. API sends a Resend confirmation email and stores a pending record.
3. User opens the tokenized confirmation URL before expiry.
4. The API changes the subscription to confirmed.
5. The scheduled checker looks for matching future showtimes. In the current safe default, it only logs detections; after live delivery is explicitly enabled, it sends an alert and records that cinema in `notified_cinemas`.

If a user reports no confirmation email, check that `RESEND_API_KEY` is configured, `MAIL_FROM_EMAIL` is allowed by Resend, and the provider accepted the request. The `onboarding@resend.dev` test sender is restricted to the account's registered email; verify a domain for other recipients. Check Resend email logs and the recipient's inbox/spam folder. An accepted API request is not proof of inbox delivery.

If a user reports no booking alert, check in order:

- Is the subscription confirmed and not expired?
- Is the movie title normalized/matched correctly?
- Does Supabase contain a future showtime for the selected cinema?
- Did the scraper successfully refresh that source?
- Did the GitHub Actions checker run?
- Is the workflow still in dry-run mode? If live mode is enabled, did Resend accept the message?

## Operational data

Local `scrape_output/` data, reconciliation backups, CSV exports and logs are intentionally excluded from the Git repository. Back up important operational evidence separately and protect it because it can contain source data and identifiers.

## Incident response

- API unavailable: inspect Cloudflare Worker logs, configuration and Supabase connectivity.
- Catalogue stale: inspect Cron Trigger dispatches, GitHub Actions runs, source collectors and last audit artifacts.
- Email failure: verify Resend credentials, sender authorization and provider logs.
- Duplicate alerts: inspect subscription status and `notified_cinemas` before editing records.
- Incorrect cinema mapping: verify `location_guard.py`, `src/data/cinemas.json` and the source cinema name normalization before changing data.

Never expose service-role keys, Resend API keys, GitHub tokens, confirmation tokens or subscriber email addresses in issues, screenshots or logs attached to public discussions.
