"""Show the repository's photo guide without an external Markdown association."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "data-organization.md"
TITLE = "Photograph and folder organization"


def show_data_organization(parent):
    try:
        body = GUIDE.read_text(encoding="utf-8")
    except OSError as exc:
        messagebox.showerror(TITLE, f"Could not read {GUIDE}:\n{exc}", parent=parent)
        return None
    win = tk.Toplevel(parent)
    win.title(TITLE)
    win.transient(parent)
    area = ttk.Frame(win)
    area.pack(fill="both", expand=True)
    text = tk.Text(area, wrap="word", width=100, height=32, padx=12, pady=10)
    scroll = ttk.Scrollbar(area, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.insert("1.0", body)
    text.configure(state="disabled")
    scroll.pack(side="right", fill="y")
    text.pack(fill="both", expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)
    win.bind("<Escape>", lambda _event: win.destroy())
    return win
