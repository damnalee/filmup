# FilmUp deployment guide

This guide describes the current repository architecture. It is a setup plan, not evidence that any Cloudflare resources have already been created. Cloudflare Pages, the Worker, secrets and GitHub repository secrets must be configured in their respective dashboards before deployment.

## Deployment topology

| Component | Host | Responsibility |
|---|---|---|
| React/Vite frontend | Cloudflare Pages | Static app build and HTTPS delivery |
| Public API and confirmation routes | Cloudflare Worker (TypeScript) | Supabase reads, advance-booking subscriptions and confirmation links |
| Scheduled job dispatcher | Same Cloudflare Worker | Dispatches GitHub Actions on Cron Triggers |
| Python scraper and booking checker | GitHub Actions runners | Scrapes cinema sources and checks selected-cinema showtimes |
| Database | Existing Supabase project | Movie catalogue, showtimes, subscriptions and scraper history |
| Transactional email | Resend | Subscription confirmation and, after explicit activation, booking alerts |

The Worker is in `cloudflare/worker/src/index.ts`. It is TypeScript; it does not run FastAPI or the Python scraper. The full FastAPI backend remains in `backend/` for local development or conventional Python hosting.

## Before starting

You need:

- Access to the GitHub repository `damnalee/filmup`.
- A Cloudflare account.
- The existing Supabase project URL and a private server-side key.
- A Resend API key and sender address. Resend's `onboarding@resend.dev` test sender is restricted to the account's own email address; use a sender on a verified domain for general recipients.
- Node.js 22 or newer.

Do not put Supabase service-role keys, Resend API keys or GitHub tokens in frontend variables or committed files.

## 1. Cloudflare Pages frontend

1. In Cloudflare, open **Workers & Pages → Create → Pages → Connect to Git**.
2. Connect `damnalee/filmup` and select `main` as the production branch.
3. Configure the build:
   - Framework preset: `Vite`.
   - Build command: `npm run build`.
   - Build output directory: `dist`.
   - Root directory: `/`.
4. Note the Pages URL, usually `https://filmup.pages.dev` if the project is named `filmup`.
5. After the API Worker is deployed, set the Pages production environment variable `VITE_CINEMA_API_URL` to the Worker API base URL, for example `https://filmup-api.YOUR-WORKERS-SUBDOMAIN.workers.dev/api`, then trigger a Pages redeploy.

`VITE_` variables are public and embedded in the browser bundle. Never place private keys there.

## 2. GitHub Actions repository secrets

In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**. Add:

- `SUPABASE_URL` — existing Supabase project URL.
- `SUPABASE_KEY` — private server-side key with the database permissions the scraper needs.
- `RESEND_API_KEY` — required only when live booking-alert email delivery is explicitly enabled.
- `MAIL_FROM_EMAIL` — sender address allowed by Resend; required only for live booking-alert delivery.

The scheduled workflow is `.github/workflows/cloudflare-jobs.yml`. It supports `scrape` and `advance_booking_check` through `workflow_dispatch`. The booking checker is deliberately configured with `ADVANCE_BOOKING_DRY_RUN=true`: it can detect matching slots but does not send booking alerts or mark cinemas as notified. Do not change this to `false` until the sender domain, email delivery, and notification behavior have been tested and approved.

## 3. Cloudflare Worker configuration

The Worker config is `cloudflare/worker/wrangler.jsonc`. It sets the Worker name to `filmup-api`, the public Pages CORS origin, and two Cron Triggers. If the Pages hostname differs, update `CORS_ALLOWED_ORIGINS` before deployment.

Set these Worker secrets in **Workers & Pages → filmup-api → Settings → Variables and Secrets** (or with Wrangler):

- `SUPABASE_URL`
- `SUPABASE_KEY` — private server-side database key.
- `GITHUB_ACTIONS_TOKEN` — fine-grained GitHub token scoped to `damnalee/filmup`, with Actions read/write permission so the Worker can dispatch the workflow.
- `ADMIN_MONITORING_TOKEN` — long random secret protecting `/api/monitoring/scraper`.
- `RESEND_API_KEY` — Resend API key used by the subscription confirmation route.
- `MAIL_FROM_EMAIL` — Resend-authorized sender address.

`MAIL_FROM_NAME` is already configured as a non-secret Worker variable. The Worker local template is `cloudflare/worker/.dev.vars.example`; copy it to `.dev.vars` for local development. `.dev.vars` is gitignored.

## 4. Cron behavior and deployment warning

The Worker config defines:

| Cron (UTC) | Lahore time | Action |
|---|---|---|
| `5 * * * *` | Every hour at :05 | Dispatch the Python cinema scraper |
| `*/5 * * * *` | Every five minutes | Dispatch the advance-booking checker |

Cloudflare Cron Triggers are activated by Worker deployment. The hourly scraper writes to Supabase, so deploying is not a harmless preview. The five-minute checker currently defaults to dry-run and does not send booking alerts. Confirm the repository secrets, target database, and workflow branch before enabling these triggers.

## 5. Validate before deployment

From the repository root:

```bash
npm ci
npm run typecheck
npm run build
python3 -m compileall -q backend
python3 -m unittest discover -s backend -p 'test_*.py' -v
npx wrangler@4 deploy --dry-run --outdir /tmp/filmup-worker-dist --config cloudflare/worker/wrangler.jsonc
```

The Wrangler dry-run builds the Worker but does not deploy it. If it fails due to a missing platform-specific `workerd` binary, reinstall Wrangler with optional dependencies enabled or run the build in GitHub Actions; do not treat a failed local dry-run as a successful validation.

## 6. Deploy only after approval

After Pages, Worker secrets, GitHub Actions secrets and CORS are configured, deploy the Worker from the repository root:

```bash
npx wrangler@4 deploy --config cloudflare/worker/wrangler.jsonc
```

This activates both Cron Triggers. Then verify:

- `GET /health` returns HTTP 200.
- `GET /api/movies` and `GET /api/showtimes` return expected Supabase data.
- Pages can call the Worker without CORS errors.
- The Worker can dispatch the intended GitHub Actions workflow.
- The scraper job completes and creates a `public.scraper_runs` record.
- The booking checker remains in dry-run mode and does not send or mark notifications.
- The monitoring endpoint rejects requests without the admin bearer token.
- A subscription confirmation email is accepted by Resend and confirmed through the Worker route.

Check Cloudflare Worker logs, GitHub Actions run logs and Resend's email logs. An accepted email request is not a guarantee of inbox delivery.

## 7. Enable live booking alerts later

Do not enable live booking alerts as part of the initial deployment. Once a verified sender domain is ready and test subscriptions are successful, explicitly change `ADVANCE_BOOKING_DRY_RUN` to `false` in `.github/workflows/cloudflare-jobs.yml`, verify that the Resend secrets are configured in GitHub, and review the resulting workflow diff before committing. This is the switch that allows the five-minute checker to send real messages and update each subscription's `notified_cinemas` state.

## Avoid duplicate schedulers

If the full FastAPI backend is hosted elsewhere, do not run its in-process scheduler alongside the Cloudflare/GitHub schedule. Running both can duplicate scraping and notification checks. Keep one scheduler active.
