-- Phase 2 of fixture-driven records:
--   * athletes' records come from confirmed fixtures ("I played in this game")
--   * team sheets (each team's coach), the main referee's report, cross-signatures per record
--   * D.A.R.T Reps (role 'Rep', an O-D.A.R.T user type that attests records) - no new column needed
-- Only ADDS columns / tables. Safe to run more than once.

begin;

alter table public.sport_record add column if not exists fixture_id bigint;
alter table public.sport_record add column if not exists queen_of_the_match integer default 0;
alter table public.sport_record add column if not exists dunks integer default 0;
alter table public.sport_record add column if not exists passes_completed integer;
create index if not exists ix_sport_record_fixture_id on public.sport_record (fixture_id);

alter table public.fixture add column if not exists players_notified_at timestamp;
alter table public.fixture add column if not exists referee_report_at timestamp;
alter table public.fixture add column if not exists referee_report_by bigint;

create table if not exists public.fixture_sheet (
    id                    bigserial primary key,
    fixture_id            bigint not null references public.fixture (id) on delete cascade,
    side                  varchar(4) not null check (side in ('home', 'away')),
    coach_id              bigint not null,
    competition_category  varchar(50) not null,
    venue                 varchar(150) not null,
    submitted_at          timestamp not null default now(),
    constraint uq_fixture_sheet unique (fixture_id, side)
);
create index if not exists ix_fixture_sheet_fixture_id on public.fixture_sheet (fixture_id);

create table if not exists public.fixture_sheet_line (
    id          bigserial primary key,
    sheet_id    bigint not null references public.fixture_sheet (id) on delete cascade,
    athlete_id  bigint not null references public."user" (id) on delete cascade,
    played      boolean not null default false,
    mvp         boolean not null default false,
    minutes     integer check (minutes is null or minutes between 0 and 130),
    position    varchar(50),
    passes      integer check (passes is null or passes between 0 and 500),
    cut_base    integer check (cut_base is null or cut_base between 0 and 30),
    constraint uq_fixture_sheet_line unique (sheet_id, athlete_id)
);
create index if not exists ix_fixture_sheet_line_sheet_id on public.fixture_sheet_line (sheet_id);
create index if not exists ix_fixture_sheet_line_athlete_id on public.fixture_sheet_line (athlete_id);

create table if not exists public.fixture_discipline (
    id            bigserial primary key,
    fixture_id    bigint not null references public.fixture (id) on delete cascade,
    athlete_id    bigint not null references public."user" (id) on delete cascade,
    referee_id    bigint not null,
    yellow_cards  integer not null default 0 check (yellow_cards between 0 and 2),
    red_cards     integer not null default 0 check (red_cards between 0 and 1),
    fouls         integer not null default 0 check (fouls between 0 and 30),
    sent_off      integer not null default 0 check (sent_off between 0 and 1),
    created_at    timestamp not null default now(),
    constraint uq_fixture_discipline unique (fixture_id, athlete_id)
);
create index if not exists ix_fixture_discipline_fixture_id on public.fixture_discipline (fixture_id);
create index if not exists ix_fixture_discipline_athlete_id on public.fixture_discipline (athlete_id);

create table if not exists public.record_check (
    id          bigserial primary key,
    record_id   integer not null references public.sport_record (id) on delete cascade,
    user_id     bigint references public."user" (id) on delete set null,
    role        varchar(16) not null check (role in ('team_coach', 'opponent_coach', 'referee', 'rep')),
    decision    varchar(10) not null check (decision in ('confirmed', 'disputed')),
    note        varchar(300),
    created_at  timestamp not null default now(),
    constraint uq_record_check unique (record_id, role)
);
create index if not exists ix_record_check_record_id on public.record_check (record_id);

alter table public.fixture_sheet enable row level security;
alter table public.fixture_sheet_line enable row level security;
alter table public.fixture_discipline enable row level security;
alter table public.record_check enable row level security;

commit;
