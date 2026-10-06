-- ==========================================================
-- D.A.R.T.: Shirt numbers + athlete highlights (photos / videos)
-- ==========================================================
-- 1. Optional shirt number on each game record, and the athlete's
--    current shirt number (shown with their personal info).
--    Text, not integer, so basketball's "00" is kept.
-- 2. athlete_highlight: photos and videos (60 seconds max) athletes
--    upload. Deleting a user deletes their highlight rows.
-- 3. Storage bucket 'athlete-highlights' (public, so highlights play on
--    athlete profiles). Storage itself rejects files over 50 MB and any
--    type other than JPG / PNG / WEBP / MP4 / MOV.
--    50 MB is the largest file the Supabase Free plan allows. On a paid
--    plan you can raise file_size_limit here AND set
--    HIGHLIGHT_VIDEO_MAX_MB in the app's environment to the same value.
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE STARTING/DEPLOYING the app code that uses these
-- columns (the app reads shirt_number on every record and user query).
-- Safe to run more than once.
-- ==========================================================

begin;

-- 1) Shirt numbers
alter table public.sport_record
    add column if not exists shirt_number varchar(2);

alter table public."user"
    add column if not exists shirt_number varchar(2);

-- 2) Highlights
create table if not exists public.athlete_highlight (
    id               bigserial primary key,
    user_id          bigint not null references public."user" (id) on delete cascade,
    media_type       varchar(10) not null check (media_type in ('photo', 'video')),
    storage_path     varchar(255) not null unique,
    content_type     varchar(50) not null,
    size_bytes       bigint not null,
    duration_seconds numeric(6, 2),
    caption          varchar(150),
    created_at       timestamp without time zone not null default now()
);

create index if not exists ix_athlete_highlight_user_id
    on public.athlete_highlight (user_id);

-- The app connects to Postgres directly, so this only blocks the public
-- Supabase REST API (anon key) from reading or changing the table.
alter table public.athlete_highlight enable row level security;

-- 3) Storage bucket
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'athlete-highlights',
    'athlete-highlights',
    true,
    52428800,  -- 50 MB
    array['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime']
)
on conflict (id) do update
set public             = excluded.public,
    file_size_limit    = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

commit;

-- Check: expect 4 rows (2 shirt_number columns, the table, the bucket).
select 'sport_record.shirt_number' as item from information_schema.columns
 where table_schema = 'public' and table_name = 'sport_record' and column_name = 'shirt_number'
union all
select 'user.shirt_number' from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'shirt_number'
union all
select 'athlete_highlight table' from information_schema.tables
 where table_schema = 'public' and table_name = 'athlete_highlight'
union all
select 'athlete-highlights bucket' from storage.buckets where id = 'athlete-highlights';
