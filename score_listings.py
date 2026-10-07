#!/usr/bin/env python3
"""Family-fit score for every listing (0-100), run from the pre-commit hook
after stamp_first_seen.py. The whole set is rescored on every commit so the
"Highly recommended" cut recalibrates to what is on the market now.

All renovation and operating numbers are ESTIMATES from rules of thumb
(Austria 2026), not quotes. Unknown rooms/garden/cellar score conservatively.

Weights (max points, sum 100):
  cost 45     effective cost = all-in price + renovation midpoint + 10 years of
              operating costs (BK + heating). <= EUR 420k -> 45, >= EUR 700k -> 0, linear.
  commute 25  PT to the 8th district 15 (<=55 min full, >=110 min zero)
              car to the 21st district 10 (<=20 min full, >=55 min zero)
  space 18    living m2 8 (<=90 zero, >=150 full); estimated garden 10
              (plot - footprint; footprint ~ 60% of living m2; <=100 zero, >=600 full)
  storage 5   cellar named 5; other storage only (Dachboden, Abstellraum, Schuppen, Garage) 2
  5th room 4  rooms >= 5
  sauna 3
Penalties: rooms < 4 -> -25; rooms unknown -> -8; not a family-home type -> -15.

Renovation rule of thumb (EUR per m2 living area, estimate):
  schluesselfertig / turnkey                0 - 10k flat (minor works); 250 - 450 /m2 if the
                                            listing says parts are still roh / to be finished
  belagsfertig (floors, doors, paint, ...)  250 - 450 /m2
  rohbaufertig (interior completion)        1,100 - 1,700 /m2
  sanierungsbedarf (renovation needed)      500 - 1,000 /m2;  700 - 1,400 if "komplett/kernsanierung"
                                            or year built < 1960 or HWB F/G
  unknown finish, by year built / HWB class:
     (worse of year-built and HWB bucket wins)
     >= 2010 or HWB A++..B                   0 - 15k flat
     1990 - 2009 or HWB C                    80 - 250 /m2
     1970 - 1989 or HWB D/E                  250 - 600 /m2
     < 1970 or HWB F/G                       450 - 900 /m2
     nothing known                           150 - 500 /m2
  + oil heating (replacement expected)      15k - 25k
  Living area unknown -> 120 m2 assumed.
Operating costs (estimate): BK from the listing if given, else EUR 150/month assumed
  (Grundsteuer, water, sewage, waste, insurance). Heating kWh/year = HWB x living m2
  (HWB from class midpoint if only the letter is known, 180 if unknown) after
  renovation-free state; EUR 0.12/kWh for gas/oil/pellets/district/unknown,
  EUR 0.07/kWh for heat pumps (electricity / COP ~3.5).
Labels: Highly recommended = rooms >= 4, family home, total-cost midpoint <= EUR 510k,
  PT <= 80 min or car <= 40 min, ranked by score, score >= 50, at most 8.
  If fewer than 3 qualify, the total-cost cap is relaxed in 20k steps up to 550k.
  Worth a look = score >= 55 (not HR). Not recommended = rest.
"""
import csv, io, json, os, re
import classify_heating as ch

here = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(here, "archive")
research = "/workspace/property-research"
PROPS_RE = re.compile(r"const PROPERTIES = (\[.*?\n\]);\n", re.S)
FAMILY = {"EFH", "DHH", "RH", "Reihenhaus", "Einfamilienhaus", "Bungalow", "Doppelhaushälfte", "Haus", "Villa"}
HWB_MID = {"A++": 15, "A+": 20, "A": 35, "B": 65, "C": 85, "D": 130, "E": 180, "F": 230, "G": 300}
FIELDS = ["fit_score", "fit_label", "fit_components", "rooms_ok", "rec_summary", "rec_strengths",
          "rec_weaknesses", "rec_opportunities", "reno_estimate_low", "reno_estimate_high",
          "total_cost_low", "total_cost_high", "operating_monthly_est", "operating_note",
          "has_cellar", "garden_est_m2", "fit_scored_at"]
MAX = {"cost": 45, "pt": 15, "drive": 10, "living": 8, "garden": 10, "storage": 5, "room5": 4, "sauna": 3}

def lin(v, lo, hi, pts, invert=False):
    if v is None:
        return None
    f = (v - lo) / (hi - lo)
    f = max(0.0, min(1.0, f))
    return round(pts * (1 - f if invert else f), 1)

def num(v):
    if v is None or v == "":
        return None
    m = re.search(r"\d{4}", str(v)) if not isinstance(v, (int, float)) else None
    if isinstance(v, (int, float)):
        return v
    return int(m.group()) if m else None

def k(v):
    return f"€{round(v / 1000):,}k".replace(",", ".")

