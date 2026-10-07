-- Media safety + personal-details privacy (terms version 2026-10-07).
--  * Highlights: sport, review status, AI check result, reviewer.
--    Existing highlights become 'pending' so a Super Admin reviews them
--    before they show again.
--  * Private 'highlight-review' bucket: new uploads wait there, unseen.
--  * Users: who may see their personal details; upload strikes / block.
--  * Terms acceptances: the personal-details choice made when accepting.
--  * Record rejections: the coach's reason; fake-record strikes and
--    account suspension (3 fake records).

begin;

-- 1) Users
alter table public."user" add column if not exists personal_visibility varchar(10);
alter table public."user" add column if not exists media_strikes integer not null default 0;
alter table public."user" add column if not exists media_blocked boolean not null default false;
-- Fake-record strikes from coaches (3 = suspended) and account suspension.
alter table public."user" add column if not exists fake_record_strikes integer not null default 0;
alter table public."user" add column if not exists is_suspended boolean not null default false;
alter table public."user" add column if not exists suspended_at timestamp without time zone;
alter table public."user" add column if not exists suspension_reason varchar(200);

-- Why a coach rejected a record.
alter table public.sport_record add column if not exists rejection_reason varchar(20);
alter table public.sport_record add column if not exists rejection_note varchar(200);
alter table public.sport_record add column if not exists rejected_by bigint;
alter table public.sport_record add column if not exists rejected_at timestamp without time zone;

-- 2) Highlights
alter table public.athlete_highlight add column if not exists sport varchar(20);
alter table public.athlete_highlight add column if not exists status varchar(12) not null default 'pending';
-- Existing files are in the public bucket; new ones start in the private one.
alter table public.athlete_highlight add column if not exists storage_bucket varchar(40) not null default 'athlete-highlights';
alter table public.athlete_highlight alter column storage_bucket set default 'highlight-review';
alter table public.athlete_highlight add column if not exists ai_checked boolean not null default false;
alter table public.athlete_highlight add column if not exists ai_summary varchar(300);
alter table public.athlete_highlight add column if not exists ai_flags varchar(120);
alter table public.athlete_highlight add column if not exists review_reason varchar(300);
alter table public.athlete_highlight add column if not exists reviewed_by bigint references public."user" (id) on delete set null;
alter table public.athlete_highlight add column if not exists reviewed_at timestamp without time zone;
create index if not exists ix_athlete_highlight_status on public.athlete_highlight (status);

-- 3) Terms acceptances
alter table public.terms_acceptance add column if not exists personal_visibility varchar(10);

-- 4) Private bucket for uploads waiting for review (NOT public)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'highlight-review',
    'highlight-review',
    false,
    52428800,  -- 50 MB
    array['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime']
)
on conflict (id) do update
set public             = false,
    file_size_limit    = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

commit;

-- Check: expect 8 rows.
select 'user.personal_visibility' as item from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'personal_visibility'
union all
select 'user.media_blocked' from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'media_blocked'
union all
select 'athlete_highlight.status' from information_schema.columns
 where table_schema = 'public' and table_name = 'athlete_highlight' and column_name = 'status'
union all
select 'athlete_highlight.storage_bucket' from information_schema.columns
 where table_schema = 'public' and table_name = 'athlete_highlight' and column_name = 'storage_bucket'
union all
select 'terms_acceptance.personal_visibility' from information_schema.columns
 where table_schema = 'public' and table_name = 'terms_acceptance' and column_name = 'personal_visibility'
union all
select 'user.is_suspended' from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'is_suspended'
union all
select 'sport_record.rejection_reason' from information_schema.columns
 where table_schema = 'public' and table_name = 'sport_record' and column_name = 'rejection_reason'
union all
select 'highlight-review bucket (private)' from storage.buckets where id = 'highlight-review' and public = false;
