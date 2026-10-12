-- Record integrity (anti-bribery): two independent sign-offs per record,
-- match officials ('Statistician' role) and their match reports, proof for
-- unusual numbers, coach fraud flags / licence revocation, phone numbers,
-- and a permanent, append-only, hash-chained verification history.
-- Only ADDS columns, tables, indexes and a trigger. Safe to run more than once.

begin;

-- ---------- user: phone, fraud flags, licence, official type ----------
alter table public."user" add column if not exists phone varchar(20);
alter table public."user" add column if not exists phone_verified_at timestamp;
alter table public."user" add column if not exists fraud_flags integer not null default 0;
alter table public."user" add column if not exists license_revoked boolean not null default false;
alter table public."user" add column if not exists license_revoked_at timestamp;
alter table public."user" add column if not exists official_type varchar(40);
-- One D.A.R.T. account per mobile number.
create unique index if not exists uq_user_phone on public."user" (phone) where phone is not null;

-- ---------- sport_record: sign-offs, disputes, proof ----------
alter table public.sport_record add column if not exists coach_approved_by bigint;
alter table public.sport_record add column if not exists coach_approved_at timestamp;
alter table public.sport_record add column if not exists countersign_target varchar(16);
alter table public.sport_record add column if not exists countersigned_by bigint;
alter table public.sport_record add column if not exists countersigned_at timestamp;
alter table public.sport_record add column if not exists countersign_role varchar(16);
alter table public.sport_record add column if not exists dispute_reason varchar(300);
alter table public.sport_record add column if not exists disputed_by bigint;
alter table public.sport_record add column if not exists disputed_at timestamp;
alter table public.sport_record add column if not exists anomaly_flags varchar(300);
alter table public.sport_record add column if not exists proof_required boolean not null default false;
alter table public.sport_record add column if not exists proof_path varchar(255);
alter table public.sport_record add column if not exists proof_highlight_id bigint;
alter table public.sport_record add column if not exists proof_uploaded_at timestamp;
create index if not exists ix_sport_record_status on public.sport_record (status);
create index if not exists ix_sport_record_coach_approved_by on public.sport_record (coach_approved_by);

-- ---------- permanent verification history ----------
create table if not exists public.record_verification_event (
    id bigserial primary key,
    record_id bigint not null,              -- no FK on purpose: the history outlives the record
    actor_id bigint,
    actor_role varchar(20),
    action varchar(30) not null,
    detail varchar(400),
    stats_hash varchar(64) not null,
    prev_hash varchar(64),
    event_hash varchar(64) not null unique,
    created_at timestamp not null default now()
);
create index if not exists ix_record_verification_event_record_id on public.record_verification_event (record_id);
create index if not exists ix_record_verification_event_actor_id on public.record_verification_event (actor_id);
create index if not exists ix_record_verification_event_created_at on public.record_verification_event (created_at);

-- Append-only: the database itself refuses to change or remove history.
create or replace function public.dart_history_is_permanent() returns trigger
language plpgsql as $$
begin
    raise exception 'record_verification_event is permanent: % is not allowed', tg_op;
end;
$$;
drop trigger if exists trg_record_verification_event_no_update on public.record_verification_event;
create trigger trg_record_verification_event_no_update
    before update or delete on public.record_verification_event
    for each row execute function public.dart_history_is_permanent();
drop trigger if exists trg_record_verification_event_no_truncate on public.record_verification_event;
create trigger trg_record_verification_event_no_truncate
    before truncate on public.record_verification_event
    for each statement execute function public.dart_history_is_permanent();

-- ---------- match officials' reports ----------
create table if not exists public.match_report (
    id bigserial primary key,
    official_id bigint not null references public."user"(id) on delete cascade,
    sport varchar(20) not null,
    match_date date not null,
    home_team varchar(150) not null,
    away_team varchar(150) not null,
    competition varchar(60),
    notes varchar(300),
    created_at timestamp not null default now()
);
create index if not exists ix_match_report_official_id on public.match_report (official_id);
create index if not exists ix_match_report_match_date on public.match_report (match_date);

create table if not exists public.match_report_line (
    id bigserial primary key,
    report_id bigint not null references public.match_report(id) on delete cascade,
    athlete_id bigint not null references public."user"(id) on delete cascade,
    goals integer not null default 0,
    assists integer not null default 0,
    points integer not null default 0,
    rebounds integer not null default 0,
    blocks integer not null default 0,
    home_runs integer not null default 0
);
create index if not exists ix_match_report_line_report_id on public.match_report_line (report_id);
create index if not exists ix_match_report_line_athlete_id on public.match_report_line (athlete_id);

-- The app connects with its own database role; nothing is exposed to the
-- public Supabase API (no policies = no access through anon / authenticated keys).
alter table public.record_verification_event enable row level security;
alter table public.match_report enable row level security;
alter table public.match_report_line enable row level security;

commit;

-- Check: should return 3 rows (the new tables) ...
select table_name from information_schema.tables
 where table_schema = 'public' and table_name in ('record_verification_event', 'match_report', 'match_report_line');
-- ... and 20 rows (the new columns).
select table_name, column_name from information_schema.columns
 where table_schema = 'public'
   and ((table_name = 'user' and column_name in ('phone', 'phone_verified_at', 'fraud_flags', 'license_revoked', 'license_revoked_at', 'official_type'))
     or (table_name = 'sport_record' and column_name in ('coach_approved_by', 'coach_approved_at', 'countersign_target', 'countersigned_by',
         'countersigned_at', 'countersign_role', 'dispute_reason', 'disputed_by', 'disputed_at', 'anomaly_flags', 'proof_required',
         'proof_path', 'proof_highlight_id', 'proof_uploaded_at')));
