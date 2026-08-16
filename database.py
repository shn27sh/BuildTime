"""
database.py — Local-first SQLite data layer for BuildTime.

Design notes
------------
- Every write lands here FIRST and is durable immediately (offline-first).
  Cloud sync (see sync_manager.py) is a separate, best-effort layer that
  reads from this database and never blocks or is required for the app
  to function.
- Each session and session_item gets a UUID primary key, generated locally.
  That UUID is reused as the primary key in Supabase, so re-uploading the
  same row is a harmless no-op (upsert) instead of a duplicate.
- "Snapshot" columns (table_name_snapshot, hourly_rate_snapshot,
  item_name_snapshot, unit_price_snapshot, category_snapshot) freeze the
  values that were true *at the time of the sale*. If the owner renames a
  table, edits an item's price, or changes the hourly rate later, old
  records keep showing exactly what the customer was actually charged.
- A connection is opened fresh for every call and closed right after
  (see _connect()). That keeps the module safe to call from multiple
  threads at once (e.g. the UI thread and the background sync thread)
  without sharing a connection object. WAL mode is enabled so concurrent
  reads/writes don't block each other.
"""

import sqlite3
import uuid
import math
from pathlib import Path
from datetime import datetime, date
from contextlib import contextmanager

APP_DIR = Path.home() / "BuildTime"
DB_PATH = APP_DIR / "buildtime.db"

DEFAULT_TABLES = ["Table 1", "Table 2", "Table 3", "Table 4", "Table 5", "Table 6"]

# Just starter examples — meant to be edited via Settings > Snacks & Drinks.
DEFAULT_ITEMS = [
    ("Water", "Drink", 1.00),
    ("Soda", "Drink", 1.50),
    ("Tea", "Drink", 1.00),
    ("Chips", "Snack", 2.00),
    ("Chocolate Bar", "Snack", 2.00),
]

DEFAULT_SETTINGS = {
    "hourly_rate": "5.00",
    "currency_symbol": "$",
    "round_billed_minutes": "0",       # 0 = bill exact duration, no rounding
    "supabase_url": "",
    "supabase_key": "",
    "auto_sync_enabled": "0",          # off by default — sync is opt-in
    "auto_sync_interval_minutes": "15",
}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _today_str():
    return date.today().isoformat()


