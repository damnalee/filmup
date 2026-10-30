# Deployment guide

This is a deployment recipe, not a claim that FilmUp has already been deployed. Configure each service using the provider dashboards and keep secrets out of Git.

## Recommended topology

| Component | Suggested service | Configuration |
|---|---|---|
| React/Vite frontend | Vercel Static Build | Root directory `.`, build `npm run build`, output `dist` |
| FastAPI API | Render Web Service or another Python host | Root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Database | Existing Supabase project | Store URL/key only in backend secrets |
| Email | Transactional email provider | Use provider API or SMTP supported by the host |
| Scraping/notifications | Always-on worker or reliable scheduled job | Do not depend on a sleeping web service for production scheduling |

## Frontend on Vercel

1. Import the GitHub repository into Vercel.
2. Set the project root to the repository root.
3. Framework preset: Vite.
4. Build command: `npm run build`.
5. Output directory: `dist`.
6. Add `VITE_CINEMA_API_URL=https://YOUR-BACKEND-HOST/api` as a build environment variable.
7. Deploy and note the exact production frontend origin, e.g. `https://your-project.vercel.app`.

The Vercel Hobby plan is free for personal projects, but its terms limit Hobby use to non-commercial personal use. Review the current plan terms before using it for a public commercial service: https://vercel.com/docs/plans/hobby.

## Backend on Render

1. Create a Web Service from the GitHub repository.
2. Set Root Directory to `backend`.
3. Runtime: Python.
4. Build command: `pip install -r requirements.txt`.
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
6. Set health-check path to `/health` if offered.
7. Add backend variables from `backend/.env.example` using the Render Environment dashboard. Never paste secrets into source files.
8. Set `CORS_ALLOWED_ORIGINS` to the exact frontend origin(s), comma-separated, without trailing slash.
9. Set `TIMEZONE=Asia/Karachi`.
10. Review the logs and verify `/health` and `/api/movies`.

Provider documentation: https://render.com/docs/web-services.

## Critical free-tier limitations

Do not treat a free Render web service as production-ready for FilmUp's current scheduler and email flow:

- Free web services spin down after 15 minutes without inbound traffic and take about a minute to wake up. An in-process APScheduler cannot run reliably while the service is asleep.
- Render free web services cannot send outbound traffic on SMTP ports 25, 465 or 587. The current Gmail SMTP configuration uses port 587, so email delivery will fail from that tier.
- The free filesystem is ephemeral; do not store durable scraper state or user data on it.

Sources: https://render.com/docs/free and https://render.com/changelog/free-web-services-will-no-longer-allow-outbound-traffic-to-smtp-ports.

For a demo, the frontend and API can be deployed separately with the understanding that the API may cold-start and notification features are not reliable. For a real notification service, use an always-on backend/worker and a transactional email provider supported by the host. Keep scraping and notification jobs in a worker/scheduler that is not suspended with the public API.

## Environment variables

Frontend build-time variable:

- `VITE_CINEMA_API_URL`: public API base path, normally `https://YOUR-BACKEND-HOST/api`.

Backend runtime variables are documented in [SETUP.md](SETUP.md) and `backend/.env.example`. Set `CORS_ALLOWED_ORIGINS` to the deployed frontend URL.

## Post-deploy checks

- [ ] Frontend is served over HTTPS.
- [ ] `GET /health` returns HTTP 200.
- [ ] `GET /api/movies?limit=10` returns expected JSON.
- [ ] Browser console has no CORS or mixed-content errors.
- [ ] Email provider is reachable from the host.
- [ ] Confirmation links use the correct public backend/frontend URL.
- [ ] Scraper and notification schedule is running in a durable process.
- [ ] No secrets appear in deployment logs, browser bundles or Git history.
