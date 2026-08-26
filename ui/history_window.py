"""
ui/history_window.py — Browse completed sessions, export CSV, trigger sync,
and edit the "Received" amount after the fact if a payment was corrected.
"""
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import date, timedelta


class HistoryWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.db = app.db
        self.title("History & Records")
        self.geometry("1040x520")
        # Deliberately NOT self.transient(app.root) here: a transient
        # window is treated by the window manager (and by Windows' own
        # title-bar handling) as a dialog subordinate to its parent, which
        # conventionally strips the Minimize/Maximize buttons and leaves
        # only Close. This window is meant to be freely resized/maximized/
        # minimized on its own, so it stays a full, independent top-level
        # window instead -- that's what restores the normal three-button
        # title bar. It doesn't grab_set() either, so the main window
        # stays fully usable alongside it either way.
        self.resizable(True, True)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.range_var = tk.StringVar(value="All time")
        ttk.Combobox(
            top, textvariable=self.range_var, state="readonly",
            values=["Today", "This Week", "This Month", "All time", "Custom Range"], width=14,
        ).pack(side="left", padx=(4, 12))

        # Custom "From ... to ..." range -- only editable once "Custom
        # Range" is picked above, so it's clear these two fields aren't
        # in effect otherwise. Defaults to the last 30 days so there's
        # something sensible to tweak the moment it's enabled.
        ttk.Label(top, text="From:").pack(side="left")
        self.from_var = tk.StringVar(value=(date.today() - timedelta(days=30)).isoformat())
        self.from_entry = ttk.Entry(top, textvariable=self.from_var, width=11, state="disabled")
        self.from_entry.pack(side="left", padx=(4, 8))
        self.from_entry.bind("<Return>", lambda e: self._apply_custom_range())

        ttk.Label(top, text="to").pack(side="left")
        self.to_var = tk.StringVar(value=date.today().isoformat())
        self.to_entry = ttk.Entry(top, textvariable=self.to_var, width=11, state="disabled")
        self.to_entry.pack(side="left", padx=(4, 8))
        self.to_entry.bind("<Return>", lambda e: self._apply_custom_range())

        self.apply_range_btn = ttk.Button(
            top, text="Apply", command=self._apply_custom_range, state="disabled"
        )
        self.apply_range_btn.pack(side="left", padx=(0, 12))

        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(top, text="Sync Now", command=self.app.sync_now).pack(side="left", padx=4)
        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side="left", padx=4)
        self.range_var.trace_add("write", lambda *a: self._on_range_changed())

        # Free-text search -- filters within whatever date range is
        # currently selected above, live as you type, the same way the
        # main window's "Search table" box works. Matches against exactly
        # what's shown in each row (table, date/times, snacks, drinks,
        # cost, received, comment) so if you can see it in the list, you
        # can find it by typing part of it.
        search_row = ttk.Frame(self, padding=(8, 0, 8, 8))
        search_row.pack(fill="x")
        ttk.Label(search_row, text="Search:").pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *a: self._apply_search())
        ttk.Button(search_row, text="Clear", command=lambda: self.search_var.set("")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            search_row, text="matches date (Gregorian or Shamsi), table, snacks, drinks, cost, received, comment",
            foreground="gray",
        ).pack(side="left", padx=(10, 0))

        # Records list, with a vertical scrollbar wired to it -- a bare
        # Treeview can silently clip rows below the visible area with no
        # visual indicator and no way to drag to a position, so both the
        # scrollbar and the Treeview share the same yview/yscrollcommand
        # link (the standard Tk pairing for this).
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        cols = ("date", "shamsi", "table", "start", "end", "duration", "snacks", "drinks", "cost", "received", "comment", "synced")
        headers = ["Date (Gregorian)", "Date (Shamsi)", "Table", "Start", "End", "Duration", "Snacks", "Drinks", "Cost", "Received", "Comment", "Synced"]
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            width = 140 if c in ("snacks", "drinks", "comment") else (105 if c in ("date", "shamsi") else 85)
            self.tree.column(c, width=width, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._on_row_double_click)

        # Mouse wheel scrolling over a Treeview is usually already bound
        # by Tk's built-in class bindings, but that can be inconsistent
        # across platforms/Tk builds -- bind it explicitly so it's
        # guaranteed to work regardless: Button-4/5 for X11, MouseWheel
        # (with its signed `delta`) for Windows and macOS.
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Button-4>", lambda e: self.tree.yview_scroll(-1, "units"))
        self.tree.bind("<Button-5>", lambda e: self.tree.yview_scroll(1, "units"))

        self.summary_label = ttk.Label(self, text="", foreground="gray")
        self.summary_label.pack(anchor="w", padx=8, pady=(0, 8))

        self._rows_cache = []
        self._visible_rows = []
        self._custom_from = None
        self._custom_to = None
        self.refresh()

    def _on_range_changed(self):
        is_custom = self.range_var.get() == "Custom Range"
        state = "normal" if is_custom else "disabled"
        self.from_entry.config(state=state)
        self.to_entry.config(state=state)
        self.apply_range_btn.config(state=state)
        if is_custom:
            self._apply_custom_range()
        else:
            self.refresh()

    def _apply_custom_range(self):
        d_from = self.from_var.get().strip()
        d_to = self.to_var.get().strip()
        try:
            date.fromisoformat(d_from)
            date.fromisoformat(d_to)
        except ValueError:
            messagebox.showerror(
                "Invalid date", "Please enter both dates as YYYY-MM-DD (e.g. 2026-01-31).", parent=self
            )
            return
        if d_from > d_to:
            messagebox.showerror(
                "Invalid range", "The 'From' date must be on or before the 'to' date.", parent=self
            )
            return
        self._custom_from, self._custom_to = d_from, d_to
        self.refresh()

    def _date_range(self):
        today = date.today()
        v = self.range_var.get()
        if v == "Today":
            return today.isoformat(), today.isoformat()
        if v == "This Week":
            start = today - timedelta(days=today.weekday())
            return start.isoformat(), today.isoformat()
        if v == "This Month":
            return today.replace(day=1).isoformat(), today.isoformat()
        if v == "Custom Range":
            return self._custom_from, self._custom_to
        return None, None

    def refresh(self):
        d_from, d_to = self._date_range()
        self._rows_cache = self.db.get_history(date_from=d_from, date_to=d_to)
        self._apply_search()

    def _row_display_values(self, r):
        """The exact strings shown in one row -- used both to populate the
        Treeview and, joined together, as the text the search box matches
        against, so search always finds whatever's visibly on screen."""
        cur = self.db.get_currency_symbol()
        return (
            r["date"], r["shamsi_date"] or "-", r["table_name"],
            self._fmt_time(r["start_time"]), self._fmt_time(r["end_time"]),
            self._fmt_duration(r["duration_seconds"]),
            r["snacks_text"] or "-", r["drinks_text"] or "-",
            f"{cur}{r['total_cost']:.2f}" if r["total_cost"] is not None else "-",
            f"{cur}{r['received_amount']:.2f}" if r["received_amount"] is not None else "-",
            r["comment"] or "-",
            "\u2713" if r["synced"] else "\u23f3",
        )

    def _apply_search(self):
        """Filter the current date-range results (self._rows_cache) by the
        search box, live as it's typed, without re-querying the database --
        the date range and the search text are independent, so changing
        one never resets the other."""
        query = self.search_var.get().strip().lower()
        if query:
            rows = [
                r for r in self._rows_cache
                if query in " ".join(str(v) for v in self._row_display_values(r)).lower()
            ]
        else:
            rows = self._rows_cache
        self._visible_rows = rows

        self.tree.delete(*self.tree.get_children())
        cur = self.db.get_currency_symbol()
        total_cost, total_received = 0.0, 0.0
        for r in rows:
            self.tree.insert("", "end", iid=r["id"], values=self._row_display_values(r))
            total_cost += r["total_cost"] or 0
            total_received += r["received_amount"] or 0

        if query:
            summary = (
                f"{len(rows)} of {len(self._rows_cache)} record(s) match \u00b7 "
                f"Total cost {cur}{total_cost:.2f} \u00b7 Total received {cur}{total_received:.2f}"
            )
        else:
            summary = (
                f"{len(rows)} record(s) \u00b7 Total cost {cur}{total_cost:.2f} \u00b7 "
                f"Total received {cur}{total_received:.2f}"
            )
        self.summary_label.config(text=summary)

    @staticmethod
    def _fmt_time(iso_str):
        if not iso_str:
            return "-"
        if "T" in iso_str:
            return iso_str.split("T")[1][:5]
        return iso_str[-8:-3]

    @staticmethod
    def _fmt_duration(seconds):
        if seconds is None:
            return "-"
        h, rem = divmod(int(seconds), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m}m"

    def _on_mousewheel(self, event):
        # Windows reports event.delta in multiples of 120; macOS reports
        # small per-notch values. Normalizing both to a single "unit" step
        # keeps scroll speed reasonable instead of jumping by 120 rows.
        direction = -1 if event.delta > 0 else 1
        self.tree.yview_scroll(direction, "units")

    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        row = next((r for r in self._visible_rows if r["id"] == sel[0]), None)
        if row:
            EditReceivedDialog(self, self.app, row)

    def export_csv(self):
        """Exports exactly what's currently visible -- if the search box
        or date range is narrowing the list, the export matches it,
        rather than silently exporting everything regardless of filters."""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], initialfile="buildtime_history.csv"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(
                ["Date (Gregorian)", "Date (Shamsi)", "Table", "Start", "End", "Duration (s)", "Snacks", "Drinks",
                 "Items Cost", "Duration Cost", "Total Cost", "Received", "Comment", "Synced"]
            )
            for r in self._visible_rows:
                w.writerow(
                    [r["date"], r["shamsi_date"] or "", r["table_name"], r["start_time"], r["end_time"],
                     r["duration_seconds"], r["snacks_text"] or "", r["drinks_text"] or "", r["items_cost"],
                     r["duration_cost"], r["total_cost"], r["received_amount"], r["comment"] or "",
                     "Yes" if r["synced"] else "No"]
                )
        messagebox.showinfo("Exported", f"History exported to:\n{path}", parent=self)


