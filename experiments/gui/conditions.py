"""Treatment names and exclusions; reference decisions live in final Analysis."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

from uikit import tokens

_NONE = "(use the experiment's)"


class ConditionsPanel(ttk.Frame):
    def __init__(self, master, controller, on_change: Callable[[], None]):
        super().__init__(master, padding=(10, 8))
        self.ctl = controller
        self.on_change = on_change
        self.condition_control = tk.StringVar(value="")
        self._exclude_vars: dict[int, tk.BooleanVar] = {}
        from .groups import GroupSelector
        self.group_selector = GroupSelector(self, controller, on_change, allow_add=False)
        self.group_selector.pack(fill="x", pady=(0, 8))

        self.intro = ttk.Label(
            self,
            text=("Each medium or treatment the panel was spotted on. "
                  "Choose normalization and comparison references per condition "
                  "in the final Analysis / comparisons tab. Categories (carbon "
                  "source, temperature...) say how treatments differ, so figures "
                  "can group and compare them."),
            foreground=tokens.TEXT_MUTED, justify="left",
        )
        self.intro.pack(anchor="w", fill="x", pady=(0, 8))
        self.bind("<Configure>", lambda e: self.intro.configure(
            wraplength=max(160, e.width - 24)))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Add...", command=self._add).pack(side="left")
        ttk.Button(bar, text="Rename", command=self._rename).pack(side="left", padx=4)
        ttk.Button(bar, text="Remove", command=self._remove).pack(side="left", padx=4)
        ttk.Button(bar, text="Categories...", command=self._categories).pack(
            side="left", padx=(12, 0))
        ttk.Button(bar, text="Fill from names...", command=self._fill_from_names).pack(
            side="left", padx=4)
        self.adopt = ttk.Button(bar, text="Add the ones found in the photos",
                                command=self._adopt)
        self.adopt.pack(side="left", padx=(12, 0))

        panes = ttk.PanedWindow(self, orient="horizontal")
        self.panes = panes
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 10, 0))
        self.inspector = ttk.LabelFrame(
            panes, text="Selected condition", padding=(12, 10))
        panes.add(left, weight=3)
        panes.add(self.inspector, weight=2)
        panes.bind("<Configure>", self._resize_panes)

        # No dilution column: which row to score is judged against the picture
        # and is set per plate, on the Plates tab.
        table = ttk.Frame(left)
        self.table_frame = table
        table.pack(fill="both", expand=True)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        # Windows scales ttk's font but not Treeview rows or pixel-based
        # columns.  Without compensating, the lower half of every condition is
        # clipped on a high-DPI display and longer values are cut off inside
        # their cells even when the table itself has room.
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.tree_style = "Conditions.Treeview"
        ttk.Style().configure(self.tree_style, rowheight=int(24 * self.scale))
        self.tree = ttk.Treeview(
            table, columns=("label",), show="headings", height=8,
            style=self.tree_style, selectmode="extended",
        )
        self._categories_shown: tuple[str, ...] | None = None
        self._set_columns(())
        self.condition_y_scroll = ttk.Scrollbar(
            table, orient="vertical", command=self.tree.yview)
        self.condition_x_scroll = ttk.Scrollbar(
            table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.condition_y_scroll.set,
                            xscrollcommand=self.condition_x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.condition_y_scroll.grid(row=0, column=1, sticky="ns")
        self.condition_x_scroll.grid(row=1, column=0, sticky="ew")
        table.bind("<Configure>", self._resize_columns)
        self._selection_anchor = ""
        self.tree.bind("<Button-1>", self._select_click)
        self.tree.bind("<Triple-1>", self._select_click)
        self.tree.bind("<Double-1>", self._edit)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        ttk.Label(left, text="Ctrl-click toggles; Shift-click selects a range. Remove acts on the selection.",
                  wraplength=440, foreground=tokens.TEXT_MUTED).pack(anchor="w", pady=(4, 0))

        self.condition_name = ttk.Label(
            self.inspector, text="No condition selected", font=("", 11, "bold"),
            anchor="w", justify="left",
        )
        self.condition_name.pack(fill="x")
        self.condition_photos = ttk.Label(
            self.inspector, text="", foreground=tokens.TEXT_MUTED, anchor="w")
        self.condition_photos.pack(fill="x", pady=(2, 12))
        ttk.Label(self.inspector, text="Normalization is set in final Analysis / comparisons.",
                  wraplength=250).pack(anchor="w")
        self.control_picker = ttk.Combobox(
            self.inspector, textvariable=self.condition_control,
            state="readonly",
        )
        # Kept unmounted for legacy controller compatibility.
        self.control_picker.bind("<<ComboboxSelected>>", self._control_selected)
        # One value picker per category; filled in by _refresh_inspector.
        self.category_frame = ttk.Frame(self.inspector)
        self.category_frame.pack(fill="x", pady=(0, 10))
        self.category_frame.columnconfigure(1, weight=1)
        self._category_boxes: list = []
        self._category_rows: list = []
        ttk.Label(self.inspector, text="Excluded slots",
                  font=("", 9, "bold")).pack(anchor="w")
        self.exclude_hint = ttk.Label(
            self.inspector,
            text="Uncheck every slot to include the full panel.",
            foreground=tokens.TEXT_MUTED, justify="left",
        )
        self.exclude_hint.pack(anchor="w", fill="x", pady=(2, 4))
        exclude_area = ttk.Frame(self.inspector)
        exclude_area.pack(fill="both", expand=True)
        self.exclude_canvas = tk.Canvas(exclude_area, highlightthickness=0,
                                        borderwidth=0, height=100)
        exclude_scroll = ttk.Scrollbar(
            exclude_area, orient="vertical", command=self.exclude_canvas.yview)
        self.exclude_canvas.configure(yscrollcommand=exclude_scroll.set)
        self.exclude_canvas.pack(side="left", fill="both", expand=True)
        exclude_scroll.pack(side="right", fill="y")
        self.exclude_frame = ttk.Frame(self.exclude_canvas)
        self._exclude_window = self.exclude_canvas.create_window(
            (0, 0), window=self.exclude_frame, anchor="nw")
        self.exclude_frame.bind("<Configure>", lambda _e:
                                self.exclude_canvas.configure(
                                    scrollregion=self.exclude_canvas.bbox("all")))
        self.exclude_canvas.bind("<Configure>", lambda e:
                                 self.exclude_canvas.itemconfigure(
                                     self._exclude_window, width=e.width))
        self.inspector.bind("<Configure>", self._resize_inspector)

    # -- display -------------------------------------------------------------

    def _set_columns(self, categories) -> None:
        """One table column per category, between the name and the rest."""
        categories = tuple(categories)
        if categories == self._categories_shown:
            return
        self._categories_shown = categories
        self._column_specs = (
            ("label", "Treatment", 110, 3),
            *((f"cat{i}", name, 70, 2) for i, name in enumerate(categories)),
            ("control", "Normalization (Analysis)", 70, 2),
            ("exclude", "Excluded slots", 78, 2),
            ("photos", "Photos", 45, 1),
        )
        self.tree.configure(columns=[spec[0] for spec in self._column_specs])
        for key, title, width, _weight in self._column_specs:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=int(width * self.scale),
                             minwidth=int(38 * self.scale), stretch=False,
                             anchor="w")
        width = self.table_frame.winfo_width()
        if width > 1:
            self._resize_columns(type("Event", (), {"width": width})())

    def _resize_panes(self, event) -> None:
        """Keep enough room for all columns while retaining a useful inspector."""
        if event.width > 1:
            try:
                self.panes.sashpos(0, int(event.width * 0.64))
            except tk.TclError:
                pass

    def _resize_columns(self, event) -> None:
        """Fit every column into the table; scroll only at extreme widths."""
        available = max(
            1, event.width - self.condition_y_scroll.winfo_reqwidth() - 4)
        font = tkfont.nametofont("TkDefaultFont")
        minimums = [
            max(int(base * self.scale), font.measure(title) + int(10 * self.scale))
            for _key, title, base, _weight in self._column_specs
        ]
        minimum_total = sum(minimums)
        if available < minimum_total:
            widths = minimums
            self.condition_x_scroll.grid()
        else:
            weights = [spec[3] for spec in self._column_specs]
            extra = available - minimum_total
            widths = [minimum + extra * weight // sum(weights)
                      for minimum, weight in zip(minimums, weights)]
            widths[-1] += available - sum(widths)
            self.condition_x_scroll.grid_remove()
        for (key, _title, _base, _weight), width in zip(
                self._column_specs, widths):
            self.tree.column(key, width=width)

    def refresh(self) -> None:
        editor = getattr(self, "_inline", None)
        if editor and not editor.closed:
            editor.cancel()
        self.group_selector.refresh()
        e = self.ctl.panel_experiment
        selected = self.tree.selection()
        focused = self.tree.focus()
        counts = {}
        if self.ctl.resolution is not None:
            for row in self.ctl.found_condition_rows():
                if self.ctl.experiment.strain_groups and row.set_key != e.set_key:
                    continue
                counts[row.condition] = counts.get(row.condition, 0) + 1

        self._set_columns(self.ctl.experiment.categories)
        # Discovery order depends on photo paths; present labels alphabetically
        # without changing saved condition order or their stable identities.
        rows = []
        for c in sorted(e.conditions, key=lambda c: (c.display().casefold(), c.code)):
            own = e.control_for(c.code)
            control = (f"slot {own} ({e.strain(own) or 'empty'})" if own
                       else "Choose in Analysis")
            rows.append((c.code, (c.display(),
                                  *(c.category(name) or "-" for name in self.ctl.experiment.categories),
                                  control,
                                  ", ".join(str(s) for s in c.exclude) or "-",
                                  counts.get(c.code, 0))))
        if tuple(self.tree.get_children()) == tuple(code for code, _ in rows):
            # Same rows: update them where they stand. Emptying and refilling
            # the table makes it blink on every edit.
            for code, values in rows:
                self.tree.item(code, values=values)
        else:
            self.tree.delete(*self.tree.get_children())
            for code, values in rows:
                self.tree.insert("", "end", iid=code, values=values)
        selected = [code for code in selected if self.tree.exists(code)]
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(focused if focused in selected else selected[0])
        elif self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

        found = {r.condition for r in self.ctl.found_condition_rows()}
        missing = found - set(e.condition_codes())
        self.adopt.state(["!disabled"] if missing else ["disabled"])
        self.adopt.configure(
            text=(f"Add the {len(missing)} found in the photos" if missing
                  else "Add the ones found in the photos")
        )
        self._refresh_inspector(counts)

    def selected_code(self) -> str:
        sel = self.tree.selection()
        return sel[0] if len(sel) == 1 else ""

    def _select_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        code = self.tree.identify_row(event.y)
        if not code:
            return
        self.tree.focus_set()
        children = self.tree.get_children()
        if event.state & 0x0001:  # Shift: exact contiguous range from anchor.
            anchor = self._selection_anchor
            if anchor not in children:
                anchor = self.tree.focus() or code
            start, end = sorted((children.index(anchor), children.index(code)))
            selection = children[start:end + 1]
            if event.state & 0x0004:
                selection = tuple(set(self.tree.selection()) | set(selection))
            self.tree.selection_set(selection)
        elif event.state & 0x0004:  # Ctrl: toggle just this identity.
            if code in self.tree.selection():
                self.tree.selection_remove(code)
            else:
                self.tree.selection_add(code)
            if self._selection_anchor not in children:
                self._selection_anchor = code
        else:
            self.tree.selection_set(code)
            self._selection_anchor = code
        self.tree.focus(code)
        return "break"

    # -- edits ---------------------------------------------------------------

    def _add(self) -> None:
        name = _ask_text(
            self,
            "Add condition",
            "Treatment name (e.g. Glucose or Glucose + H2O2):",
        )
        if not name:
            return
        try:
            code = self.ctl.add_named_condition(name)
            self.on_change()
            self._show(code)
        except ValueError as exc:
            messagebox.showerror("Add condition", str(exc), parent=self)

    def _remove(self) -> None:
        codes = tuple(self.tree.selection())
        if not codes:
            return
        labels = [self.ctl.experiment.condition(code).display() for code in codes]
        links = self.ctl.condition_photo_links(codes)
        if not messagebox.askokcancel(
                "Remove conditions",
                f"Remove exactly {len(codes)} condition(s):\n" + "\n".join(labels)
                + f"\n\nConditions are shared by all strain groups. Their plate picks, "
                  f"group settings and analysis references will be removed. "
                  f"{len(links)} linked photo assignment(s) will be cleared; "
                  "these photos stay in Data for reassignment. Original photo files "
                  "are preserved. Undo restores this entire edit.\n\nContinue?", parent=self):
            return
        try:
            if self.ctl.remove_conditions(codes):
                self.on_change()
        except Exception as exc:
            messagebox.showerror("Remove conditions", str(exc), parent=self)

    def _rename(self) -> None:
        old = self.selected_code()
        if not old:
            return
        from uikit.shortcuts import InlineLabel
        previous = getattr(self, "_inline", None)
        if previous and not previous.closed:
            return
        self.tree.see(old)
        self.tree.update_idletasks()
        bounds = self.tree.bbox(old, "label")
        if not bounds:
            return
        def commit(label):
            if self.ctl.set_condition_name(old, label):
                self.on_change()
            self._show(old)
        x, y, width, height = bounds
        self._inline = InlineLabel(
            self.tree, self.ctl.experiment.condition(old).display(), commit,
            self.tree, x=x, y=y, width=width, height=height)

    def _adopt(self) -> None:
        before = set(self.ctl.experiment.condition_codes())
        if self.ctl.adopt_found_conditions():
            self.on_change()
            added = [c for c in self.ctl.experiment.condition_codes()
                     if c not in before]
            if added:
                self._show(added[-1])

    def _show(self, code: str) -> None:
        """Select a newly-added condition and scroll all of its row into view."""
        if self.tree.exists(code):
            self.tree.selection_set(code)
            self.tree.focus(code)
            self.tree.see(code)

    def _edit(self, event) -> None:
        """Open the editor belonging to the double-clicked table cell."""
        if event.state & 0x0005:  # Shift / Control keep their selection meaning.
            return self._select_click(event)
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        code = self.tree.identify_row(event.y)
        if not code:
            return
        self.tree.selection_set(code)
        self.tree.focus(code)
        column = self.tree.identify_column(event.x)
        try:
            key = self.tree["columns"][int(column[1:]) - 1]
        except (ValueError, IndexError):
            return
        if key == "label":
            self._rename()
        elif key.startswith("cat"):
            self._focus_category(int(key[3:]))
        elif key == "exclude":
            self._set_exclude()
        # Final analysis owns normalization choices.

    def _selection_changed(self, _event=None) -> None:
        self._refresh_inspector()

    def _resize_inspector(self, event) -> None:
        width = max(120, event.width - 28)
        self.condition_name.configure(wraplength=width)
        self.condition_photos.configure(wraplength=width)
        self.exclude_hint.configure(wraplength=width)

    def _refresh_inspector(self, counts: dict[str, int] | None = None) -> None:
        self._refresh_categories()
        code = self.selected_code()
        if not code:
            count = len(self.tree.selection())
            self.condition_name.configure(text=f"{count} conditions selected" if count else "No condition selected")
            self.condition_photos.configure(text="Categories set here apply to every selected condition. Remove affects exactly this selection across all strain groups. Select one to edit its name or slots."
                                            if count else "Add or select a condition.")
            self.control_picker.configure(values=(), state="disabled")
            self.condition_control.set("")
            for child in self.exclude_frame.winfo_children():
                child.destroy()
            self._exclude_vars.clear()
            self._exclude_layout = None
            return

        e = self.ctl.panel_experiment
        condition = e.condition(code)
        if counts is None:
            counts = {}
            if self.ctl.resolution is not None:
                for row in self.ctl.found_condition_rows():
                    if self.ctl.experiment.strain_groups and row.set_key != e.set_key:
                        continue
                    counts[row.condition] = counts.get(row.condition, 0) + 1
        self.condition_name.configure(text=condition.display())
        photos = counts.get(code, 0)
        self.condition_photos.configure(
            text=f"{photos} photograph{'s' if photos != 1 else ''} found")

        options = [_NONE] + [f"{s}: {e.strain(s)}" for s in e.filled_slots()]
        self.control_picker.configure(values=options, state="readonly")
        if condition.control_slot is None:
            self.condition_control.set(_NONE)
        else:
            self.condition_control.set(
                f"{condition.control_slot}: "
                f"{e.strain(condition.control_slot) or 'empty'}")

        excluded = set(condition.exclude)
        layout = (code, tuple((s, e.strain(s)) for s in e.filled_slots()))
        if layout == getattr(self, "_exclude_layout", None):
            for slot, var in self._exclude_vars.items():
                var.set(slot in excluded)
            return
        self._exclude_layout = layout
        for child in self.exclude_frame.winfo_children():
            child.destroy()
        self._exclude_vars.clear()
        for row, slot in enumerate(e.filled_slots()):
            var = tk.BooleanVar(value=slot in excluded)
            self._exclude_vars[slot] = var
            ttk.Checkbutton(
                self.exclude_frame, text=f"{slot}: {e.strain(slot)}",
                variable=var, command=self._excluded_toggled,
            ).grid(row=row // 2, column=row % 2, sticky="w", padx=(0, 12), pady=2)
        self.exclude_frame.columnconfigure(0, weight=1)
        self.exclude_frame.columnconfigure(1, weight=1)
        self.exclude_canvas.yview_moveto(0)

    def _refresh_categories(self) -> None:
        """A value picker per category, for every selected condition at once.

        The widgets are rebuilt only when the categories or the selection
        change; an edit just updates them, so nothing blinks.
        """
        e = self.ctl.experiment
        codes = tuple(c for c in self.tree.selection() if e.has_condition(c))
        layout = (tuple(e.categories), codes)
        if layout != getattr(self, "_category_layout", None):
            self._build_categories(e, codes)
            self._category_layout = layout
        for name, (var, box, note) in zip(e.categories, self._category_rows):
            values = {e.condition(code).category(name) for code in codes}
            mixed = len(values) > 1
            current = "" if mixed else next(iter(values))
            box.configure(values=e.category_values(name))
            box.shown = current
            if self.focus_get() is not box or var.get().strip() == current:
                var.set(current)
            note.configure(text="(differs)" if mixed else "")

    def _build_categories(self, e, codes) -> None:
        for child in self.category_frame.winfo_children():
            child.destroy()
        self._category_boxes, self._category_rows = [], []
        if not e.categories:
            ttk.Label(self.category_frame, foreground=tokens.TEXT_MUTED,
                      text="No categories yet. Add some (e.g. Carbon source, "
                           "Temperature) with Categories... to tell figures "
                           "which treatments belong together.",
                      wraplength=250, justify="left").grid(
                row=0, column=0, columnspan=3, sticky="w")
            return
        if not codes:
            return
        ttk.Label(self.category_frame, text="Categories",
                  font=("", 9, "bold")).grid(row=0, column=0, columnspan=3,
                                             sticky="w", pady=(0, 2))
        for row, name in enumerate(e.categories, 1):
            ttk.Label(self.category_frame, text=name).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar()
            box = ttk.Combobox(self.category_frame, textvariable=var, width=18)
            box.grid(row=row, column=1, sticky="ew", pady=2)
            box.shown = ""
            note = ttk.Label(self.category_frame, text="",
                             foreground=tokens.TEXT_MUTED)
            note.grid(row=row, column=2, sticky="w", padx=(4, 0))

            def commit(_event=None, name=name, var=var, box=box, codes=codes):
                value = var.get().strip()
                if value == box.shown:
                    return
                try:
                    # The controller's change notice refreshes the window.
                    self.ctl.set_category_value(codes, name, value)
                except (KeyError, ValueError) as exc:
                    messagebox.showerror("Category", str(exc), parent=self)
            box.bind("<<ComboboxSelected>>", commit)
            box.bind("<Return>", commit)
            box.bind("<FocusOut>", commit)
            self._category_boxes.append(box)
            self._category_rows.append((var, box, note))

    def _focus_category(self, index: int) -> None:
        if 0 <= index < len(self._category_boxes):
            box = self._category_boxes[index]
            box.focus_set()
            box.selection_range(0, "end")

    def _categories(self) -> None:
        CategoryEditor(self, self.ctl, self.on_change)

    def _fill_from_names(self) -> None:
        if not self.ctl.experiment.conditions:
            messagebox.showinfo("Fill from names", "Add treatments first.", parent=self)
            return
        FillFromNames(self, self.ctl)

    def _control_selected(self, _event=None) -> None:
        code = self.selected_code()
        chosen = self.condition_control.get()
        if not code or not chosen:
            return
        slot = None if chosen == _NONE else int(chosen.split(":", 1)[0])
        if self.ctl.set_condition_control(code, slot):
            self.on_change()

    def _excluded_toggled(self) -> None:
        code = self.selected_code()
        if not code:
            return
        slots = [slot for slot, var in self._exclude_vars.items() if var.get()]
        if self.ctl.set_condition_exclude(code, slots):
            self.on_change()

    def _set_control(self) -> None:
        code = self.selected_code()
        if not code:
            return
        e = self.ctl.panel_experiment
        options = [_NONE] + [
            f"{s}: {e.strain(s)}" for s in e.filled_slots()
        ]
        chosen = _ask_choice(self, f"Control for {e.condition(code).display()}",
                             "Which slot is the positive control here?", options)
        if chosen is None:
            return
        slot = None if chosen == _NONE else int(chosen.split(":", 1)[0])
        if self.ctl.set_condition_control(code, slot):
            self.on_change()

    def _set_exclude(self) -> None:
        code = self.selected_code()
        if not code:
            return
        e = self.ctl.panel_experiment
        slot_options = {
            s: f"{s}: {e.strain(s)}" for s in e.filled_slots()
        }
        chosen = _ask_choices(
            self,
            f"Excluded slots for {e.condition(code).display()}",
            "Which slots should be excluded here? Click a slot to toggle it.",
            list(slot_options.values()),
            selected={slot_options[s] for s in e.exclude_for(code)
                      if s in slot_options},
        )
        if chosen is None:
            return
        slots = [int(option.split(":", 1)[0]) for option in chosen]
        if self.ctl.set_condition_exclude(code, slots):
            self.on_change()


class CategoryEditor(tk.Toplevel):
    """Add, rename, remove and order the experiment's treatment categories."""

    def __init__(self, parent, controller, on_change):
        super().__init__(parent)
        self.ctl, self.on_change = controller, on_change
        self.title("Treatment categories")
        self.transient(parent.winfo_toplevel())
        ttk.Label(self, padding=(10, 8), wraplength=360, justify="left",
                  text="Ways your treatments differ, such as Carbon source "
                       "(Glucose, Glycerol) or Temperature (30\u00b0C, 37\u00b0C). "
                       "Give each treatment its value on the Conditions tab.").pack(anchor="w")
        body = ttk.Frame(self, padding=(10, 0))
        body.pack(fill="both", expand=True)
        self.box = tk.Listbox(body, height=8, width=40, activestyle="none",
                              exportselection=False)
        self.box.pack(side="left", fill="both", expand=True)
        self.box.bind("<Double-1>", lambda _e: self._rename())
        buttons = ttk.Frame(body, padding=(8, 0, 0, 0))
        buttons.pack(side="left", fill="y")
        for text, command in (("Add...", self._add), ("Rename...", self._rename),
                              ("Remove", self._remove),
                              ("Move up", lambda: self._move(-1)),
                              ("Move down", lambda: self._move(1))):
            ttk.Button(buttons, text=text, command=command).pack(fill="x", pady=2)
        ttk.Button(self, text="Close", command=self.destroy).pack(
            anchor="e", padx=10, pady=8)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._fill()
        self.grab_set()
        parent.winfo_toplevel().wait_window(self)

    def _fill(self, select: str | None = None) -> None:
        names = self.ctl.experiment.categories
        self.box.delete(0, "end")
        for name in names:
            values = self.ctl.experiment.category_values(name)
            self.box.insert("end", f"{name}  ({', '.join(values)})" if values else name)
        if select in names:
            self.box.selection_set(names.index(select))
            self.box.see(names.index(select))

    def _selected(self) -> str | None:
        sel = self.box.curselection()
        return self.ctl.experiment.categories[sel[0]] if sel else None

    def _changed(self, select) -> None:
        self.on_change()
        self._fill(select)

    def _add(self) -> None:
        name = _ask_text(self, "Add category",
                         "Category name (e.g. Carbon source or Temperature):")
        if not name:
            return
        try:
            self._changed(self.ctl.add_category(name))
        except ValueError as exc:
            messagebox.showerror("Add category", str(exc), parent=self)

    def _rename(self) -> None:
        old = self._selected()
        if old is None:
            return
        name = _ask_text(self, "Rename category", "Category name:", old)
        if not name:
            return
        try:
            if self.ctl.rename_category(old, name):
                self._changed(" ".join(name.split()))
        except ValueError as exc:
            messagebox.showerror("Rename category", str(exc), parent=self)

    def _remove(self) -> None:
        name = self._selected()
        if name is None:
            return
        used = sum(1 for c in self.ctl.experiment.conditions if c.category(name))
        if used and not messagebox.askokcancel(
                "Remove category",
                f"Remove {name}? {used} treatment(s) lose their {name} value. "
                "Undo restores it.", parent=self):
            return
        if self.ctl.remove_category(name):
            self._changed(None)

    def _move(self, step: int) -> None:
        name = self._selected()
        if name is not None and self.ctl.move_category(name, step):
            self._changed(name)


