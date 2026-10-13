-- D.A.R.T Market$: cart, vendor business cards (person / business), vendor verification.
-- Vendor Premium uses the existing subscription table (plan_code 'vendor_pro').
-- Only ADDS columns and a table. Safe to run more than once.

begin;

alter table public."user" add column if not exists vendor_type varchar(10);
alter table public."user" add column if not exists business_address varchar(200);
alter table public."user" add column if not exists business_location varchar(80);
alter table public."user" add column if not exists vendor_verified_at timestamp;

create table if not exists public.market_cart_item (
    id bigserial primary key,
    user_id bigint not null references public."user"(id) on delete cascade,
    listing_id bigint not null references public.market_listing(id) on delete cascade,
    quantity integer not null default 1 check (quantity between 1 and 50),
    size varchar(15),
    color varchar(15),
    created_at timestamp not null default now(),
    constraint uq_market_cart_user_listing unique (user_id, listing_id)
);
create index if not exists ix_market_cart_item_user_id on public.market_cart_item (user_id);
create index if not exists ix_market_cart_item_listing_id on public.market_cart_item (listing_id);
alter table public.market_cart_item enable row level security;

commit;

-- Check: should return 1 row.
select table_name from information_schema.tables where table_schema = 'public' and table_name = 'market_cart_item';
