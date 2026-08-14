"""
sync_manager.py — Optional, best-effort cloud sync to Supabase.

This is deliberately a thin layer on top of database.py's plain REST calls
(PostgREST, which is what Supabase exposes at <project>/rest/v1/<table>)
rather than the official supabase-py SDK. Two reasons:
  1. Fewer dependencies — just `requests`, which is tiny and stable.
  2. The upsert semantics we need are a single documented HTTP header
     (Prefer: resolution=merge-duplicates), so the SDK doesn't buy us much.

Duplicate-upload prevention: every session/session_item row already has a
UUID primary key assigned locally at creation time (see database.py). We
POST with `Prefer: resolution=merge-duplicates`, which tells PostgREST to
treat a primary-key collision as an update instead of an error. So syncing
the same row twice (e.g. a retry after a dropped connection) just overwrites
itself — it can never create a duplicate row.

Nothing in this file is ever required for the app to work. Every method
fails soft: no configuration, no internet, a bad key, a missing table —
all of it comes back as (False, "human readable reason") or a result dict,
never an uncaught exception into the UI thread.
"""

import requests


class SyncManager:
    def __init__(self, db):
        self.db = db

    def is_configured(self):
        url = self.db.get_setting("supabase_url", "")
        key = self.db.get_setting("supabase_key", "")
        return bool(url and key)

    def _headers(self, extra=None):
        key = self.db.get_setting("supabase_key", "")
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _base_url(self):
        url = self.db.get_setting("supabase_url", "").rstrip("/")
        return f"{url}/rest/v1"

    def test_connection(self):
        if not self.is_configured():
            return False, "Supabase URL and API key are not set yet."
        try:
            resp = requests.get(
                f"{self._base_url()}/sessions?select=id&limit=1",
                headers=self._headers(), timeout=10,
            )
            if resp.status_code == 200:
                return True, "Connected successfully."
            if resp.status_code in (401, 403):
                return False, f"Authentication failed ({resp.status_code}). Check your API key."
            if resp.status_code == 404:
                return False, "Table 'sessions' not found. Run supabase_schema.sql in your Supabase project first."
            return False, f"Unexpected response: {resp.status_code} {resp.text[:200]}"
        except requests.exceptions.ConnectionError:
            return False, "No internet connection, or the project URL is wrong."
        except requests.exceptions.Timeout:
            return False, "Connection timed out."
        except requests.exceptions.RequestException as e:
            return False, f"Connection error: {e}"

    def _session_to_row(self, session):
        items = self.db.get_session_items(session["id"])
        snacks = ", ".join(
            f"{i['item_name_snapshot']} x{i['quantity']}"
            for i in items if i["category_snapshot"] == "Snack"
        )
        drinks = ", ".join(
            f"{i['item_name_snapshot']} x{i['quantity']}"
            for i in items if i["category_snapshot"] == "Drink"
        )
        row = {
            "id": session["id"],
            "table_name": session["table_name_snapshot"],
            "date": session["date"],
            "start_time": session["start_time"],
            "end_time": session["end_time"],
            "duration_seconds": session["duration_seconds"],
            "hourly_rate": session["hourly_rate_snapshot"],
            "snacks_text": snacks,
            "drinks_text": drinks,
            "items_cost": session["items_cost"],
            "duration_cost": session["duration_cost"],
            "total_cost": session["total_cost"],
            "received_amount": session["received_amount"],
            "comment": session.get("comment") or "",
        }
        return row, items

    def sync_all(self, progress_callback=None):
        """
        Pushes every locally unsynced, completed session (and its line items)
        up to Supabase. Returns a summary dict; never raises.
        progress_callback(i, n), if given, is called before each row is sent.
        """
        if not self.is_configured():
            return {"ok": False, "message": "Cloud sync is not configured yet.", "synced": 0, "failed": 0}

        pending = self.db.get_unsynced_sessions()
        if not pending:
            return {"ok": True, "message": "Nothing to sync — everything is already up to date.", "synced": 0, "failed": 0}

        synced, failed = 0, 0
        upsert_headers = self._headers({"Prefer": "resolution=merge-duplicates,return=minimal"})

        for i, session in enumerate(pending):
            if progress_callback:
                progress_callback(i + 1, len(pending))
            try:
                row, items = self._session_to_row(session)
                resp = requests.post(
                    f"{self._base_url()}/sessions", headers=upsert_headers, json=[row], timeout=15,
                )
                if resp.status_code not in (200, 201, 204):
                    failed += 1
                    continue

                if items:
                    item_rows = [
                        {
                            "id": it["id"],
                            "session_id": it["session_id"],
                            "item_name": it["item_name_snapshot"],
                            "category": it["category_snapshot"],
                            "unit_price": it["unit_price_snapshot"],
                            "quantity": it["quantity"],
                            "subtotal": it["subtotal"],
                        }
                        for it in items
                    ]
                    resp2 = requests.post(
                        f"{self._base_url()}/session_items", headers=upsert_headers, json=item_rows, timeout=15,
                    )
                    if resp2.status_code not in (200, 201, 204):
                        failed += 1
                        continue

                self.db.mark_synced(session["id"])
                synced += 1
            except requests.exceptions.RequestException:
                # Likely offline mid-batch — stop hammering, the rest stay
                # queued as synced=0 and will be retried next time.
                failed += len(pending) - i
                break

        message = f"Synced {synced} record(s)."
        if failed:
            message += f" {failed} still pending — will retry next time."
        return {"ok": failed == 0, "message": message, "synced": synced, "failed": failed}
