-- Organizations: ownership (Government owned / Private-owned, local / international).
-- Only ADDS a column. Safe to run more than once.

alter table public.organization_profile add column if not exists ownership varchar(40);

-- Check: should return 1 row.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'organization_profile' and column_name = 'ownership';
