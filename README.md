# FilmUp

**Find the show. Find the cinema. Make a night of it.**

FilmUp is a Lahore-focused cinema discovery experience. It brings cinema locations, movie listings and upcoming showtimes into one interactive map, with email alerts for advance-booking openings on selected upcoming films.

<p align="center">
  <img src="docs/screenshots/filmup-home.jpg" alt="FilmUp running locally — interactive Lahore cinema map and movie discovery interface" width="100%" />
</p>

<p align="center">
  <strong>React · TypeScript · Vite · FastAPI · Supabase · MapLibre</strong>
</p>

## What FilmUp does

- **Explore Lahore cinemas:** discover supported venues on an interactive map and browse by cinema or area.
- **Find movies and showtimes:** search the live catalogue, inspect upcoming sessions and follow booking links supplied by cinema sources.
- **See what's coming:** browse upcoming titles even when a cinema has not published sessions yet.
- **Get advance-booking alerts:** choose one or more cinemas, confirm your email address, and receive an alert when matching future showtimes appear.
- **Use your location optionally:** browser geolocation can help find nearby venues; it is only requested after the user chooses the location feature.

FilmUp is a discovery and notification product. It does not sell tickets or guarantee that a cinema's external booking page has seats available.

## Launch video

[![Watch the FilmUp launch video](brag-output/brag.jpg)](brag-output/brag.mp4)

[Watch the 20-second FilmUp launch video](brag-output/brag.mp4) · [Share caption](brag-output/share-copy.txt) · [Audio and asset credits](brag-output/CREDITS.md)

The launch film was produced with the [latent-spaces/brag](https://github.com/latent-spaces/brag) workflow and Hyperframes. Its source storyboard and editable composition are included in `brag-output/`.

## Product preview

| Experience | What it helps with |
|---|---|
| Interactive cinema map | Find supported venues and explore Lahore areas |
| Movie discovery | Search current and upcoming catalogue entries |
| Showtime details | Review future sessions and follow source booking links |
| Advance-booking alerts | Get notified after confirming an email subscription |

The screenshot above was captured from the running local application. Live listings can change as cinema sources and the scraper update.

## Architecture

```mermaid
flowchart LR
    U[Visitor] --> FE[React + Vite frontend]
    FE --> API[FastAPI backend]
    API --> DB[(Supabase PostgreSQL)]
    SCR[Cinema source collectors] --> DB
    API --> AI[Groq chat service]
    API --> MAIL[SMTP email delivery]
    API --> SCHED[APScheduler]
    SCHED --> SCR
    SCHED --> ALERT[Advance-booking checker]
    ALERT --> DB
    ALERT --> MAIL
```

The frontend and backend are maintained in one repository. The React app is at the repository root; the Python API is in `backend/`.

## Technology

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS 4 |
| Map | MapLibre GL, React Map GL, Supercluster |
| UI motion | Framer Motion |
| API | FastAPI, Uvicorn, Pydantic |
| Data | Supabase (PostgreSQL) |
| Collection | Python, HTTPX/aiohttp, BeautifulSoup, APScheduler |
| AI assistant service | Groq SDK (backend endpoint; separate from the map UI) |
| Email | SMTP with a Gmail app password or another compatible SMTP provider |

## Quick start

See the full [local setup guide](docs/SETUP.md). In short:

### 1. Requirements

- Node.js 22 or newer and npm
- Python 3.12 (recommended; see `backend/.python-version`)
- A Supabase project with the FilmUp catalogue tables
- Groq API key for the optional chat endpoint
- SMTP credentials for advance-booking confirmation and alert emails

### 2. Configure the backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set the Supabase URL/key. Set Groq and SMTP values if you use those services. Never commit `.env`.

### 3. Start the API

From `backend/`:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/docs`.

### 4. Configure and start the frontend

In a second terminal, from the repository root:

```bash
npm ci
cp .env.example .env.local
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8000` by default. To change the proxy target, set `VITE_API_PROXY_TARGET` in `.env.local`.

### 5. Build and verify

```bash
npm run typecheck
npm run build
```

For backend syntax verification:

```bash
python -m compileall -q backend
```

## Documentation

- [Local setup and environment variables](docs/SETUP.md)
- [Architecture and request/data flows](docs/ARCHITECTURE.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Operations, scraping and alert behaviour](docs/OPERATIONS.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [GitHub repository maintenance checklist](docs/GITHUB_MAINTENANCE.md)

## API overview

| Endpoint | Purpose |
|---|---|
| `GET /health` | API and scheduler health |
| `GET /api/movies` | Movie catalogue with future showtimes |
| `POST /api/advance-booking/subscribe` | Create a pending email-alert subscription |
| `GET /api/advance-booking/confirm/{token}` | Confirm an email subscription |
| `POST /chat` | Optional cinema assistant endpoint |
| `GET /docs` | Interactive OpenAPI documentation |

The catalogue API is the source of truth for movie/showtime data. The frontend intentionally does not invent sample movies or showtimes when the API is unavailable.

## Data and source integrity

- Cinema coordinates and area metadata are curated frontend data used to place map markers.
- Movie catalogue and showtime records are loaded from the backend and Supabase.
- Only future showtime rows are exposed as upcoming sessions.
- External booking URLs are links to cinema/provider pages; FilmUp does not complete ticket purchases.
- Scraper output, reconciliation backups and local audit exports are intentionally excluded from Git because they are operational data, not application source.

## Project status and limitations

FilmUp is an actively developed project. Cinema websites can change their markup or APIs, and a source may temporarily fail or publish incomplete information. Treat the cinema's own booking page as authoritative before travelling or purchasing tickets.

The current backend starts the scraper schedule inside the FastAPI process. A host that sleeps or restarts free services cannot guarantee scheduled scraping or five-minute alert checks. Some free hosts also block outbound SMTP. See [deployment limitations](docs/DEPLOYMENT.md) before using a free tier for real notifications.

## License

No open-source license has been applied yet. Until a license is added, all rights are reserved by the project owner. Do not assume permission to reuse or redistribute this code.
