"""Keyword rules: "photos whose name contains X are Y", stated in plain words.

This is the middle tier of identifying photos. Detection from names comes
first and needs nothing from the user; per-photo corrections come last and
cost one click per photo. A keyword rule is the one in between: one sentence
that fixes a whole treatment, plate or sitting at once, without knowing which
folder level a word sits at or what a capture group is.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from uikit import tokens

from ..profiles import KeywordRule

FACET_TITLES = {"condition": "Treatment", "plate": "Plate number",
                "timepoint": "Hours since spotting", "set": "Strain group"}
WHERE_TITLES = {"anywhere": "folder or file name", "folder": "folder names", "filename": "file name"}


def describe_value(rule: KeywordRule, controller) -> str:
    """The value as the user knows it: a treatment's name, not its code."""
    if rule.facet == "condition":
        return controller.condition_name(rule.value)
    if rule.facet == "timepoint":
        return f"{float(rule.value):g} hours"
    if rule.facet == "plate":
        return f"plate {rule.value}"
    return str(rule.value)


def _value_text(rule: KeywordRule, controller) -> str:
    """What to put back in the value box when editing a saved rule."""
    if rule.facet == "condition":
        return controller.condition_name(rule.value)
    if rule.facet == "timepoint":
        return f"{float(rule.value):g}"
    return str(rule.value)


def describe_keyword(rule: KeywordRule, controller) -> str:
    return (f"photos whose {WHERE_TITLES[rule.where]} contains ‘{rule.text}’ are "
            f"{FACET_TITLES[rule.facet].lower()} {describe_value(rule, controller)}")


