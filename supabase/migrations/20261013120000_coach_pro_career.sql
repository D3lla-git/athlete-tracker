-- Coach Pro: teams a coach coaches (verified by the Super Admin), coaching
-- game records and trophies / awards (verified, then public for good).

begin;

create table if not exists public.coach_team (
    id              bigserial primary key,
    coach_id        bigint not null references public."user" (id) on delete cascade,
    team_name       varchar(150) not null,
    coach_category  varchar(50) not null,
    role            varchar(30) not null default 'Head Coach',
    status          varchar(12) not null default 'pending',
    is_primary      boolean not null default false,
    start_date      date,
    end_date        date,
    review_note     varchar(200),
    reviewed_by     bigint,
    reviewed_at     timestamp without time zone,
    created_at      timestamp without time zone not null default now()
);
create index if not exists ix_coach_team_coach_id on public.coach_team (coach_id);
create index if not exists ix_coach_team_status on public.coach_team (status);

create table if not exists public.coach_game_record (
    id              bigserial primary key,
    coach_id        bigint not null references public."user" (id) on delete cascade,
    team_name       varchar(150) not null,
    sport           varchar(20) not null,
    competition     varchar(60),
    game_date       date not null,
    season          integer not null,
    opponent        varchar(150) not null,
    result          varchar(5) not null,
    score_for       integer,
    score_against   integer,
    formation       varchar(12),
    substitutions   integer not null default 0,
    yellow_cards    integer not null default 0,
    red_cards       integer not null default 0,
    notes           varchar(200),
    status          varchar(12) not null default 'pending',
    review_note     varchar(200),
    reviewed_by     bigint,
    reviewed_at     timestamp without time zone,
    created_at      timestamp without time zone not null default now(),
    constraint ck_coach_game_result check (result in ('win', 'draw', 'loss'))
);
create index if not exists ix_coach_game_record_coach_id on public.coach_game_record (coach_id);
create index if not exists ix_coach_game_record_status on public.coach_game_record (status);

create table if not exists public.coach_achievement (
    id              bigserial primary key,
    coach_id        bigint not null references public."user" (id) on delete cascade,
    kind            varchar(10) not null,
    title           varchar(150) not null,
    competition     varchar(80),
    team_name       varchar(150),
    season          integer not null,
    status          varchar(12) not null default 'pending',
    review_note     varchar(200),
    reviewed_by     bigint,
    reviewed_at     timestamp without time zone,
    created_at      timestamp without time zone not null default now(),
    constraint ck_coach_achievement_kind check (kind in ('trophy', 'award'))
);
create index if not exists ix_coach_achievement_coach_id on public.coach_achievement (coach_id);
create index if not exists ix_coach_achievement_status on public.coach_achievement (status);

-- Faster search / dashboards (record team lookups, pins).
create index if not exists ix_sport_record_team_lower on public.sport_record (lower(trim(team)));
create index if not exists ix_sport_record_user_status on public.sport_record (user_id, status);
create index if not exists ix_pinned_sport_record_record on public.pinned_sport_record (sport_record_id);

-- The app connects to Postgres directly; RLS only blocks the public
-- Supabase REST API (anon key) from these tables.
alter table public.coach_team enable row level security;
alter table public.coach_game_record enable row level security;
alter table public.coach_achievement enable row level security;

commit;

-- Check: expect 3 rows.
select table_name from information_schema.tables
 where table_schema = 'public' and table_name in ('coach_team', 'coach_game_record', 'coach_achievement');
