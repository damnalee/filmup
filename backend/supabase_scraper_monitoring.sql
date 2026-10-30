-- FilmUp: independent scraper observability history.
-- Run once in Supabase SQL Editor with the project owner/admin role.
-- This table is separate from movie_catalog, showtimes and advance-booking data.

create table if not exists public.scraper_runs (
    run_id uuid primary key,
    trigger text not null default 'scheduled',
    status text not null default 'running'
        check (status in ('running', 'success', 'completed_with_warnings', 'failed', 'interrupted')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    duration_seconds double precision,
    authoritative_rows integer not null default 0 check (authoritative_rows >= 0),
    lahore_rows integer not null default 0 check (lahore_rows >= 0),
    unique_lahore_rows integer not null default 0 check (unique_lahore_rows >= 0),
    catalogue_rows_upserted integer not null default 0 check (catalogue_rows_upserted >= 0),
    warning_count integer not null default 0 check (warning_count >= 0),
    error_count integer not null default 0 check (error_count >= 0),
    sources jsonb not null default '{}'::jsonb,
    audits jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists scraper_runs_started_at_idx
    on public.scraper_runs (started_at desc);
create index if not exists scraper_runs_status_started_at_idx
    on public.scraper_runs (status, started_at desc);

alter table public.scraper_runs enable row level security;

-- Only the server-side backend key may read/write monitoring history.
revoke all on table public.scraper_runs from anon, authenticated;
grant all on table public.scraper_runs to service_role;
