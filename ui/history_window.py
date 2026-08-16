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
        self.geometry("920x520")
        self.transient(app.root)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.range_var = tk.StringVar(value="All time")
        ttk.Combobox(
            top, textvariable=self.range_var, state="readonly",
            values=["Today", "This Week", "This Month", "All time"], width=14,
        ).pack(side="left", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(top, text="Sync Now", command=self.app.sync_now).pack(side="left", padx=4)
        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side="left", padx=4)
        self.range_var.trace_add("write", lambda *a: self.refresh())

        cols = ("date", "table", "start", "end", "duration", "snacks", "drinks", "cost", "received", "comment", "synced")
        headers = ["Date", "Table", "Start", "End", "Duration", "Snacks", "Drinks", "Cost", "Received", "Comment", "Synced"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=140 if c in ("snacks", "drinks", "comment") else 85, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.tree.bind("<Double-1>", self._on_row_double_click)

        self.summary_label = ttk.Label(self, text="", foreground="gray")
        self.summary_label.pack(anchor="w", padx=8, pady=(0, 8))

        self._rows_cache = []
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
        return None, None

    def refresh(self):
        d_from, d_to = self._date_range()
        rows = self.db.get_history(date_from=d_from, date_to=d_to)
        self._rows_cache = rows
        self.tree.delete(*self.tree.get_children())
        cur = self.db.get_currency_symbol()
        total_cost, total_received = 0.0, 0.0
        for r in rows:
            self.tree.insert(
                "", "end", iid=r["id"],
                values=(
                    r["date"], r["table_name"],
                    self._fmt_time(r["start_time"]), self._fmt_time(r["end_time"]),
                    self._fmt_duration(r["duration_seconds"]),
                    r["snacks_text"] or "-", r["drinks_text"] or "-",
                    f"{cur}{r['total_cost']:.2f}",
                    f"{cur}{r['received_amount']:.2f}" if r["received_amount"] is not None else "-",
                    r["comment"] or "-",
                    "\u2713" if r["synced"] else "\u23f3",
                ),
            )
            total_cost += r["total_cost"] or 0
            total_received += r["received_amount"] or 0
        self.summary_label.config(
            text=f"{len(rows)} record(s) \u00b7 Total cost {cur}{total_cost:.2f} \u00b7 "
                 f"Total received {cur}{total_received:.2f}"
        )

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

    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        row = next((r for r in self._rows_cache if r["id"] == sel[0]), None)
        if row:
            EditReceivedDialog(self, self.app, row)

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], initialfile="buildtime_history.csv"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fp:
            w = csv.writer(fp)
            w.writerow(
                ["Date", "Table", "Start", "End", "Duration (s)", "Snacks", "Drinks",
                 "Items Cost", "Duration Cost", "Total Cost", "Received", "Comment", "Synced"]
            )
            for r in self._rows_cache:
                w.writerow(
                    [r["date"], r["table_name"], r["start_time"], r["end_time"], r["duration_seconds"],
                     r["snacks_text"] or "", r["drinks_text"] or "", r["items_cost"], r["duration_cost"],
                     r["total_cost"], r["received_amount"], r["comment"] or "", "Yes" if r["synced"] else "No"]
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
        ttk.Label(self, text=f"Total cost: {cur}{row['total_cost']:.2f}", **pad).pack(anchor="w")

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
