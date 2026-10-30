# Contributing to FilmUp

Thanks for your interest in improving FilmUp. Contributions should protect data accuracy and keep the product focused on Lahore cinema discovery.

## Before opening an issue

- Search existing issues for duplicates.
- Include a concise description, expected behaviour and actual behaviour.
- For scraper issues, include the cinema/source name, approximate time and sanitized logs. Do not attach credentials, subscriber emails or confirmation tokens.
- For UI issues, include browser/OS details and a screenshot with personal information removed.

## Development workflow

1. Fork the repository and create a focused branch.
2. Install the frontend and backend as described in [docs/SETUP.md](docs/SETUP.md).
3. Keep changes scoped; avoid unrelated formatting or generated output.
4. Run frontend typecheck and production build.
5. Run backend syntax checks and relevant manual API checks.
6. Update documentation when configuration, endpoints or operational behaviour changes.
7. Open a pull request with a clear summary, testing performed and any migration notes.

## Quality expectations

- Do not invent cinema showtimes, movie availability or booking URLs.
- Keep secrets in environment variables and out of browser code.
- Validate and normalize external cinema data at the backend boundary.
- Preserve the opt-in email confirmation requirement for advance-booking alerts.
- Avoid destructive database changes without a migration and rollback plan.
- Keep map coordinates and venue names traceable to verified sources.

## Pull request checklist

- [ ] Scope and motivation are clear.
- [ ] No `.env`, keys, tokens, subscriber data or local scraper outputs are included.
- [ ] `npm run typecheck` passes.
- [ ] `npm run build` passes.
- [ ] Backend syntax checks pass.
- [ ] Documentation and `.env.example` are updated if needed.
- [ ] Screenshots are included for meaningful UI changes.
