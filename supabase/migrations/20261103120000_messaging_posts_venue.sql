-- Messaging (Cc/Bcc, reply, forward, delete for me, 1-minute edit), Market$ chat replies,
-- athletes edit posts, visitor device names, record venue, organization address + map.
-- O-D.A.R.T users are role 'Member' (no new column). Only ADDS columns. Safe to run more than once.

begin;

-- Inbox messages
alter table public.chat_message add column if not exists group_key varchar(32);
alter table public.chat_message add column if not exists header_line varchar(600);
alter table public.chat_message add column if not exists is_bcc boolean not null default false;
alter table public.chat_message add column if not exists reply_to_id bigint;
alter table public.chat_message add column if not exists forwarded boolean not null default false;
alter table public.chat_message add column if not exists edited_at timestamp;
alter table public.chat_message add column if not exists deleted_by_sender boolean not null default false;
alter table public.chat_message add column if not exists deleted_by_recipient boolean not null default false;
create index if not exists ix_chat_message_group_key on public.chat_message (group_key);

-- Market$ chat
alter table public.market_message add column if not exists reply_to_id bigint;
alter table public.market_message add column if not exists edited_at timestamp;
alter table public.market_message add column if not exists deleted_by_buyer boolean not null default false;
alter table public.market_message add column if not exists deleted_by_seller boolean not null default false;

-- Highlights: edited posts, visitor device on comments
alter table public.athlete_highlight add column if not exists edited_at timestamp;
alter table public.highlight_comment add column if not exists visitor_device varchar(40);

-- Records: venue (required on new records)
alter table public.sport_record add column if not exists venue varchar(150);

-- Organizations: physical address + map pin
alter table public.organization_profile add column if not exists address varchar(200);
alter table public.organization_profile add column if not exists map_lat double precision;
alter table public.organization_profile add column if not exists map_lng double precision;

commit;

-- Check: should return 19 rows.
select table_name, column_name from information_schema.columns
where table_schema = 'public' and (
     (table_name = 'chat_message' and column_name in ('group_key', 'header_line', 'is_bcc', 'reply_to_id', 'forwarded', 'edited_at', 'deleted_by_sender', 'deleted_by_recipient'))
  or (table_name = 'market_message' and column_name in ('reply_to_id', 'edited_at', 'deleted_by_buyer', 'deleted_by_seller'))
  or (table_name = 'athlete_highlight' and column_name = 'edited_at')
  or (table_name = 'highlight_comment' and column_name = 'visitor_device')
  or (table_name = 'sport_record' and column_name = 'venue')
  or (table_name = 'organization_profile' and column_name in ('address', 'map_lat', 'map_lng')));
