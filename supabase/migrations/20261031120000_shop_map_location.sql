-- Vendors pin their shop on the map: buyers get Google Maps directions.
-- Only ADDS columns. Safe to run more than once.

begin;

alter table public."user" add column if not exists shop_lat double precision;
alter table public."user" add column if not exists shop_lng double precision;
alter table public."user" add column if not exists shop_location_updated_at timestamp;

commit;

-- Check: should return 3 rows.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'user' and column_name in ('shop_lat', 'shop_lng', 'shop_location_updated_at');
