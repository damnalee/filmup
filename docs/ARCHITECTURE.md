# Architecture

## System context

```mermaid
flowchart TB
  Visitor[Browser visitor]
  Web[React + TypeScript + MapLibre]
  Edge[TypeScript Cloudflare Worker]
  DB[(Supabase PostgreSQL)]
  Jobs[GitHub Actions Python jobs]
  Scraper[Cinema source collectors]
  Checker[Advance-booking checker · dry run by default]
  Resend[Resend API]
  Groq[Groq API]

  Visitor --> Web
  Web -->|GET /api/movies and /api/showtimes| Edge
  Web -->|POST /api/advance-booking/subscribe| Edge
  Edge --> DB
  Edge --> Resend
  Edge -->|Cron dispatch| Jobs
  Edge -->|Confirmation dispatch| Jobs
  Jobs --> Scraper
  Jobs --> Checker
  Scraper --> DB
  Checker --> DB
  Edge -->|Optional chat route not enabled at edge| Groq
```

## Frontend

The frontend is a single-page React application. `src/App.tsx` coordinates the map, cinema/movie panels, filtering and the selected date. UI components live in `src/components/`; reusable domain logic is under `src/lib/`; the API boundary is under `src/services/`.

The map uses curated Lahore cinema coordinates and area data from `src/data/`. This static geospatial metadata is not the source of movie or showtime availability.

`src/services/dataSource.ts` requests both the movie catalogue and future showtimes. It accepts the wrapped response shapes returned by the local FastAPI backend and the raw arrays returned by the Cloudflare Worker. It deliberately has no fictional movie/showtime fallback: an API failure is surfaced to the user.

## API and collection

- `cloudflare/worker/src/index.ts` is the deployed TypeScript edge API. It reads catalogue and future showtime data from Supabase, handles advance-booking subscriptions and confirmation links, protects scraper monitoring, and dispatches scheduled GitHub Actions jobs.
- `backend/main.py` creates the full FastAPI application for local development or a conventional Python host. It includes the health endpoint, optional Groq chat route, catalogue endpoints and APScheduler lifecycle. Do not run its scheduler alongside the Cloudflare/GitHub schedule.
- `backend/catalog_routes.py` assembles movie catalogue entries and exposes future showtime rows.
- `backend/advance_booking_routes.py` creates pending email subscriptions, confirms tokens and checks for matching future sessions.
- `backend/scraper.py` collects movie/showtime data from supported cinema sources, normalizes records and writes to Supabase.
- `backend/cloud_jobs.py` is the GitHub Actions entry point for the scraper and advance-booking checker.
- `backend/database.py` owns the Supabase client and persistence operations.
- `backend/location_guard.py` restricts venue handling to the supported Lahore venue allowlist.
- `backend/chatbot.py` provides the optional Groq-backed assistant endpoint; the Cloudflare edge `/chat` route is not implemented yet.

## Advance-booking notification lifecycle

```mermaid
stateDiagram-v2
  [*] --> Pending: visitor submits email + cinemas
  Pending --> Confirmed: visitor opens confirmation link
  Pending --> Expired: confirmation window passes
  Confirmed --> Notified: live checker sends a matching alert
  Notified --> Notified: another selected cinema publishes a slot
  Confirmed --> Confirmed: no matching slot yet or checker is dry-run
```

1. Visitor chooses an upcoming movie, email address and one or more cinemas.
2. The API stores a pending subscription and sends a time-limited Resend confirmation link.
3. The visitor must confirm before notifications are considered.
4. The scraper updates catalogue/showtime data.
5. The notification checker matches the movie title and selected cinema names against future showtimes.
6. Cloudflare-dispatched GitHub Actions checks default to dry-run: they log matches but do not send booking alerts or mark cinemas notified. Live delivery requires an explicit workflow opt-in and configured Resend sender secrets.

The confirmation token has a finite expiry. The database status and `notified_cinemas` field are used to avoid duplicate alerts.

## Data integrity principles

- The backend/Supabase catalogue is authoritative for listings and showtimes.
- Only future sessions are returned to the UI.
- The frontend must not fabricate showtimes or infer availability from a release date alone.
- Cinema coordinates are presentation metadata and should be checked before adding a new venue.
- Scraper audit/reconciliation output is operational evidence and is not committed to the public source repository.

## Deployment topology

- Cloudflare Pages hosts the Vite frontend.
- A TypeScript Cloudflare Worker serves the edge API and owns Cron Triggers.
- GitHub Actions runners execute the Python scraper and booking checker.
- Supabase stores catalogue, showtimes, subscriptions and scraper history.
- Resend sends subscription confirmation emails and can send booking alerts after live delivery is explicitly enabled.

See [Deployment](DEPLOYMENT.md) for setup, secret configuration and the warning that deploying the Worker activates its Cron Triggers.
