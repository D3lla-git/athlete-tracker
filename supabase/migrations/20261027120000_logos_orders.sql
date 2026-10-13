-- Organization team badge / logo (approved by the D.A.R.T Admin) and the
-- D.A.R.T Market$ order tracker (cart -> sent -> sold -> received).
-- Only ADDS columns. Safe to run more than once.

begin;

alter table public.organization_profile add column if not exists logo_path varchar(255);
alter table public.organization_profile add column if not exists logo_pending_path varchar(255);
alter table public.organization_profile add column if not exists logo_status varchar(12);
alter table public.organization_profile add column if not exists logo_note varchar(200);
alter table public.organization_profile add column if not exists logo_reviewed_at timestamp;

alter table public.market_cart_item add column if not exists status varchar(12) not null default 'cart';
alter table public.market_cart_item add column if not exists requested_at timestamp;
alter table public.market_cart_item add column if not exists sold_at timestamp;
alter table public.market_cart_item add column if not exists received_at timestamp;
alter table public.market_cart_item add column if not exists cancelled_at timestamp;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'chk_market_cart_item_status') then
    alter table public.market_cart_item add constraint chk_market_cart_item_status
      check (status in ('cart', 'requested', 'sold', 'received', 'cancelled'));
  end if;
end $$;
create index if not exists ix_market_cart_item_status on public.market_cart_item (status, requested_at);
create index if not exists ix_organization_profile_logo_status on public.organization_profile (logo_status);

commit;

-- Check: should return 10 rows.
select table_name, column_name from information_schema.columns
where table_schema = 'public'
  and ((table_name = 'organization_profile' and column_name like 'logo%')
    or (table_name = 'market_cart_item' and column_name in ('status', 'requested_at', 'sold_at', 'received_at', 'cancelled_at')));