def hwb_letter(p):
    s = str(p.get("energy_hwb_class") or "").upper().replace("HWB", "").strip()
    m = re.match(r"A\+\+|A\+|[A-G]", s)
    return m.group() if m else None

def text_of(p):
    return ch.listing_text(p, ch.load_archive_html(p, ARCHIVE))

def cellar(text):
    t = text.lower()
    if re.search(r"kein(en)? keller|nicht unterkellert|ohne keller|keller: nein", t):
        neg = True
    else:
        neg = False
    if not neg and re.search(r"keller|unterkellert|souterrain", t):
        return "yes"
    if re.search(r"dachboden|abstellraum|schuppen|gerätehaus|geräteraum|lagerraum|garage", t):
        return "storage"
    return "no" if neg else "unknown"

def renovation(p, text):
    m2 = p.get("living_m2") or 120
    fs = p.get("finish_status") or "unknown"
    yb = num(p.get("year_built"))
    cls = hwb_letter(p)
    t = text.lower()
    cn = (str(p.get("condition_note") or "") + " " + str(p.get("finish_status_note") or "")).lower()
    if fs == "schluesselfertig" and re.search(r"\broh\b|fertigstellen|nicht fertig", cn):
        lo, hi, why = 250 * m2, 450 * m2, "turnkey in part, but listing says parts are unfinished (roh): €250–450/m²"
    elif fs == "schluesselfertig":
        lo, hi, why = 0, 10000, "turnkey: minor works only"
    elif fs == "belagsfertig":
        lo, hi, why = 250 * m2, 450 * m2, "belagsfertig: floors, doors, painting, fittings at €250–450/m²"
    elif fs == "rohbaufertig":
        lo, hi, why = 1100 * m2, 1700 * m2, "Rohbau: interior completion at €1,100–1,700/m²"
    elif fs == "sanierungsbedarf":
        heavy = re.search(r"komplett|kernsanier|generalsanier|abbruch", t) or (yb and yb < 1960) or cls in ("F", "G")
        if heavy:
            lo, hi, why = 700 * m2, 1400 * m2, "renovation needed, heavy (old/poor energy/complete): €700–1,400/m²"
        else:
            lo, hi, why = 500 * m2, 1000 * m2, "renovation needed: €500–1,000/m²"
    else:
        sev_y = None if not yb else (0 if yb >= 2010 else 1 if yb >= 1990 else 2 if yb >= 1970 else 3)
        sev_c = {"A++": 0, "A+": 0, "A": 0, "B": 0, "C": 1, "D": 2, "E": 2, "F": 3, "G": 3}.get(cls)
        sevs = [x for x in (sev_y, sev_c) if x is not None]
        sev = max(sevs) if sevs else None
        if sev == 0:
            lo, hi, why = 0, 15000, "recent/efficient: €0–15k"
        elif sev == 1:
            lo, hi, why = 80 * m2, 250 * m2, "1990–2009 or HWB C: refresh €80–250/m²"
        elif sev == 2:
            lo, hi, why = 250 * m2, 600 * m2, "1970–89 or HWB D/E: partial renovation €250–600/m²"
        elif sev == 3:
            lo, hi, why = 450 * m2, 900 * m2, "pre-1970 or HWB F/G: substantial renovation €450–900/m²"
        else:
            lo, hi, why = 150 * m2, 500 * m2, "condition unknown: €150–500/m²"
    if "Oil" in str(p.get("heating_tech") or "") and fs != "schluesselfertig":
        lo, hi, why = lo + 15000, hi + 25000, why + "; + €15–25k oil-heating replacement"
    r = lambda x: int(round(x / 5000.0) * 5000)
    return r(lo), r(hi), why

def operating(p):
    m2 = p.get("living_m2") or 120
    cls = hwb_letter(p)
    hwb = p.get("energy_hwb_value") or HWB_MID.get(cls) or 180
    tech = str(p.get("heating_tech") or "")
    rate = 0.07 if "heat pump" in tech.lower() or "Geothermal" in tech else 0.12
    heat = hwb * m2 * rate / 12
    bk = p.get("betriebskosten_monat_eur")
    bk_known = bk is not None
    bkv = bk if bk_known else 150
    total = round(bkv + heat)
    note = (f"Est. ~€{total}/month: BK €{round(bkv)} ({'listing' if bk_known else 'assumed, not in listing'})"
            f" + heating ~€{round(heat)} (HWB {round(hwb)}{'' if p.get('energy_hwb_value') else (' from class ' + cls if cls else ' assumed')},"
            f" {tech or 'heating unknown'}). Estimate.")
    return total, note

