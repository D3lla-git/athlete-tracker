-- ==========================================================
-- D.A.R.T.: Basketball rebound counts
-- ==========================================================
-- Adds offensive_rebounds and defensive_rebounds (whole numbers) to
-- sport_record, so scouts can filter and rank by rebounds. Total
-- rebounds = offensive + defensive.
--
-- Existing records get 0 for both. Older records keep their
-- rebound_type ("Offensive rebound" / "Defensive rebound"), which is
-- still shown until the athlete enters real counts.
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE DEPLOYING the app code that uses these columns.
-- Safe to run more than once.
-- ==========================================================

begin;

alter table public.sport_record
    add column if not exists offensive_rebounds integer not null default 0;

alter table public.sport_record
    add column if not exists defensive_rebounds integer not null default 0;

commit;

-- Check: both columns exist.
select column_name, data_type, column_default, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name = 'sport_record'
  and column_name in ('offensive_rebounds', 'defensive_rebounds')
order by column_name;
