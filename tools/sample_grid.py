#!/usr/bin/env python3
"""Usage: PT_DEST_LID=... CAR_DEST_LATLON=lat,lon python3 tools/sample_grid.py && python3 tools/build_geojson.py
Sample a hex grid across the Tulln region: VAO HAFAS PT minutes to the 8th-district work
destination (weekday 10:00, best of 5, same method as the watch) + OSRM car minutes to the
21st-district destination. Results cached in grid_cache.json (resumable)."""
import json, math, os, sys, time, urllib.request, concurrent.futures as cf
from pathlib import Path
HERE = Path(__file__).parent
CACHE = HERE / "grid_cache.json"
UA = "PropertyResearchBot/1.0 (property research; local)"
HAFAS_URL = "https://vao.demo.hafas.de/gate"
DEST_LID = os.environ["PT_DEST_LID"]  # HAFAS location id of the 8th-district work destination (kept out of the repo)
DRIVE = tuple(map(float, os.environ["CAR_DEST_LATLON"].split(",")))  # "lat,lon" of the 21st-district destination (kept out of the repo)
PT_DATE, PT_TIME = os.environ.get("PT_DATE", "20261008"), "100000"
LAT0, LAT1, LON0, LON1 = 48.12, 48.50, 15.62, 16.46
R_KM = 2.2
KM_LAT = 111.2; KM_LON = 111.32 * math.cos(math.radians(48.3))

def grid():
    dy = 1.5 * R_KM / KM_LAT; dx = math.sqrt(3) * R_KM / KM_LON
    pts = []; r = 0; lat = LAT0
    while lat <= LAT1 + 1e-9:
        lon = LON0 + (dx / 2 if r % 2 else 0)
        while lon <= LON1 + 1e-9:
            pts.append((round(lat, 5), round(lon, 5))); lon += dx
        lat += dy; r += 1
    return pts, dx, dy

def post(body, timeout=60):
    req = urllib.request.Request(HAFAS_URL, data=json.dumps(body).encode(), headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def dur(s):
    s = str(s).zfill(6)
    if len(s) == 6: return int(s[:2]) * 60 + int(s[2:4]) + (1 if int(s[4:6]) >= 30 else 0)
    s = s.zfill(9); return int(s[:3]) * 1440 + int(s[3:5]) * 60 + int(s[5:7])

def pt(lat, lon):
    body = {"svcReqL": [{"req": {"outDate": PT_DATE, "outTime": PT_TIME, "getPasslist": False, "economic": False, "getPolyline": False, "numF": 5,
             "arrLocL": [{"lid": DEST_LID}], "depLocL": [{"type": "C", "crd": {"x": int(round(lon * 1e6)), "y": int(round(lat * 1e6))}}]},
             "meth": "TripSearch", "id": "1|1|"}], "client": {"id": "VAO", "v": "1", "type": "AND", "name": "nextgen"}, "ver": "1.73", "lang": "de", "auth": {"aid": "nextgen", "type": "AID"}}
    for attempt in range(2):
        try:
            res = post(body); svc = res.get("svcResL", [{}])[0]
            if svc.get("err") not in (None, "OK"): return None, "err:" + str(svc.get("err"))
            cons = (svc.get("res") or {}).get("outConL") or []
            ms = [dur(c.get("dur") or c.get("durS")) for c in cons if (c.get("dur") or c.get("durS"))]
            return (min(ms), "ok") if ms else (None, "no_conn")
        except Exception as e:
            err = str(e); time.sleep(2)
    return None, "exc:" + err

def osrm_batch(pts):
    coords = ";".join([f"{DRIVE[1]},{DRIVE[0]}"] + [f"{lon},{lat}" for lat, lon in pts])
    url = f"https://router.project-osrm.org/table/v1/driving/{coords}?sources={';'.join(str(i) for i in range(1, len(pts)+1))}&destinations=0"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r: d = json.load(r)
    return [None if row[0] is None else round(row[0] / 60) for row in d["durations"]]

def main():
    pts, dx, dy = grid()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {"meta": {}, "pt": {}, "car": {}}
    cache["meta"] = {"date": PT_DATE, "time": PT_TIME, "R_km": R_KM, "dx": dx, "dy": dy, "n": len(pts)}
    print("grid points:", len(pts), flush=True)
    # car first (cheap, batched)
    todo = [p for p in pts if f"{p[0]},{p[1]}" not in cache["car"]]
    for i in range(0, len(todo), 80):
        b = todo[i:i+80]
        try:
            for p, m in zip(b, osrm_batch(b)): cache["car"][f"{p[0]},{p[1]}"] = m
        except Exception as e: print("osrm fail", e, flush=True)
        time.sleep(1.2)
    CACHE.write_text(json.dumps(cache))
    print("car done", len(cache["car"]), flush=True)
    todo = [p for p in pts if f"{p[0]},{p[1]}" not in cache["pt"]]
    done = 0; t0 = time.time()
    def work(p):
        time.sleep(0.5); return p, pt(*p)
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for p, (m, note) in ex.map(work, todo):
            cache["pt"][f"{p[0]},{p[1]}"] = [m, note]; done += 1
            if done % 10 == 0:
                CACHE.write_text(json.dumps(cache)); print(f"pt {done}/{len(todo)} {time.time()-t0:.0f}s last={m} {note}", flush=True)
    CACHE.write_text(json.dumps(cache)); print("pt done", flush=True)
if __name__ == "__main__":
    main()
