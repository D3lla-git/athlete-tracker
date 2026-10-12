-- Awards: everyone can vote (members AND visitors), ONE final vote per award.
-- Visitors are counted by their anonymous 'dart_vid' cookie (visitor_key) plus a
-- one-way hash of their IP (ip_hash, never the IP itself).
-- Run AFTER 20261022120000_individual_athletes_awards.sql. Safe to run more than once.

begin;

alter table public.award_vote alter column voter_id drop not null;
alter table public.award_vote add column if not exists visitor_key varchar(32);
alter table public.award_vote add column if not exists ip_hash varchar(32);
create index if not exists ix_award_vote_ip_hash on public.award_vote (ip_hash);

-- One vote per visitor cookie per award (members: uq_award_vote_contest_voter).
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'uq_award_vote_contest_visitor') then
        alter table public.award_vote add constraint uq_award_vote_contest_visitor unique (contest_id, visitor_key);
    end if;
    -- Every vote is either a member's or a visitor's.
    if not exists (select 1 from pg_constraint where conname = 'chk_award_vote_who') then
        alter table public.award_vote add constraint chk_award_vote_who check (voter_id is not null or visitor_key is not null);
    end if;
end $$;

commit;

-- Check: should return 3 rows.
select column_name, is_nullable from information_schema.columns
 where table_schema = 'public' and table_name = 'award_vote' and column_name in ('voter_id', 'visitor_key', 'ip_hash');
