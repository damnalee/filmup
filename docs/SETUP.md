# Local development setup

This guide sets up FilmUp on macOS, Linux or Windows with Node.js and Python installed.

## Prerequisites

| Tool | Version | Purpose |
|---|---:|---|
| Node.js | 22+ | Vite, React and build tooling |
| npm | bundled with Node | Frontend dependencies |
| Python | 3.12 | FastAPI and scraper |
| Git | current | Source control |
| Supabase project | existing | Catalogue, showtimes and alert subscriptions |

Optional services:

- Groq API key for the `/chat` endpoint.
- SMTP provider credentials for subscription confirmation and notification emails.

## Repository layout

```text
FilmUp/
├── src/                    # React + TypeScript application
├── public/                 # Static assets and intro media
├── docs/                   # Setup, architecture, operations and screenshots
├── backend/
│   ├── main.py             # FastAPI app and APScheduler lifecycle
│   ├── catalog_routes.py   # Movie catalogue API
│   ├── advance_booking_routes.py
│   ├── scraper.py          # Cinema collectors and scheduled refresh
│   ├── database.py         # Supabase client and persistence
│   ├── chatbot.py          # Optional Groq-backed chat endpoint
│   ├── requirements.txt
│   └── .env.example
├── package.json
└── README.md
```

## 1. Supabase setup

1. Create or select a Supabase project.
2. Ensure the catalogue and showtime tables expected by `backend/database.py` and `backend/catalog_routes.py` exist.
3. Run `backend/supabase_advance_booking_subscriptions.sql` in the Supabase SQL Editor to create the advance-booking subscription table and indexes.
4. Copy the project URL and backend service-role key from the Supabase project settings into `backend/.env`.
5. Keep the service-role key on the server only. Never expose it as a `VITE_` variable or commit it.

The SQL migration enables row-level security and restricts direct access; the backend uses its private server credential.

## 2. Backend setup

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Fill in `backend/.env`:

| Variable | Required? | Description |
|---|---|---|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Private backend/service-role key; never put in frontend |
| `GROQ_API_KEY` | For chat | Groq API key |
| `GROQ_MODEL` | Optional | Model identifier; backend has a default |
| `APP_ENV` | Optional | Environment label |
| `LOG_LEVEL` | Optional | Python logging level |
| `TIMEZONE` | Optional | Defaults to `Asia/Karachi` |
| `CORS_ALLOWED_ORIGINS` | Production | Comma-separated frontend origins, without trailing slash |
| `SMTP_HOST` | For email | SMTP host; defaults to Gmail SMTP |
| `SMTP_PORT` | For email | SMTP port; defaults to 587 |
| `SMTP_EMAIL` | For email | Sender mailbox |
| `SMTP_PASSWORD` | For email | App password/provider credential; keep secret |
| `SMTP_SENDER_NAME` | Optional | Sender display name |
| `SCRAPER_ENABLED` | Optional | Enables scheduler when `true` |
| `SCRAPER_SCHEDULE_MINUTE` | Optional | Minute within the configured hour; default `5` |
| `SCRAPER_INTERVAL_HOURS` | Optional | Hour cadence; default `1` |
| `SCRAPER_REQUEST_TIMEOUT` | Optional | HTTP request timeout in seconds |
| `SCRAPER_MAX_CONCURRENCY` | Optional | Maximum concurrent source requests |

Start the API:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl -fsS http://127.0.0.1:8000/health
```

The health response reports API status, timezone, whether the scheduler is running, and the next scheduled scrape. It does not prove that every cinema source succeeded.

## 3. Frontend setup

In a second terminal at the repository root:

```bash
npm ci
cp .env.example .env.local
npm run dev
```

The frontend environment file supports:

```dotenv
VITE_CINEMA_API_URL=/api
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
```

- `VITE_CINEMA_API_URL` is the API base path used by browser requests.
- `VITE_API_PROXY_TARGET` is read by Vite's local development proxy only.
- For local development, leave the API base as `/api`; Vite forwards it to FastAPI.
- For a separately hosted frontend, set `VITE_CINEMA_API_URL` to the public backend origin plus `/api`, and add the frontend origin to backend `CORS_ALLOWED_ORIGINS`.

Open `http://127.0.0.1:5173`.

## 4. Verification checklist

- [ ] `GET /health` returns HTTP 200.
- [ ] `GET /api/movies?limit=10` returns a JSON response with a `movies` array.
- [ ] Frontend loads without an API connection error.
- [ ] Search and map browsing work.
- [ ] Movie details show only showtimes returned by the backend.
- [ ] Advance-booking form is shown for eligible upcoming titles.
- [ ] Test email subscription only with an address you control; verify confirmation and expiry behaviour.
- [ ] `npm run typecheck` and `npm run build` both pass.

## 5. Manual scraper run

From `backend/` with its virtual environment active:

```bash
python scraper.py
```

A manual scrape can make real changes to Supabase. Use it deliberately and inspect the logs/audit output. Do not run multiple scrapers against the same tables concurrently.

## Troubleshooting

### Frontend says it cannot reach the API

- Confirm FastAPI is running on port 8000.
- Check `VITE_API_PROXY_TARGET` in `.env.local`.
- Restart Vite after changing environment variables.
- Check `GET http://127.0.0.1:8000/health` directly.

### Catalogue is empty or incomplete

- Check Supabase connectivity and table permissions.
- Inspect backend logs and scraper audit output.
- Confirm the source collectors still match the cinema websites' current markup.
- Do not add mock showtimes as a workaround; resolve the source/data issue.

### Email confirmation fails

- Verify SMTP host, port, sender and app password.
- For Gmail, use an App Password, not the normal account password.
- Check the hosting provider's outbound SMTP policy.
- Confirm the recipient's inbox and spam folder.

### Scheduler is not running

- Check `SCRAPER_ENABLED=true`.
- Read `/health` and backend startup logs.
- Remember that a sleeping/restarted host cannot provide reliable in-process scheduled work.
