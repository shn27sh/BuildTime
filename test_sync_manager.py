"""Standalone runtime test for sync_manager.py — no tkinter needed.
Run with: python3 test_sync_manager.py

Note: this sandbox has no network access, which is actually perfect for
proving the "offline / Supabase unreachable" path fails soft instead of
crashing — exactly the offline-first guarantee the app is supposed to give.
"""
import os
import sys
import tempfile

from database import Database
from sync_manager import SyncManager

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
    sm = SyncManager(db)

    # --- not configured yet ---
    check("is_configured() False before any URL/key set", sm.is_configured() is False)
    ok, msg = sm.test_connection()
    check("test_connection() fails gracefully when unconfigured", ok is False and isinstance(msg, str))

    result = sm.sync_all()
    check("sync_all() refuses gracefully when unconfigured", result["ok"] is False and result["synced"] == 0)

    # --- configure with fake creds (sandbox has no network, so this
    #     exercises the real "can't reach the internet" failure path) ---
    db.set_setting("supabase_url", "https://example-project.supabase.co")
    db.set_setting("supabase_key", "fake-key-for-testing")
    check("is_configured() True once both fields are set", sm.is_configured() is True)

    ok, msg = sm.test_connection()
    check("test_connection() does not raise with no network", isinstance(ok, bool))
    check("test_connection() returns a human-readable message", isinstance(msg, str) and len(msg) > 0)
    print(f"       -> message was: {msg!r}")

    # --- build a real completed session so we can check row-shaping logic ---
    table = db.list_tables()[0]
    water = next(i for i in db.list_items() if i["name"] == "Water")
    sid = db.start_session(table["id"], table["name"])
    db.add_or_increment_item(sid, water, delta=2)
    stopped = db.stop_session(sid)
    db.finish_session(sid, stopped["total_cost"])
    session = db.get_session(sid)

    row, items = sm._session_to_row(session)
    check("_session_to_row includes the session id", row["id"] == sid)
    check("_session_to_row computes drinks_text from line items", row["drinks_text"] == "Water x2")
    check("_session_to_row leaves snacks_text empty (no snacks bought)", row["snacks_text"] == "")
    check("_session_to_row carries total_cost through", row["total_cost"] == session["total_cost"])
    check("session_items rows returned alongside", len(items) == 1 and items[0]["quantity"] == 2)

    # sync_all should attempt the batch and fail soft (no network in this sandbox)
    # without ever raising, and without marking anything synced on failure.
    calls = []
    result = sm.sync_all(progress_callback=lambda i, n: calls.append((i, n)))
    check("sync_all() with pending records does not raise", isinstance(result, dict))
    check("sync_all() reports failure without network rather than crashing", result["ok"] is False)
    check("progress_callback was invoked", len(calls) >= 1)
    unsynced_after = db.get_unsynced_sessions()
    check("session NOT marked synced after a failed network attempt", any(s["id"] == sid for s in unsynced_after))
    print(f"       -> sync_all() message: {result['message']!r}")

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
