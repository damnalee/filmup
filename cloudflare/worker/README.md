# FilmUp Cloudflare Worker

This TypeScript Worker is FilmUp's lightweight public API edge and schedule dispatcher. It reads catalogue and future-showtime rows from Supabase PostgREST, handles advance-booking confirmation requests, and dispatches scheduled Python jobs to GitHub Actions. The Python scraper does not run inside the Worker.

## Routes

- `GET /health` — Worker health.
- `GET /api/movies` — movie catalogue rows.
- `GET /api/movies/{movie_id}` — one catalogue record.
- `GET /api/showtimes` — future showtime rows.
- `GET /api/monitoring/scraper` — scraper history; requires the admin bearer token.
- `POST /api/advance-booking/subscribe` — create a pending subscription and send a Resend confirmation email.
- `GET /api/advance-booking/confirm/{token}` — confirm a subscription.

## Local development

Install Node.js 22 or newer, then from the repository root:

```bash
cp cloudflare/worker/.dev.vars.example cloudflare/worker/.dev.vars
# Edit cloudflare/worker/.dev.vars with local development credentials.
npx wrangler@4 dev --config cloudflare/worker/wrangler.jsonc
```

`.dev.vars` is ignored by Git. Use a Supabase server-side key only in this server-side file; never put it in a `VITE_` frontend variable.

## Required Worker secrets

Set these in the Cloudflare dashboard or with Wrangler secrets:

- `SUPABASE_URL`
- `SUPABASE_KEY` — private server-side Supabase key.
- `GITHUB_ACTIONS_TOKEN` — fine-grained token with Actions read/write access to `damnalee/filmup`.
- `ADMIN_MONITORING_TOKEN` — long random token for the monitoring endpoint.
- `RESEND_API_KEY` — Resend API key.
- `MAIL_FROM_EMAIL` — sender address permitted by the Resend account.

`MAIL_FROM_NAME` and `CORS_ALLOWED_ORIGINS` are non-secret Worker variables in `wrangler.jsonc`. Update the allowed origin if the Pages project uses a different hostname.

## Scheduled jobs

The two Cron Triggers dispatch GitHub Actions workflows:

- `5 * * * *` — run the Python cinema scraper hourly.
- `*/5 * * * *` — run the advance-booking checker every five minutes.

The checker defaults to dry-run mode (`ADVANCE_BOOKING_DRY_RUN=true` in the GitHub workflow). It detects matching slots but does not send booking alerts or mark cinemas as notified. Live delivery requires an explicit change to the workflow and configured Resend sender secrets.

**Important:** deploying this Worker activates its Cron Triggers. Do not deploy until the GitHub repository secrets and scheduled-job behavior have been reviewed. The scraper cron writes to the connected Supabase database.

## Deploy

After configuring the Cloudflare account, secrets, GitHub Actions repository secrets and Pages origin, run from the repository root:

```bash
npx wrangler@4 deploy --config cloudflare/worker/wrangler.jsonc
```

See [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) for the full setup sequence.
