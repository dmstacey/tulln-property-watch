#!/usr/bin/env python3
"""Give every listing a first_seen_at timestamp (Vienna time).
Known times live in first-seen-times.json; listings first seen today without
a time get the current time. Runs from the git pre-commit hook."""
import json, re, os
from datetime import datetime
from zoneinfo import ZoneInfo
here = os.path.dirname(os.path.abspath(__file__))
now = datetime.now(ZoneInfo("Europe/Vienna"))
today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M")
side_path = os.path.join(here, "first-seen-times.json")
side = json.load(open(side_path, encoding="utf-8"))

def fill(arr):
    for p in arr:
        pid = p.get("id")
        if pid in side:
            p["first_seen_at"] = side[pid]
        elif p.get("first_seen_at"):
            side[pid] = p["first_seen_at"]
        elif p.get("first_seen") == today:
            p["first_seen_at"] = side[pid] = stamp

html_path = os.path.join(here, "index.html")
s = open(html_path, encoding="utf-8").read()
m = re.search(r"const PROPERTIES = (\[.*?\n\]);\n", s, re.S)
arr = json.loads(m.group(1)); fill(arr)
s = s[:m.start(1)] + json.dumps(arr, indent=2, ensure_ascii=False) + s[m.end(1):]
open(html_path, "w", encoding="utf-8").write(s)

pj = "/workspace/property-research/properties.json"
if os.path.exists(pj):
    data = json.load(open(pj, encoding="utf-8"))
    lst = data if isinstance(data, list) else data.get("properties", [])
    fill(lst)
    json.dump(data, open(pj, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

json.dump(dict(sorted(side.items())), open(side_path, "w", encoding="utf-8"), indent=2)
