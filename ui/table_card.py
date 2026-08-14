"""
ui/table_card.py — One timer card for one table.

State machine per card:
    idle -> running -> awaiting_checkout -> idle (next customer)
                 ^-------------------------|
                 (Resume, if Stop was clicked by accident)

Every transition writes to the database immediately (see database.py) so
a crash or restart never loses a running session — main_window.py restores
any in-progress card on startup via restore().

The item catalog (Water, Chips, ...) is shown inline at the bottom of the
card itself while a table is running — a "+ Name (price)" button, a live
"xN" quantity, and a "-" button per item, plus running Items/Cost totals.
There's no separate "Add Items" popup; tapping a button writes straight to
the database immediately, the same as everything else in this app.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime


class TableCard(ttk.LabelFrame):
    def __init__(self, parent, app, table):
        super().__init__(parent, text=table["name"], padding=10)
        self.app = app
        self.db = app.db
        self.table = table
        self.session_id = None
        self.status = "idle"  # idle | running | awaiting_checkout
        self._tick_job = None
        self._cached_items_cost = 0.0
        self._cached_start = None
        self._cached_rate = 0.0
        self.qty_vars = {}     # item_id -> StringVar for "xN", rebuilt per session
        self.item_by_id = {}   # item_id -> item dict, rebuilt per session

        self.timer_label = ttk.Label(self, text="00:00:00", font=("Consolas", 22, "bold"))
        self.timer_label.pack(pady=(0, 4))

        self.status_label = ttk.Label(self, text="Idle", foreground="gray")
        self.status_label.pack()

        self.btn_frame = ttk.Frame(self)
        self.btn_frame.pack(fill="x", pady=(4, 0))

        self.start_btn = ttk.Button(self.btn_frame, text="\u25b6 Start", command=self.on_start)
        self.stop_btn = ttk.Button(self.btn_frame, text="\u25a0 Stop", command=self.on_stop)

        # Checkout panel (shown once a table is stopped, before the next customer)
        self.checkout_frame = ttk.Frame(self)
        self.cost_label = ttk.Label(self.checkout_frame, text="", justify="left", font=("Consolas", 9))
        self.cost_label.pack(anchor="w")

        rec_row = ttk.Frame(self.checkout_frame)
        rec_row.pack(fill="x", pady=(4, 4))
        ttk.Label(rec_row, text="Received:").pack(side="left")
        self.received_var = tk.StringVar()
        self.received_entry = ttk.Entry(rec_row, textvariable=self.received_var, width=10)
        self.received_entry.pack(side="left", padx=4)

        comment_row = ttk.Frame(self.checkout_frame)
        comment_row.pack(fill="x", pady=(0, 4))
        ttk.Label(comment_row, text="Comment (optional):").pack(anchor="w")
        self.comment_var = tk.StringVar()
        self.comment_entry = ttk.Entry(comment_row, textvariable=self.comment_var)
        self.comment_entry.pack(fill="x")

        checkout_btns = ttk.Frame(self.checkout_frame)
        checkout_btns.pack(fill="x")
        ttk.Button(checkout_btns, text="\u2713 Finish", command=self.on_finish).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(checkout_btns, text="\u21ba Resume", command=self.on_resume).pack(
            side="left", expand=True, fill="x", padx=(4, 0)
        )

        # Inline "Add Items" area — lives at the very bottom of the card,
        # only shown while the table is running. Built fresh each time a
        # session starts/resumes/restores so it always reflects the
        # current item catalog.
        self.items_section = ttk.Frame(self)
        self.items_rows_frame = ttk.Frame(self.items_section)
        self.items_rows_frame.pack(fill="x")
        self.items_totals_label = ttk.Label(self.items_section, text="", font=("", 10, "bold"))
        self.items_totals_label.pack(anchor="e", pady=(6, 0))

        self._render_idle()

    # ------------------------------------------------------------------
    # State renders
    # ------------------------------------------------------------------
    def _clear_buttons(self):
        for w in (self.start_btn, self.stop_btn):
            w.pack_forget()
        self.checkout_frame.pack_forget()
        self.items_section.pack_forget()

    def _render_idle(self):
        self._clear_buttons()
        self.status = "idle"
        self.session_id = None
        self.timer_label.config(text="00:00:00")
        self.status_label.config(text="Idle", foreground="gray")
        self.start_btn.pack(fill="x")
        self._stop_tick()

    def _render_running(self):
        self._clear_buttons()
        self.status = "running"
        self.status_label.config(text="Running", foreground="#1a7f37")
        self.stop_btn.pack(fill="x")
        self._build_items_section()
        self.items_section.pack(fill="x", pady=(8, 0))
        self._start_tick()

    def _render_awaiting_checkout(self, session):
        self._clear_buttons()
        self.status = "awaiting_checkout"
        self.status_label.config(text="Awaiting Checkout", foreground="#b35900")
        self._stop_tick()
        secs = session["duration_seconds"] or 0
        self.timer_label.config(text=self._fmt(secs))
        cur = self.db.get_currency_symbol()
        self.cost_label.config(
            text=(
                f"Items:    {cur}{session['items_cost']:.2f}\n"
                f"Duration: {cur}{session['duration_cost']:.2f}\n"
                f"Total:    {cur}{session['total_cost']:.2f}"
            )
        )
        self.received_var.set(f"{session['total_cost']:.2f}")
        self.comment_var.set(session.get("comment") or "")
        self.checkout_frame.pack(fill="x")

    # ------------------------------------------------------------------
    # Inline item rows ("+ Name (price)"  xN  "-")
    # ------------------------------------------------------------------
    def _build_items_section(self):
        for w in self.items_rows_frame.winfo_children():
            w.destroy()
        self.qty_vars = {}

        items = self.db.list_items(active_only=True)
        self.item_by_id = {i["id"]: i for i in items}

        if not items:
            ttk.Label(
                self.items_rows_frame,
                text="No items configured.\nAdd some via Settings \u25b8 Manage Items.",
                foreground="gray", justify="center",
            ).pack(pady=6)
            self.items_totals_label.config(text="")
            return

        self.items_rows_frame.columnconfigure(0, weight=1)
        for r, item in enumerate(items):
            add_btn = ttk.Button(
                self.items_rows_frame,
                text=f"+ {item['name']} ({item['price']:.2f})",
                command=lambda it=item: self._add_item(it),
            )
            add_btn.grid(row=r, column=0, sticky="ew", padx=(0, 6), pady=2)

            qty_var = tk.StringVar(value="x0")
            self.qty_vars[item["id"]] = qty_var
            ttk.Label(self.items_rows_frame, textvariable=qty_var, width=4, anchor="center").grid(
                row=r, column=1, padx=(0, 6)
            )

            ttk.Button(
                self.items_rows_frame, text="-", width=3, command=lambda it=item: self._remove_item(it)
            ).grid(row=r, column=2)

        self._refresh_items_section()

    def _refresh_items_section(self):
        rows = self.db.get_session_items(self.session_id)
        qty_by_item = {r["item_id"]: r["quantity"] for r in rows}
        total_items = 0
        total_cost = 0.0
        for item_id, qty_var in self.qty_vars.items():
            qty = qty_by_item.get(item_id, 0)
            qty_var.set(f"x{qty}")
            total_items += qty
        for r in rows:
            total_cost += r["subtotal"]
        self.items_totals_label.config(text=f"Items: {total_items}   Cost: {total_cost:.2f}")

    def _add_item(self, item):
        self.db.add_or_increment_item(self.session_id, item, delta=1)
        self._on_items_changed()

    def _remove_item(self, item):
        self.db.add_or_increment_item(self.session_id, item, delta=-1)
        self._on_items_changed()

    def _on_items_changed(self):
        session = self.db.get_session(self.session_id)
        self._cached_items_cost = session["items_cost"]
        self._refresh_items_section()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def on_start(self):
        self.session_id = self.db.start_session(self.table["id"], self.table["name"])
        session = self.db.get_session(self.session_id)
        self._cached_start = datetime.fromisoformat(session["start_time"])
        self._cached_rate = session["hourly_rate_snapshot"]
        self._cached_items_cost = 0.0
        self._render_running()

    def on_stop(self):
        if not messagebox.askyesno("Stop table", f"Stop the timer for {self.table['name']}?"):
            return
        round_minutes = int(self.db.get_setting("round_billed_minutes", "0") or 0)
        session = self.db.stop_session(self.session_id, round_minutes=round_minutes)
        self._render_awaiting_checkout(session)

    def on_resume(self):
        self.db.resume_session(self.session_id)
        session = self.db.get_session(self.session_id)
        self._cached_start = datetime.fromisoformat(session["start_time"])
        self._cached_rate = session["hourly_rate_snapshot"]
        self._cached_items_cost = session["items_cost"]
        self._render_running()

    def on_finish(self):
        try:
            amount = float(self.received_var.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid amount", "Please enter a valid number for the received amount.")
            return
        comment = self.comment_var.get().strip()
        self.db.finish_session(self.session_id, amount, comment)
        self.app.on_session_completed()
        self._render_idle()

    # ------------------------------------------------------------------
    # Crash / restart recovery
    # ------------------------------------------------------------------
    def restore(self, session):
        """Re-attach an already-running or awaiting-checkout session found in the DB at startup."""
        self.session_id = session["id"]
        if session["status"] == "running":
            self._cached_start = datetime.fromisoformat(session["start_time"])
            self._cached_rate = session["hourly_rate_snapshot"]
            self._cached_items_cost = session["items_cost"]
            self._render_running()
        elif session["status"] == "awaiting_checkout":
            self._render_awaiting_checkout(session)

    # ------------------------------------------------------------------
    # Ticking (wall-clock based, so it can't drift even under system load)
    # ------------------------------------------------------------------
    def _start_tick(self):
        self._tick()

    def _stop_tick(self):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None

    def _tick(self):
        if self.status != "running" or not self._cached_start:
            return
        elapsed = (datetime.now() - self._cached_start).total_seconds()
        self.timer_label.config(text=self._fmt(elapsed))
        live_cost = self._cached_items_cost + (elapsed / 3600.0) * self._cached_rate
        cur = self.db.get_currency_symbol()
        self.status_label.config(text=f"Running \u00b7 est. {cur}{live_cost:.2f}", foreground="#1a7f37")
        self._tick_job = self.after(500, self._tick)

    @staticmethod
    def _fmt(seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
