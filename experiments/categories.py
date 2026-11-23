"""Guessing treatment categories from treatment names, and ordering by them.

"Glucose + 37C" says its carbon source and its temperature; "Glycerol +
NaAsO2" its carbon source and the compound added. These guesses only ever
PROPOSE values: the Conditions tab shows them for the user to confirm, and
nothing reads a guess as if it had been stated.

Headless and stdlib-only.
"""

from __future__ import annotations

import re

#: Carbon sources recognised at the start of a treatment's label or code, with
#: the spellings that name them. Longer spellings are tried first.
CARBON_SOURCES = {
    "Glucose": ("glucose", "dextrose", "glu", "ypd", "scd"),
    "Galactose": ("galactose", "gal"),
    "Glycerol": ("glycerol", "gly", "ypg"),
    "K-OAc": ("potassiumacetate", "koac", "kac", "acetate"),
    "Raffinose": ("raffinose", "raf"),
    "Sucrose": ("sucrose", "suc"),
    "Ethanol": ("ethanol", "etoh"),
    "Lactate": ("lactate",),
    "Maltose": ("maltose",),
    "Fructose": ("fructose",),
}

CARBON = "Carbon source"
TEMPERATURE = "Temperature"
ADDITIVE = "Additive"
#: What a guessed category may already be called in an experiment.
SYNONYMS = {
    CARBON: ("carbon source", "carbon", "carbon sources", "medium", "media", "sugar"),
    TEMPERATURE: ("temperature", "temp", "heat"),
    ADDITIVE: ("additive", "additives", "compound", "drug", "stressor", "stress",
               "chemical", "treatment"),
}
#: Filled in for treatments whose name says nothing about that category, when
#: others do: no temperature in the name means the standard 30°C, no
#: compound means none was added.
ASSUMED = {TEMPERATURE: "30°C", ADDITIVE: "None"}

_SEPARATORS = " +-_/,;:()&"
_TEMPERATURE = re.compile(
    r"(?<![\d.])(\d{2}(?:\.\d)?)\s*(?:°|º|deg(?:rees?)?)?\s*C\b", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"^\s*(\d{2}(?:\.\d)?)\s*$")


def carbon_source(text):
    """(carbon source, the rest of the text) when ``text`` starts with one."""
    text = str(text)
    plain = "".join(ch for ch in text.lower() if ch.isalnum())
    spellings = sorted(((a, name) for name, names in CARBON_SOURCES.items() for a in names),
                       key=lambda x: -len(x[0]))
    for alias, name in spellings:
        if plain.startswith(alias):
            # Walk the original text past the alias's letters for the remainder.
            seen = i = 0
            while i < len(text) and seen < len(alias):
                seen += text[i].isalnum(); i += 1
            return name, text[i:].strip(_SEPARATORS)
    return None, text


def _temperature(text: str) -> tuple[str | None, str]:
    """("37°C", the text without it), or (None, text)."""
    match = _TEMPERATURE.search(text)
    if match is None:
        bare = _BARE_NUMBER.match(text)
        if bare and 15 <= float(bare.group(1)) <= 45:
            match = bare
    if match is None:
        return None, text
    value = f"{float(match.group(1)):g}°C"
    return value, (text[:match.start()] + " " + text[match.end():]).strip(_SEPARATORS)


def guess(label: str, code: str = "") -> dict[str, str]:
    """What a treatment's name says about each category; absent ones are left out.

    The label is what the user wrote. The code is only a fallback for the
    carbon source: it is generated and truncated ("GLUCOSE3"), so anything
    after the carbon source in it is noise.
    """
    out = {}
    source, rest = carbon_source(label)
    if source is None and code:
        source = carbon_source(code)[0]
        rest = label if source is None else rest
    if source:
        out[CARBON] = source
    temperature, rest = _temperature(rest)
    if temperature:
        out[TEMPERATURE] = temperature
    rest = re.sub(r"\s+", " ", rest.strip(_SEPARATORS))
    if rest and source:
        # Only what follows a recognised carbon source is an addition to it;
        # an unrecognised name as a whole is not a compound.
        out[ADDITIVE] = rest
    return out


def guess_all(treatments) -> tuple[list[str], dict[str, dict[str, str]], set]:
    """Guesses for ``[(code, label)]``.

    Returns the categories some name spoke to (in CARBON, TEMPERATURE,
    ADDITIVE order), each code's values, and the (code, category) pairs whose
    value was ASSUMED rather than read.
    """
    found = {code: guess(label, code) for code, label in treatments}
    names = [n for n in (CARBON, TEMPERATURE, ADDITIVE)
             if any(n in values for values in found.values())]
    assumed = set()
    for name, default in ASSUMED.items():
        if name not in names:
            continue
        for code, values in found.items():
            if name not in values and values.get(CARBON):
                values[name] = default
                assumed.add((code, name))
    return names, found, assumed


def existing_name(guessed: str, categories) -> str | None:
    """The experiment's own category a guessed one corresponds to, if any."""
    wanted = SYNONYMS.get(guessed, (guessed.casefold(),))
    for name in categories:
        if " ".join(name.split()).casefold() in wanted:
            return name
    return None


def order(codes, values: dict, names, first: str | None = None) -> list:
    """``codes`` sorted by category: ``first`` (default the first of ``names``)
    leads, then the rest in ``names`` order.

    Within a category, values keep the order they are first used in
    ``values`` (the experiment's own condition order); a code with no value
    in a category goes after those that have one. Ties keep their order.
    """
    names = list(names)
    if first in names:
        names.remove(first)
        names.insert(0, first)
    if not names:
        return list(codes)
    rank = {}
    for name in names:
        seen = {}
        for own in values.values():
            value = own.get(name)
            if value and value not in seen:
                seen[value] = len(seen)
        rank[name] = seen

    def key(code):
        own = values.get(code, {})
        return tuple(rank[n].get(own.get(n), len(rank[n])) for n in names)
    return sorted(codes, key=key)
