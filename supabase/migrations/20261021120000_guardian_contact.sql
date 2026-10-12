-- Athletes under 17: parent / guardian contact + safeguarding notice confirmation.
-- Only ADDS columns. Safe to run more than once.

begin;
alter table public."user" add column if not exists guardian_name varchar(100);
alter table public."user" add column if not exists guardian_relationship varchar(30);
alter table public."user" add column if not exists guardian_phone varchar(20);
alter table public."user" add column if not exists guardian_updated_at timestamp;
alter table public."user" add column if not exists minor_notice_ack_at timestamp;
commit;

-- Check: should return 5 rows.
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'user'
   and column_name in ('guardian_name', 'guardian_relationship', 'guardian_phone', 'guardian_updated_at', 'minor_notice_ack_at');
