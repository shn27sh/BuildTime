-- BuildTime — Supabase schema
-- Run this once in your Supabase project: Project > SQL Editor > New query > Run.
-- It mirrors the local SQLite tables so sync is a straightforward 1:1 upsert.
--
-- Already ran an older version of this file before "comment" existed?
-- Just run this one line once instead of the whole script:
--   alter table sessions add column comment text default '';

create table if not exists sessions (
    id uuid primary key,
    table_name text not null,
    date date not null,
    start_time timestamptz not null,
    end_time timestamptz,
    duration_seconds integer,
    hourly_rate numeric,
    snacks_text text,
    drinks_text text,
    items_cost numeric,
    duration_cost numeric,
    total_cost numeric,
    received_amount numeric,
    comment text default '',
    synced_at timestamptz default now()
);

create table if not exists session_items (
    id uuid primary key,
    session_id uuid references sessions(id),
    item_name text,
    category text,
    unit_price numeric,
    quantity integer,
    subtotal numeric
);

-- Recommended for a single-business internal tool: use the "service_role"
-- key (Project Settings > API) in the app's Cloud Sync settings. It bypasses
-- Row Level Security entirely, which is fine here because only your own
-- desktop app(s) hold that key — just don't publish or share it publicly.
--
-- If you'd rather use the public "anon" key instead, enable RLS and add
-- permissive policies so that key is allowed to read/write:
--
-- alter table sessions enable row level security;
-- alter table session_items enable row level security;
-- create policy "allow anon key" on sessions for all using (true) with check (true);
-- create policy "allow anon key" on session_items for all using (true) with check (true);
