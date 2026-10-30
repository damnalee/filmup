# FilmUp backend

FastAPI service for the FilmUp Lahore cinema discovery frontend. It serves the movie catalogue, manages advance-booking email subscriptions, exposes an optional Groq chat endpoint, and coordinates the cinema scraper.

## Run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your private Supabase, Groq and SMTP credentials.
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Health: `http://127.0.0.1:8000/health`

OpenAPI: `http://127.0.0.1:8000/docs`

## Manual scrape

```bash
python scraper.py
```

A manual scrape writes catalogue/showtime data to Supabase. Run only one scraper at a time and review its logs/audit output.

## Environment

See `.env.example` and the [complete setup guide](../docs/SETUP.md). Keep `.env` private and never expose `SUPABASE_KEY`, `SMTP_PASSWORD` or other backend credentials to browser code.

## Runtime notes

The scheduler currently runs inside the FastAPI process. It starts an hourly cinema refresh and a five-minute advance-booking notification check when `SCRAPER_ENABLED=true`. A sleeping/restarted free-tier service cannot guarantee these jobs. See [deployment notes](../docs/DEPLOYMENT.md).

## Scraper monitoring layer

The existing scraper remains the source of truth for collection, parsing, Supabase writes, reconciliation, and showtime audits. A separate `scraper_monitoring.py` wrapper observes scheduled and manual CLI executions without changing that logic. It records run status, duration, row counts, source snapshot outcomes, warning/error counts, and summaries from the existing read-only audit JSON reports.

For shared cloud history, run `supabase_scraper_monitoring.sql` once in the Supabase SQL Editor and set `SCRAPER_MONITOR_STORAGE=supabase` in the backend environment. This creates a separate `public.scraper_runs` table protected by RLS; only the server-side `service_role` can access it. `SCRAPER_MONITOR_STORAGE=auto` tries Supabase first and falls back to local SQLite if the table/API is unavailable. Set `SCRAPER_MONITOR_STORAGE=sqlite` for local-only development. The SQLite database defaults to `scrape_output/monitoring.sqlite3` and is ignored by Git.

Read the latest run and recent history at `GET /api/monitoring/scraper` (optional `?limit=20`, maximum 100). This endpoint returns operational counts and audit summaries, not raw logs or credentials. Runs are classified as `success`, `completed_with_warnings`, `failed`, or `interrupted`. An in-progress run is marked interrupted if the process restarts and it remains running for more than six hours.

The read-only `GET /api/monitoring/scraper` endpoint reads from the configured monitoring store, so Supabase-backed history is visible across backend instances and can power a future admin dashboard. Keep this endpoint behind your deployment's admin authentication or gateway policy before exposing it publicly. The SQLite fallback is local to one backend instance and is intended for development/offline use, not multi-instance cloud deployments.

Run the isolated monitor tests (no network, Supabase writes, or real scraper execution):

```bash
python -m unittest -v test_scraper_monitoring.py
```
