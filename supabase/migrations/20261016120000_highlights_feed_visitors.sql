-- Highlights feed: anyone can react (members AND visitors), Super Admin analysis.
--  * Likes, reposts, saves and comments can belong to a verified member
--    (user_id) or an anonymous visitor (visitor_key from a cookie).
--  * ip_hash: one-way hash of a visitor's IP, only used to slow down spam.
--  * highlight_share: who shared what (once per member / visitor).
-- Run AFTER 20261015120000_highlights_feed.sql. Safe to run more than once.

begin;

-- 1) Likes / reposts / saves: member or visitor
do $$
declare t text;
begin
  foreach t in array array['highlight_like', 'highlight_repost', 'highlight_save'] loop
    execute format('alter table public.%I alter column user_id drop not null', t);
    execute format('alter table public.%I add column if not exists visitor_key varchar(64)', t);
    execute format('alter table public.%I add column if not exists ip_hash varchar(32)', t);
    execute format('create unique index if not exists uq_%s_visitor on public.%I (highlight_id, visitor_key)', t, t);
    execute format('create index if not exists ix_%s_visitor_key on public.%I (visitor_key)', t, t);
    execute format('create index if not exists ix_%s_created_at on public.%I (created_at)', t, t);
    begin
      execute format('alter table public.%I add constraint ck_%s_who check ((user_id is null) <> (visitor_key is null))', t, t);
    exception when duplicate_object then null;
    end;
  end loop;
end $$;

-- 2) Comments: member or visitor (with the name they typed)
alter table public.highlight_comment alter column user_id drop not null;
alter table public.highlight_comment add column if not exists visitor_key varchar(64);
alter table public.highlight_comment add column if not exists visitor_name varchar(40);
alter table public.highlight_comment add column if not exists ip_hash varchar(32);
create index if not exists ix_highlight_comment_visitor_key on public.highlight_comment (visitor_key);
create index if not exists ix_highlight_comment_created_at on public.highlight_comment (created_at);
do $$
begin
  alter table public.highlight_comment add constraint ck_highlight_comment_who check ((user_id is null) <> (visitor_key is null));
exception when duplicate_object then null;
end $$;

-- 3) Shares
create table if not exists public.highlight_share (
    id            bigserial primary key,
    highlight_id  bigint not null references public.athlete_highlight (id) on delete cascade,
    user_id       bigint references public."user" (id) on delete cascade,
    visitor_key   varchar(64),
    ip_hash       varchar(32),
    created_at    timestamp without time zone not null default now(),
    constraint ck_highlight_share_who check ((user_id is null) <> (visitor_key is null))
);
create index if not exists ix_highlight_share_highlight_id on public.highlight_share (highlight_id);
create index if not exists ix_highlight_share_user_id on public.highlight_share (user_id);
create index if not exists ix_highlight_share_visitor_key on public.highlight_share (visitor_key);
create index if not exists ix_highlight_share_created_at on public.highlight_share (created_at);
alter table public.highlight_share enable row level security;

commit;

-- Check: should return 13 rows.
select table_name, column_name from information_schema.columns
 where table_schema = 'public'
   and ((table_name in ('highlight_like', 'highlight_repost', 'highlight_save', 'highlight_comment')
         and column_name in ('visitor_key', 'ip_hash'))
     or (table_name = 'highlight_comment' and column_name = 'visitor_name')
     or (table_name = 'highlight_share' and column_name in ('visitor_key', 'ip_hash', 'user_id', 'highlight_id')))
 order by table_name, column_name;
