# Keeping the FilmUp GitHub repository professional

Use this checklist when preparing a public update or release.

## Repository profile

- Keep the repository name `filmup`, with a short description explaining Lahore cinema discovery.
- Add focused topics: `lahore`, `cinema`, `movie-discovery`, `showtimes`, `react`, `typescript`, `fastapi`, `supabase` and `maplibre`.
- Set the README as the repository landing page; keep the live-app URL blank until a real deployment exists.
- Add a license only after the project owner chooses the reuse terms. Until then, the repository explicitly reserves all rights.

## Before each push

- Run `npm run typecheck` and `npm run build`.
- Run `python -m compileall -q backend`.
- Check the Git diff for secrets, local environment files, generated data and unrelated edits.
- Update `.env.example` and docs whenever configuration changes.
- Include before/after screenshots for visible UI changes.
- Keep movie/showtime claims grounded in backend data and source audits.

## Before a release

- Verify frontend and API behaviour on a clean checkout.
- Verify email confirmation and alert behaviour using a test mailbox.
- Verify the scraper schedule on an always-on runtime.
- Review the current hosting and email provider terms.
- Update `CHANGELOG.md` and create a GitHub release with a concise summary.
- Confirm the release has no exposed credentials, tokens, subscriber data or operational backups.

## Project presentation checklist

- [x] Product-focused README and real application screenshot
- [x] Feature list and architecture diagram
- [x] Local setup and environment-variable reference
- [x] Deployment and free-tier caveats
- [x] Operations and scraper runbook
- [x] Security and contribution policies
- [x] Issue and pull-request templates
- [x] CI workflow for frontend build and backend syntax checks
- [x] Promotional video, poster, storyboard and asset credits
- [ ] Production deployment URL
- [ ] Project license selected by owner

Reference: [GitHub Community discussion #88715](https://github.com/orgs/community/discussions/88715).