def score_one(p):
    text = text_of(p)
    rooms = p.get("rooms")
    try:
        rooms = int(float(rooms)) if rooms not in (None, "") else None
    except (TypeError, ValueError):
        rooms = None
    typ = str(p.get("type") or "")
    family = typ in FAMILY
    lo, hi, why = renovation(p, text)
    allin = p.get("all_in_price_eur") or (p.get("price_eur") or 0) * 1.1
    tlo, thi = int(allin + lo), int(allin + hi)
    op, opnote = operating(p)
    eff = (tlo + thi) / 2 + op * 120
    comp = {}
    comp["cost"] = lin(eff, 420000, 700000, MAX["cost"], invert=True)
    pt, dr = p.get("pt_lange_gasse_min"), p.get("drive_anton_boeck_min")
    comp["pt"] = lin(pt, 55, 110, MAX["pt"], invert=True) if pt is not None else round(MAX["pt"] * 0.3, 1)
    comp["drive"] = lin(dr, 20, 55, MAX["drive"], invert=True) if dr is not None else round(MAX["drive"] * 0.3, 1)
    lm = p.get("living_m2")
    comp["living"] = lin(lm, 90, 150, MAX["living"]) if lm else 0.0
    plot = p.get("plot_m2")
    garden = None
    if p.get("has_garden") is False:
        garden = 0
    elif plot:
        garden = max(0, int(plot - (lm or 120) * 0.6))
    comp["garden"] = lin(garden, 100, 600, MAX["garden"]) if garden is not None else 1.0
    c = cellar(text)
    comp["storage"] = {"yes": 5, "storage": 2}.get(c, 0)
    comp["room5"] = MAX["room5"] if rooms and rooms >= 5 else 0
    comp["sauna"] = MAX["sauna"] if p.get("has_sauna") else 0
    pen = 0
    if rooms is None:
        pen -= 8
    elif rooms < 4:
        pen -= 25
    if not family:
        pen -= 15
    comp["penalty"] = pen
    score = max(0, min(100, round(sum(comp.values()))))
    rooms_ok = None if rooms is None else rooms >= 4

    S, W, O = [], [], []
    if eff <= 520000: S.append(f"Low overall cost (est. {k(tlo)}–{k(thi)} incl. renovation)")
    elif tlo > 550000: W.append(f"High overall cost (est. {k(tlo)}–{k(thi)} incl. renovation)")
    if pt is not None and pt <= 65: S.append(f"Good public transport to the 8th district ({pt} min)")
    elif pt is not None and pt > 90: W.append(f"Long public-transport commute to the 8th district ({pt} min)")
    if dr is not None and dr <= 30: S.append(f"Short drive to the 21st district ({dr} min)")
    elif dr is not None and dr > 45: W.append(f"Long drive to the 21st district ({dr} min)")
    if rooms is None: W.append("Room count unknown — check before viewing")
    elif rooms < 4: W.append(f"Only {rooms} rooms — fails the 4-room need")
    elif rooms >= 5: S.append(f"{rooms} rooms: one spare for guests/office")
    else: S.append("4 rooms: meets the parents/boys/girls need")
    if lm and lm >= 140: S.append(f"Spacious ({round(lm)} m² living)")
    elif lm and lm < 100: W.append(f"Small living area ({round(lm)} m²)")
    elif not lm: W.append("Living area unknown")
    if garden is None: W.append("Garden size unknown")
    elif garden >= 450: S.append(f"Large garden (est. ~{garden} m²)")
    elif garden < 150: W.append(f"Little garden (est. ~{garden} m²)")
    if c == "yes": S.append("Cellar mentioned")
    elif c == "storage": S.append("Some storage (no cellar named)")
    elif c == "no": W.append("No cellar")
    else: W.append("Cellar unknown")
    if p.get("has_sauna"): S.append("Sauna")
    if not family: W.append(f"Type {typ or 'unknown'} is not a plain family home")
    if p.get("finish_status") == "rohbaufertig": W.append("Rohbau: large completion cost and time")
    if "Oil" in str(p.get("heating_tech") or ""): W.append("Oil heating: replacement expected")
    if p.get("seller_type") == "private": O.append("Private seller: no buyer commission")
    if p.get("price_change_eur") is not None and p["price_change_eur"] < 0:
        O.append(f"Price already cut by {k(-p['price_change_eur'])}: seller may negotiate")
    if p.get("finish_status") in ("sanierungsbedarf",) or lo >= 50000:
        O.append("Renovate to own taste; NÖ/federal renovation subsidies (e.g. Sanierungsbonus, heating replacement) may reduce cost")
    if p.get("finish_status") == "belagsfertig": O.append("Choose own floors/finishes")
    if garden and garden >= 600: O.append("Big plot: room for play area, garden shed or extension")
    if c == "yes" and (rooms or 0) == 4: O.append("Cellar could take an office/hobby room")
    if not O: O.append("Negotiate on price; check BK and heating bills")

    return {
        "fit_score": score, "fit_components": comp, "rooms_ok": rooms_ok,
        "rec_strengths": S, "rec_weaknesses": W, "rec_opportunities": O,
        "reno_estimate_low": lo, "reno_estimate_high": hi, "reno_basis": why,
        "total_cost_low": tlo, "total_cost_high": thi,
        "operating_monthly_est": op, "operating_note": opnote,
        "has_cellar": c, "garden_est_m2": garden,
        "_eff": eff, "_family": family, "_pt": pt, "_dr": dr,
    }