class FillFromNames(tk.Toplevel):
    """Propose categories read from the treatment names; apply what is ticked."""

    def __init__(self, parent, controller):
        from .. import categories as guesser
        super().__init__(parent)
        self.ctl = controller
        self.title("Fill categories from names")
        self.transient(parent.winfo_toplevel())
        e = controller.experiment
        conditions = sorted(e.conditions, key=lambda c: (c.display().casefold(), c.code))
        guessed, self.values, assumed = guesser.guess_all(
            [(c.code, c.display()) for c in conditions])
        # A guessed category the experiment already has, under its own name.
        self.names = {g: guesser.existing_name(g, e.categories) or g for g in guessed}
        if not guessed:
            ttk.Label(self, padding=12, wraplength=380, justify="left",
                      text="None of the treatment names says a carbon source, "
                           "temperature or added compound that could be read. "
                           "Add categories with Categories... and set them by hand.").pack()
            ttk.Button(self, text="Close", command=self.destroy).pack(anchor="e", padx=10, pady=8)
            self._wait(parent)
            return

        ttk.Label(self, padding=(10, 8), wraplength=520, justify="left",
                  text="Read from each treatment's name. Check the guesses, untick "
                       "anything you don't want, then Apply. * marks a value "
                       "assumed because the name says nothing (no temperature = "
                       f"{guesser.ASSUMED[guesser.TEMPERATURE]}, no compound = "
                       f"{guesser.ASSUMED[guesser.ADDITIVE]}).").pack(anchor="w")
        ticks = ttk.Frame(self, padding=(10, 0))
        ticks.pack(fill="x")
        self.use = {}
        for g in guessed:
            self.use[g] = tk.BooleanVar(value=True)
            shown = self.names[g] + ("" if self.names[g] == g else f" (as {g})")
            ttk.Checkbutton(ticks, text=shown, variable=self.use[g]).pack(side="left", padx=(0, 12))

        table = ttk.Treeview(self, columns=("t", *guessed), show="headings",
                             height=min(14, len(conditions)))
        table.heading("t", text="Treatment")
        table.column("t", width=180)
        for g in guessed:
            table.heading(g, text=self.names[g])
            table.column(g, width=120)
        for c in conditions:
            own = self.values.get(c.code, {})
            cells = []
            for g in guessed:
                value = own.get(g, "")
                current = c.category(self.names[g])
                if current and current != value:
                    value = f"{current} (set)" + (f" -> {value}" if value else "")
                elif (c.code, g) in assumed:
                    value += "*"
                cells.append(value or "-")
            table.insert("", "end", values=(c.display(), *cells))
        table.pack(fill="both", expand=True, padx=10, pady=8)

        self.keep = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Keep values already set (marked \"(set)\")",
                        variable=self.keep).pack(anchor="w", padx=10)
        buttons = ttk.Frame(self, padding=(10, 8))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Apply", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        self._wait(parent)

    def _wait(self, parent) -> None:
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        parent.winfo_toplevel().wait_window(self)

    def _apply(self) -> None:
        chosen = {g for g, var in self.use.items() if var.get()}
        values = {code: {self.names[g]: v for g, v in own.items() if g in chosen}
                  for code, own in self.values.items()}
        try:
            self.ctl.fill_categories(values, keep_existing=self.keep.get())
        except ValueError as exc:
            messagebox.showerror("Fill from names", str(exc), parent=self)
            return
        self.destroy()


