-- ==========================================================
-- D.A.R.T.: Saved searches
-- ==========================================================
-- Lets any logged-in user save athlete-search filters and run them again
-- (search page + scout dashboard). Deleting a user deletes their saved
-- searches.
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE DEPLOYING the app code that uses this table.
-- Safe to run more than once.
-- ==========================================================

begin;

create table if not exists public.saved_search (
    id           bigserial primary key,
    user_id      bigint not null references public."user" (id) on delete cascade,
    name         varchar(80) not null,
    query_string text not null,
    created_at   timestamp without time zone not null default now()
);

create index if not exists ix_saved_search_user_id
    on public.saved_search (user_id);

commit;

-- Check: the table exists (expect 1 row).
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name = 'saved_search';
