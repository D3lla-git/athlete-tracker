-- Highlights feed (Home | Highlights).
--  * New uploads need two approvals: the Super Admin and the coach of the
--    athlete's team (when that team has a coach on D.A.R.T.).
--  * Likes, reposts, saved posts (bookmarks), comments and share counts.
-- Safe to run more than once.

begin;

-- 1) Highlights: second approval + feed counters
alter table public.athlete_highlight add column if not exists team_name varchar(150);
alter table public.athlete_highlight add column if not exists admin_approved_at timestamp without time zone;
alter table public.athlete_highlight add column if not exists coach_approval varchar(12);
alter table public.athlete_highlight add column if not exists coach_reviewed_by bigint references public."user" (id) on delete set null;
alter table public.athlete_highlight add column if not exists coach_reviewed_at timestamp without time zone;
alter table public.athlete_highlight add column if not exists published_at timestamp without time zone;
alter table public.athlete_highlight add column if not exists like_count integer not null default 0;
alter table public.athlete_highlight add column if not exists repost_count integer not null default 0;
alter table public.athlete_highlight add column if not exists comment_count integer not null default 0;
alter table public.athlete_highlight add column if not exists save_count integer not null default 0;
alter table public.athlete_highlight add column if not exists share_count integer not null default 0;

-- Highlights that are already live keep showing (in the feed by the date they went live).
update public.athlete_highlight
   set published_at = coalesce(reviewed_at, created_at)
 where status = 'approved' and published_at is null;

create index if not exists ix_athlete_highlight_published_at on public.athlete_highlight (published_at);
create index if not exists ix_athlete_highlight_feed on public.athlete_highlight (status, published_at desc);
create index if not exists ix_athlete_highlight_coach_queue on public.athlete_highlight (coach_approval, status);

-- 2) Likes, reposts, saved posts: one row per (post, user)
create table if not exists public.highlight_like (
    id            bigserial primary key,
    highlight_id  bigint not null references public.athlete_highlight (id) on delete cascade,
    user_id       bigint not null references public."user" (id) on delete cascade,
    created_at    timestamp without time zone not null default now(),
    constraint uq_highlight_like unique (highlight_id, user_id)
);
create index if not exists ix_highlight_like_highlight_id on public.highlight_like (highlight_id);
create index if not exists ix_highlight_like_user_id on public.highlight_like (user_id);

create table if not exists public.highlight_repost (
    id            bigserial primary key,
    highlight_id  bigint not null references public.athlete_highlight (id) on delete cascade,
    user_id       bigint not null references public."user" (id) on delete cascade,
    created_at    timestamp without time zone not null default now(),
    constraint uq_highlight_repost unique (highlight_id, user_id)
);
create index if not exists ix_highlight_repost_highlight_id on public.highlight_repost (highlight_id);
create index if not exists ix_highlight_repost_user_id on public.highlight_repost (user_id);

create table if not exists public.highlight_save (
    id            bigserial primary key,
    highlight_id  bigint not null references public.athlete_highlight (id) on delete cascade,
    user_id       bigint not null references public."user" (id) on delete cascade,
    created_at    timestamp without time zone not null default now(),
    constraint uq_highlight_save unique (highlight_id, user_id)
);
create index if not exists ix_highlight_save_highlight_id on public.highlight_save (highlight_id);
create index if not exists ix_highlight_save_user_id on public.highlight_save (user_id);

-- 3) Comments
create table if not exists public.highlight_comment (
    id            bigserial primary key,
    highlight_id  bigint not null references public.athlete_highlight (id) on delete cascade,
    user_id       bigint not null references public."user" (id) on delete cascade,
    body          varchar(300) not null,
    created_at    timestamp without time zone not null default now()
);
create index if not exists ix_highlight_comment_highlight_id on public.highlight_comment (highlight_id);
create index if not exists ix_highlight_comment_user_id on public.highlight_comment (user_id);

-- Only the app's server reads/writes these tables (never the public API).
alter table public.highlight_like enable row level security;
alter table public.highlight_repost enable row level security;
alter table public.highlight_save enable row level security;
alter table public.highlight_comment enable row level security;

commit;

-- Check: should return 4 rows, then 11 rows.
select table_name from information_schema.tables
 where table_schema = 'public'
   and table_name in ('highlight_like', 'highlight_repost', 'highlight_save', 'highlight_comment');
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'athlete_highlight'
   and column_name in ('team_name', 'admin_approved_at', 'coach_approval', 'coach_reviewed_by', 'coach_reviewed_at',
                       'published_at', 'like_count', 'repost_count', 'comment_count', 'save_count', 'share_count');
