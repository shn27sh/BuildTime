"""Standalone runtime test for database.py — no tkinter needed.
Run with: python3 test_database.py
Uses a throwaway temp DB file so it never touches the real one.
"""
import os
import sys
import tempfile
import time

from database import Database

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

    # simulate ~1 hour, but we don't want the test to actually sleep an hour,
    # so directly check the cost formula via stop_session's math using a tiny sleep instead
    time.sleep(1.2)
    stopped = db.stop_session(sid)
    check("status becomes awaiting_checkout after stop", stopped["status"] == "awaiting_checkout")
    check("duration_seconds is roughly 1s", 0 <= stopped["duration_seconds"] <= 3)
    expected_duration_cost = (stopped["duration_seconds"] / 3600.0) * 5.00
    check("duration_cost matches hourly formula",
          abs(stopped["duration_cost"] - expected_duration_cost) < 0.01)
    expected_total = stopped["items_cost"] + stopped["duration_cost"]
    check("total_cost = items_cost + duration_cost",
          abs(stopped["total_cost"] - expected_total) < 1e-9)

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

    # --- duplicate-safe id generation ---
    ids_seen = set()
    for _ in range(20):
        s = db.start_session(table["id"], table["name"])
        check_dup = s not in ids_seen
        ids_seen.add(s)
    check("20 rapid session starts all got unique UUIDs", len(ids_seen) == 20)

finally:
    os.unlink(db_path)
    for ext in ("-wal", "-shm"):
        p = db_path + ext
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
