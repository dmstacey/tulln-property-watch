#!/usr/bin/env python3
"""Classify heating technology from listing text.

The twice-daily watch has no single script: each run edits properties.json
and index.html, then the pre-commit hook (stamp_first_seen.py) calls
annotate_property(). Keep this file next to that hook so new rows get
heating_tech / heating_tech_source on commit.

Does not guess. Zentralheizung, Fußbodenheizung and Etagenheizung are
distribution, not a fuel. Solar and an open fireplace are notes unless
they are the only named system (solar) or a real stove (Kachelofen, Holzofen).
"""
import os
import re
from html import unescape

ORDER = ["air", "geo", "hp", "gas", "oil", "pellets", "wood", "district", "electric", "solar"]
LABEL = {
    "air": "Air-source heat pump",
    "geo": "Geothermal",
    "hp": "Heat pump",
    "gas": "Gas",
    "oil": "Oil",
    "pellets": "Pellets",
    "wood": "Wood",
    "district": "District heating",
    "electric": "Electric",
    "solar": "Solar",
}

# (tech, regex, kind) kind is "primary" or "supplement" or "fireplace"
PATTERNS = [
    ("geo", re.compile(r"Erdwärme(?:heizung)?|Erdsonden|Sole[-\s]?Wasser(?:wärmepumpe)?|Tiefenbohrung", re.I), "primary"),
    ("air", re.compile(r"Luft[-\s/]?Wasser[-\s]?Wärmepumpe|Luftwärmepumpe|Luft[-\s]Wärmepumpe", re.I), "primary"),
    ("hp", re.compile(r"Wärmepumpe", re.I), "primary"),
    ("gas", re.compile(
        r"Gas[-\s]?Zentralheizung|Gaszentralheizung|Gastherme|Gas[-\s]Therme|"
        r"Gasetagenheizung|Gasheizung|Gaskessel|Gas\s*ZH|Basis von Gas|"
        r"Gas\s*/\s*Fest|Vaillant[-\s]?Therme|Zentralheizung mit Gas|Heizung mit Gas",
        re.I), "primary"),
    ("oil", re.compile(
        r"Öl[-\s]?Warmwasser[-\s]?Zentralheizung|Ölheizung|Heizöl|Ölkessel|"
        r"Zentralheizung mit Öl|Heizung mit Öl",
        re.I), "primary"),
    ("pellets", re.compile(r"Pelletheizung|Pelletofen|Pellets(?:heizung|kessel|ofen)?", re.I), "primary"),
    ("wood", re.compile(
        r"Festbrennstoff(?:anlage|e|en)?|Stückholz|Hackgut|Holzvergaser|"
        r"Kachelofen|Schwedenofen|Holzofen|Kaminofen|Holzbrenner|Allesbrenner|"
        r"Heizungsart:\s*HOLZ|Einzelofenheizung|Ofen für feste Brennstoffe|"
        r"feste[nmr]? Brennstoffe(?:n|anlage)?",
        re.I), "primary"),
    ("fireplace", re.compile(r"offenen? Kamin|Kamin mentioned", re.I), "fireplace"),
    ("district", re.compile(r"Fernwärme", re.I), "primary"),
    ("electric", re.compile(r"Elektroheizung|Nachtspeicher(?:heizung)?|Infrarotheizung|Infrarot[-\s]?Heizung", re.I), "primary"),
    ("solar", re.compile(r"Solarheizung|Solaranlage|thermische[nr]?\s+Solar\w*|Photovoltaik|\bPV\b|Balkonkraftwerk", re.I), "supplement"),
]

WOOD_NOT = re.compile(r"holz(?:fenster|haus|bau|stiege|balken|schuppen|rahmen)", re.I)
SUPPLEMENT_BEFORE = re.compile(r"zusätzlich|zusaetzlich|kann zusätzlich|kann zusaetzlich", re.I)
DEINSTALL_AFTER = re.compile(r"deinstall|stillgelegt|außer betrieb|ausser betrieb", re.I)


def html_to_text(html):
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = unescape(html)
    return re.sub(r"\s+", " ", html)


def listing_text(prop, archive_html=None):
    parts = []
    for key in ("heating", "condition_note", "finish_status_note", "notes"):
        val = prop.get(key)
        if val:
            parts.append(str(val))
    if archive_html:
        parts.append(html_to_text(archive_html))
    return " \n ".join(parts)


