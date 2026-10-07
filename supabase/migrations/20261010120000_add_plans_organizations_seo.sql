-- ==========================================================
-- D.A.R.T.: Plans & payments, Organizations, Google listing
-- ==========================================================
-- 1. user.search_visibility: athlete's "Show my profile on Google" choice
--    (NULL = automatic: adults listed, under-18s not; 'on'; 'off').
-- 2. Billing tables (subscription, payment, entitlement) are created if
--    this database doesn't have them yet, and payment gets the fields used
--    to review mobile-money payments (plan, method, payer phone, reviewer).
-- 3. organization_profile + organization_roster_exclusion for Enterprise /
--    Institution / Academy accounts.
--
-- HOW TO RUN
--   Supabase Dashboard → SQL Editor → New query → paste → Run.
--
-- RUN THIS BEFORE STARTING/DEPLOYING the app code that uses it (the app
-- reads user.search_visibility on every user query).
-- Safe to run more than once.
-- ==========================================================

begin;

-- 1) Google listing choice
alter table public."user"
    add column if not exists search_visibility varchar(10);

-- 2) Billing
create table if not exists public.subscription (
    id                       bigserial primary key,
    user_id                  bigint not null references public."user" (id) on delete cascade,
    plan_code                varchar(80) not null,
    status                   varchar(30) not null default 'pending',
    provider                 varchar(50),
    provider_customer_id     varchar(255),
    provider_subscription_id varchar(255) unique,
    current_period_start     timestamp without time zone,
    current_period_end       timestamp without time zone,
    cancel_at_period_end     boolean not null default false,
    created_at               timestamp without time zone not null default now(),
    updated_at               timestamp without time zone not null default now()
);
create index if not exists ix_subscription_user_id on public.subscription (user_id);
create index if not exists ix_subscription_plan_code on public.subscription (plan_code);
create index if not exists ix_subscription_status on public.subscription (status);

create table if not exists public.payment (
    id                  bigserial primary key,
    user_id             bigint not null references public."user" (id) on delete cascade,
    subscription_id     bigint references public.subscription (id) on delete set null,
    provider            varchar(50) not null,
    provider_payment_id varchar(255) unique,
    payment_reference   varchar(120) not null unique,
    amount              numeric(12, 2) not null,
    currency            varchar(10) not null default 'USD',
    status              varchar(30) not null default 'pending',
    description         varchar(255),
    failure_reason      varchar(255),
    paid_at             timestamp without time zone,
    created_at          timestamp without time zone not null default now(),
    updated_at          timestamp without time zone not null default now()
);
create index if not exists ix_payment_user_id on public.payment (user_id);
create index if not exists ix_payment_status on public.payment (status);

alter table public.payment add column if not exists plan_code varchar(80);
alter table public.payment add column if not exists payment_method varchar(30);
alter table public.payment add column if not exists payer_phone varchar(30);
alter table public.payment add column if not exists reviewed_by bigint references public."user" (id) on delete set null;
alter table public.payment add column if not exists reviewed_at timestamp without time zone;
create index if not exists ix_payment_plan_code on public.payment (plan_code);

create table if not exists public.entitlement (
    id               bigserial primary key,
    user_id          bigint not null references public."user" (id) on delete cascade,
    entitlement_code varchar(100) not null,
    status           varchar(30) not null default 'active',
    source           varchar(50) not null default 'subscription',
    source_reference varchar(255),
    starts_at        timestamp without time zone not null default now(),
    expires_at       timestamp without time zone,
    created_at       timestamp without time zone not null default now(),
    updated_at       timestamp without time zone not null default now()
);
create index if not exists ix_entitlement_user_id on public.entitlement (user_id);
create index if not exists ix_entitlement_code on public.entitlement (entitlement_code);

-- 3) Organizations
create table if not exists public.organization_profile (
    id                  bigserial primary key,
    user_id             bigint not null unique references public."user" (id) on delete cascade,
    org_name            varchar(150) not null,
    org_type            varchar(50) not null,
    sports              varchar(200),
    country             varchar(100),
    city                varchar(100),
    website             varchar(255),
    contact_name        varchar(150) not null,
    contact_title       varchar(100),
    contact_phone       varchar(30),
    document_path       varchar(255),
    approved_aliases    text,
    requested_aliases   text,
    verification_status varchar(20) not null default 'pending',
    verified_at         timestamp without time zone,
    terms_version       varchar(20),
    terms_accepted_at   timestamp without time zone,
    created_at          timestamp without time zone not null default now()
);
create index if not exists ix_organization_profile_status on public.organization_profile (verification_status);

create table if not exists public.organization_roster_exclusion (
    id                   bigserial primary key,
    organization_user_id bigint not null references public."user" (id) on delete cascade,
    athlete_id           bigint not null references public."user" (id) on delete cascade,
    created_at           timestamp without time zone not null default now(),
    constraint uq_org_roster_exclusion unique (organization_user_id, athlete_id)
);
create index if not exists ix_org_roster_exclusion_org on public.organization_roster_exclusion (organization_user_id);
create index if not exists ix_org_roster_exclusion_athlete on public.organization_roster_exclusion (athlete_id);

-- The app connects to Postgres directly; RLS only blocks the public
-- Supabase REST API (anon key) from these tables.
alter table public.subscription enable row level security;
alter table public.payment enable row level security;
alter table public.entitlement enable row level security;
alter table public.organization_profile enable row level security;
alter table public.organization_roster_exclusion enable row level security;

commit;

-- Check: expect 7 rows.
select 'user.search_visibility' as item from information_schema.columns
 where table_schema = 'public' and table_name = 'user' and column_name = 'search_visibility'
union all
select 'payment.plan_code' from information_schema.columns
 where table_schema = 'public' and table_name = 'payment' and column_name = 'plan_code'
union all
select 'payment.reviewed_by' from information_schema.columns
 where table_schema = 'public' and table_name = 'payment' and column_name = 'reviewed_by'
union all
select table_name from information_schema.tables
 where table_schema = 'public' and table_name in ('subscription', 'entitlement', 'organization_profile', 'organization_roster_exclusion');
