-- D.A.R.T Market$ (sports gear marketplace) + D.A.R.T Predictions (free game).
-- Only ADDS columns and tables. Safe to run more than once.

begin;

-- Accounts: market ban + vendor shop name. (Vendors are role 'Vendor'.)
alter table public."user" add column if not exists market_banned boolean not null default false;
alter table public."user" add column if not exists shop_name varchar(80);

create table if not exists public.market_listing (
    id bigserial primary key,
    seller_id bigint not null references public."user"(id) on delete cascade,
    title varchar(80) not null,
    category varchar(30) not null,
    sport varchar(20) not null,
    brand varchar(40),
    condition varchar(10) not null default 'new',
    price numeric(10, 2) not null check (price > 0),
    currency varchar(3) not null default 'USD' check (currency in ('USD', 'LRD')),
    sizes varchar(120),
    colors varchar(120),
    description varchar(1500) not null,
    specs varchar(1000),
    location varchar(80),
    quantity integer not null default 1 check (quantity between 1 and 500),
    status varchar(12) not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'sold', 'removed')),
    review_note varchar(200),
    reviewed_by bigint,
    reviewed_at timestamp,
    views integer not null default 0,
    created_at timestamp not null default now(),
    updated_at timestamp not null default now()
);
create index if not exists ix_market_listing_seller_id on public.market_listing (seller_id);
create index if not exists ix_market_listing_status on public.market_listing (status);
create index if not exists ix_market_listing_category on public.market_listing (category);
create index if not exists ix_market_listing_sport on public.market_listing (sport);
create index if not exists ix_market_listing_created_at on public.market_listing (created_at);

create table if not exists public.market_image (
    id bigserial primary key,
    listing_id bigint not null references public.market_listing(id) on delete cascade,
    storage_path varchar(255) not null,
    bucket varchar(40) not null,
    position integer not null default 0
);
create index if not exists ix_market_image_listing_id on public.market_image (listing_id);

create table if not exists public.market_thread (
    id bigserial primary key,
    listing_id bigint not null references public.market_listing(id) on delete cascade,
    buyer_id bigint not null references public."user"(id) on delete cascade,
    seller_id bigint not null references public."user"(id) on delete cascade,
    created_at timestamp not null default now(),
    last_message_at timestamp not null default now(),
    constraint uq_market_thread_listing_buyer unique (listing_id, buyer_id),
    constraint chk_market_thread_two_people check (buyer_id <> seller_id)
);
create index if not exists ix_market_thread_listing_id on public.market_thread (listing_id);
create index if not exists ix_market_thread_buyer_id on public.market_thread (buyer_id);
create index if not exists ix_market_thread_seller_id on public.market_thread (seller_id);
create index if not exists ix_market_thread_last_message_at on public.market_thread (last_message_at);

create table if not exists public.market_message (
    id bigserial primary key,
    thread_id bigint not null references public.market_thread(id) on delete cascade,
    sender_id bigint not null references public."user"(id) on delete cascade,
    body varchar(1000) not null,
    created_at timestamp not null default now(),
    read_at timestamp
);
create index if not exists ix_market_message_thread_id on public.market_message (thread_id);

create table if not exists public.market_report (
    id bigserial primary key,
    listing_id bigint not null references public.market_listing(id) on delete cascade,
    reporter_id bigint not null references public."user"(id) on delete cascade,
    reason varchar(30) not null,
    detail varchar(300),
    status varchar(12) not null default 'open',
    created_at timestamp not null default now(),
    constraint uq_market_report_listing_reporter unique (listing_id, reporter_id)
);
create index if not exists ix_market_report_listing_id on public.market_report (listing_id);

-- Predictions: free game (points only, no money).
create table if not exists public.prediction_question (
    id bigserial primary key,
    athlete_id bigint not null references public."user"(id) on delete cascade,
    sport varchar(20) not null,
    stat varchar(20) not null,
    line integer not null check (line between 0 and 100),
    match_date date not null,
    match_label varchar(120),
    closes_at timestamp not null,
    status varchar(10) not null default 'open' check (status in ('open', 'settled', 'void')),
    result_value integer,
    settled_at timestamp,
    created_by bigint,
    created_at timestamp not null default now()
);
create index if not exists ix_prediction_question_athlete_id on public.prediction_question (athlete_id);

create table if not exists public.prediction_pick (
    id bigserial primary key,
    question_id bigint not null references public.prediction_question(id) on delete cascade,
    user_id bigint not null references public."user"(id) on delete cascade,
    pick varchar(5) not null check (pick in ('yes', 'no')),
    points integer,
    created_at timestamp not null default now(),
    constraint uq_prediction_pick_question_user unique (question_id, user_id)
);
create index if not exists ix_prediction_pick_question_id on public.prediction_pick (question_id);
create index if not exists ix_prediction_pick_user_id on public.prediction_pick (user_id);

-- The app uses its own database role; nothing is exposed to the public Supabase API.
alter table public.market_listing enable row level security;
alter table public.market_image enable row level security;
alter table public.market_thread enable row level security;
alter table public.market_message enable row level security;
alter table public.market_report enable row level security;
alter table public.prediction_question enable row level security;
alter table public.prediction_pick enable row level security;

commit;

-- Check: should return 7 rows.
select table_name from information_schema.tables where table_schema = 'public'
   and table_name in ('market_listing', 'market_image', 'market_thread', 'market_message', 'market_report',
                      'prediction_question', 'prediction_pick');