def _skip(tech, text, start, end, phrase):
    before = text[max(0, start - 80):start]
    after = text[end:end + 70]
    if tech == "wood" and re.search(r"holz(?:fenster|haus|bauweise|rahmenbau|stiege|balken|schuppen)", phrase, re.I):
        if not re.search(r"ofen|festbrenn|hackgut|stückholz|kachel|schweden|brenner|heizungsart", phrase, re.I):
            return True
    if tech == "hp":
        pre = text[max(0, start - 16):start]
        if re.search(r"Luft[-\s/]?$|Erd$", pre, re.I) or re.search(r"Luft[-\s/]?Wasser[-\s]?$", pre, re.I):
            return True
        # "beispielsweise auf eine Wärmepumpe" is a suggested replacement, not the installed system.
        if re.search(r"beispielsweise\s+(?:auf\s+(?:eine[n]?\s+)?)?$", before, re.I):
            return True
        if re.search(r"umstellung\s+(?:der\s+\w+\s+){0,4}auf\s+(?:eine[n]?\s+)?$", before, re.I):
            return True
    if tech == "gas" and re.search(r"möglichkeit\s+(?:auf|zur)\s*$", before, re.I):
        return True
    if tech in ("wood", "pellets", "oil", "gas", "air", "electric"):
        near = text[max(0, start - 55):start]
        if re.search(r"nachträglich", near, re.I) or re.search(r"Einbau eines", near, re.I):
            return True
    if tech == "district":
        if re.search(r"nicht\s+vorhanden|nicht\s+möglich", after, re.I):
            return True
        if re.match(r"\s*:\s*möglich", after, re.I):
            return True
        if re.search(r"Grundstücksgrenze|Grundstuecksgrenze", after, re.I):
            return True
    # Only the system this phrase names. A later "Elektroheizung ... deinstalliert"
    # must not blank the stove named earlier in the same sentence.
    if re.search(r"deinstall|stillgelegt", after[:28], re.I):
        return True
    return False


def _is_supplement(tech, kind, text, start, end, phrase):
    if kind in ("supplement", "fireplace"):
        return True
    if tech != "wood":
        return False
    before = text[max(0, start - 70):start]
    after = text[end:end + 45]
    if re.search(r"Heizungsart:\s*$", before, re.I):
        return False
    if SUPPLEMENT_BEFORE.search(before) or re.search(r"kann zusätzlich|für zusätzliche|als zusätzliche", after, re.I):
        return True
    if re.search(r"gemütlicher|stilvollen|schönster|schoenster|zusätzlicher", before, re.I):
        return True
    if re.search(r"für (?:behagliche|wohlige|gemütliche)|sorgt für gemütliche", after, re.I):
        return True
    if re.search(r"ehemalige[rn]?\s+$|alternativ\s+wäre", before, re.I):
        return True
    return False


def classify_text(text):
    """Return heating_tech, heating_tech_source from a plain-text blob."""
    if not text or not text.strip():
        return "Unknown", "no heating system named in the listing text"
    found = []  # (tech, phrase, supplement)
    for tech, cre, kind in PATTERNS:
        for m in cre.finditer(text):
            phrase = re.sub(r"\s+", " ", m.group(0)).strip()
            if _skip(tech, text, m.start(), m.end(), phrase):
                continue
            use_tech = tech
            if tech == "gas" and re.search(r"Vaillant", phrase, re.I):
                # Vaillant-Therme is a gas boiler when the listing also says gas.
                # On its own it is the air-source unit David asked to recognise.
                if not re.search(r"Gas\s*ZH|Gasheizung|Gaszentralheizung|Gas[-\s]?Zentralheizung|Gaskessel|Gastherme", text, re.I):
                    use_tech = "air"
            if tech == "fireplace":
                use_tech = "wood"
            supp = _is_supplement(use_tech, kind if tech != "fireplace" else "fireplace", text, m.start(), m.end(), phrase)
            found.append((use_tech, phrase, supp))

    # Drop generic heat-pump hits when air or ground was named.
    techs_primary = {t for t, _, s in found if not s}
    if "air" in techs_primary or "geo" in techs_primary:
        found = [item for item in found if item[0] != "hp"]

    primaries = []
    supplements = []
    seen_p, seen_s = set(), set()
    for tech, phrase, supp in found:
        if supp or tech == "solar":
            key = (tech, phrase.lower())
            if key not in seen_s:
                seen_s.add(key)
                supplements.append((tech, phrase))
        else:
            key = (tech, phrase.lower())
            if key not in seen_p:
                seen_p.add(key)
                primaries.append((tech, phrase))

    primary_techs = []
    for tech in ORDER:
        if any(t == tech for t, _ in primaries):
            primary_techs.append(tech)

    # Solar becomes the type only when nothing else is named.
    if not primary_techs:
        solar_phrases = [ph for t, ph in supplements if t == "solar"]
        fire = [ph for t, ph in supplements if t == "wood"]
        if solar_phrases and not fire:
            primary_techs = ["solar"]
            primaries = [("solar", solar_phrases[0])]
            supplements = [(t, ph) for t, ph in supplements if t != "solar"]
        elif fire or solar_phrases:
            bits = []
            if fire:
                bits.append("only an open fireplace mentioned (" + fire[0] + "), not counted as the heating system")
            if solar_phrases and not fire:
                pass
            vague = _vague_distribution(text)
            reason = "; ".join(x for x in [vague, "; ".join(bits) if bits else None] if x)
            if not reason:
                reason = "no heating system named in the listing text"
            return "Unknown", reason
        else:
            vague = _vague_distribution(text)
            return "Unknown", vague or "no heating system named in the listing text"

    phrases = []
    seen_ph = set()
    for tech in primary_techs:
        for t, ph in primaries:
            if t == tech and ph.lower() not in seen_ph:
                seen_ph.add(ph.lower())
                phrases.append(ph)
    phrases = _collapse(phrases)
    label = " + ".join(LABEL[t] for t in primary_techs)
    extra = []
    seen_e = set()
    folded_primary = {re.sub(r"[^a-z0-9äöüß]", "", ph.lower()) for ph in phrases}
    for t, ph in supplements:
        fold = re.sub(r"[^a-z0-9äöüß]", "", ph.lower())
        if ph.lower() in seen_e or fold in folded_primary:
            continue
        seen_e.add(ph.lower())
        extra.append(ph)
    extra = _collapse(extra)
    source = "; ".join(phrases)
    if extra:
        source += " · also " + "; ".join(extra)
    if primary_techs == ["hp"]:
        source += " · air or ground not stated"
    if len(source) > 280:
        source = source[:277] + "…"
    return label, source



