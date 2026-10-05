-- ==========================================================
-- D.A.R.T.: AFCON / WAFU / World Cup team on sport records
-- ==========================================================
-- Adds sport_record.competition_team, which holds the AFCON or
-- World Cup team (e.g. "Lonestar Men's Team") or the WAFU age
-- category (e.g. "Male-U17"). competition_category says which.
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE DEPLOYING the app code that uses the column.
-- The app reads every SportRecord column, so pages that list
-- records would fail if the code is live before the column exists.
--
-- Safe to run more than once.
-- ==========================================================

begin;

alter table public.sport_record
    add column if not exists competition_team varchar(50);

-- Older code listed combined values such as 'WAFU - Male-U17' as
-- valid competition categories. Split any stored that way into
-- competition_category + competition_team.
update public.sport_record
set competition_team     = substring(competition_category from char_length('AFCON - ') + 1),
    competition_category = 'AFCON'
where competition_category like 'AFCON - %';

update public.sport_record
set competition_team     = substring(competition_category from char_length('WAFU - ') + 1),
    competition_category = 'WAFU'
where competition_category like 'WAFU - %';

update public.sport_record
set competition_team     = substring(competition_category from char_length('World Cup - ') + 1),
    competition_category = 'World Cup'
where competition_category like 'World Cup - %';

commit;

-- Check the result: national-team records and their team/category.
select competition_category, competition_team, status, count(*) as records
from public.sport_record
where competition_category in ('AFCON', 'WAFU', 'World Cup')
group by competition_category, competition_team, status
order by competition_category, competition_team, status;
