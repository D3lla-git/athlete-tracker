-- Club League division chosen at registration (Club League / All Athlete).
-- Only ADDS a column. Safe to run more than once.
begin;
alter table public."user" add column if not exists club_division varchar(20);
commit;

-- Check: should return 1 row.
select column_name from information_schema.columns where table_schema = 'public' and table_name = 'user' and column_name = 'club_division';
