import json, math
from pathlib import Path
import os, sys
os.environ.setdefault('PT_DEST_LID', 'x'); os.environ.setdefault('CAR_DEST_LATLON', '0,0')
sys.path.insert(0, os.path.dirname(__file__))
import sample_grid as g
c = json.loads(Path(__file__).with_name("grid_cache.json").read_text())
R = c["meta"]["R_km"]
def band(v, edges):
    if v is None: return None
    for e in edges:
        if v <= e: return e
    return None
feats = []; stats = {"pt_ok": 0, "pt_none": 0, "car_ok": 0}
for key, (pm, note) in c["pt"].items():
    lat, lon = map(float, key.split(","))
    cm = c["car"].get(key)
    pb = band(pm, [45, 60, 75, 90]); cb = band(cm, [20, 30, 40, 55])
    stats["pt_ok" if pm is not None else "pt_none"] += 1
    if cm is not None: stats["car_ok"] += 1
    if pb is None and cb is None: continue
    ring = []
    for k in range(6):
        a = math.radians(30 + 60 * k)
        ring.append([round(lon + R * 0.985 * math.cos(a) / g.KM_LON, 5), round(lat + R * 0.985 * math.sin(a) / g.KM_LAT, 5)])
    ring.append(ring[0])
    feats.append({"type": "Feature", "properties": {"pt": pm, "pt_band": pb, "car": cm, "car_band": cb},
                  "geometry": {"type": "Polygon", "coordinates": [ring]}})
gj = {"type": "FeatureCollection",
      "metadata": {"description": "Commute reachability hex grid (~3.8 km cells). pt = public transport minutes to the 8th district (VAO HAFAS, weekday 10:00, best of 5, door-to-door incl. walking from the cell centre); car = OSRM free-flow minutes to the 21st district.",
                   "pt_date": c["meta"]["date"], "pt_time": c["meta"]["time"], "generated": __import__("datetime").date.today().isoformat(), "cells": len(feats),
                   "pt_bands": [45, 60, 75, 90], "car_bands": [20, 30, 40, 55]},
      "features": feats}
(Path(__file__).resolve().parent.parent / "commute-zones.geojson").write_text(json.dumps(gj, separators=(",", ":"), ensure_ascii=False))
print(stats, "features", len(feats))
from collections import Counter
print("pt bands", Counter(f["properties"]["pt_band"] for f in feats)); print("car bands", Counter(f["properties"]["car_band"] for f in feats))
print(Counter(n for _, n in c["pt"].values()))
