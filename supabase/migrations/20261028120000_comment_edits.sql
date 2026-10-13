-- Highlights comments: writers can edit their comment; edited ones show "edited".
-- Only ADDS a column. Safe to run more than once.

alter table public.highlight_comment add column if not exists edited_at timestamp;

-- Check: should return 1 row.
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'highlight_comment' and column_name = 'edited_at';
