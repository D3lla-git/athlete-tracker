-- Highlights: one post can hold several photos / videos (swipe through them).
-- Files posted together share a post_key; post_position is their order.
-- Existing highlights stay single posts (post_key NULL). Safe to run more than once.

begin;
alter table public.athlete_highlight add column if not exists post_key varchar(32);
alter table public.athlete_highlight add column if not exists post_position smallint not null default 0;
create index if not exists ix_athlete_highlight_post_key on public.athlete_highlight (post_key);
commit;

-- Check: should return 2 rows.
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'athlete_highlight' and column_name in ('post_key', 'post_position');
