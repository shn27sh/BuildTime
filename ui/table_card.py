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

from database import compute_duration_cost, validate_discount_percent, apply_discount


class TableCard(ttk.LabelFrame):
    def __init__(self, parent, app, table):
        super().__init__(parent, padding=10)
        self.app = app
        self.db = app.db
        self.table = table
        self.session_id = None
        self.status = "idle"  # idle | running | awaiting_checkout
        self._tick_job = None
        self._blink_job = None
        self._blink_on = True
        self._blink_color = "gray"
        self._cached_items_cost = 0.0
        self._cached_start = None
        self._cached_rate = 0.0
        self.qty_vars = {}     # item_id -> StringVar for "xN", rebuilt per session
        self.item_by_id = {}   # item_id -> item dict, rebuilt per session
        self._folded = False

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text=table["name"], font=("", 11, "bold")).pack(side="left")
        self.fold_btn = ttk.Button(header, text="Fold", width=8, command=self._toggle_fold)
        self.fold_btn.pack(side="right")

        self.timer_label = ttk.Label(self, text="00:00:00", font=("Consolas", 22, "bold"))
        self.timer_label.pack(pady=(0, 4))

        self.status_label = ttk.Label(self, text="Idle", foreground="gray")
        self.status_label.pack()

        self.btn_frame = ttk.Frame(self)
        self.btn_frame.pack(fill="x", pady=(4, 0))

        self.start_btn = ttk.Button(self.btn_frame, text="\u25b6 Start", command=self.on_start)
        self.stop_btn = ttk.Button(self.btn_frame, text="\u25a0 Stop", command=self.on_stop)

        self.comment_var = tk.StringVar()
        self.comment_var.trace_add("write", lambda *a: self._on_comment_changed())
        self.compact_comment_frame = ttk.Frame(self)
        ttk.Label(self.compact_comment_frame, text="Comment (optional):").pack(anchor="w")
        self.compact_comment_entry = ttk.Entry(self.compact_comment_frame, textvariable=self.comment_var)

        # Manually adjust stopwatch time (e.g. if user forgot to press Start)
        # Shown only while running, positioned right after Stop button and
        # before the comment box.
        self.adjustment_frame = ttk.Frame(self)
        adj_label = ttk.Label(self.adjustment_frame, text="Adjust time (min):", width=16)
        adj_label.pack(side="left")
        self.adjustment_var = tk.StringVar(value="0")
        adj_entry = ttk.Entry(self.adjustment_frame, textvariable=self.adjustment_var, width=5)
        adj_entry.pack(side="left", padx=(0, 6))
        ttk.Button(
            self.adjustment_frame, text="\u2212", width=3, command=self._subtract_time
        ).pack(side="left", padx=2)
        ttk.Button(
            self.adjustment_frame, text="\u002b", width=3, command=self._add_time
        ).pack(side="left", padx=2)

        # Comment -- ONE shared variable behind two entry boxes, so typing
        # in either one (while running, or later at checkout) keeps them
        # in sync automatically with no manual copying between them. Every
        # keystroke is saved immediately via the trace below (matching how
        # everything else in this app is written to the database the
        # instant it happens), rather than only being captured at Finish --
        # a note jotted down early in a long session shouldn't be at risk
        # if the app closes before that table is ever stopped.
        self.compact_comment_entry.pack(fill="x")
        self.compact_comment_frame.pack(fill="x", pady=(8, 0))

        # Checkout panel (shown once a table is stopped, before the next customer)
        self.checkout_frame = ttk.Frame(self)

        # Percentage discount -- applies ONLY to the duration/hourly charge
        # below, never to items. Editable at checkout time; 0% (no
        # discount) is the default for every new session. Deliberately its
        # own Entry+Apply pair (not a live-as-you-type recalculation) so a
        # partially-typed number never flashes an intermediate result --
        # same pattern as the History window's custom date range.
        discount_row = ttk.Frame(self.checkout_frame)
        discount_row.pack(fill="x", pady=(0, 4))
        ttk.Label(discount_row, text="Discount %:").pack(side="left")
        self.discount_var = tk.StringVar(value="0")
        self.discount_entry = ttk.Entry(discount_row, textvariable=self.discount_var, width=6)
        self.discount_entry.pack(side="left", padx=4)
        self.discount_entry.bind("<Return>", lambda e: self._apply_discount())
        ttk.Button(discount_row, text="Apply", command=self._apply_discount).pack(side="left")
        ttk.Label(discount_row, text="(duration only, never items)", foreground="gray").pack(
            side="left", padx=(6, 0)
        )

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

    def _toggle_fold(self):
        if self.status == "awaiting_checkout":
            return
        self._folded = not self._folded
        self.fold_btn.config(text="Unfold" if self._folded else "Fold")
        if self._folded:
            self.adjustment_frame.pack_forget()
            self.items_section.pack_forget()
            self.checkout_frame.pack_forget()
        elif self.status == "running":
            self.adjustment_frame.pack(fill="x", pady=(4, 0))
            self.items_section.pack(fill="x", pady=(8, 0))

    def _show_expanded_details(self):
        if not self._folded:
            return
        self._folded = False
        self.fold_btn.config(text="Fold")

    # ------------------------------------------------------------------
    # State renders
    # ------------------------------------------------------------------
    def _clear_buttons(self):
        for w in (self.start_btn, self.stop_btn):
            w.pack_forget()
        self.adjustment_frame.pack_forget()
        self.checkout_frame.pack_forget()
        self.items_section.pack_forget()

    def _render_idle(self):
        self._clear_buttons()
        self.status = "idle"
        self.session_id = None
        self._stop_blink()
        self.timer_label.config(text="00:00:00")
        self.status_label.config(text="Idle", foreground="gray")
        self.start_btn.pack(fill="x")
        self._stop_tick()

    def _render_running(self):
        self._clear_buttons()
        self.status = "running"
        self.status_label.config(text="Running", foreground="#1a7f37")
        self.stop_btn.pack(fill="x")
        self.adjustment_frame.pack(fill="x", pady=(4, 0))
        self._build_items_section()
        if not self._folded:
            self.items_section.pack(fill="x", pady=(8, 0))
        self._start_blink("#1a7f37")
        self._start_tick()

    def _render_awaiting_checkout(self, session):
        self._clear_buttons()
        self.status = "awaiting_checkout"
        self.status_label.config(text="Awaiting Checkout", foreground="#b35900")
        self._stop_tick()
        secs = session["duration_seconds"] or 0
        self.timer_label.config(text=self._fmt(secs))
        # Discount % and Comment reflect the session's stored values only
        # on this first render (Stop was just clicked, or this is a
        # restore/resume-then-stop) -- _apply_discount() below deliberately
        # does NOT re-run this line, so it never overwrites a comment the
        # user has already started typing.
        self.discount_var.set(self._fmt_discount(session["discount_percent"]))
        self.comment_var.set(session.get("comment") or "")
        self._update_cost_breakdown(session)
        self.checkout_frame.pack(fill="x")
        self._show_expanded_details()
        self._start_blink("#b35900")

    def _update_cost_breakdown(self, session):
        """Redraw just the cost lines (and re-sync Received to the new
        total) -- shared by the initial checkout render and by applying a
        discount, without touching Comment or rebuilding the whole panel."""
        cur = self.db.get_currency_symbol()
        lines = [
            f"Items:      {cur}{session['items_cost']:.2f}",
            f"Duration:   {cur}{session['duration_cost']:.2f}",
        ]
        discount_pct = session["discount_percent"] or 0
        if discount_pct > 0:
            _discounted, discount_amount = apply_discount(session["duration_cost"], discount_pct)
            lines.append(f"Discount:   -{cur}{discount_amount:.2f} ({self._fmt_discount(discount_pct)}%)")
        lines.append(f"Total:      {cur}{session['total_cost']:.2f}")
        self.cost_label.config(text="\n".join(lines))
        self.received_var.set(f"{session['total_cost']:.2f}")

    def _apply_discount(self):
        try:
            pct = validate_discount_percent(self.discount_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid discount", str(e))
            return
        session = self.db.set_session_discount(self.session_id, pct)
        self.discount_var.set(self._fmt_discount(pct))
        self._update_cost_breakdown(session)

    @staticmethod
    def _fmt_discount(pct):
        """0 -> '0', 25 -> '25', 12.5 -> '12.5' -- never a trailing '.0'
        for a whole-number percentage, but decimals still show cleanly."""
        pct = float(pct or 0)
        return f"{pct:g}"

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

    def _on_comment_changed(self):
        """Fires on every keystroke in either comment box (they share one
        StringVar). Saves immediately, same as items adding to the DB the
        instant you tap "+" -- a note jotted down early in a long session
        shouldn't be at risk of being lost. Guarded on session_id existing
        since this also fires from programmatic .set() calls made while
        idle (e.g. clearing it in on_start(), before a session exists)."""
        if self.session_id:
            self.db.update_comment(self.session_id, self.comment_var.get())

    def _add_time(self):
        """Add the specified number of minutes to the stopwatch by adjusting
        the session's start_time in the database."""
        try:
            minutes = float(self.adjustment_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter a valid number for minutes.")
            return
        if minutes < 0:
            messagebox.showwarning("Invalid input", "Cannot add negative time. Use the minus button to subtract.")
            return
        delta_seconds = minutes * 60
        session = self.db.adjust_session_elapsed_time(self.session_id, delta_seconds)
        if session:
            self._cached_start = datetime.fromisoformat(session["start_time"])
            self.adjustment_var.set("0")

    def _subtract_time(self):
        """Subtract the specified number of minutes from the stopwatch by
        adjusting the session's start_time in the database. Refuses to
        subtract more time than has already elapsed, to keep the timer from
        going negative."""
        try:
            minutes = float(self.adjustment_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter a valid number for minutes.")
            return
        if minutes < 0:
            messagebox.showwarning("Invalid input", "Cannot subtract negative time. Use the plus button to add.")
            return
        if not self.session_id:
            messagebox.showwarning("No active session", "There is no active table session to adjust.")
            return

        session = self.db.get_session(self.session_id)
        if not session:
            return

        elapsed_seconds = (datetime.now() - datetime.fromisoformat(session["start_time"])).total_seconds()
        requested_seconds = minutes * 60
        if requested_seconds > elapsed_seconds:
            elapsed_minutes = elapsed_seconds / 60.0
            messagebox.showwarning(
                "Time adjustment blocked",
                f"Cannot subtract {minutes:g} minute(s) because only {elapsed_minutes:.1f} minute(s) have elapsed. "
                "Please enter a smaller value or use the plus button to add time.",
            )
            return

        delta_seconds = -requested_seconds
        session = self.db.adjust_session_elapsed_time(self.session_id, delta_seconds)
        if session:
            self._cached_start = datetime.fromisoformat(session["start_time"])
            self.adjustment_var.set("0")

    def refresh_items_catalog(self):
        """Rebuild the item-catalog buttons in place if they're currently
        visible (this table is running) -- called by MainWindow whenever
        Settings adds, edits, hides, or shows an item. In practice this
        table can't actually BE running while Settings is open (a running
        stopwatch locks Settings), but it costs nothing to handle it
        properly rather than lean on that as a guarantee."""
        if self.status == "running":
            self._build_items_section()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def on_start(self):
        self.session_id = self.db.start_session(self.table["id"], self.table["name"])
        session = self.db.get_session(self.session_id)
        self._cached_start = datetime.fromisoformat(session["start_time"])
        self._cached_rate = session["hourly_rate_snapshot"]
        self._cached_items_cost = 0.0
        self.comment_var.set("")  # fresh customer -- clear any leftover text from the last one
        self._render_running()
        self.app.notify_stopwatch_state_changed()

    def on_stop(self):
        if not messagebox.askyesno("Stop table", f"Stop the timer for {self.table['name']}?"):
            return
        session = self.db.stop_session(self.session_id)
        self._render_awaiting_checkout(session)
        self.app.notify_stopwatch_state_changed()

    def on_resume(self):
        self.db.resume_session(self.session_id)
        session = self.db.get_session(self.session_id)
        self._cached_start = datetime.fromisoformat(session["start_time"])
        self._cached_rate = session["hourly_rate_snapshot"]
        self._cached_items_cost = session["items_cost"]
        self._render_running()
        self.app.notify_stopwatch_state_changed()

    def on_finish(self):
        try:
            amount = float(self.received_var.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid amount", "Please enter a valid number for the received amount.")
            return
        comment = self.comment_var.get().strip()
        self.db.finish_session(self.session_id, amount, comment)
        self.app.on_session_completed()
        self._render_idle()  # clears self.session_id first...
        self.comment_var.set("")  # ...so THIS doesn't overwrite the comment just saved above

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
            self.comment_var.set(session.get("comment") or "")
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

    def _start_blink(self, color):
        self._stop_blink()
        self._blink_color = color
        self._blink_on = True
        self._blink()

    def _stop_blink(self):
        if self._blink_job:
            self.after_cancel(self._blink_job)
            self._blink_job = None
        self._blink_on = True
        self.timer_label.config(foreground="")

    def _blink(self):
        if self.status not in ("running", "awaiting_checkout"):
            self._blink_job = None
            return
        self._blink_on = not self._blink_on
        color = self._blink_color if self._blink_on else "gray"
        self.timer_label.config(foreground=color)
        self.status_label.config(foreground=color)
        self._blink_job = self.after(650, self._blink)

    def _tick(self):
        if self.status != "running" or not self._cached_start:
            return
        elapsed = (datetime.now() - self._cached_start).total_seconds()
        self.timer_label.config(text=self._fmt(elapsed))
        live_cost = self._cached_items_cost + compute_duration_cost(elapsed, self._cached_rate)
        cur = self.db.get_currency_symbol()
        status_color = self._blink_color if self._blink_on else "gray"
        self.status_label.config(text=f"Running \u00b7 est. {cur}{live_cost:.2f}", foreground=status_color)
        self._tick_job = self.after(500, self._tick)

    @staticmethod
    def _fmt(seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
