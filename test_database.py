"""Standalone runtime test for database.py — no tkinter needed.
Run with: python3 test_database.py
Uses a throwaway temp DB file so it never touches the real one.
"""
import os
import sys
import tempfile
import time
from decimal import Decimal

from database import Database, compute_billable_hours, compute_duration_cost, gregorian_to_shamsi

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
db_path = tmp.name

try:
    db = Database(db_path=db_path)

    # --- seeding ---
    tables = db.list_tables()
    items = db.list_items()
    check("default tables seeded (6)", len(tables) == 6)
    check("default items seeded (5)", len(items) == 5)
    check("hourly rate default is 5.00", db.get_hourly_rate() == 5.00)
    check("currency symbol default is $", db.get_currency_symbol() == "$")

    # --- settings round-trip ---
    db.set_setting("hourly_rate", 8.5)
    check("hourly rate updates", db.get_hourly_rate() == 8.5)
    db.set_setting("hourly_rate", 5.00)  # reset for later calc checks

    # --- tables CRUD ---
    new_id = db.add_table("Table 7")
    check("add_table returns an id", isinstance(new_id, int))
    check("new table appears in active list", any(t["id"] == new_id for t in db.list_tables()))
    db.rename_table(new_id, "VIP Room")
    renamed = next(t for t in db.list_tables() if t["id"] == new_id)
    check("rename_table works", renamed["name"] == "VIP Room")
    db.set_table_active(new_id, False)
    check("hidden table drops out of active list", all(t["id"] != new_id for t in db.list_tables()))
    check("hidden table still present with active_only=False",
          any(t["id"] == new_id for t in db.list_tables(active_only=False)))

    # move Table 2 up (should swap with Table 1)
    t1, t2 = tables[0], tables[1]
    db.move_table(t2["id"], -1)
    reordered = db.list_tables()
    check("move_table reorders", reordered[0]["id"] == t2["id"])

    # --- items CRUD ---
    item_id = db.add_item("Coffee", "Drink", 2.25)
    check("add_item works", any(i["id"] == item_id for i in db.list_items()))
    db.update_item(item_id, price=2.75)
    updated = next(i for i in db.list_items() if i["id"] == item_id)
    check("update_item changes price", updated["price"] == 2.75)
    db.set_item_active(item_id, False)
    check("hidden item drops out of active list", all(i["id"] != item_id for i in db.list_items()))

    # --- full session lifecycle ---
    table = db.list_tables()[0]
    water = next(i for i in db.list_items() if i["name"] == "Water")
    chips = next(i for i in db.list_items() if i["name"] == "Chips")

    sid = db.start_session(table["id"], table["name"])
    session = db.get_session(sid)
    check("session starts with status=running", session["status"] == "running")
    check("session items_cost starts at 0", session["items_cost"] == 0)

    # add 2 water, 1 chips
    db.add_or_increment_item(sid, water, delta=1)
    db.add_or_increment_item(sid, water, delta=1)
    db.add_or_increment_item(sid, chips, delta=1)
    session = db.get_session(sid)
    expected_items_cost = 2 * water["price"] + 1 * chips["price"]
    check(f"items_cost = {expected_items_cost} after adding 2 water + 1 chips",
          abs(session["items_cost"] - expected_items_cost) < 1e-9)

    items_in_session = db.get_session_items(sid)
    water_row = next(r for r in items_in_session if r["item_name_snapshot"] == "Water")
    check("water quantity is 2", water_row["quantity"] == 2)

    # decrement water by 1 -> should leave 1
    db.add_or_increment_item(sid, water, delta=-1)
    items_in_session = db.get_session_items(sid)
    water_row = next(r for r in items_in_session if r["item_name_snapshot"] == "Water")
    check("water quantity is 1 after decrement", water_row["quantity"] == 1)

    # decrement water by 1 again -> should remove the row entirely
    db.add_or_increment_item(sid, water, delta=-1)
    items_in_session = db.get_session_items(sid)
    check("water row removed once qty hits 0", all(r["item_name_snapshot"] != "Water" for r in items_in_session))

    # sanity: decrementing something not in the cart is a no-op, no crash
    soda = next(i for i in db.list_items() if i["name"] == "Soda")
    db.add_or_increment_item(sid, soda, delta=-1)
    items_in_session = db.get_session_items(sid)
    check("decrementing an absent item is a safe no-op", all(r["item_name_snapshot"] != "Soda" for r in items_in_session))

    # simulate ~1 second running, then stop -- under the new tiered billing
    # rule (see compute_billable_hours below), ANY duration up to and
    # including 1 hour bills as a flat 1-hour minimum, so this is now a
    # deterministic check rather than needing a sleep-based tolerance
    # window against a proportional formula.
    time.sleep(1.2)
    stopped = db.stop_session(sid)
    check("status becomes awaiting_checkout after stop", stopped["status"] == "awaiting_checkout")
    check("duration_seconds is roughly 1s", 0 <= stopped["duration_seconds"] <= 3)
    check("a few seconds bills as a flat 1-hour minimum",
          abs(stopped["duration_cost"] - 5.00) < 1e-9)
    expected_total = stopped["items_cost"] + stopped["duration_cost"]
    check("total_cost = items_cost + duration_cost",
          abs(stopped["total_cost"] - expected_total) < 1e-9)

    # --- tiered stopwatch billing rule (compute_billable_hours / compute_duration_cost) ---
    # Pure-function checks against every worked example from the spec:
    #   <= 1 hour bills as a flat 1-hour minimum; past 1 hour, every
    #   *started* 10-minute block adds another 1/6 of the hourly rate.
    # NOTE on "1 hour 34 minutes": the spec listed this as "1 hour + 3/6",
    # but applying the spec's own formula literally --
    #   extraTime=34min=2040s, extraBlocks=ceil(2040/600)=4 -> 1 + 4/6 --
    # gives 4/6, not 3/6. It also has to be 4/6 to stay consistent with
    # the neighboring examples: 1h30m is explicitly 3/6 and 1h40m is
    # explicitly 4/6, and 1h34m falls strictly between those two
    # boundaries, so it must land in the same block as 1h40m. Treating
    # "1h34m -> 3/6" as a one-off typo and following the stated formula
    # (which 16 of the 17 worked examples already agree with).
    billable_cases = [
        (1, Decimal(1), "1 second"),
        (10 * 60, Decimal(1), "10 minutes"),
        (47 * 60, Decimal(1), "47 minutes"),
        (59 * 60 + 59, Decimal(1), "59 minutes 59 seconds"),
        (3600, Decimal(1), "1 hour exactly"),
        (3600 + 1, Decimal(1) + Decimal(1) / 6, "1 hour 1 second"),
        (3600 + 10 * 60, Decimal(1) + Decimal(1) / 6, "1 hour 10 minutes"),
        (3600 + 11 * 60, Decimal(1) + Decimal(2) / 6, "1 hour 11 minutes"),
        (3600 + 20 * 60, Decimal(1) + Decimal(2) / 6, "1 hour 20 minutes"),
        (3600 + 21 * 60, Decimal(1) + Decimal(3) / 6, "1 hour 21 minutes"),
        (3600 + 30 * 60, Decimal(1) + Decimal(3) / 6, "1 hour 30 minutes"),
        (3600 + 34 * 60, Decimal(1) + Decimal(4) / 6, "1 hour 34 minutes (see NOTE above)"),
        (3600 + 40 * 60, Decimal(1) + Decimal(4) / 6, "1 hour 40 minutes"),
        (3600 + 50 * 60, Decimal(1) + Decimal(5) / 6, "1 hour 50 minutes"),
        (3600 + 59 * 60, Decimal(2), "1 hour 59 minutes (6/6 = 2 hours)"),
        (2 * 3600, Decimal(2), "2 hours exactly"),
        (2 * 3600 + 1, Decimal(2) + Decimal(1) / 6, "2 hours 1 second"),
    ]
    for secs, expected_hours, label in billable_cases:
        got = compute_billable_hours(secs)
        check(f"billable hours for {label}: expected {expected_hours}", got == expected_hours)

    # A fraction of a second past a boundary still counts as having
    # *started* the next block (ceiling behavior applies to fractional
    # seconds too, not just whole-minute inputs).
    check("0.5s past the 1-hour mark still starts the next block",
          compute_billable_hours(3600.5) == Decimal(1) + Decimal(1) / 6)
    check("0.5s past a 10-minute mark still starts the next block",
          compute_billable_hours(3600 + 600.5) == Decimal(1) + Decimal(2) / 6)

    # Money side: billable hours (a clean fraction) x an hourly rate that
    # doesn't divide evenly by 6, rounded to the nearest cent. Computed via
    # Decimal internally so the well-known float error in repeating
    # fractions like 1/6 (0.1666...) can't leak into the charged amount.
    check("$10.00/hr, 1h11m -> $13.33 (10 + 2*10/6 = 13.333... rounds to 13.33)",
          compute_duration_cost(3600 + 11 * 60, 10.00) == 13.33)
    check("$10.00/hr, 1h21m -> $15.00 (10 + 3*10/6 = 15.00 exactly)",
          compute_duration_cost(3600 + 21 * 60, 10.00) == 15.00)
    check("$5.00/hr, 1h1s -> $5.83 (5 + 5/6 = 5.8333... rounds to 5.83)",
          compute_duration_cost(3600 + 1, 5.00) == 5.83)
    check("$0/hr never crashes and bills $0 regardless of duration",
          compute_duration_cost(3600 + 21 * 60, 0) == 0.0)

    # --- full stop_session() integration for the >1 hour tier ---
    # Rather than actually sleeping for over an hour, backdate a fresh
    # session's start_time and let stop_session() compute against real
    # wall-clock elapsed time -- this exercises the *actual* code path
    # (including the datetime math and the DB round-trip), not just the
    # pure function in isolation.
    from datetime import datetime, timedelta

    def start_and_backdate(seconds_ago):
        new_sid = db.start_session(table["id"], table["name"])
        backdated = (datetime.now() - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")
        with db._connect() as conn:
            conn.execute("UPDATE sessions SET start_time=? WHERE id=?", (backdated, new_sid))
        return new_sid

    sid_1h11m = start_and_backdate(3600 + 11 * 60)
    stopped_1h11m = db.stop_session(sid_1h11m)
    check("stop_session: 1h11m at $5/hr bills 1 + 2/6 hours = $6.67",
          abs(stopped_1h11m["duration_cost"] - 6.67) < 0.01)

    sid_1h59m = start_and_backdate(3600 + 59 * 60)
    stopped_1h59m = db.stop_session(sid_1h59m)
    check("stop_session: 1h59m at $5/hr bills a full 2 hours = $10.00",
          abs(stopped_1h59m["duration_cost"] - 10.00) < 0.01)

    sid_47m = start_and_backdate(47 * 60)
    stopped_47m = db.stop_session(sid_47m)
    check("stop_session: 47m at $5/hr still bills the 1-hour minimum = $5.00",
          abs(stopped_47m["duration_cost"] - 5.00) < 0.01)

    # accidental stop -> resume
    db.resume_session(sid)
    resumed = db.get_session(sid)
    check("resume_session reverts to running", resumed["status"] == "running")
    check("resume_session clears end_time", resumed["end_time"] is None)

    # stop again for real, then finish
    stopped = db.stop_session(sid)
    db.finish_session(sid, stopped["total_cost"])
    finished = db.get_session(sid)
    check("finish_session marks completed", finished["status"] == "completed")
    check("received_amount stored", finished["received_amount"] == stopped["total_cost"])

    # --- history / session_summary view ---
    history = db.get_history()
    check("finished session shows up in history", any(h["id"] == sid for h in history))
    hist_row = next(h for h in history if h["id"] == sid)
    check("history row has snacks_text with Chips", "Chips" in (hist_row["snacks_text"] or ""))
    check("history row has no drinks_text (water/soda were removed)", not hist_row["drinks_text"])

    # --- active session recovery (crash simulation) ---
    sid2 = db.start_session(table["id"], table["name"])
    db.add_or_increment_item(sid2, water, delta=3)
    active = db.get_active_sessions()
    check("get_active_sessions finds the still-running session", any(s["id"] == sid2 for s in active))
    db.stop_session(sid2)
    active = db.get_active_sessions()
    check("awaiting_checkout session still counts as active", any(s["id"] == sid2 for s in active))

    # --- sync bookkeeping ---
    db.finish_session(sid2, 0)
    unsynced = db.get_unsynced_sessions()
    check("both finished sessions are unsynced", len(unsynced) == 2)
    stats = db.sync_stats()
    check("sync_stats total=2", stats["total"] == 2)
    check("sync_stats pending=2", stats["pending"] == 2)

    db.mark_synced(sid)
    stats = db.sync_stats()
    check("sync_stats pending=1 after marking one synced", stats["pending"] == 1)

    # editing a received amount after sync should flip it back to unsynced
    db.update_received_amount(sid, 99.99)
    unsynced = db.get_unsynced_sessions()
    check("editing received_amount re-flags session as unsynced", any(s["id"] == sid for s in unsynced))

    # --- walk-in sales (no table, no timer) ---
    from database import WALKIN_TABLE_ID, WALKIN_TABLE_NAME

    wsid = db.start_walkin_sale()
    wsession = db.get_session(wsid)
    check("walk-in sale starts with status=walkin_open", wsession["status"] == "walkin_open")
    check("walk-in sale uses the reserved table_id", wsession["table_id"] == WALKIN_TABLE_ID)
    check("walk-in sale is labeled 'Walk-in Sale'", wsession["table_name_snapshot"] == WALKIN_TABLE_NAME)
    check("walk-in sale has no hourly rate", wsession["hourly_rate_snapshot"] == 0)
    check("walk-in sale is NOT counted as a running stopwatch",
          not db.has_running_session())

    db.add_or_increment_item(wsid, water, delta=2)
    db.add_or_increment_item(wsid, chips, delta=1)
    wsession = db.get_session(wsid)
    expected_walkin_items_cost = 2 * water["price"] + 1 * chips["price"]
    check("walk-in items_cost matches cart", abs(wsession["items_cost"] - expected_walkin_items_cost) < 1e-9)

    wstopped = db.stop_walkin_sale(wsid)
    check("Complete Sale moves status to walkin_checkout", wstopped["status"] == "walkin_checkout")
    check("walk-in duration_cost is 0 (no timer)", wstopped["duration_cost"] == 0)
    check("walk-in total_cost = items_cost only",
          abs(wstopped["total_cost"] - wstopped["items_cost"]) < 1e-9)

    db.resume_walkin_sale(wsid)
    wresumed = db.get_session(wsid)
    check("Back to Cart reverts to walkin_open", wresumed["status"] == "walkin_open")
    check("Back to Cart clears total_cost", wresumed["total_cost"] is None)
    still_there = db.get_session_items(wsid)
    check("cart items survive Back to Cart", len(still_there) == 2)

    db.stop_walkin_sale(wsid)
    db.finish_session(wsid, 5.00, "walk-in test")
    wfinished = db.get_session(wsid)
    check("finish_session works for walk-in sales too", wfinished["status"] == "completed")
    walkin_history = next((h for h in db.get_history() if h["id"] == wsid), None)
    check("finished walk-in sale shows up in history", walkin_history is not None)
    check("walk-in history row shows the Walk-in Sale label",
          walkin_history is not None and walkin_history["table_name"] == WALKIN_TABLE_NAME)

    # emptying a walk-in cart back to 0 items should drop the ghost session
    wsid2 = db.start_walkin_sale()
    db.add_or_increment_item(wsid2, water, delta=1)
    db.add_or_increment_item(wsid2, water, delta=-1)  # back to 0
    db.delete_session_if_empty(wsid2)
    check("emptied walk-in cart is deleted, not left as a ghost session", db.get_session(wsid2) is None)

    # a non-empty session must NOT be deleted by delete_session_if_empty
    wsid3 = db.start_walkin_sale()
    db.add_or_increment_item(wsid3, water, delta=1)
    db.delete_session_if_empty(wsid3)
    check("delete_session_if_empty is a no-op when items are still present", db.get_session(wsid3) is not None)

    # an open walk-in cart still counts as "active" for crash recovery / close-warning...
    active_now = db.get_active_sessions()
    check("open walk-in cart counts as an active session", any(s["id"] == wsid3 for s in active_now))
    # ...but never as a running *stopwatch* (that's specifically for tables)
    check("open walk-in cart still doesn't count as a running stopwatch", not db.has_running_session())

    # clean up wsid3 so it doesn't leak into the sync-bookkeeping counts below
    db.finish_session(wsid3, 1.00)

    # --- duplicate-safe id generation ---
    ids_seen = set()
    for _ in range(20):
        s = db.start_session(table["id"], table["name"])
        check_dup = s not in ids_seen
        ids_seen.add(s)
    check("20 rapid session starts all got unique UUIDs", len(ids_seen) == 20)

    # --- Shamsi (Solar Hijri / Persian) date, alongside the Gregorian date ---
    check("gregorian_to_shamsi(None) is None (safe on a NULL column value)",
          gregorian_to_shamsi(None) is None)
    check("gregorian_to_shamsi('') is None", gregorian_to_shamsi("") is None)
    # A handful of independently-documented Nowruz reference points (the
    # Persian New Year always falls on the spring equinox, March 19-21).
    check("1979-03-21 -> 1358-01-01 (well-documented historical reference)",
          gregorian_to_shamsi("1979-03-21") == "1358-01-01")
    check("2024-03-20 -> 1403-01-01", gregorian_to_shamsi("2024-03-20") == "1403-01-01")
    check("2026-03-21 -> 1405-01-01", gregorian_to_shamsi("2026-03-21") == "1405-01-01")
    check("1970-01-01 -> 1348-10-11 (Unix epoch, commonly cited)",
          gregorian_to_shamsi("1970-01-01") == "1348-10-11")

    # A new table session gets a shamsi_date written at start time, matching
    # its Gregorian "date" column (same calendar day, two representations).
    sid_shamsi = db.start_session(table["id"], table["name"])
    session_shamsi = db.get_session(sid_shamsi)
    check("new session has a shamsi_date", session_shamsi["shamsi_date"] is not None)
    check("shamsi_date matches gregorian_to_shamsi(date) for the same row",
          session_shamsi["shamsi_date"] == gregorian_to_shamsi(session_shamsi["date"]))
    db.stop_session(sid_shamsi)
    db.finish_session(sid_shamsi, 5.00)

    # Same for a walk-in sale.
    wsid_shamsi = db.start_walkin_sale()
    walkin_shamsi_session = db.get_session(wsid_shamsi)
    check("new walk-in sale also has a shamsi_date",
          walkin_shamsi_session["shamsi_date"] == gregorian_to_shamsi(walkin_shamsi_session["date"]))
    db.stop_walkin_sale(wsid_shamsi)
    db.finish_session(wsid_shamsi, 1.00)

    # get_history() (backed by the session_summary view) must surface it too.
    hist_row = next(h for h in db.get_history() if h["id"] == sid_shamsi)
    check("shamsi_date is present in get_history() rows (view was updated)",
          hist_row["shamsi_date"] == session_shamsi["shamsi_date"])

finally:
    os.unlink(db_path)
    for ext in ("-wal", "-shm"):
        p = db_path + ext
        if os.path.exists(p):
            os.unlink(p)

# --- migration/backfill: a database created BEFORE shamsi_date existed ---
# Manually build the *old* schema (no shamsi_date column at all) with some
# pre-existing rows, exactly as a real user's existing buildtime.db would
# look coming into this update, then open it with the current Database
# class and confirm it migrates cleanly AND backfills old rows correctly
# rather than leaving their shamsi_date blank forever.
import sqlite3

tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp2.close()
old_db_path = tmp2.name
try:
    raw = sqlite3.connect(old_db_path)
    raw.execute(
        """CREATE TABLE sessions (
            id TEXT PRIMARY KEY, table_id INTEGER NOT NULL, table_name_snapshot TEXT NOT NULL,
            date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT, duration_seconds INTEGER,
            hourly_rate_snapshot REAL NOT NULL, items_cost REAL NOT NULL DEFAULT 0,
            duration_cost REAL, total_cost REAL, received_amount REAL, comment TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running', synced INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    raw.execute(
        """INSERT INTO sessions (id, table_id, table_name_snapshot, date, start_time,
           hourly_rate_snapshot, items_cost, status, synced, created_at, updated_at)
           VALUES ('pre-existing-1', 1, 'Table 1', '2024-03-20', '2024-03-20T10:00:00',
                   5.0, 0, 'completed', 0, '2024-03-20T10:00:00', '2024-03-20T10:00:00')"""
    )
    raw.commit()
    raw.close()

    # Confirm the OLD schema genuinely has no shamsi_date column, so the
    # upcoming check is actually testing the migration, not a no-op.
    check_conn = sqlite3.connect(old_db_path)
    cols_before = [r[1] for r in check_conn.execute("PRAGMA table_info(sessions)")]
    check_conn.close()
    check("(sanity) the old schema truly has no shamsi_date column yet",
          "shamsi_date" not in cols_before)

    old_db = Database(db_path=old_db_path)
    migrated_row = old_db.get_session("pre-existing-1")
    check("migration added a usable shamsi_date to a pre-existing row",
          migrated_row["shamsi_date"] == "1403-01-01")

    # Re-opening again (column already exists this time) must not error or
    # touch an already-populated value.
    old_db2 = Database(db_path=old_db_path)
    check("re-opening an already-migrated database is a harmless no-op",
          old_db2.get_session("pre-existing-1")["shamsi_date"] == "1403-01-01")
finally:
    os.unlink(old_db_path)
    for ext in ("-wal", "-shm"):
        p = old_db_path + ext
        if os.path.exists(p):
            os.unlink(p)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
