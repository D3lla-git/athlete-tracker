-- Optional email at registration (verified within 2 business days), Buyer accounts
-- (role 'Buyer', no new columns), and live users (members + visitors).
-- Only ADDS columns / a table. Safe to run more than once.

begin;

-- Email verification + deadline
alter table public."user" add column if not exists email_verified_at timestamp;
alter table public."user" add column if not exists email_verify_token varchar(64);
alter table public."user" add column if not exists email_verify_sent_at timestamp;
alter table public."user" add column if not exists email_due_at timestamp;
create index if not exists ix_user_email_verify_token on public."user" (email_verify_token);

-- Existing accounts that already gave an email are treated as verified (they are not asked again).
update public."user" set email_verified_at = coalesce(created_at, now())
where email is not null and email <> '' and email_verified_at is null;
-- Existing accounts WITHOUT an email: 2 business days from today to add one.
update public."user" set email_due_at = now() + (case extract(dow from now())::int
        when 4 then 4   -- Thursday -> Monday
        when 5 then 4   -- Friday   -> Tuesday
        when 6 then 3   -- Saturday -> Tuesday
        else 2 end) * interval '1 day'
where (email is null or email = '') and email_due_at is null and role <> 'System';
-- Empty emails become NULL (several accounts can have no email).
update public."user" set email = null where email = '';

-- Live users: one row per browser session (random id), touched at most once a minute.
create table if not exists public.site_presence (
    sid varchar(32) primary key,
    user_id bigint references public."user"(id) on delete cascade,
    first_seen timestamp not null default now(),
    last_seen timestamp not null default now()
);
create index if not exists ix_site_presence_last_seen on public.site_presence (last_seen);
create index if not exists ix_site_presence_user_id on public.site_presence (user_id);
alter table public.site_presence enable row level security;

commit;

-- Check: should return 4 rows, then 1 row.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'user'
  and column_name in ('email_verified_at', 'email_verify_token', 'email_verify_sent_at', 'email_due_at');
select table_name from information_schema.tables where table_schema = 'public' and table_name = 'site_presence';