class KeywordDialog(tk.Toplevel):
    """One sentence with blanks, and a live count of the photos it would catch."""

    def __init__(self, parent, controller, on_change, *, facet: str = "condition",
                 text: str = "", initial=None):
        super().__init__(parent)
        self.ctl, self.on_change = controller, on_change
        self.title("Add keyword rule")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        if initial is not None:
            facet, text = initial.facet, initial.text

        body = ttk.Frame(self, padding=(tokens.SPACE_M, tokens.SPACE_S))
        body.pack(fill="both", expand=True)
        ttk.Label(body, justify="left", wraplength=520, text=(
            "Every photo whose name contains this text gets the value you choose. "
            "The text can be part of a word and capital letters do not matter; "
            "if two keywords for the same detail both match, the longer one wins.")
                  ).pack(fill="x", pady=(0, tokens.SPACE_S))

        sentence = ttk.Frame(body)
        sentence.pack(fill="x")
        ttk.Label(sentence, text="Photos whose").grid(row=0, column=0, sticky="w")
        self.where = tk.StringVar(value=WHERE_TITLES[initial.where if initial else "anywhere"])
        where_box = ttk.Combobox(sentence, textvariable=self.where, state="readonly", width=20,
                                 values=list(WHERE_TITLES.values()))
        where_box.grid(row=0, column=1, padx=tokens.SPACE_XS)
        ttk.Label(sentence, text="contains").grid(row=0, column=2, sticky="w")
        self.text = tk.StringVar(value=text)
        self.text_entry = ttk.Entry(sentence, textvariable=self.text, width=24)
        self.text_entry.grid(row=0, column=3, padx=tokens.SPACE_XS, sticky="ew")

        ttk.Label(sentence, text="are").grid(row=1, column=0, sticky="w", pady=(tokens.SPACE_S, 0))
        self.facet = tk.StringVar(value=FACET_TITLES[facet])
        facet_box = ttk.Combobox(sentence, textvariable=self.facet, state="readonly", width=20,
                                 values=list(FACET_TITLES.values()))
        facet_box.grid(row=1, column=1, padx=tokens.SPACE_XS, pady=(tokens.SPACE_S, 0))
        self.value = tk.StringVar(value=_value_text(initial, controller) if initial else "")
        self.value_box = ttk.Combobox(sentence, textvariable=self.value, width=24)
        self.value_box.grid(row=1, column=3, padx=tokens.SPACE_XS, pady=(tokens.SPACE_S, 0), sticky="ew")
        self.value_hint = ttk.Label(sentence, text="", foreground=tokens.TEXT_MUTED)
        self.value_hint.grid(row=2, column=3, sticky="w", padx=tokens.SPACE_XS)
        sentence.columnconfigure(3, weight=1)

        self.preview = ttk.Label(body, justify="left", wraplength=520, foreground=tokens.TEXT_MUTED)
        self.preview.pack(fill="x", pady=(tokens.SPACE_S, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(tokens.SPACE_S, 0))
        ttk.Button(buttons, text="Add rule", style="Accent.TButton", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=tokens.SPACE_XS)

        facet_box.bind("<<ComboboxSelected>>", lambda _e: self._facet_changed())
        where_box.bind("<<ComboboxSelected>>", lambda _e: self._preview())
        self.text.trace_add("write", lambda *_: self._preview())
        self.bind("<Return>", lambda _e: self._apply())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._facet_changed()
        self.text_entry.focus_set()
        self.text_entry.icursor("end")

    # -- the blanks --------------------------------------------------------------

    def _facet_key(self) -> str:
        return next(k for k, v in FACET_TITLES.items() if v == self.facet.get())

    def _where_key(self) -> str:
        return next(k for k, v in WHERE_TITLES.items() if v == self.where.get())

    def _facet_changed(self):
        facet = self._facet_key()
        e = self.ctl.experiment
        if facet == "condition":
            names = [c.display() for c in e.conditions]
            names += [self.ctl.condition_name(r.condition) for r in self.ctl.found_condition_rows()]
            values, state, hint = list(dict.fromkeys(names)), "normal", "Choose a treatment or type a new name."
        elif facet == "set":
            values, state = list(e.strain_groups), "readonly"
            hint = "Add strain groups on Panel first." if not values else ""
            if values and self.value.get() not in values:
                self.value.set(values[0])
        elif facet == "plate":
            template = self.ctl.template
            values = [str(p.id) for p in template.plates] if template is not None and template.plates else []
            state, hint = "normal", "The template's plate number, not the biological replicate."
        else:
            values, state, hint = [], "normal", "Hours since spotting, e.g. 24 or 48."
        self.value_box.configure(values=values, state=state)
        self.value_hint.configure(text=hint)
        self._preview()

    def rule(self) -> KeywordRule | None:
        """The rule as typed, or None while the text is blank."""
        text = self.text.get().strip()
        if not text:
            return None
        return KeywordRule(text, self._facet_key(), self.value.get().strip() or None, self._where_key())

    def _preview(self):
        rule = self.rule()
        if rule is None:
            self.preview.configure(text="Type the text to look for; matching photos are listed here.")
            return
        hits = [f.relpath for f in self.ctl.files if rule.matches(f.parts)]
        if not hits:
            self.preview.configure(text=f"No photo's {self.where.get()} contains ‘{rule.text}’.")
            return
        shown = ", ".join(hits[:3]) + (", …" if len(hits) > 3 else "")
        self.preview.configure(text=f"Matches {len(hits)} of {len(self.ctl.files)} photos: {shown}")

    def _apply(self):
        rule = self.rule()
        if rule is None:
            messagebox.showerror("Add keyword rule", "Enter the text to look for.", parent=self)
            return
        try:
            self.ctl.add_keyword(rule.text, rule.facet, self.value.get(), rule.where)
        except ValueError as exc:
            messagebox.showerror("Add keyword rule", str(exc), parent=self)
            return
        self.on_change()
        self.destroy()


def add_keyword(parent, controller, on_change, **kw) -> None:
    KeywordDialog(parent, controller, on_change, **kw)


class KeywordRulesDialog(tk.Toplevel):
    """The saved rules, with how many photos each catches, and the three buttons.

    Kept out of the Data tab itself: most experiments need no rule at all, and
    an empty table is clutter that pushes the photo list off the screen.
    """

    def __init__(self, parent, controller, on_change, *, text: str = ""):
        super().__init__(parent)
        self.ctl, self.on_change, self.initial_text = controller, on_change, text
        self.title("Keyword rules")
        self.transient(parent.winfo_toplevel())
        self.minsize(620, 360)

        body = ttk.Frame(self, padding=(tokens.SPACE_M, tokens.SPACE_S))
        body.pack(fill="both", expand=True)
        intro = ttk.Label(body, justify="left", wraplength=580, text=(
            "A word in a folder or file name means a treatment, plate, hours or strain group: "
            "‘Gluc’ is Glucose, ‘IMG_9’ is plate 1, ‘day2’ is 48 hours. Rules win over what was "
            "read from names automatically; assignments made by hand still win over rules. "
            "When two keywords for the same detail both match, the longer one wins."))
        intro.pack(fill="x", pady=(0, tokens.SPACE_S))

        area = ttk.Frame(body)
        area.pack(fill="both", expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        scale = max(1.0, self.winfo_fpixels("1i") / 96)
        ttk.Style().configure("Data.Treeview", rowheight=int(24 * scale))
        self.table = ttk.Treeview(area, columns=("text", "where", "means", "photos"),
                                  show="headings", height=8, selectmode="extended",
                                  style="Data.Treeview")
        for key, width, title in zip(("text", "where", "means", "photos"), (150, 130, 240, 60),
                                     ("Contains", "Look in", "Means", "Photos")):
            self.table.heading(key, text=title)
            self.table.column(key, width=int(width * scale), minwidth=40, stretch=key == "means")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        buttons = ttk.Frame(area)
        buttons.grid(row=0, column=2, sticky="n", padx=(tokens.SPACE_XS, 0))
        ttk.Button(buttons, text="Add rule...", style="Accent.TButton", command=self._add).pack(fill="x")
        ttk.Button(buttons, text="Edit...", command=self._edit).pack(fill="x", pady=(tokens.SPACE_XS, 0))
        ttk.Button(buttons, text="Remove", command=self._remove).pack(fill="x", pady=(tokens.SPACE_XS, 0))
        self.table.bind("<Double-1>", lambda _e: self._edit())

        self.empty = ttk.Label(body, foreground=tokens.TEXT_MUTED)
        self.empty.pack(fill="x", pady=(tokens.SPACE_XS, 0))
        ttk.Button(body, text="Close", command=self.destroy).pack(side="right", pady=(tokens.SPACE_S, 0))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Configure>", lambda ev: ev.widget is self and intro.configure(
            wraplength=max(300, ev.width - 40)))
        self.refresh()
        if not controller.experiment.keywords:
            self._add()

    def selected(self):
        rules = self.ctl.experiment.keywords
        return [rules[int(i)] for i in self.table.selection() if int(i) < len(rules)]

    def refresh(self):
        selected = set(self.table.selection())
        self.table.delete(*self.table.get_children())
        for i, rule in enumerate(self.ctl.experiment.keywords):
            self.table.insert("", "end", iid=str(i), values=(
                rule.text, WHERE_TITLES[rule.where],
                f"{FACET_TITLES[rule.facet]}: {describe_value(rule, self.ctl)}",
                self.ctl.keyword_matches(rule)))
        self.table.selection_set([i for i in selected if self.table.exists(i)])
        self.empty.configure(text=(
            "No keyword rules yet. Add rule... starts one." if not self.ctl.experiment.keywords
            else "Photos: how many of the folder's photos each rule matches right now."))

    def _changed(self):
        self.on_change()
        if self.winfo_exists():
            self.refresh()

    def _add(self):
        KeywordDialog(self, self.ctl, self._changed, text=self.initial_text)
        self.initial_text = ""

    def _edit(self):
        rules = self.selected()
        if len(rules) == 1:
            KeywordDialog(self, self.ctl, self._changed, initial=rules[0])

    def _remove(self):
        rules = self.selected()
        if rules:
            self.ctl.remove_keywords(rules)
            self._changed()


def edit_keywords(parent, controller, on_change, **kw) -> KeywordRulesDialog:
    return KeywordRulesDialog(parent, controller, on_change, **kw)