def _ask_text(parent, title: str, prompt: str, initial: str = "", *, choices=None) -> str | None:
    """A text prompt that reliably gives its entry keyboard focus on Windows."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)

    ttk.Label(win, text=prompt, padding=(10, 8)).pack(anchor="w")
    value = tk.StringVar(value=initial)
    entry = (ttk.Combobox(win, textvariable=value, values=choices, width=42)
             if choices is not None else ttk.Entry(win, textvariable=value, width=42))
    entry.pack(fill="x", padx=10)

    result: dict[str, str | None] = {"value": None}

    def accept(_event=None) -> None:
        result["value"] = value.get()
        win.destroy()

    def cancel(_event=None) -> None:
        win.destroy()

    buttons = ttk.Frame(win, padding=(10, 8))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="OK", command=accept).pack(side="right")
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right", padx=4)
    win.bind("<Return>", accept)
    win.bind("<Escape>", cancel)
    win.protocol("WM_DELETE_WINDOW", cancel)

    def focus_entry() -> None:
        if win.winfo_exists():
            win.lift()
            entry.focus_force()
            entry.selection_range(0, "end")
            entry.icursor("end")

    entry.focus_set()
    win.after_idle(focus_entry)
    win.grab_set()
    parent.winfo_toplevel().wait_window(win)
    return result["value"]


def _ask_choice(parent, title: str, prompt: str, options: list[str]) -> str | None:
    """A small modal list picker; tkinter has no built-in one."""
    chosen = _ask_list(parent, title, prompt, options, multiple=False)
    return chosen[0] if chosen else None


def _ask_choices(parent, title: str, prompt: str, options: list[str], *,
                 selected: set[str] | None = None) -> list[str] | None:
    """The same picker, allowing several slots to be toggled at once."""
    return _ask_list(parent, title, prompt, options, multiple=True,
                     selected=selected)


def _ask_list(parent, title: str, prompt: str, options: list[str], *,
              multiple: bool, selected: set[str] | None = None) -> list[str] | None:
    if not options:
        return None
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    ttk.Label(win, text=prompt, padding=(10, 8)).pack(anchor="w")

    box = tk.Listbox(
        win, height=min(10, len(options)), activestyle="none",
        exportselection=False, selectmode=tk.MULTIPLE if multiple else tk.BROWSE,
    )
    for option in options:
        box.insert("end", option)
    selected = selected or ({options[0]} if not multiple else set())
    for index, option in enumerate(options):
        if option in selected:
            box.selection_set(index)
    box.pack(fill="both", expand=True, padx=10)

    result: dict[str, list[str] | None] = {"value": None}

    def accept(_event=None) -> None:
        result["value"] = [options[index] for index in box.curselection()]
        win.destroy()

    buttons = ttk.Frame(win, padding=(10, 8))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="OK", command=accept).pack(side="right")
    ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=4)
    if not multiple:
        box.bind("<Double-1>", accept)
    win.bind("<Return>", accept)
    win.bind("<Escape>", lambda _e: win.destroy())

    box.focus_set()
    win.grab_set()
    parent.winfo_toplevel().wait_window(win)
    return result["value"]
