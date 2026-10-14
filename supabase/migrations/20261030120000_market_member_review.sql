-- D.A.R.T Market$: the D.A.R.T Admin verifies OR rejects vendors and buyers (with a reason);
-- rejected members fix their details and resubmit. Only ADDS columns. Safe to run more than once.

begin;

alter table public."user" add column if not exists market_status varchar(12);
alter table public."user" add column if not exists market_reject_reason varchar(300);
alter table public."user" add column if not exists market_reviewed_at timestamp;
alter table public."user" add column if not exists market_resubmitted_at timestamp;
alter table public."user" add column if not exists market_resubmit_note varchar(300);

-- Vendors already verified keep their badge.
update public."user" set market_status = 'verified'
where role in ('Vendor', 'Buyer') and vendor_verified_at is not null and market_status is null;

commit;

-- Check: should return 5 rows.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'user' and column_name like 'market_%'
  and column_name in ('market_status', 'market_reject_reason', 'market_reviewed_at', 'market_resubmitted_at', 'market_resubmit_note');