class EditReceivedDialog(tk.Toplevel):
    def __init__(self, parent, app, row):
        super().__init__(parent)
        self.app = app
        self.row = row
        self.title(f"{row['table_name']} \u2014 {row['date']}")
        self.transient(parent)
        self.grab_set()
        cur = app.db.get_currency_symbol()

        pad = dict(padx=10, pady=4)
        ttk.Label(self, text=f"Duration: {(row['duration_seconds'] or 0) // 60} min", **pad).pack(anchor="w")
        ttk.Label(self, text=f"Snacks: {row['snacks_text'] or '-'}", **pad).pack(anchor="w")
        ttk.Label(self, text=f"Drinks: {row['drinks_text'] or '-'}", **pad).pack(anchor="w")
        total_display = f"{cur}{row['total_cost']:.2f}" if row["total_cost"] is not None else "-"
        ttk.Label(self, text=f"Total cost: {total_display}", **pad).pack(anchor="w")

        row2 = ttk.Frame(self); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Received amount:").pack(side="left")
        initial = f"{row['received_amount']:.2f}" if row["received_amount"] is not None else "0"
        self.received_var = tk.StringVar(value=initial)
        ttk.Entry(row2, textvariable=self.received_var, width=10).pack(side="left", padx=4)

        ttk.Label(self, text="Comment:", **pad).pack(anchor="w")
        self.comment_var = tk.StringVar(value=row.get("comment") or "")
        ttk.Entry(self, textvariable=self.comment_var).pack(fill="x", padx=10)

        btns = ttk.Frame(self); btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=10)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

    def _save(self):
        try:
            amount = float(self.received_var.get())
        except ValueError:
            messagebox.showerror("Invalid amount", "Please enter a valid number.", parent=self)
            return
        self.app.db.update_received_amount(self.row["id"], amount)
        self.app.db.update_comment(self.row["id"], self.comment_var.get().strip())
        self.app.history_window_refresh()
        self.destroy()
