"""Photos taken in the same order in every folder.

Some benches photograph several strain sets at one sitting and file them all in
one folder per medium, time and treatment: set A plate 1, set A plate 2, set B
plate 1, ... Nothing in the filenames says which photo is which; the position
does. This dialog states that order once. Every folder is then read by it,
including folders added later, and the table shows what each position means in
a real folder before anything changes.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from uikit import tokens

from .. import intake

_ONE_PANEL = "One strain panel"


def plate_choices(template) -> list[tuple[int, str]]:
    """(plate number, display name) for every numbered plate of the template."""
    if template is None or not template.plates:
        return [(1, "Plate 1"), (2, "Plate 2")]
    out = []
    for plate in template.plates:
        if not str(plate.id).isdigit():
            continue
        text = plate.label or f"Plate {plate.id}"
        reps = sorted(plate.replicates_present())
        if reps and "rep" not in text.lower():
            text += f" (replicates {'+'.join(map(str, reps))})"
        out.append((int(plate.id), text))
    return out


def order_summary(experiment, resolution) -> str:
    """One line saying what the photo order is and how well the folders fit."""
    order = experiment.photo_order
    if not order:
        return ""
    shown = [f"{s.group + ' ' if s.group else ''}plate {s.plate}" for s in order[:4]]
    text = "By position in each folder: " + ", ".join(shown) + (", …" if len(order) > 4 else "")
    if resolution is not None and resolution.order_mismatches:
        n = len(resolution.order_mismatches)
        text += (f" · {n} {'folder holds' if n == 1 else 'folders hold'} a different "
                 f"number of photos; those photos need attention below")
    return text


class PhotoOrderDialog(tk.Toplevel):
    """Build the order from the strain groups and plates, then check a folder."""

    def __init__(self, parent, controller, on_change):
        super().__init__(parent)
        self.ctl, self.on_change = controller, on_change
        self.title("Photo order in each folder")
        self.transient(parent.winfo_toplevel())
        self.minsize(640, 520)

        e = controller.experiment
        saved = tuple(e.photo_order)
        self.plates = plate_choices(controller.template)
        self.plate_names = dict(self.plates)
        known = [p for p, _ in self.plates]
        self.plate_order = list(dict.fromkeys(
            [s.plate for s in saved if s.plate in known] + known))
        groups = list(e.strain_groups)
        self.group_order = (list(dict.fromkeys(
            [s.group for s in saved if s.group in groups] + groups)) or [None])
        self.groups_first = tk.BooleanVar(value=True)
        self.custom = None
        if saved:
            regular = {flag: intake.build_order(self.group_order, self.plate_order,
                                                groups_first=flag)
                       for flag in (True, False)}
            match = next((flag for flag, built in regular.items() if built == saved), None)
            if match is None:
                # Written by hand or by a later version: show it as it is until
                # the user changes something here.
                self.custom = saved
            else:
                self.groups_first.set(match)

        body = ttk.Frame(self, padding=(tokens.SPACE_M, tokens.SPACE_S))
        body.pack(fill="both", expand=True)
        intro = ttk.Label(body, justify="left", wraplength=600, text=(
            "Use this when the photos in every folder were taken in the same order, for "
            "example set A plate 1, set A plate 2, then set B plate 1. Each photo's strain "
            "group and plate then come from its position in its folder, sorted by filename "
            "as Explorer lists them. Ignored photos do not take a position."))
        intro.pack(fill="x", pady=(0, tokens.SPACE_S))

        lists = ttk.Frame(body)
        lists.pack(fill="x")
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        self.group_box = self._reorderable(
            lists, 0, "Strain groups, in the order photographed", self.group_order)
        ttk.Button(self.group_box.master, text="Add strain group...",
                   command=self._add_group).grid(row=2, column=0, sticky="w", pady=(tokens.SPACE_XS, 0))
        self.plate_box = self._reorderable(
            lists, 1, "Plates of each strain group, in order", self.plate_order)

        nesting = ttk.LabelFrame(body, text="Within a folder", padding=tokens.SPACE_S)
        nesting.pack(fill="x", pady=(tokens.SPACE_S, 0))
        ttk.Radiobutton(nesting, variable=self.groups_first, value=True, command=self._changed,
                        text="Every plate of one strain group, then the next group (A1, A2, B1, B2, …)").pack(anchor="w")
        ttk.Radiobutton(nesting, variable=self.groups_first, value=False, command=self._changed,
                        text="Plate 1 of every strain group, then plate 2 (A1, B1, A2, B2, …)").pack(anchor="w")

        check = ttk.Frame(body)
        check.pack(fill="x", pady=(tokens.SPACE_S, tokens.SPACE_XS))
        ttk.Label(check, text="Check against folder:").pack(side="left")
        self.folders = intake.order_folders(e, controller.files)
        self.folder_labels = {
            f"{folder or '(top folder)'} — {len(photos)} {'photo' if len(photos) == 1 else 'photos'}": folder
            for folder, photos in self.folders.items()}
        self.folder = tk.StringVar(value=next(iter(self.folder_labels), ""))
        folder_box = ttk.Combobox(check, textvariable=self.folder, state="readonly",
                                  values=list(self.folder_labels), width=60)
        folder_box.pack(side="left", fill="x", expand=True, padx=(tokens.SPACE_XS, 0))
        folder_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        area = ttk.Frame(body)
        area.pack(fill="both", expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        scale = max(1.0, self.winfo_fpixels("1i") / 96)
        ttk.Style().configure("Order.Treeview", rowheight=int(24 * scale))
        columns = ("position", "group", "plate", "photo")
        self.table = ttk.Treeview(area, columns=columns, show="headings", height=8,
                                  selectmode="none", style="Order.Treeview")
        for key, width, title in zip(columns, (40, 150, 190, 240),
                                     ("#", "Strain group", "Plate", "Photo in this folder")):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=30, stretch=key in ("group", "plate", "photo"))
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.fit = ttk.Label(body, justify="left", wraplength=600)
        self.fit.pack(fill="x", pady=(tokens.SPACE_XS, 0))
        self.bind("<Configure>", lambda ev: ev.widget is self and [
            label.configure(wraplength=max(300, ev.width - 40)) for label in (intro, self.fit)])

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(tokens.SPACE_S, 0))
        ttk.Button(buttons, text="Use this order", style="Accent.TButton",
                   command=self._apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=tokens.SPACE_XS)
        if saved:
            ttk.Button(buttons, text="Stop using photo order", command=self._clear).pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        self._refresh()

    # -- building the order ---------------------------------------------------

    def _reorderable(self, master, column, title, items):
        frame = ttk.LabelFrame(master, text=title, padding=tokens.SPACE_S)
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(0, tokens.SPACE_S) if column == 0 else 0)
        frame.columnconfigure(0, weight=1)
        box = tk.Listbox(frame, height=4, activestyle="none", exportselection=False)
        box.grid(row=0, column=0, rowspan=2, sticky="nsew")
        box.items = items
        ttk.Button(frame, text="Up", width=6,
                   command=lambda: self._move(box, -1)).grid(row=0, column=1, padx=(tokens.SPACE_XS, 0), sticky="n")
        ttk.Button(frame, text="Down", width=6,
                   command=lambda: self._move(box, 1)).grid(row=1, column=1, padx=(tokens.SPACE_XS, 0), sticky="n")
        return box

    def _text(self, item) -> str:
        if item is None:
            return _ONE_PANEL
        if isinstance(item, int):
            return self.plate_names.get(item, f"Plate {item}")
        return item

    def _move(self, box, delta):
        chosen = box.curselection()
        if not chosen:
            return
        i = chosen[0]
        j = i + delta
        if not 0 <= j < len(box.items):
            return
        box.items[i], box.items[j] = box.items[j], box.items[i]
        self._changed()
        box.selection_clear(0, "end")
        box.selection_set(j)
        box.see(j)

    def _changed(self):
        self.custom = None
        self._refresh()

    def order(self):
        if self.custom is not None:
            return self.custom
        return intake.build_order(self.group_order, self.plate_order,
                                  groups_first=self.groups_first.get())

    def _add_group(self):
        from .groups import add_group
        try:
            added = add_group(self, self.ctl, self.on_change)
        finally:
            # The name prompt took the grab; take it back while still open.
            try:
                if self.winfo_exists():
                    self.grab_set()
            except tk.TclError:
                pass
        if not added:
            return
        keys = list(self.ctl.experiment.strain_groups)
        self.group_order[:] = ([g for g in self.group_order if g in keys]
                               + [k for k in keys if k not in self.group_order])
        self._changed()

    # -- showing it ------------------------------------------------------------

    def _refresh(self):
        for box in (self.group_box, self.plate_box):
            selected = box.curselection()
            box.delete(0, "end")
            for item in box.items:
                box.insert("end", self._text(item))
            for i in selected:
                box.selection_set(i)

        order = self.order()
        folder = self.folder_labels.get(self.folder.get())
        photos = self.folders.get(folder, []) if folder is not None else []
        self.table.delete(*self.table.get_children())
        for i in range(max(len(order), len(photos))):
            slot = order[i] if i < len(order) else None
            photo = photos[i].parts[-1] if i < len(photos) else "(no photo)"
            self.table.insert("", "end", values=(
                i + 1,
                (slot.group or _ONE_PANEL) if slot else "",
                self._text(slot.plate) if slot else "not in the order",
                photo))

        if not self.folders:
            text, colour = "Choose a data folder to check this order against your photos.", tokens.TEXT_MUTED
        else:
            fits = sum(1 for p in self.folders.values() if len(p) == len(order))
            total = len(self.folders)
            if folder is not None and len(photos) != len(order):
                head = (f"This folder has {len(photos)} photos but the order lists {len(order)}, "
                        f"so none of its photos will be assigned by position. ")
            else:
                head = ""
            if fits == total:
                text = head + f"All {total} {'folder holds' if total == 1 else 'folders hold'} {len(order)} photos."
                colour = tokens.OK if not head else tokens.WARNING
            else:
                rest = total - fits
                text = head + (f"{fits} of {total} folders hold {len(order)} photos. The photos in the "
                               f"other {'folder' if rest == 1 else f'{rest} folders'} stay unassigned "
                               f"until you ignore extra photos or assign them by hand in Data.")
                colour = tokens.WARNING
        self.fit.configure(text=text, foreground=colour)

    # -- finishing --------------------------------------------------------------

    def _apply(self):
        try:
            self.ctl.set_photo_order(self.order())
        except ValueError as exc:
            messagebox.showerror("Photo order", str(exc), parent=self)
            return
        self.on_change()
        self.destroy()

    def _clear(self):
        self.ctl.set_photo_order(())
        self.on_change()
        self.destroy()


def edit_photo_order(parent, controller, on_change) -> None:
    dialog = PhotoOrderDialog(parent, controller, on_change)
    dialog.grab_set()
    parent.winfo_toplevel().wait_window(dialog)
