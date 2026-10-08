-- New athletes: the coach AND the organization of their team / school
-- (when it is on D.A.R.T.) approve the profile before it goes live.
-- Existing accounts keep org_approval NULL (old rule: coach only).

begin;

alter table public."user" add column if not exists coach_verified boolean;
alter table public."user" add column if not exists org_approval varchar(12);
alter table public."user" add column if not exists org_approval_by bigint;
alter table public."user" add column if not exists org_approval_name varchar(150);
alter table public."user" add column if not exists org_approved_at timestamp without time zone;
create index if not exists ix_user_org_approval on public."user" (org_approval_by, org_approval);

commit;

-- Check: expect 5 rows.
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'user'
   and column_name in ('coach_verified', 'org_approval', 'org_approval_by', 'org_approval_name', 'org_approved_at');
