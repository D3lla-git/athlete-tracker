-- Phase 1 of fixture-driven records:
--   * National ID number (NIN) for athletes / coaches - encrypted, only the D.A.R.T Admin can read it - and age verification
--   * Federation accounts (MYS, LFA, LBA, LKF, ISSA): registered teams per season
--   * Fixtures and who must confirm them (each team's coaches, referee, linesmen)
-- Only ADDS columns / tables. Safe to run more than once.

begin;

alter table public."user" add column if not exists nin_cipher text;
alter table public."user" add column if not exists nin_hash varchar(64);
alter table public."user" add column if not exists nin_added_at timestamp;
alter table public."user" add column if not exists age_verified_at timestamp;
alter table public."user" add column if not exists age_verified_by bigint;
alter table public."user" add column if not exists age_verified_source varchar(20);
alter table public."user" add column if not exists age_check_note varchar(200);
alter table public."user" add column if not exists federation_code varchar(8);
create unique index if not exists uq_user_nin_hash on public."user" (nin_hash) where nin_hash is not null;
create unique index if not exists uq_user_federation_code on public."user" (federation_code) where federation_code is not null;

create table if not exists public.federation_team (
    id           bigserial primary key,
    body         varchar(8) not null check (body in ('MYS', 'LFA', 'LBA', 'LKF', 'ISSA')),
    season       varchar(9) not null,
    sport        varchar(20) not null check (sport in ('Football', 'Basketball', 'Kickball')),
    competition  varchar(80) not null,
    name         varchar(150) not null,
    name_key     varchar(150) not null,
    created_by   bigint,
    created_at   timestamp not null default now(),
    constraint uq_federation_team unique (body, season, sport, name_key)
);
create index if not exists ix_federation_team_body on public.federation_team (body);
create index if not exists ix_federation_team_season on public.federation_team (season);

create table if not exists public.fixture (
    id            bigserial primary key,
    body          varchar(8) check (body is null or body in ('MYS', 'LFA', 'LBA', 'LKF', 'ISSA')),
    season        varchar(9) not null,
    sport         varchar(20) not null check (sport in ('Football', 'Basketball', 'Kickball')),
    competition   varchar(80) not null,
    home_team     varchar(150) not null,
    away_team     varchar(150) not null,
    kickoff_at    timestamp not null,
    venue         varchar(150) not null,
    status        varchar(12) not null default 'scheduled' check (status in ('requested', 'scheduled', 'rejected', 'cancelled')),
    note          varchar(300),
    requested_by  bigint,
    created_by    bigint,
    approved_by   bigint,
    approved_at   timestamp,
    created_at    timestamp not null default now()
);
create index if not exists ix_fixture_body on public.fixture (body);
create index if not exists ix_fixture_kickoff_at on public.fixture (kickoff_at);
create index if not exists ix_fixture_status on public.fixture (status);

create table if not exists public.fixture_party (
    id              bigserial primary key,
    fixture_id      bigint not null references public.fixture (id) on delete cascade,
    user_id         bigint not null references public."user" (id) on delete cascade,
    role            varchar(12) not null check (role in ('home_coach', 'away_coach', 'referee', 'linesman')),
    notified_at     timestamp,
    confirmed_at    timestamp,
    declined_at     timestamp,
    decline_reason  varchar(200),
    constraint uq_fixture_party unique (fixture_id, user_id, role)
);
create index if not exists ix_fixture_party_fixture_id on public.fixture_party (fixture_id);
create index if not exists ix_fixture_party_user_id on public.fixture_party (user_id);

-- Only the app's server connection reads/writes these (no public API access).
alter table public.federation_team enable row level security;
alter table public.fixture enable row level security;
alter table public.fixture_party enable row level security;

commit;
