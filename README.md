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
- **Walk-in sales, no table needed** — a **Walk-in Sale** card is pinned
  in its own column to the right of the table grid, always visible: no
  name to configure and no stopwatch, just the same tap-to-add item
  catalog and a **Complete Sale** button for a customer who's only buying
  a snack or drink rather than sitting at a table. It goes through the
  same checkout (Received amount, optional Comment), the same crash-safe
  local database, and the same History/sync as every table session — just
  without a duration or an hourly charge.
- **Tiered hourly billing** — set an hourly rate once in
  **Settings ▸ Pricing**. The first hour is always billed as a flat
  1-hour minimum, even if a table only ran for a few seconds. Past that
  first hour, billing switches to 10-minute blocks: every *started*
  10-minute increment adds another 1/6 of the hourly rate (so 1h11m
  bills as 1 hour + 2/6, 1h21m as 1 hour + 3/6, and so on). The live
  "Running · est. $X.XX" estimate and the final charge after Stop always
  agree, since both go through the same calculation
  (`compute_duration_cost` in `database.py`). The money math runs through
  Python's `Decimal` rather than plain floats specifically so a
  repeating fraction like 1/6 can't introduce rounding error into the
  charged amount.
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
- **History & Records** window — filter by day/week/month or a custom
  "From ... to ..." date range, see sync status per row, edit a "Received"
  amount or a "Comment" after the fact, and export to CSV (UTF-8 with a
  BOM, so non-Latin text like Persian or Arabic opens correctly in Excel).

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

## 4. Build a standalone Windows .exe (optional)

If you want to hand the app to someone without them installing Python at
all, `build_windows.bat` (included here) packages everything into a
single `dist\BuildTime.exe` using [PyInstaller](https://pyinstaller.org/).

1. Copy the whole project folder onto a Windows PC that has Python 3.9+
   installed (with "Add python.exe to PATH" checked during setup).
2. Double-click `build_windows.bat`, or run it from a terminal.
3. It creates a throwaway build environment, installs `requirements.txt`
   plus PyInstaller into it, builds the `.exe`, then cleans up after
   itself (the temporary venv, PyInstaller's `build\` folder, and the
   generated `.spec` file) — leaving just `dist\BuildTime.exe` behind.

That one file is everything — no installer, and no Python required on
the machine that runs it. Re-run the script any time you release a new
version. Want a custom icon? Open `build_windows.bat` and add
`--icon=path\to\icon.ico` to the `pyinstaller` line, as noted in the
script's own comments.

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
- Duration billing follows a **fixed tiered rule** (see the Features list
  above), not a user-configurable rounding setting — the increment sizes
  (1 hour minimum, then 10-minute blocks) are specific enough that they
  replaced the old general-purpose "round up to nearest N minutes" option
  rather than sitting alongside it.
- Cloud sync is **manual by default** ("Sync Now" button) since you
  described wanting to sync "whenever internet is available and they
  wanted it" — automatic background sync is there as an opt-in toggle,
  not the default.
- **Walk-in sales get a fixed label** ("Walk-in Sale") rather than an
  editable name, and skip duration billing entirely (items cost only) —
  since by definition there's no table and no timer involved.

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
│   ├── walkin_card.py        # pinned Walk-in Sale card: same item rows and
│   │                          # checkout as a table, no name, no stopwatch
│   ├── settings_window.py    # Tables / Snacks & Drinks / Pricing / Cloud Sync tabs
│   │                          # (locked/read-only while any stopwatch is running)
│   └── history_window.py     # records browser, CSV export, edit Received
├── test_database.py          # automated checks for the data layer (59 checks)
├── test_sync_manager.py      # automated checks for the sync layer (16 checks)
├── requirements.txt
├── supabase_schema.sql
├── build_windows.bat         # packages the app into dist\BuildTime.exe (see step 4 above)
└── README.md
```

`test_database.py` and `test_sync_manager.py` are plain scripts (no
pytest needed) — run `python3 test_database.py` any time, including after
you make your own changes, to sanity-check that the core logic still
behaves. Both currently pass in full.

## Possible future extensions (not built, in case you want them next)

- Per-table hourly rates (e.g. a VIP room priced differently).
- Multi-device conflict handling beyond last-write-wins (fine for one
  front-desk computer; worth revisiting if two devices might edit the same
  record at once).
- Receipt printing.
