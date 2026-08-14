"""
BuildTime — entry point.
Run with: python3 main.py
"""
import os
import sys

# Make sure "database", "sync_manager", and the "ui" package resolve
# regardless of the working directory the app was launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

from database import Database
from ui.main_window import MainWindow


def main():
    db = Database()
    root = tk.Tk()
    MainWindow(root, db)
    root.mainloop()


if __name__ == "__main__":
    main()
