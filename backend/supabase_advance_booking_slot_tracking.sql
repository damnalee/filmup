-- FilmUp: upgrade existing advance-booking subscriptions to per-showtime notifications.
-- Run once in Supabase SQL Editor BEFORE deploying the updated checker.

alter table public.advance_booking_subscriptions
    add column if not exists notified_slots jsonb not null default '{}'::jsonb;

-- Preserve the alerts already sent by the previous cinema-level checker.
-- Seed the current future showtimes for cinemas that were already notified, so
-- the first run of the new checker does not resend those same slots.
with previously_alerted_slots as (
    select
        subscription.id as subscription_id,
        regexp_replace(lower(cinema.cinema_name), '[^a-z0-9]', '', 'g') as cinema_key,
        array_agg(
            distinct floor(extract(epoch from showtime.showtime) * 1000000)::bigint::text
        ) as slot_keys
    from public.advance_booking_subscriptions as subscription
    cross join lateral unnest(subscription.notified_cinemas) as cinema(cinema_name)
    join public.showtimes as showtime
      on regexp_replace(lower(showtime.cinema_name), '[^a-z0-9]', '', 'g')
         = regexp_replace(lower(cinema.cinema_name), '[^a-z0-9]', '', 'g')
     and lower(btrim(showtime.movie_title)) = lower(btrim(subscription.movie_title))
     and showtime.showtime >= now()
    where cardinality(subscription.notified_cinemas) > 0
    group by subscription.id, regexp_replace(lower(cinema.cinema_name), '[^a-z0-9]', '', 'g')
),
slot_maps as (
    select
        subscription_id,
        jsonb_object_agg(cinema_key, to_jsonb(slot_keys)) as notified_slots
    from previously_alerted_slots
    group by subscription_id
)
update public.advance_booking_subscriptions as subscription
set notified_slots = coalesce(slot_maps.notified_slots, '{}'::jsonb)
from slot_maps
where subscription.id = slot_maps.subscription_id
  and subscription.notified_slots = '{}'::jsonb;

-- Service-role access only; existing RLS policy remains unchanged.
