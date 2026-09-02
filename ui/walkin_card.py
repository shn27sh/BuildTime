"""
ui/walkin_card.py — One pinned, always-visible card for walk-in snack/drink
sales that aren't tied to any table: no table name to configure, no
stopwatch, no duration billing. Tap items to build a cart, then Complete
Sale to check out. Mirrors TableCard's item-tapping and checkout UI (see
table_card.py), minus everything timer-related.

State machine per card (shorter than TableCard's -- no timer involved):
    open (session_id is None until the first item is tapped -- created
          lazily so an untouched cart leaves no row in the database)
        -> [Complete Sale] -> checkout (status='walkin_checkout')
                                   -> [Finish] -> open (ready for next sale)
                                   -> [Back to Cart] -> open (add more first)

Every transition writes to the database immediately, exactly like
TableCard, so a crash never loses a cart in progress -- main_window.py
restores it on startup via restore(), the same way it restores tables.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from database import WALKIN_TABLE_NAME
from ui.theme import COLORS


class WalkInCard(ttk.LabelFrame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.db = app.db
        self.session_id = None
        self.status = "open"  # open | checkout
        self.qty_vars = {}     # item_id -> StringVar for "xN", rebuilt per catalog refresh
        self.item_by_id = {}   # item_id -> item dict, rebuilt per catalog refresh
        self._folded = False

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text=WALKIN_TABLE_NAME, font=("", 11, "bold")).pack(side="left")
        self.fold_btn = ttk.Button(header, text="Fold", width=8, command=self._toggle_fold)
        self.fold_btn.pack(side="right")

        self.status_label = ttk.Label(self, text="Open", foreground="#1a7f37")
        self.status_label.pack()

        self.comment_var = tk.StringVar()
        self.compact_comment_frame = ttk.Frame(self)
        ttk.Label(self.compact_comment_frame, text="Comment (optional):").pack(anchor="w")
        ttk.Entry(self.compact_comment_frame, textvariable=self.comment_var).pack(fill="x")
        self.compact_comment_frame.pack(fill="x", pady=(8, 0))

        ttk.Label(
            self, text="Snacks & drinks not tied to a table", foreground=COLORS["muted"],
            wraplength=190, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        # Checkout panel (shown once "Complete Sale" is tapped)
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
        self.comment_entry = ttk.Entry(comment_row, textvariable=self.comment_var)
        self.comment_entry.pack(fill="x")

        checkout_btns = ttk.Frame(self.checkout_frame)
        checkout_btns.pack(fill="x")
        ttk.Button(checkout_btns, text="\u2713 Finish", style="Accent.TButton", command=self.on_finish).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(checkout_btns, text="\u2190 Back to Cart", style="Start.TButton", command=self.on_back_to_cart).pack(
            side="left", expand=True, fill="x", padx=(4, 0)
        )

        # Inline "Add Items" area -- always visible while the cart is open,
        # same "+ Name (price)" / "xN" / "-" pattern as a running table.
        self.items_section = ttk.Frame(self)
        self.items_rows_frame = ttk.Frame(self.items_section)
        self.items_rows_frame.pack(fill="x")
        self.items_totals_label = ttk.Label(self.items_section, text="", font=("", 10, "bold"))
        self.items_totals_label.pack(anchor="e", pady=(6, 0))

        self.complete_sale_btn = ttk.Button(
            self.items_section, text="Complete Sale", style="Checkout.TButton", command=self.on_complete_sale
        )
        self.complete_sale_btn.pack(fill="x", pady=(8, 0))

        self._render_open()

    def _toggle_fold(self):
        if self.status == "checkout":
            return
        self._folded = not self._folded
        self.fold_btn.config(text="Unfold" if self._folded else "Fold")
        if self._folded:
            self.items_section.pack_forget()
        else:
            self.items_section.pack(fill="x")

    # ------------------------------------------------------------------
    # State renders
    # ------------------------------------------------------------------
    def _render_open(self):
        self.status = "open"
        self.status_label.config(text="Open", foreground="#1a7f37")
        self.checkout_frame.pack_forget()
        self._build_items_section()
        if not self._folded:
            self.items_section.pack(fill="x")

    def _render_checkout(self, session):
        self.status = "checkout"
        self._folded = False
        self.fold_btn.config(text="Fold")
        self.status_label.config(text="Awaiting Checkout", foreground="#b35900")
        self.items_section.pack_forget()
        cur = self.db.get_currency_symbol()
        self.cost_label.config(text=f"Items:    {cur}{session['items_cost']:.2f}\nTotal:    {cur}{session['total_cost']:.2f}")
        self.received_var.set(f"{session['total_cost']:.2f}")
        self.comment_var.set(session.get("comment") or "")
        self.checkout_frame.pack(fill="x")

    # ------------------------------------------------------------------
    # Inline item rows -- same pattern as TableCard._build_items_section
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
            self.complete_sale_btn.config(state="disabled")
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
        rows = self.db.get_session_items(self.session_id) if self.session_id else []
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
        self.complete_sale_btn.config(state=("normal" if total_items > 0 else "disabled"))

    def _add_item(self, item):
        if self.session_id is None:
            # Lazily open the underlying session on the very first tap, so
            # an untouched cart never leaves a row in the database.
            self.session_id = self.db.start_walkin_sale()
        self.db.add_or_increment_item(self.session_id, item, delta=1)
        self._refresh_items_section()

    def _remove_item(self, item):
        if self.session_id is None:
            return
        self.db.add_or_increment_item(self.session_id, item, delta=-1)
        session = self.db.get_session(self.session_id)
        if session and session["items_cost"] == 0:
            # Cart emptied back out via "-" taps -- drop the now-empty
            # session instead of leaving a ghost "active session" behind.
            self.db.delete_session_if_empty(self.session_id)
            self.session_id = None
        self._refresh_items_section()

    def refresh_items_catalog(self):
        """Rebuild the item-catalog buttons in place if the cart is
        currently open -- called by MainWindow whenever Settings adds,
        edits, hides, or shows an item. Unlike a table, this card's item
        buttons stay visible at all times and Settings is deliberately NOT
        locked while the cart just sits open (there's no stopwatch), so
        without this the buttons would silently go stale."""
        if self.status == "open":
            self._build_items_section()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def on_complete_sale(self):
        if self.session_id is None:
            return
        session = self.db.stop_walkin_sale(self.session_id)
        self._render_checkout(session)

    def on_back_to_cart(self):
        self.db.resume_walkin_sale(self.session_id)
        self._render_open()

    def on_finish(self):
        try:
            amount = float(self.received_var.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid amount", "Please enter a valid number for the received amount.")
            return
        comment = self.comment_var.get().strip()
        self.db.finish_session(self.session_id, amount, comment)
        self.session_id = None
        self.app.on_session_completed()
        self._render_open()

    # ------------------------------------------------------------------
    # Crash / restart recovery
    # ------------------------------------------------------------------
    def restore(self, session):
        """Re-attach an already-open or already-at-checkout walk-in sale
        found in the DB at startup."""
        self.session_id = session["id"]
        if session["status"] == "walkin_open":
            self._render_open()
        elif session["status"] == "walkin_checkout":
            self._render_checkout(session)
