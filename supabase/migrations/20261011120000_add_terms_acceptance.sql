-- Mandatory Terms of Use / Privacy Policy acceptance.
-- Every user (athlete, coach, scout, organization) must tick "I agree"
-- before using D.A.R.T.; each acceptance is kept as a permanent record.

begin;

alter table public."user" add column if not exists terms_version varchar(20);
alter table public."user" add column if not exists terms_accepted_at timestamp without time zone;

create table if not exists public.terms_acceptance (
    id            bigserial primary key,
    user_id       bigint not null references public."user" (id) on delete cascade,
    terms_version varchar(20) not null,
    role          varchar(20) not null,
    full_name     varchar(150) not null,
    documents     varchar(120) not null,
    accepted_at   timestamp without time zone not null default now(),
    ip_address    varchar(64),
    user_agent    varchar(300),
    constraint uq_terms_acceptance_user_version unique (user_id, terms_version)
);
create index if not exists ix_terms_acceptance_user_id on public.terms_acceptance (user_id);
create index if not exists ix_terms_acceptance_accepted_at on public.terms_acceptance (accepted_at);

-- The app connects to Postgres directly; RLS only blocks the public
-- Supabase REST API (anon key) from this table.
alter table public.terms_acceptance enable row level security;

commit;

-- Check: expect 3 rows.
select 'user.terms_version' as item from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'terms_version'
union all
select 'user.terms_accepted_at' from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'terms_accepted_at'
union all
select 'terms_acceptance table' from information_schema.tables
 where table_schema = 'public' and table_name = 'terms_acceptance';
