# BuildTime

An offline-first Tkinter desktop app for a game center: one timer per
table, a configurable snack/drink catalog you can tap to add mid-session,
automatic cost calculation, a local SQLite database, and optional Supabase
cloud sync that never blocks the app and never uploads a duplicate.

## Features

- **Multiple simultaneous table timers** — start as many as you're running
  at once (6 by default; add, rename, reorder, or hide tables any time via
  **Settings ▸ Manage Tables**, no code changes needed).
- **Search box** — at the top of the main window, typing a table name or
  number (e.g. "10") instantly filters the grid down to matching tables,
  so you don't have to scroll through a long list to find one.
- **Snacks & drinks** — define your own items and prices in
  **Settings ▸ Snacks & Drinks**. While a table is running, each item shows
  up as its own row right at the bottom of that table's card (e.g.
  "+ Water (2.00)"  "x0"  "-") that counts quantity and cost live — no
  extra popup window, just tap directly on the card.
- **Automatic billing** — set an hourly rate once in **Settings ▸ Pricing**.
  Total cost = (sum of items) + (duration × hourly rate). An optional
  "round up to nearest N minutes" setting is available if you bill in
  fixed increments instead of exact time.
- **Optional comment at checkout** — after you press Stop, an optional
  "Comment" box sits between the Received amount and the Finish/Resume
  buttons for a quick note (e.g. "paid cash", "asked for a receipt").
  It's saved to the local database the moment you press Finish and syncs
  to Supabase along with everything else — leave it blank if there's
  nothing to note.
- **Crash-safe by design** — a session is written to the database the
  moment you press Start, and every item tap writes immediately too. If
  the app or the computer crashes mid-session, reopening the app finds it
  and puts the table right back where it was — nothing is lost.
- **Local SQLite database**, offline-first — every write lands locally
  first and the app is 100% usable with no internet connection, ever.
- **Optional Supabase cloud sync** — push records to the cloud on demand
  (**Sync Now**) or on a timer if you enable auto-sync. Each record carries
  a UUID it keeps forever, and syncing uses an upsert, so re-syncing the
  same record (e.g. after a dropped connection) can never create a
  duplicate row in Supabase.
- **History & Records** window — filter by day/week/month, see sync status
  per row, edit a "Received" amount or a "Comment" after the fact, and
  export to CSV.

## 1. Install

You'll need **Python 3.9+** with Tkinter, which ships with the standard
installer on Windows and macOS. On Debian/Ubuntu Linux it's a separate
package:

```bash
# Windows / macOS: already included with python.org's installer — nothing to do.
# Ubuntu/Debian:
sudo apt install python3-tk
```

Then, from the project folder:

```bash
pip install -r requirements.txt
```

That's the only third-party dependency (`requests`) — everything else
(Tkinter, sqlite3, uuid, csv…) is part of the Python standard library.

## 2. Run

```bash
python3 main.py
```

The local database is created automatically on first run at:

- Windows: `C:\Users\<you>\BuildTime\buildtime.db`
- macOS/Linux: `~/BuildTime/buildtime.db`

It comes pre-seeded with 6 example tables and a handful of example items
(Water, Soda, Tea, Chips, Chocolate Bar) — edit or replace all of these in
Settings; they're just placeholders so the app isn't empty on first launch.

## 3. Set up Supabase cloud sync (optional, do this whenever you're ready)

1. Create a free project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** in your project, paste in the contents of
   `supabase_schema.sql` (included here), and run it once. This creates the
   `sessions` and `session_items` tables that mirror the local database.
3. In your Supabase project, go to **Project Settings ▸ API** and copy:
   - **Project URL**
   - **service_role** key (recommended for this use case — see the note
     inside `supabase_schema.sql` about why, and the alternative if you'd
     rather use the public `anon` key with Row Level Security instead).
4. In the app: **Settings ▸ Cloud Sync**, paste both in, click
   **Test Connection**, then **Save**.
5. Click **Sync Now** whenever you want to push local records up — or turn
   on **automatic background sync** and set an interval if you'd rather it
   happen on its own.

If there's no internet, or Supabase is unreachable, sync fails silently
into a "try again later" state — it never interrupts the timers or
loses local data. The status bar and History window always show how many
records are still waiting to sync.

## How the data is organized

The local schema is normalized (a `sessions` table plus a `session_items`
table for line items) so arbitrary items and quantities are tracked
accurately — but a `session_summary` SQL view exposes exactly the flat
shape you described:

```
Date · Table · Start · End · Duration · Snacks · Drinks · Cost · Received
```

`Snacks` and `Drinks` in that view are generated on the fly from the
underlying items (e.g. `"Water x2, Chips x1"`), so the catalog can grow to
any number of items without changing the table shape. The `synced` column
is exactly the 0/1 you described: `0` the moment it's saved locally,
flipped to `1` only after a confirmed successful upload.

Prices and rates are **snapshotted onto each record** at the moment of the
sale — if you edit an item's price or the hourly rate later, past records
keep showing what the customer actually paid; only new sessions use the
new numbers.

## Assumptions I made (easy to change if any are wrong)

- **Currency is just a symbol you set** (default `$`) rather than a
  specific currency, so it's not tied to any particular country — change
  it any time in **Settings ▸ Pricing**.
- **"Received"** is stored as an editable amount (pre-filled with the
  total cost when you check out, but you can change it) rather than a
  simple yes/no, so it also covers partial payments or change given.
- **One global hourly rate** applies to all tables, not a different rate
  per table (per-table rates would be a natural follow-up if you ever
  need, e.g., a pricier VIP table).
- Duration billing is **exact by default** (no rounding); the optional
  "round up to nearest N minutes" setting is off unless you turn it on.
- Cloud sync is **manual by default** ("Sync Now" button) since you
  described wanting to sync "whenever internet is available and they
  wanted it" — automatic background sync is there as an opt-in toggle,
  not the default.

## Project layout

```
buildtime/
├── main.py                  # entry point
├── database.py               # local SQLite layer (offline-first, all writes go here first)
├── sync_manager.py           # optional Supabase upload (best-effort, never blocks the app)
├── ui/
│   ├── main_window.py        # menu bar, search box, status bar, grid of table cards
│   ├── table_card.py         # one table timer: idle → running → checkout,
│   │                          # plus the inline item rows while running
│   ├── settings_window.py    # Tables / Snacks & Drinks / Pricing / Cloud Sync tabs
│   └── history_window.py     # records browser, CSV export, edit Received
├── test_database.py          # automated checks for the data layer (37 checks)
├── test_sync_manager.py      # automated checks for the sync layer (16 checks)
├── requirements.txt
├── supabase_schema.sql
└── README.md
```

`test_database.py` and `test_sync_manager.py` are plain scripts (no
pytest needed) — run `python3 test_database.py` any time, including after
you make your own changes, to sanity-check that the core logic still
behaves. Both currently pass in full.

## Possible future extensions (not built, in case you want them next)

- Per-table hourly rates (e.g. a VIP room priced differently).
- Walk-in snack/drink sales that aren't tied to a table.
- Multi-device conflict handling beyond last-write-wins (fine for one
  front-desk computer; worth revisiting if two devices might edit the same
  record at once).
- Receipt printing.
