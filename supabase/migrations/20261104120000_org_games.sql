-- Organization Pro: games, line-ups (lock 5 minutes before kick-off, confirmed by the D.A.R.T Admin) and results.
-- Only CREATES a new table. Safe to run more than once.

begin;

create table if not exists public.org_game (
    id                   bigserial primary key,
    org_id               bigint not null references public.organization_profile (id) on delete cascade,
    sport                varchar(20) not null check (sport in ('Football', 'Basketball', 'Kickball')),
    competition          varchar(60) not null,
    opponent             varchar(150) not null,
    is_home              boolean not null default true,
    kickoff_at           timestamp not null,
    venue                varchar(150) not null,
    capacity             integer check (capacity is null or capacity between 0 and 200000),
    lineup               text,
    lineup_saved_at      timestamp,
    lineup_confirmed_at  timestamp,
    lineup_confirmed_by  bigint,
    score_for            integer check (score_for is null or score_for >= 0),
    score_against        integer check (score_against is null or score_against >= 0),
    summary              varchar(120),
    yellow_cards         integer check (yellow_cards is null or yellow_cards >= 0),
    red_cards            integer check (red_cards is null or red_cards >= 0),
    fouls                integer check (fouls is null or fouls >= 0),
    gk_saves             integer check (gk_saves is null or gk_saves >= 0),
    standing_position    integer check (standing_position is null or standing_position >= 0),
    standing_played      integer check (standing_played is null or standing_played >= 0),
    standing_points      integer check (standing_points is null or standing_points >= 0),
    result_posted_at     timestamp,
    created_at           timestamp not null default now()
);

create index if not exists ix_org_game_org_id on public.org_game (org_id);
create index if not exists ix_org_game_kickoff_at on public.org_game (kickoff_at);

-- Only the app's server connection reads/writes it (no public API access).
alter table public.org_game enable row level security;

commit;
