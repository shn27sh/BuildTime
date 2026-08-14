"""
ui/settings_window.py — Tabbed Settings dialog reachable from the
Settings dropdown menu, exactly as requested:
    Settings > Manage Tables / Snacks & Drinks / Pricing / Cloud Sync
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, initial_tab=0):
        super().__init__(app.root)
        self.app = app
        self.db = app.db
        self.title("Settings")
        self.geometry("560x480")
        self.transient(app.root)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tables_tab = ttk.Frame(nb)
        self.items_tab = ttk.Frame(nb)
        self.pricing_tab = ttk.Frame(nb)
        self.sync_tab = ttk.Frame(nb)

        nb.add(self.tables_tab, text="Tables")
        nb.add(self.items_tab, text="Snacks & Drinks")
        nb.add(self.pricing_tab, text="Pricing")
        nb.add(self.sync_tab, text="Cloud Sync")

        self._build_tables_tab()
        self._build_items_tab()
        self._build_pricing_tab()
        self._build_sync_tab()

        nb.select(initial_tab)

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def _build_tables_tab(self):
        f = self.tables_tab
        cols = ("name", "status")
        self.tables_tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        self.tables_tree.heading("name", text="Table Name")
        self.tables_tree.heading("status", text="Status")
        self.tables_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add Table", command=self._add_table).pack(side="left")
        ttk.Button(btns, text="Rename", command=self._rename_table).pack(side="left", padx=4)
        ttk.Button(btns, text="Hide/Show", command=self._toggle_table).pack(side="left")
        ttk.Button(btns, text="Move Up", command=lambda: self._move_table(-1)).pack(side="left", padx=4)
        ttk.Button(btns, text="Move Down", command=lambda: self._move_table(1)).pack(side="left")
        ttk.Label(
            f, text="This is where you add more tables \u2014 e.g. 5, 7, or as many as you run.",
            foreground="gray", wraplength=500,
        ).pack(anchor="w", padx=8)

        self._refresh_tables_tree()

    def _refresh_tables_tree(self):
        self.tables_tree.delete(*self.tables_tree.get_children())
        for t in self.db.list_tables(active_only=False):
            self.tables_tree.insert(
                "", "end", iid=str(t["id"]), values=(t["name"], "Active" if t["active"] else "Hidden")
            )

    def _selected_table_id(self):
        sel = self.tables_tree.selection()
        return int(sel[0]) if sel else None

    def _add_table(self):
        name = simpledialog.askstring("Add Table", "Table name:", parent=self)
        if name:
            self.db.add_table(name)
            self._refresh_tables_tree()
            self.app.refresh_tables()

    def _rename_table(self):
        tid = self._selected_table_id()
        if tid is None:
            return
        name = simpledialog.askstring("Rename Table", "New name:", parent=self)
        if name:
            self.db.rename_table(tid, name)
            self._refresh_tables_tree()
            self.app.refresh_tables()

    def _toggle_table(self):
        tid = self._selected_table_id()
        if tid is None:
            return
        tables = {t["id"]: t for t in self.db.list_tables(active_only=False)}
        cur = tables[tid]
        if cur["active"] and self.app.table_has_active_session(tid):
            messagebox.showwarning("Table busy", "This table has a running session and can't be hidden.", parent=self)
            return
        self.db.set_table_active(tid, not cur["active"])
        self._refresh_tables_tree()
        self.app.refresh_tables()

    def _move_table(self, direction):
        tid = self._selected_table_id()
        if tid is None:
            return
        self.db.move_table(tid, direction)
        self._refresh_tables_tree()
        self.app.refresh_tables()

    # ------------------------------------------------------------------
    # Items (snacks & drinks)
    # ------------------------------------------------------------------
    def _build_items_tab(self):
        f = self.items_tab
        cols = ("name", "category", "price", "status")
        self.items_tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c, label, w in (
            ("name", "Name", 140), ("category", "Category", 80),
            ("price", "Price", 80), ("status", "Status", 80),
        ):
            self.items_tree.heading(c, text=label)
            self.items_tree.column(c, width=w)
        self.items_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add Item", command=self._add_item).pack(side="left")
        ttk.Button(btns, text="Edit", command=self._edit_item).pack(side="left", padx=4)
        ttk.Button(btns, text="Hide/Show", command=self._toggle_item).pack(side="left")
        ttk.Label(
            f, text="Price changes only apply to new orders \u2014 past records keep their original price.",
            foreground="gray", wraplength=500,
        ).pack(anchor="w", padx=8)

        self._refresh_items_tree()

    def _refresh_items_tree(self):
        self.items_tree.delete(*self.items_tree.get_children())
        cur = self.db.get_currency_symbol()
        for i in self.db.list_items(active_only=False):
            self.items_tree.insert(
                "", "end", iid=str(i["id"]),
                values=(i["name"], i["category"], f"{cur}{i['price']:.2f}", "Active" if i["active"] else "Hidden"),
            )

    def _selected_item_id(self):
        sel = self.items_tree.selection()
        return int(sel[0]) if sel else None

    def _add_item(self):
        result = ItemDialog(self, "Add Item").result
        if result:
            self.db.add_item(result["name"], result["category"], result["price"])
            self._refresh_items_tree()

    def _edit_item(self):
        iid = self._selected_item_id()
        if iid is None:
            return
        item = next(i for i in self.db.list_items(active_only=False) if i["id"] == iid)
        result = ItemDialog(self, "Edit Item", item).result
        if result:
            self.db.update_item(iid, result["name"], result["category"], result["price"])
            self._refresh_items_tree()

    def _toggle_item(self):
        iid = self._selected_item_id()
        if iid is None:
            return
        item = next(i for i in self.db.list_items(active_only=False) if i["id"] == iid)
        self.db.set_item_active(iid, not item["active"])
        self._refresh_items_tree()

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------
    def _build_pricing_tab(self):
        f = self.pricing_tab
        pad = dict(padx=8, pady=6)

        row = ttk.Frame(f); row.pack(fill="x", **pad)
        ttk.Label(row, text="Hourly rate:", width=26).pack(side="left")
        self.rate_var = tk.StringVar(value=self.db.get_setting("hourly_rate", "0"))
        ttk.Entry(row, textvariable=self.rate_var, width=12).pack(side="left")

        row2 = ttk.Frame(f); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Currency symbol:", width=26).pack(side="left")
        self.currency_var = tk.StringVar(value=self.db.get_setting("currency_symbol", "$"))
        ttk.Entry(row2, textvariable=self.currency_var, width=6).pack(side="left")

        row3 = ttk.Frame(f); row3.pack(fill="x", **pad)
        ttk.Label(row3, text="Round billed time up to (min):", width=26).pack(side="left")
        self.round_var = tk.StringVar(value=self.db.get_setting("round_billed_minutes", "0"))
        ttk.Entry(row3, textvariable=self.round_var, width=6).pack(side="left")

        ttk.Label(
            f, text="0 = bill the exact duration. E.g. 15 rounds each session up to the next 15 minutes.",
            foreground="gray", wraplength=500,
        ).pack(anchor="w", padx=8)

        ttk.Button(f, text="Save Pricing", command=self._save_pricing).pack(anchor="w", padx=8, pady=12)

    def _save_pricing(self):
        try:
            rate = float(self.rate_var.get())
            round_min = int(self.round_var.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid input", "Hourly rate and rounding must be numbers.", parent=self)
            return
        self.db.set_setting("hourly_rate", rate)
        self.db.set_setting("currency_symbol", self.currency_var.get() or "$")
        self.db.set_setting("round_billed_minutes", round_min)
        self.app.refresh_status_bar()
        messagebox.showinfo("Saved", "Pricing settings saved.", parent=self)

    # ------------------------------------------------------------------
    # Cloud sync
    # ------------------------------------------------------------------
    def _build_sync_tab(self):
        f = self.sync_tab
        pad = dict(padx=8, pady=6)

        ttk.Label(
            f,
            text="This app works fully offline. Records always save locally first.\n"
                 "Cloud sync is optional \u2014 click 'Sync Now' or enable auto-sync below.",
            foreground="gray", wraplength=500, justify="left",
        ).pack(anchor="w", padx=8, pady=(8, 12))

        row = ttk.Frame(f); row.pack(fill="x", **pad)
        ttk.Label(row, text="Supabase Project URL:", width=20).pack(side="left")
        self.url_var = tk.StringVar(value=self.db.get_setting("supabase_url", ""))
        ttk.Entry(row, textvariable=self.url_var, width=40).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(f); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Supabase API Key:", width=20).pack(side="left")
        self.key_var = tk.StringVar(value=self.db.get_setting("supabase_key", ""))
        self.key_entry = ttk.Entry(row2, textvariable=self.key_var, width=40, show="*")
        self.key_entry.pack(side="left", fill="x", expand=True)

        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Show key", variable=self.show_key, command=self._toggle_key_visibility).pack(
            anchor="w", padx=8
        )

        row3 = ttk.Frame(f); row3.pack(fill="x", **pad)
        self.auto_sync_var = tk.BooleanVar(value=self.db.get_setting("auto_sync_enabled", "0") == "1")
        ttk.Checkbutton(row3, text="Enable automatic background sync every", variable=self.auto_sync_var).pack(
            side="left"
        )
        self.interval_var = tk.StringVar(value=self.db.get_setting("auto_sync_interval_minutes", "15"))
        ttk.Entry(row3, textvariable=self.interval_var, width=5).pack(side="left", padx=4)
        ttk.Label(row3, text="minutes").pack(side="left")

        btn_row = ttk.Frame(f); btn_row.pack(fill="x", padx=8, pady=8)
        ttk.Button(btn_row, text="Save", command=self._save_sync).pack(side="left")
        ttk.Button(btn_row, text="Test Connection", command=self._test_connection).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Sync Now", command=self.app.sync_now).pack(side="left")

        self.sync_status_label = ttk.Label(f, text="", foreground="gray", wraplength=500, justify="left")
        self.sync_status_label.pack(anchor="w", padx=8, pady=(8, 0))
        stats = self.db.sync_stats()
        self.sync_status_label.config(
            text=f"{stats['total']} completed record(s) total \u00b7 {stats['pending']} pending sync."
        )

    def _toggle_key_visibility(self):
        self.key_entry.config(show="" if self.show_key.get() else "*")

    def _save_sync(self):
        self._save_sync_silent()
        self.db.set_setting("auto_sync_enabled", "1" if self.auto_sync_var.get() else "0")
        try:
            interval = max(1, int(self.interval_var.get()))
        except ValueError:
            interval = 15
        self.db.set_setting("auto_sync_interval_minutes", interval)
        self.app.reload_sync_settings()
        messagebox.showinfo("Saved", "Cloud sync settings saved.", parent=self)

    def _save_sync_silent(self):
        self.db.set_setting("supabase_url", self.url_var.get().strip())
        self.db.set_setting("supabase_key", self.key_var.get().strip())

    def _test_connection(self):
        self._save_sync_silent()
        ok, msg = self.app.sync_manager.test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("Connection Test", msg, parent=self)


class ItemDialog(tk.Toplevel):
    """Modal add/edit dialog for a single snack/drink item."""

    def __init__(self, parent, title, item=None):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.transient(parent)
        self.grab_set()

        pad = dict(padx=8, pady=6)
        row = ttk.Frame(self); row.pack(fill="x", **pad)
        ttk.Label(row, text="Name:", width=10).pack(side="left")
        self.name_var = tk.StringVar(value=item["name"] if item else "")
        ttk.Entry(row, textvariable=self.name_var).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(self); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Category:", width=10).pack(side="left")
        self.cat_var = tk.StringVar(value=item["category"] if item else "Snack")
        ttk.Combobox(
            row2, textvariable=self.cat_var, values=["Snack", "Drink"], state="readonly", width=10
        ).pack(side="left")

        row3 = ttk.Frame(self); row3.pack(fill="x", **pad)
        ttk.Label(row3, text="Price:", width=10).pack(side="left")
        self.price_var = tk.StringVar(value=str(item["price"]) if item else "")
        ttk.Entry(row3, textvariable=self.price_var).pack(side="left")

        btns = ttk.Frame(self); btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=8)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        self.wait_window(self)

    def _save(self):
        name = self.name_var.get().strip()
        try:
            price = float(self.price_var.get())
        except ValueError:
            messagebox.showerror("Invalid price", "Price must be a number.", parent=self)
            return
        if not name:
            messagebox.showerror("Invalid name", "Name can't be empty.", parent=self)
            return
        self.result = {"name": name, "category": self.cat_var.get(), "price": price}
        self.destroy()
