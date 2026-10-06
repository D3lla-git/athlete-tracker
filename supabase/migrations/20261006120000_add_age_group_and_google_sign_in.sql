-- ==========================================================
-- D.A.R.T.: Grassroots age groups + Google sign-in
-- ==========================================================
-- 1. sport_record.age_group — LFA grassroots age group (U-10, U-12,
--    U-15, U-17) for Grassroots League records.
-- 2. "user".google_sub — the Google account ID once Google sign-in is
--    connected (unique: one Google account per D.A.R.T. account).
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE DEPLOYING the app code that uses these columns.
-- The app reads every column of these tables, so pages would fail if
-- the code is live before the columns exist.
--
-- Safe to run more than once. Adds nullable columns only; no existing
-- data is changed.
-- ==========================================================

begin;

alter table public.sport_record
    add column if not exists age_group varchar(10);

alter table public."user"
    add column if not exists google_sub varchar(255);

create unique index if not exists user_google_sub_key
    on public."user" (google_sub);

commit;

-- Check: both columns exist.
select table_name, column_name, data_type, character_maximum_length
from information_schema.columns
where table_schema = 'public'
  and (
        (table_name = 'sport_record' and column_name = 'age_group')
     or (table_name = 'user' and column_name = 'google_sub')
  )
order by table_name;
