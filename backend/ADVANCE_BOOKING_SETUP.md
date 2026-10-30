# FilmUp advance-booking email alerts

## What this feature does

- A user opens a coming-soon movie and chooses **Notify me when booking opens**.
- They enter an email address and select one or more cinemas.
- FilmUp creates a pending subscription and sends a confirmation email.
- The subscription is inactive until the recipient clicks the confirmation link. Links expire after 48 hours.
- Once confirmed, the backend checks the Supabase `showtimes` table for future slots matching that movie and the selected cinemas.
- Each selected cinema can trigger one alert. If a second selected cinema opens booking later, it can trigger a separate follow-up alert.
- The checker runs every five minutes while the scraper scheduler is enabled and is also called immediately after a successful scrape. If booking is already live when the user confirms, the backend checks immediately.

## 1. Create the Supabase table

Open the Supabase project → SQL Editor → New query.

Run the complete SQL from:

`supabase_advance_booking_subscriptions.sql`

The table is private to the backend. The browser never receives the Supabase service-role key.

## 2. Check backend email settings

The backend sends confirmation and advance-booking alerts through Gmail SMTP. The backend `.env` should contain:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=your_sender@example.com
SMTP_PASSWORD=YOUR_GMAIL_APP_PASSWORD
SMTP_SENDER_NAME=FilmUp Notifications
```

Keep the real App Password only in the backend `.env`. Never put it in the React/Netlify environment or commit it to Git.

## 3. Start the backend

From `/Users/alee/Documents/c#1/cinema`:

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The new endpoints are:

- `POST /api/advance-booking/subscribe`
- `GET /api/advance-booking/confirm/{token}`

The frontend uses the existing `VITE_CINEMA_API_URL` value to reach the API. If that value is not set, it uses the same-origin `/api` proxy.

## 4. Deploy

Deploy the FastAPI backend with the updated files and the SMTP environment variables. Deploy the React frontend build to Netlify. The Supabase SQL migration must be run before users submit subscriptions.

## Email behavior

- Confirmation subject: `Confirm your advance-booking alert for {movie title}`
- Booking alert subject: `Advance booking is open: {movie title}`
- The confirmation message lists the movie and all selected cinemas.
- The booking alert lists detected upcoming showtimes and includes a booking link when the scraper has captured one.
- Alerts are about advance-booking availability, not the film's release date.

## Operational note

The current scraper refreshes cinema sources approximately once per hour. The email is sent as soon as a successful refresh detects a new matching slot (or on the five-minute checker if another process has inserted the slot). The five-minute checker does not make the source websites refresh more frequently.
