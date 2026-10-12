-- Notifications (the bell): approvals of a user's own profile / records / account.
-- Only ADDS a table. Safe to run more than once.

begin;

create table if not exists public.notification (
    id bigserial primary key,
    user_id bigint not null references public."user"(id) on delete cascade,
    kind varchar(30) not null,
    message varchar(300) not null,
    link varchar(255),
    created_at timestamp not null default now(),
    read_at timestamp
);
create index if not exists ix_notification_user_id on public.notification (user_id);
create index if not exists ix_notification_created_at on public.notification (created_at);
-- Fast unread count for the bell.
create index if not exists ix_notification_user_unread on public.notification (user_id) where read_at is null;

alter table public.notification enable row level security;

commit;

-- Check: should return 1 row.
select table_name from information_schema.tables where table_schema = 'public' and table_name = 'notification';
