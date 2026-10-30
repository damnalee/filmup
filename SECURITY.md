# Security policy

## Reporting a vulnerability

Please do not publish exploitable security details in a public issue. Contact the repository owner privately through GitHub's security advisory/reporting features.

Include the affected component, impact, reproduction steps and any suggested mitigation. Do not include real subscriber email addresses, confirmation tokens, service-role keys or SMTP credentials.

## Security practices

- Keep Supabase private/service-role credentials in backend-only environment variables.
- Never prefix backend secrets with `VITE_`; Vite variables are bundled into browser assets.
- Keep `.env` and `.env.local` out of Git.
- Keep confirmation tokens time-limited and require explicit email confirmation before alerts.
- Restrict CORS to known frontend origins in production.
- Avoid logging secrets, tokens or full subscriber records.
- Rotate any credential that may have been exposed.