def _collapse(phrases):
    """Drop a shorter phrase when a longer one already names the same words."""
    out = []
    for ph in phrases:
        fold = re.sub(r"[^a-z0-9äöüß]", "", ph.lower())
        replaced = False
        keep = True
        for i, prev in enumerate(out):
            pf = re.sub(r"[^a-z0-9äöüß]", "", prev.lower())
            if not fold or not pf:
                continue
            if fold == pf or fold in pf:
                keep = False
                break
            if pf in fold:
                out[i] = ph
                keep = False
                replaced = True
                break
        if keep and not replaced:
            out.append(ph)
    return out


def _vague_distribution(text):
    # "Sonstige" in a location block is "Sonstige: Bank", not a heating type.
    named = []
    if re.search(r"Heizung:?\s*(?:Fußbodenheizung|Fussbodenheizung)|Fussbodenheizung|Fußbodenheizung", text, re.I):
        named.append("Fußbodenheizung")
    if re.search(r"Etagenheizung", text, re.I):
        named.append("Etagenheizung")
    if re.search(r"Heizung:?\s*Zentralheizung", text, re.I):
        named.append("Zentralheizung")
    if re.search(r"Heizung:?\s*Sonstige", text, re.I):
        named.append("Sonstige")
    out = []
    for n in named:
        if n not in out:
            out.append(n)
    if out:
        return "listing only names " + " / ".join(out) + ", no fuel"
    return None


def load_archive_html(prop, archive_dir):
    if not archive_dir:
        return None
    candidates = []
    if prop.get("archive"):
        candidates.append(os.path.join(archive_dir, os.path.basename(str(prop["archive"]))))
    if prop.get("id"):
        candidates.append(os.path.join(archive_dir, f"{prop['id']}.html"))
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                return open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                return None
    return None


def annotate_property(prop, archive_dir=None):
    """Set heating_tech and heating_tech_source. Leaves the raw heating text unchanged.
    Returns the property.
    """
    html = load_archive_html(prop, archive_dir) if archive_dir else None
    # Notes can mention a gas connection (Gas/Strom/Kanal) without a heating system.
    # They are still read: the watch sometimes records the only copy of the heating line there.
    text = listing_text(prop, html)
    tech, source = classify_text(text)
    new = {}
    placed = False
    for k, v in list(prop.items()):
        if k in ("heating_tech", "heating_tech_source"):
            continue
        new[k] = v
        if k == "heating":
            new["heating_tech"] = tech
            new["heating_tech_source"] = source
            placed = True
    if not placed:
        new["heating_tech"] = tech
        new["heating_tech_source"] = source
    prop.clear()
    prop.update(new)
    return prop


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "properties.json"
    archive = sys.argv[2] if len(sys.argv) > 2 else "archive"
    rows = json.load(open(path, encoding="utf-8"))
    from collections import Counter
    c = Counter()
    for p in rows:
        annotate_property(p, archive)
        c[p["heating_tech"]] += 1
        print(f"{p['id']:6} {p['heating_tech'][:40]:40} | {p['heating_tech_source'][:140]}")
    print("---")
    for k, v in c.most_common():
        print(f"{v:3} {k}")
