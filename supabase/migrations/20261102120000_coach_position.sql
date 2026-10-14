-- Coaches: Head coach / Assistant coach (assistants attest only their own team's records).
-- Organization Pro prices by ownership need no database change (plans live in the app).
-- Only ADDS a column. Safe to run more than once.

alter table public."user" add column if not exists coach_position varchar(20);

-- Existing coaches are head coaches.
update public."user" set coach_position = 'Head coach' where role = 'Coach' and coach_position is null;

-- Check: should return 1 row.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'user' and column_name = 'coach_position';