def label_all(arr):
    res = {}
    for p in arr:
        if (p.get("listing_status") or "active") == "active" and p.get("category") != "sold":
            res[p["id"]] = score_one(p)
    def eligible(r, cap):
        mid = (r["total_cost_low"] + r["total_cost_high"]) / 2
        com = (r["_pt"] is not None and r["_pt"] <= 80) or (r["_dr"] is not None and r["_dr"] <= 40)
        return r["rooms_ok"] is True and r["_family"] and mid <= cap and com and r["fit_score"] >= 50
    cap = 510000
    while True:
        hr = sorted([i for i, r in res.items() if eligible(r, cap)], key=lambda i: -res[i]["fit_score"])[:8]
        if len(hr) >= 3 or cap >= 550000:
            break
        cap += 20000
    for i, r in res.items():
        r["fit_label"] = "Highly recommended" if i in hr else ("Worth a look" if r["fit_score"] >= 55 else "Not recommended")
        lab = r["fit_label"]
        lead = {"Highly recommended": "Top family fit", "Worth a look": "Possible fit", "Not recommended": "Weak fit"}[lab]
        why = "; ".join((r["rec_strengths"][:2] + r["rec_weaknesses"][:1])) or "limited data"
        r["rec_summary"] = f"{lead} ({r['fit_score']}/100): {why}."
        r["rec_basis_cap"] = cap
    return res

def apply(arr, res):
    stamp = None
    for p in arr:
        r = res.get(p.get("id"))
        for f in FIELDS + ["reno_basis"]:
            p.pop(f, None)
        if not r:
            continue
        for f in FIELDS + ["reno_basis"]:
            if f in r:
                p[f] = r[f]

def do_html(path, res):
    if not os.path.exists(path):
        return None
    s = open(path, encoding="utf-8").read()
    m = PROPS_RE.search(s)
    if not m:
        raise SystemExit(f"{path}: const PROPERTIES not found")
    arr = json.loads(m.group(1))
    apply(arr, res)
    out = s[:m.start(1)] + json.dumps(arr, indent=2, ensure_ascii=False) + s[m.end(1):]
    if not re.search(r"\n\];\nconst BUDGET = 450000;", out):
        raise SystemExit(f"{path}: refusing to write – const BUDGET must follow PROPERTIES")
    open(path, "w", encoding="utf-8").write(out)
    return arr

def do_json(path, res):
    if not os.path.exists(path):
        return
    raw = open(path, encoding="utf-8").read()
    data = json.loads(raw)
    lst = data if isinstance(data, list) else data.get("properties", [])
    apply(lst, res)
    open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))

def cell(v):
    if v is None: return ""
    if isinstance(v, list): return " | ".join(v)
    if isinstance(v, dict): return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)

def do_csv(path, res):
    if not os.path.exists(path):
        return
    raw = open(path, encoding="utf-8", newline="").read()
    rd = csv.DictReader(io.StringIO(raw))
    rows = list(rd)
    cols = [c for c in rd.fieldnames if c not in FIELDS + ["reno_basis"]] + FIELDS + ["reno_basis"]
    for r in rows:
        x = res.get(r.get("id")) or {}
        for f in FIELDS + ["reno_basis"]:
            r[f] = cell(x.get(f))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\r\n" if "\r\n" in raw else "\n")
    w.writeheader(); w.writerows(rows)
    open(path, "w", encoding="utf-8", newline="").write(out.getvalue())

def main():
    s = open(os.path.join(here, "index.html"), encoding="utf-8").read()
    arr = json.loads(PROPS_RE.search(s).group(1))
    res = label_all(arr)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    for r in res.values():
        r["fit_scored_at"] = day
    do_html(os.path.join(here, "index.html"), res)
    do_html(os.path.join(research, "dashboard.html"), res)
    do_json(os.path.join(here, "properties.json"), res)
    do_json(os.path.join(research, "properties.json"), res)
    do_csv(os.path.join(here, "properties.csv"), res)
    do_csv(os.path.join(research, "properties.csv"), res)
    from collections import Counter
    print("score_listings:", dict(Counter(r["fit_label"] for r in res.values())), "cap", next(iter(res.values()))["rec_basis_cap"] if res else None)

if __name__ == "__main__":
    main()
