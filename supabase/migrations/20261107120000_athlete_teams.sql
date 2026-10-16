-- Athletes who play for more than one team (e.g. a school and a club): the other team's head coach confirms.
-- Only CREATES a new table. Safe to run more than once.

begin;

create table if not exists public.athlete_team (
    id          bigserial primary key,
    athlete_id  bigint not null references public."user" (id) on delete cascade,
    team_name   varchar(150) not null,
    team_key    varchar(150) not null,
    status      varchar(10) not null default 'pending' check (status in ('pending', 'approved', 'declined')),
    decided_by  bigint,
    decided_at  timestamp,
    created_at  timestamp not null default now(),
    constraint uq_athlete_team unique (athlete_id, team_key)
);
create index if not exists ix_athlete_team_athlete_id on public.athlete_team (athlete_id);
create index if not exists ix_athlete_team_team_key on public.athlete_team (team_key);

alter table public.athlete_team enable row level security;

commit;