class Database:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._seed_defaults()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS tables_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('Snack','Drink')),
                    price REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    table_id INTEGER NOT NULL,
                    table_name_snapshot TEXT NOT NULL,
                    date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds INTEGER,
                    hourly_rate_snapshot REAL NOT NULL,
                    items_cost REAL NOT NULL DEFAULT 0,
                    duration_cost REAL,
                    total_cost REAL,
                    received_amount REAL,
                    comment TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    synced INTEGER NOT NULL DEFAULT 0,
                    synced_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_items (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    item_id INTEGER,
                    item_name_snapshot TEXT NOT NULL,
                    category_snapshot TEXT NOT NULL,
                    unit_price_snapshot REAL NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    subtotal REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
                CREATE INDEX IF NOT EXISTS idx_sessions_synced ON sessions(synced);
                CREATE INDEX IF NOT EXISTS idx_session_items_session ON session_items(session_id);
                """
            )

            # Migration for databases created before the "comment" field existed —
            # CREATE TABLE IF NOT EXISTS above is a no-op on an already-existing
            # table, so add the column here if it's missing. Must run BEFORE the
            # view below, since the view selects s.comment. Safe every startup:
            # SQLite raises if the column is already there, and we just ignore it.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN comment TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

            # DROP + CREATE (not "IF NOT EXISTS") so a view definition changed in
            # a later version of this file — like adding s.comment here — always
            # takes effect, even for a database that already had an older view.
            conn.executescript(
                """
                DROP VIEW IF EXISTS session_summary;
                CREATE VIEW session_summary AS
                SELECT
                    s.id, s.date, s.table_name_snapshot AS table_name,
                    s.start_time, s.end_time, s.duration_seconds,
                    s.items_cost, s.duration_cost, s.total_cost,
                    s.received_amount, s.comment, s.status, s.synced,
                    (SELECT GROUP_CONCAT(item_name_snapshot || ' x' || quantity, ', ')
                     FROM session_items si
                     WHERE si.session_id = s.id AND si.category_snapshot = 'Snack') AS snacks_text,
                    (SELECT GROUP_CONCAT(item_name_snapshot || ' x' || quantity, ', ')
                     FROM session_items si
                     WHERE si.session_id = s.id AND si.category_snapshot = 'Drink') AS drinks_text
                FROM sessions s;
                """
            )

    def _seed_defaults(self):
        with self._connect() as conn:
            if conn.execute("SELECT COUNT(*) c FROM tables_config").fetchone()["c"] == 0:
                for i, name in enumerate(DEFAULT_TABLES):
                    conn.execute(
                        "INSERT INTO tables_config (name, active, sort_order) VALUES (?,1,?)",
                        (name, i),
                    )
            if conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
                for i, (name, cat, price) in enumerate(DEFAULT_ITEMS):
                    conn.execute(
                        "INSERT INTO items (name, category, price, active, sort_order) VALUES (?,?,?,1,?)",
                        (name, cat, price, i),
                    )
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?,?)", (k, v))

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_setting(self, key, default=None):
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key, value):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_hourly_rate(self):
        return float(self.get_setting("hourly_rate", "0") or 0)

    def get_currency_symbol(self):
        return self.get_setting("currency_symbol", "$")

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def list_tables(self, active_only=True):
        with self._connect() as conn:
            q = "SELECT * FROM tables_config"
            if active_only:
                q += " WHERE active=1"
            q += " ORDER BY sort_order, id"
            return [dict(r) for r in conn.execute(q).fetchall()]

    def add_table(self, name):
        with self._connect() as conn:
            n = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 n FROM tables_config").fetchone()["n"]
            cur = conn.execute(
                "INSERT INTO tables_config (name, active, sort_order) VALUES (?,1,?)", (name, n)
            )
            return cur.lastrowid

    def rename_table(self, table_id, new_name):
        with self._connect() as conn:
            conn.execute("UPDATE tables_config SET name=? WHERE id=?", (new_name, table_id))

    def set_table_active(self, table_id, active):
        with self._connect() as conn:
            conn.execute("UPDATE tables_config SET active=? WHERE id=?", (1 if active else 0, table_id))

    def move_table(self, table_id, direction):
        """direction: -1 to move up, +1 to move down (swaps sort_order with neighbor)."""
        tables = self.list_tables(active_only=False)
        tables.sort(key=lambda t: t["sort_order"])
        idx = next((i for i, t in enumerate(tables) if t["id"] == table_id), None)
        if idx is None:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(tables):
            return
        a, b = tables[idx], tables[new_idx]
        with self._connect() as conn:
            conn.execute("UPDATE tables_config SET sort_order=? WHERE id=?", (b["sort_order"], a["id"]))
            conn.execute("UPDATE tables_config SET sort_order=? WHERE id=?", (a["sort_order"], b["id"]))

    # ------------------------------------------------------------------
    # Items (snacks & drinks catalog)
    # ------------------------------------------------------------------
    def list_items(self, active_only=True):
        with self._connect() as conn:
            q = "SELECT * FROM items"
            if active_only:
                q += " WHERE active=1"
            q += " ORDER BY category, sort_order, id"
            return [dict(r) for r in conn.execute(q).fetchall()]

    def add_item(self, name, category, price):
        with self._connect() as conn:
            n = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 n FROM items").fetchone()["n"]
            cur = conn.execute(
                "INSERT INTO items (name, category, price, active, sort_order) VALUES (?,?,?,1,?)",
                (name, category, price, n),
            )
            return cur.lastrowid

    def update_item(self, item_id, name=None, category=None, price=None):
        fields, vals = [], []
        if name is not None:
            fields.append("name=?"); vals.append(name)
        if category is not None:
            fields.append("category=?"); vals.append(category)
        if price is not None:
            fields.append("price=?"); vals.append(price)
        if not fields:
            return
        vals.append(item_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE items SET {', '.join(fields)} WHERE id=?", vals)

    def set_item_active(self, item_id, active):
        with self._connect() as conn:
            conn.execute("UPDATE items SET active=? WHERE id=?", (1 if active else 0, item_id))

    # ------------------------------------------------------------------
    # Session lifecycle: running -> awaiting_checkout -> completed
    # ------------------------------------------------------------------
    def start_session(self, table_id, table_name):
        sid = str(uuid.uuid4())
        now = _now_iso()
        rate = self.get_hourly_rate()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, table_id, table_name_snapshot, date, start_time, end_time,
                    duration_seconds, hourly_rate_snapshot, items_cost, duration_cost,
                    total_cost, received_amount, status, synced, created_at, updated_at)
                   VALUES (?,?,?,?,?,NULL,NULL,?,0,NULL,NULL,NULL,'running',0,?,?)""",
                (sid, table_id, table_name, _today_str(), now, rate, now, now),
            )
        return sid

    def add_or_increment_item(self, session_id, item, delta=1):
        """item: dict with at least id, name, category, price. delta can be negative to remove."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_items WHERE session_id=? AND item_id=?",
                (session_id, item["id"]),
            ).fetchone()
            if row:
                new_qty = row["quantity"] + delta
                if new_qty <= 0:
                    conn.execute("DELETE FROM session_items WHERE id=?", (row["id"],))
                else:
                    subtotal = new_qty * row["unit_price_snapshot"]
                    conn.execute(
                        "UPDATE session_items SET quantity=?, subtotal=? WHERE id=?",
                        (new_qty, subtotal, row["id"]),
                    )
            else:
                if delta <= 0:
                    return
                iid = str(uuid.uuid4())
                subtotal = delta * item["price"]
                conn.execute(
                    """INSERT INTO session_items
                       (id, session_id, item_id, item_name_snapshot, category_snapshot,
                        unit_price_snapshot, quantity, subtotal)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (iid, session_id, item["id"], item["name"], item["category"],
                     item["price"], delta, subtotal),
                )
            total = conn.execute(
                "SELECT COALESCE(SUM(subtotal),0) t FROM session_items WHERE session_id=?",
                (session_id,),
            ).fetchone()["t"]
            conn.execute(
                "UPDATE sessions SET items_cost=?, updated_at=? WHERE id=?",
                (total, _now_iso(), session_id),
            )

    def get_session_items(self, session_id):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_items WHERE session_id=? ORDER BY category_snapshot, item_name_snapshot",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def stop_session(self, session_id, round_minutes=0):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                return None
            start = datetime.fromisoformat(row["start_time"])
            end = datetime.now()
            duration = (end - start).total_seconds()
            billed_seconds = duration
            if round_minutes and round_minutes > 0:
                step = round_minutes * 60
                billed_seconds = math.ceil(duration / step) * step
            duration_cost = (billed_seconds / 3600.0) * row["hourly_rate_snapshot"]
            total = row["items_cost"] + duration_cost
            conn.execute(
                """UPDATE sessions SET end_time=?, duration_seconds=?, duration_cost=?,
                   total_cost=?, status='awaiting_checkout', updated_at=? WHERE id=?""",
                (end.isoformat(timespec="seconds"), int(duration), duration_cost, total, _now_iso(), session_id),
            )
        return self.get_session(session_id)

    def resume_session(self, session_id):
        """Undo an accidental Stop — puts the session back into 'running'."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE sessions SET end_time=NULL, duration_seconds=NULL, duration_cost=NULL,
                   total_cost=NULL, status='running', updated_at=? WHERE id=?""",
                (_now_iso(), session_id),
            )

    def finish_session(self, session_id, received_amount, comment=None):
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET received_amount=?, comment=?, status='completed', updated_at=? WHERE id=?",
                (received_amount, comment or "", _now_iso(), session_id),
            )

    def get_session(self, session_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return dict(row) if row else None

    def get_active_sessions(self):
        """Sessions still running or awaiting checkout — used for crash/restart recovery."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status IN ('running','awaiting_checkout')"
            ).fetchall()
            return [dict(r) for r in rows]

    def has_running_session(self):
        """True if at least one table's stopwatch is actively ticking right
        now (status='running') — as opposed to idle or awaiting_checkout
        (stopped, but not yet finished). Used to lock the Settings window."""
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE status='running' LIMIT 1").fetchone()
            return row is not None

    # ------------------------------------------------------------------
    # History / reporting
    # ------------------------------------------------------------------
    def get_history(self, date_from=None, date_to=None, limit=1000):
        q = "SELECT * FROM session_summary WHERE status='completed'"
        vals = []
        if date_from:
            q += " AND date>=?"; vals.append(date_from)
        if date_to:
            q += " AND date<=?"; vals.append(date_to)
        q += " ORDER BY start_time DESC LIMIT ?"
        vals.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(q, vals).fetchall()]

    def update_received_amount(self, session_id, amount):
        # Editing a finished record after the fact — flag it for re-sync too.
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET received_amount=?, synced=0, updated_at=? WHERE id=?",
                (amount, _now_iso(), session_id),
            )

    def update_comment(self, session_id, comment):
        # Editing a finished record's comment after the fact — flag it for re-sync too.
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET comment=?, synced=0, updated_at=? WHERE id=?",
                (comment or "", _now_iso(), session_id),
            )

    # ------------------------------------------------------------------
    # Sync bookkeeping
    # ------------------------------------------------------------------
    def get_unsynced_sessions(self):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE synced=0 AND status='completed'"
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_synced(self, session_id):
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET synced=1, synced_at=? WHERE id=?", (_now_iso(), session_id))

    def sync_stats(self):
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM sessions WHERE status='completed'").fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) c FROM sessions WHERE status='completed' AND synced=0"
            ).fetchone()["c"]
            return {"total": total, "pending": pending}
