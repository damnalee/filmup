# FilmUp advance-booking email alerts

## How notifications work

- A user selects a coming-soon movie, an email address, and one or more cinemas.
- FilmUp sends a double-opt-in confirmation email. Alerts remain inactive until the recipient confirms.
- The checker compares future Supabase showtimes with the selected movie and cinemas.
- **Every newly appearing showtime triggers an alert**, including additional showtimes at a cinema that has already triggered an earlier email.
- Each exact showtime is recorded in `notified_slots`; previously emailed showtimes are not sent again.
- The subscription remains eligible after its first alert so later showtimes can still trigger follow-up emails.
- The checker runs every five minutes through GitHub Actions. The cinema scraper refreshes the showtime database hourly, at five minutes past the hour. A newly scraped slot should therefore be picked up by the next checker run.
- GitHub Actions runs independently of the user's Mac. The Mac does not need to remain on.

## 1. Database setup and migration

For a fresh installation, run:

`supabase_advance_booking_subscriptions.sql`

For the existing FilmUp production database, run this migration **once before deploying the updated backend**:

`supabase_advance_booking_slot_tracking.sql`

The migration adds the `notified_slots` JSONB column and seeds it with currently stored future slots for cinemas that were already notified. This avoids resending those existing slots when the new checker first runs.

Open the Supabase project → SQL Editor → New query, paste the migration contents, and run it. The table remains private to the backend; never expose the service-role key in the browser.

## 2. Email configuration

The GitHub Actions workflow reads these repository secrets:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `RESEND_API_KEY` (or legacy `RESEND_API`)
- `MAIL_FROM_EMAIL` (or legacy `MAIL_FROM`)

It sets `ADVANCE_BOOKING_EMAIL_ENABLED=true` for the scheduled checker. The sender must be configured and verified in Resend. A successful Resend API response means the provider accepted the message; inbox delivery should be checked in the Resend dashboard.

## 3. Automated schedules

Workflow files:

- `.github/workflows/filmup-advance-booking-check.yml` — every five minutes.
- `.github/workflows/filmup-hourly-scraper.yml` — hourly, at minute 5.

Both run on GitHub-hosted runners, not on the user's Mac. The checker can only detect slots after they have been written to Supabase by the scraper or another data process.

## 4. Verification

Run the unit tests from the backend directory:

```bash
python -m unittest test_advance_booking_notifications.py
```

Check the [FilmUp GitHub Actions runs](https://github.com/damnalee/filmup/actions) for scheduled execution. Check [Resend Emails](https://resend.com/emails) for provider acceptance and delivery events.

## Email behavior

- Confirmation: `Confirm your advance-booking alert for {movie title}`
- Booking alert: `Advance booking is open: {movie title}`
- Each alert lists only the newly detected showtimes and includes a booking link when available.
- Alerts indicate booking availability, not a ticket reservation. Availability can change before the user books.
