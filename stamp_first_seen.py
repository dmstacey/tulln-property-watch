#!/usr/bin/env python3
"""Give every listing a first_seen_at timestamp (Vienna time).
Known times live in first-seen-times.json; listings first seen today without
a time get the current time. Runs from the git pre-commit hook.

Also keeps price_history in step with the latest price. The daily watch has no
separate price script: it edits these files, and this hook is what runs on every
commit. A new price_eur is appended; price_eur_previous / price_change_eur /
price_changed_at stay aligned with the last step. Dates are not invented for a
backfill except the day this hook first notices a change that has no date.
"""
import csv, io, json, re, os
from datetime import datetime
from zoneinfo import ZoneInfo
here = os.path.dirname(os.path.abspath(__file__))
research = "/workspace/property-research"
now = datetime.now(ZoneInfo("Europe/Vienna"))
today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M")
side_path = os.path.join(here, "first-seen-times.json")
side = json.load(open(side_path, encoding="utf-8"))
PROPS_RE = re.compile(r"const PROPERTIES = (\[.*?\n\]);\n", re.S)

def fill(arr):
    for p in arr:
        pid = p.get("id")
        if pid in side:
            p["first_seen_at"] = side[pid]
        elif p.get("first_seen_at"):
            side[pid] = p["first_seen_at"]
        elif p.get("first_seen") == today:
            p["first_seen_at"] = side[pid] = stamp

def date_only(v):
    if not v:
        return None
    s = str(v)[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None

def put_history(p, hist):
    """Keep price_history next to the other price-change fields."""
    if "price_history" in p and list(p).index("price_history") == list(p).index("price_change_eur") + 1 if "price_change_eur" in p else False:
        p["price_history"] = hist
        return
    new = {}
    placed = False
    for k, v in p.items():
        if k == "price_history":
            continue
        new[k] = v
        if k == "price_change_eur":
            new["price_history"] = hist
            placed = True
    if not placed:
        new["price_history"] = hist
    p.clear()
    p.update(new)

def reconcile_history(p):
    """Append the current price when it is not already the last history step.
    Does not change price_eur or first_seen_at. Returns True if p changed.
    """
    price = p.get("price_eur")
    if price is None:
        return False
    hist = p.get("price_history")
    if not isinstance(hist, list):
        hist = []
    else:
        hist = [dict(step) for step in hist]
    changed = False
    if not hist and p.get("price_eur_previous") is not None and p.get("price_change_eur") not in (None, 0):
        fs = date_only(p.get("first_seen"))
        ch = date_only(p.get("price_changed_at"))
        # first_seen is the day we logged the previous price only when it is earlier
        # than the change. Same-day (or unknown) stays null so we do not invent a date.
        prev_at = fs if fs and ch and fs < ch else None
        hist = [
            {"price_eur": p["price_eur_previous"], "at": prev_at},
            {"price_eur": price, "at": ch},
        ]
        changed = True
    elif hist and hist[-1].get("price_eur") != price:
        at = date_only(p.get("price_changed_at"))
        last_at = date_only(hist[-1].get("at"))
        if not at or at == last_at:
            at = today
        hist.append({"price_eur": price, "at": at})
        changed = True
    if len(hist) >= 2:
        prev, cur = hist[-2], hist[-1]
        new_prev = prev.get("price_eur")
        new_change = cur["price_eur"] - new_prev if new_prev is not None and cur.get("price_eur") is not None else None
        new_at = cur.get("at")
        if (p.get("price_eur_previous") != new_prev or p.get("price_change_eur") != new_change
                or p.get("price_changed_at") != new_at):
            p["price_eur_previous"] = new_prev
            p["price_change_eur"] = new_change
            p["price_changed_at"] = new_at
            changed = True
    if hist and p.get("price_history") != hist:
        put_history(p, hist)
        changed = True
    return changed

def load_html(path):
    s = open(path, encoding="utf-8").read()
    m = PROPS_RE.search(s)
    if not m:
        raise SystemExit(f"{path}: const PROPERTIES not found")
    return s, m, json.loads(m.group(1))

def save_html(path, s, m, arr):
    out = s[:m.start(1)] + json.dumps(arr, indent=2, ensure_ascii=False) + s[m.end(1):]
    if "const BUDGET = 450000;" not in out:
        raise SystemExit(f"{path}: refusing to write – const BUDGET line missing")
    open(path, "w", encoding="utf-8").write(out)

def process_html(path):
    if not os.path.exists(path):
        return None
    s, m, arr = load_html(path)
    fill(arr)
    for p in arr:
        reconcile_history(p)
    save_html(path, s, m, arr)
    return arr

def process_json(path):
    if not os.path.exists(path):
        return
    raw = open(path, encoding="utf-8").read()
    data = json.loads(raw)
    lst = data if isinstance(data, list) else data.get("properties", [])
    fill(lst)
    for p in lst:
        reconcile_history(p)
    out = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")
    open(path, "w", encoding="utf-8").write(out)

def sync_csv(path, by_id):
    if not os.path.exists(path):
        return
    raw = open(path, encoding="utf-8", newline="").read()
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return
    cols = list(rows[0].keys())
    if "price_history" not in cols:
        if "price_change_eur" in cols:
            cols.insert(cols.index("price_change_eur") + 1, "price_history")
        else:
            cols.append("price_history")
    changed = False
    for r in rows:
        src = by_id.get(r.get("id"))
        hist = (src or {}).get("price_history") or []
        cell = json.dumps(hist, ensure_ascii=False, separators=(",", ":")) if hist else ""
        if r.get("price_history") != cell:
            changed = True
        r["price_history"] = cell
        # keep the latest-step fields aligned when this row is one we track
        if src and hist:
            for k in ("price_eur_previous", "price_change_eur", "price_changed_at"):
                val = src.get(k)
                text = "" if val is None else str(val)
                if (r.get(k) or "") != text:
                    r[k] = text
                    changed = True
    if not changed and "price_history" in list(csv.DictReader(io.StringIO(raw)).fieldnames or []):
        return
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\r\n" if "\r\n" in raw else "\n")
    w.writeheader()
    w.writerows(rows)
    open(path, "w", encoding="utf-8", newline="").write(out.getvalue())

index_arr = process_html(os.path.join(here, "index.html"))
process_html(os.path.join(research, "dashboard.html"))
process_json(os.path.join(here, "properties.json"))
process_json(os.path.join(research, "properties.json"))
by_id = {p.get("id"): p for p in (index_arr or [])}
sync_csv(os.path.join(here, "properties.csv"), by_id)
sync_csv(os.path.join(research, "properties.csv"), by_id)

json.dump(dict(sorted(side.items())), open(side_path, "w", encoding="utf-8"), indent=2)
