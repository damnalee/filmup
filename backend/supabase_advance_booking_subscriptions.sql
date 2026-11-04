-- FilmUp: double-opt-in advance-booking notifications.
-- Run this once in Supabase SQL Editor using the project owner account.

create table if not exists public.advance_booking_subscriptions (
    id uuid primary key default gen_random_uuid(),
    email text not null,
    movie_title text not null,
    cinema_names text[] not null,
    notified_cinemas text[] not null default '{}',
    notified_slots jsonb not null default '{}'::jsonb,
    confirmation_token text not null unique,
    status text not null default 'pending'
        check (status in ('pending', 'confirmed', 'notified', 'expired')),
    created_at timestamptz not null default now(),
    confirmed_at timestamptz,
    expires_at timestamptz not null,
    last_notified_at timestamptz
);

create index if not exists advance_booking_status_idx
    on public.advance_booking_subscriptions (status);

create index if not exists advance_booking_email_idx
    on public.advance_booking_subscriptions (email);

alter table public.advance_booking_subscriptions enable row level security;

-- The FastAPI backend accesses this table with its server-side Supabase key.
-- Do not expose the service-role key in the browser.
revoke all on table public.advance_booking_subscriptions from anon, authenticated;
grant all on table public.advance_booking_subscriptions to service_role;
