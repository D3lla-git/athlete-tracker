-- Individual athletes (no team) need no new columns: User.athlete_category = 'Individual Athlete',
-- User.school = '' and records in the new 'Unofficial Game' competition.
-- Awards: Highlight / Goal / Assist ... of the Week / Month. Only ADDS tables. Safe to run more than once.

begin;

create table if not exists public.award_contest (
    id bigserial primary key,
    award varchar(30) not null,
    period varchar(10) not null,
    sport varchar(20),
    title varchar(120) not null,
    opens_at timestamp not null,
    entry_deadline timestamp not null,
    voting_ends_at timestamp not null,
    winner_entry_id bigint,
    decided_at timestamp,
    created_by bigint,
    created_at timestamp not null default now(),
    constraint chk_award_contest_dates check (opens_at < entry_deadline and entry_deadline < voting_ends_at)
);

create table if not exists public.award_entry (
    id bigserial primary key,
    contest_id bigint not null references public.award_contest(id) on delete cascade,
    athlete_id bigint not null references public."user"(id) on delete cascade,
    highlight_id bigint not null references public.athlete_highlight(id) on delete cascade,
    note varchar(150),
    status varchar(12) not null default 'pending',
    created_at timestamp not null default now(),
    reviewed_at timestamp,
    constraint uq_award_entry_contest_athlete unique (contest_id, athlete_id)
);
create index if not exists ix_award_entry_contest_id on public.award_entry (contest_id);
create index if not exists ix_award_entry_athlete_id on public.award_entry (athlete_id);

create table if not exists public.award_vote (
    id bigserial primary key,
    contest_id bigint not null references public.award_contest(id) on delete cascade,
    entry_id bigint not null references public.award_entry(id) on delete cascade,
    voter_id bigint not null references public."user"(id) on delete cascade,
    created_at timestamp not null default now(),
    constraint uq_award_vote_contest_voter unique (contest_id, voter_id)
);
create index if not exists ix_award_vote_contest_id on public.award_vote (contest_id);
create index if not exists ix_award_vote_entry_id on public.award_vote (entry_id);

alter table public.award_contest enable row level security;
alter table public.award_entry enable row level security;
alter table public.award_vote enable row level security;

commit;

-- Check: should return 3 rows.
select table_name from information_schema.tables
 where table_schema = 'public' and table_name in ('award_contest', 'award_entry', 'award_vote');
