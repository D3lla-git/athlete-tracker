-- Usernames (@handles) for athletes, coaches and scouts.
-- Two people can share a full name, never a username. Stored in lowercase;
-- the unique index also ignores capitals, just in case.
-- Existing accounts get NULL and choose one the next time they sign in.
-- Safe to run more than once.

begin;
alter table public."user" add column if not exists username varchar(20);
alter table public."user" add column if not exists username_changed_at timestamp without time zone;
create unique index if not exists uq_user_username_lower on public."user" (lower(username));
create index if not exists ix_user_username on public."user" (username);
-- Two people may now share a full name, even on the same team (the username
-- tells them apart; emails stay unique), so the old name + team rule goes.
alter table public."user" drop constraint if exists unique_student_school;
drop index if exists public.unique_student_school;
commit;

-- Check: should return 2 rows.
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name in ('username', 'username_changed_at');
