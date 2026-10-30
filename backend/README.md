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
