# FilmUp

**Find the show. Find the cinema. Make a night of it.**

[![CI](https://github.com/damnalee/filmup/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/damnalee/filmup/actions/workflows/ci.yml)

FilmUp is a Lahore-focused cinema discovery experience. It brings cinema locations, movie listings and upcoming showtimes into one interactive map, with email alerts for advance-booking openings on selected upcoming films.

<p align="center">
  <img src="docs/screenshots/filmup-map.jpg" alt="FilmUp interactive 3D map of Lahore with cinema locations and movie ribbons" width="100%" />
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

## Product screens

The screenshots below are captures of the real FilmUp interface running locally. They show the current product flow rather than mockups.

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/screenshots/filmup-cinemas-list.jpg" alt="FilmUp cinema directory showing Lahore venues and their next listed showtimes" width="100%" />
      <p><strong>Cinema directory</strong><br />Browse supported Lahore cinemas, compare their areas and ratings, and see the next listed session.</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/screenshots/filmup-cue-showtimes.jpg" alt="CUE Cinemas detail panel with currently listed movies and showtimes" width="100%" />
      <p><strong>CUE Cinemas and showtimes</strong><br />Open a venue on the map to review its movie listings and available showtime information.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/screenshots/filmup-upcoming-dune.jpg" alt="Dune Part Three movie details marked coming soon in FilmUp" width="100%" />
      <p><strong>Upcoming movies</strong><br />View a coming-soon title such as <em>Dune: Part Three</em>, even before cinemas publish sessions.</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/screenshots/filmup-advance-booking-alert.jpg" alt="FilmUp advance-booking alert form with email and cinema choices" width="100%" />
      <p><strong>Advance-booking alerts</strong><br />Choose an email address and the cinemas to watch. FilmUp sends a confirmation email before alerts are activated.</p>
    </td>
  </tr>
</table>

The map and product screens were captured on 30 October 2026. Movie catalogues, release information and showtimes are live data and can change after these screenshots are taken.

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

For backend syntax verification and unit tests:

```bash
python -m compileall -q backend
python -m unittest discover -s backend -p 'test_*.py' -v
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

The scraper monitoring endpoint (`/api/monitoring/scraper`) exposes operational run history. Put it behind administrator authentication or an access-controlled gateway before exposing a deployed backend publicly.

## License

No open-source license has been applied yet. Until a license is added, all rights are reserved by the project owner. Do not assume permission to reuse or redistribute this code.
