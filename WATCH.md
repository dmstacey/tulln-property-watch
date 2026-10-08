# Property watch runbook (twice daily)

_Copy of the runbook kept on the research box (`/workspace/property-research/WATCH.md`). Destinations are never written here: they come from env vars / box-only files and are referred to as the 8th district (public transport) and the 21st district (car)._

Research only. **Never contact sellers, agents, banks or anyone else.** Never submit a form, request a viewing or
send an enquiry. The only outputs of a run are: updated tracker files, a git commit + push (which deploys the
dashboard), a changelog, and a short digest for the owner.

Runs: **morning ~06:30** and **evening ~17:20** Europe/Vienna.

Commute wording everywhere (dashboard, changelog, digest): **"21st district"** = car (OSRM free-flow),
**"8th district"** = public transport (VAO HAFAS, weekday 10:00, best of 5, door-to-door). Never write street
addresses or the owner's name in anything that goes into the repo or onto the public page. The destinations live only
in `/workspace/property-research/scrape-2026-09-24-commute/enrich_commute.py` (box only) and, for the zones grid, in
the env vars `PT_DEST_LID` (HAFAS location id, 8th-district destination) and `CAR_DEST_LATLON` ("lat,lon",
21st-district destination).

## Files

| What | Where |
|------|-------|
| Tracker (master) | `/workspace/property-research/properties.json` + `properties.csv` + `dashboard.html` (box mirror, not git) |
| Production | `/workspace/tulln-property-watch/` (this repo, branch `main`) → `index.html`, `properties.json`, `properties.csv` |
| Live page | https://tulln-property-watch.vercel.app (Vercel auto-deploys `main`) |
| Run folder | `/workspace/property-research/scrape-YYYY-MM-DD-{morning,evening}/` (copy the previous run's scripts and edit `OUT`/`TODAY`) |
| Changelog | `/workspace/property-research/changelogs/YYYY-MM-DD-{morning,evening}.md` + `run_summary.json` in the run folder |
| Throttled willhaben fetcher | `/workspace/property-research/whfetch.py` (use it for every willhaben request) |
| Region classifier | `/workspace/property-research/watch_regions.py` (`region()` for search hits, `region_for_row()` for rows) |
| Skip list (new regions) | `/workspace/property-research/skip_adids.json` (adid → reason); Tulln skips stay in `SKIP_ADIDS` in `match_and_liveness.py` |
| Commute helpers | `scrape-2026-09-24-commute/enrich_commute.py` (`osrm_drive_min`, `pt_min`; set `PT_DATE` to a weekday) |
| Fees | `enrich_fees.py` (formula below) |

## How a run works today (matches the 2026-10-08 morning scripts)

1. **Scrape** (`scrape_willhaben.py`): every search below, page by page, saving each page's HTML and
   `__NEXT_DATA__` JSON; parse ads with `parse_ad()` into `willhaben_parsed.json` (adid, title, url, price, m², plot,
   rooms, town, PLZ, coords, org, ISPRIVATE, published). Also the Tulln sauna keyword page and two newest-first spot
   checks (`&sort=1`, `&sfId=5`).
2. **Match + liveness** (`match_and_liveness.py`): build adid → row from the digits in `url_primary`/`url_alt`;
   for every active row matched in the scrape compare the price (price change → note it); unmatched ads that are
   not on a skip list and not junk (Kleingarten/Ferien/Wochenendhaus/Mobilheim/Tiny house, low-quality builder orgs
   ELK/Boom Living/DECUS/Aktion Fertighaus/Treeline/FreeImmotions/FABU Massivhaus, apartments, price outside
   €50k–€560k) become **candidates**. Every active willhaben row **not** matched in a search (OB2, ST rows outside the
   searches, anything else) gets a **liveness** check on its detail URL: live = HTTP 200, final URL still `/d/`,
   no `fromExpiredAdId`; a redirect to a search page = gone. Record price/OLD_PRICE from the detail JSON.
3. **Detail + commute** for each real candidate (`detail_and_commute.py <adid> <url>`): read the detail page
   (attributes, description, ISPRIVATE, ADDITIONAL_COST/FEE, MONTHCOSTS_GROSS, COORDINATES), OSRM drive minutes,
   HAFAS PT minutes. If no coordinates: Nominatim town centroid (`location_precision: town_centroid_nominatim`).
   If HAFAS answers `H9220` (no stop within walking range of the pin), query from the town centroid and say so in
   `pt_lange_gasse_note` (`…_from_town_centre`); if that fails too, leave PT empty (the scorer treats it as unknown).
4. **Decide** (filters below). Write `apply_updates.py` for this run: append new rows with every field (copy the
   shape of the last added row), set `listing_status: disappeared` + `disappeared_date` + `change_flags: disappeared`
   for gone rows, new `price_eur` for price changes (the hook keeps `price_history` / `price_eur_previous` /
   `price_change_eur` / `price_changed_at` in step), bump `last_seen` for every active row, demote last run's `new`
   flags to `seen`. Write json + csv + `dashboard.html` PROPERTIES; the script asserts the privacy rule and that
   `const BUDGET = 450000;` is still present right after the PROPERTIES block.
5. **Deploy** (below), then write the changelog + `run_summary.json`, then the digest.

**New regions (Marchfeld, S2, Hollabrunn/Korneuburg-north)** follow the same steps with the Bezirk searches below:
an ad is a candidate when `watch_regions.region(...)` is not `None`, its adid is not in any row's
`url_primary`/`url_alt`, not in `skip_adids.json`, and it passes the filters. Active MF/S2W/HL/VIE rows that are not
seen in their Bezirk search get the same liveness check as OB2/ST rows. The full 2026-10-08 build (search → prefilter →
detail → screen → decisions → commute → rows) is in `/workspace/property-research/expand-2026-10-08/` (`gap_search.py`,
`prefilter.py`, `fetch_details.py`, `build_rows.py`, `screen.py`, `decisions.py`, `stage_a.py`, `stage_b.py`) and can be
reused for a bigger catch-up.

## Searches (willhaben "Haus kaufen"; no room or type filter, so 3-room homes and unknown-room ads are seen)

Use `whfetch.fetch(url)` for each request. Area ids are willhaben `areaId` values for the Bezirk (same as the path).

| Region (dashboard label) | URL (page n) | areaId | Pages today | Keep |
|---|---|---|---|---|
| Tulln area | `https://www.willhaben.at/iad/immobilien/haus-kaufen/niederoesterreich/tulln?PRICE_TO=550000&rows=30&page={n}` + `&keyword=sauna` (page 1) + `&sort=1` and `&sfId=5` (page 1) | 321 | ~8 (226 ads) | everything in the Bezirk |
| Marchfeld | `https://www.willhaben.at/iad/immobilien/haus-kaufen/niederoesterreich/gaenserndorf?PRICE_TO=550000&rows=90&page={n}` | 308 | 4 (325 ads) | PLZ in `MARCHFELD_PLZ` (Marchfeld + Nordbahn towns: Deutsch-Wagram, Strasshof, Gänserndorf, Groß-Enzersdorf incl. KGs, Raasdorf, Markgrafneusiedl, Siebenbrunn, Leopoldsdorf, Haringsee, Lassee, Engelhartstetten, Marchegg incl. Breitensee/Groißenbrunn, Weiden/Oberweiden/Zwerndorf, Orth, Eckartsau, Groß-Schweinbarth, Auersthal, Prottes, Weikendorf, Angern/Mannersdorf/Grub/Stillfried/Ollersdorf) |
| S2 Wolkersdorf/Mistelbach | `https://www.willhaben.at/iad/immobilien/haus-kaufen/niederoesterreich/mistelbach?PRICE_TO=550000&rows=90&page={n}` | 316 | 3 (217 ads) | PLZ in `S2_PLZ` (Wolkersdorf incl. Obersdorf/Riedenthal/Münichsthal, Ulrichskirchen-Schleinbach, Hochleithen, Kreuttal, Kreuzstetten, Ladendorf, Mistelbach town + KGs, Gaweinstal incl. Schrick/Pellendorf, Pillichsdorf, Großengersdorf, Bockfließ) |
| Hollabrunn/Korneuburg-north (Hollabrunn part) | `https://www.willhaben.at/iad/immobilien/haus-kaufen/niederoesterreich/hollabrunn?PRICE_TO=550000&rows=90&page={n}` | 310 | 2 (163 ads) | coordinates ≤ 14 km from Hollabrunn centre (Hollabrunn + KGs like Puch, Breitenwaida, Mariathal, Weyerburg; Göllersdorf, Grabern/Schöngrabern, Wullersdorf, Guntersdorf, Nappersdorf, Ziersdorf edge) |
| Hollabrunn/Korneuburg-north (Korneuburg part) + Stockerau/Korneuburg liveness | `https://www.willhaben.at/iad/immobilien/haus-kaufen/niederoesterreich/korneuburg?PRICE_TO=550000&rows=90&page={n}` | 312 | 3 (200 ads) | PLZ in `KORNEUBURG_NORTH_PLZ` (Harmannsdorf/Mollmannsdorf/Rückersdorf/Würnitz/Tresdorf, Großrußbach/Wetzleinsdorf/Karnabrunn, Ernstbrunn, Großmugl, Leitzersdorf, Niederhollabrunn, Sierndorf, Rußbach). Stockerau/Korneuburg town belt (`STOCKERAU_KORNEUBURG_PLZ`): **match ST rows only, do not add new ones** (not an approved expansion) |
| Vienna | none | – | – | not a watch region; VIE1 (one 21st-district own-land house) is kept alive by a liveness check only |

Stop paginating when a page returns fewer ads than `rows`. Per run that is ~23 search requests plus a handful of
liveness/detail requests.

**Region field**: every row must carry `region` (one of `Tulln area`, `Stockerau/Korneuburg`, `Marchfeld`,
`S2 Wolkersdorf/Mistelbach`, `Hollabrunn/Korneuburg-north`, `Vienna`). For search hits use
`watch_regions.region(search_key, plz, lat, lon)`; `None` means out of scope (ignore the ad). The dashboard's Region
filter uses this field (missing → shown as Tulln area).

## willhaben throttling (hard rule)

- At most **one request every 4–6 s** (`whfetch` sleeps a random 4–6 s between calls). No parallel requests.
- On **HTTP 429** back off 3 min, then 5 min, then 8 min; if still 429, **stop** (`whfetch.Blocked`). Finish the run
  with whatever was fetched, list exactly what was not fetched in the changelog and digest, and do not retry from
  another tool or IP. On 2026-10-07 willhaben returned 429 to the box from ~23:13 to at least 23:42.
- Never run a second willhaben job while another one (e.g. the other watch run) is active.
- immowelt (DataDome) and remax.at (Turnstile) block scripted requests: use `url_alt`/cached pages, do not fight it.

## Filters / exclusions (all regions, same as the Tulln watch)

- **Family homes only**: EFH, DHH, RH, Bungalow, Landhaus/Bauernhaus/Villa used as one home. **Never apartments.**
  Mehrfamilienhaus / Zinshaus / Wohn- und Geschäftshaus / Gasthaus → skip (a house with a small granny flat is fine).
- **Price ≤ ~€550k** search window. Budget is **€450k**: rows above it get `category: over_budget`.
- **Exclude**: Baurecht / Superädifikat / leasehold; Pacht / Pachtgrund / church or Stift land lease; "ohne Grundstück",
  Aktionshaus, "auf Ihrem Grundstück", house-only builder/catalogue ads (FABU, Tolaj on Baurecht, "Grundstück nicht
  inkludiert", "für Hausbau-Kunden"); lifelong residence rights (Wohnrecht, Fruchtgenuss, Leibrente); holiday /
  weekend / Kleingarten / Badebungalow / Presshaus / Kellerstöckl / Tiny-house; Rohbau or Ausbauhaus with only a tiny
  finished area; Zwangsversteigerung unless the owner asks (category `auction`); Kaufanbot-liegt-vor republishes;
  builder packages where the house is not built yet ("Haus + Grundstück", Town & Country / Lagerhaus packages,
  "Grundstück aktuell noch unbebaut"). Pure Rohbau shells with a stated living area are kept (flagged as Rohbau).
- Check every listing pin against its town: on 2026-10-08 one Groß-Schweinbarth ad (MF50) was pinned in Vienna; if the
  pin is > 6 km from the town centroid use the centroid (`location_precision: town_centroid_nominatim`).
- **Rooms**: 4+ rooms is the requirement for recommendations. Still track 3-room homes (the scorer subtracts 25);
  skip 1–2 rooms and < 60 m². Unknown rooms: track, check the description.
- **Tenanted houses** (Anlegerobjekt, "unbefristet vermietet") are excluded: not available to live in. A small let
  part (e.g. a granny flat let for €450) is fine; note it.
- **Two-unit houses** (Zweifamilienhaus / 2 Wohneinheiten in one building) are tracked with a note; Mehrfamilienhaus
  (3+ units) is not. **Wohnungseigentum** row/semi-detached houses with their own garden are tracked (like SL7/SL8)
  with a note that there is no separate plot.
- **Same house, several agents**: before adding, compare town/coordinates + living m² + plot m² (+ HWB) with every
  active row; keep the cheaper ad and note the other adid (2026-10-08: MF96/1239096803, S2W35/1829585642,
  HL12/1541671847).
- **New-build projects** with many identical units: one row per project (cheapest unit that fits), other unit adids
  in `notes`; add the other adids to the skip list so they do not come back as "new".
- When an excluded ad is seen, add it to `skip_adids.json` with the reason so later runs stay quiet about it.

## IDs and categories

| Prefix | Meaning |
|---|---|
| SL, HM, NEW, OB, SA, SIM, AU, WF, SO | Tulln area (existing; new Tulln rows continue `NEW`/`HM`/`OB`) |
| ST | Stockerau/Korneuburg (and four Korneuburg-north towns added earlier: ST3, ST4, ST11, ST13) |
| **MF** | Marchfeld / Nordbahn (Bezirk Gänserndorf) |
| **S2W** | S2 Wolkersdorf / Mistelbach (Bezirk Mistelbach) |
| **HL** | Hollabrunn town & surroundings + Korneuburg-north |
| **VIE** | Vienna (VIE1 only; Vienna is not watched) |

Next free numbers: take max existing number + 1 per prefix (after the 2026-10-08 expansion: MF156, S2W56, HL63, VIE2). Categories (same set as before): `shortlist`
(≤ €450k, ≥ 4 rooms, family home, strong fit), `honourable` (other in-budget), `over_budget` (> €450k), `sauna`
(in-budget with sauna, not shortlist), `auction`, `sold`.

## Row fields and fee logic

Fill every field the last added row has (title, category, type, town, PLZ, district_or_region, price, m², plot,
rooms, year, HWB class/value, heating raw text, garden/parking/garage/sauna/pool, condition, lat/lon +
location_precision, url_primary/url_alt, notes, research_date, listing_status, first_seen = last_seen = today,
change_flags `new`, seller_type + note + makler_name, price_per_m2, fees, all_in, Betriebskosten + note,
drive_anton_boeck_min + note, pt_lange_gasse_min + note, finish_status + note + source, region).
Leave `heating_tech`, `image`, `archive`, `first_seen_at` and all fit fields to the pre-commit hook.

Fees on asking price: Grunderwerbsteuer 3.5% + Grundbuch 1.1% + Notar ~1.5% = 6.1% statutory; Makler 3.0% + 20% USt
= **3.6% only if `seller_type = makler`** (ISPRIVATE=0 and the Provision line is not "provisionsfrei"/"0"/"zahlt der
Abgeber"). `all_in_price_eur = price + fees_total_eur`. Betriebskosten from `ESTATE_PRICE/MONTHCOSTS_GROSS` or a
"Betriebskosten … €" phrase, else null.

## Deploy (only way: git)

```bash
cp /workspace/property-research/dashboard.html /workspace/tulln-property-watch/index.html
cp /workspace/property-research/properties.csv /workspace/property-research/properties.json /workspace/tulln-property-watch/
cd /workspace/tulln-property-watch
git add -A index.html properties.csv properties.json
git commit -m "Morning watch YYYY-MM-DD: …"     # hook runs here
git push origin main
```

- The **pre-commit hook** runs `archive_listings.py` (photo `img/<id>.jpg` + `archive/<id>.html`), then
  `stamp_first_seen.py` (first_seen_at, price_history, heating classification via `classify_heating.py`), then
  `score_listings.py` (family-fit score/label for every active row, written into index.html, dashboard.html and both
  json/csv copies) and stages the results. Do not run these by hand or undo their edits; check afterwards that every
  new row has `fit_score` and `image`. "Highly recommended" = top 8 eligible rows across **all** regions (4+ rooms,
  family type, total-cost midpoint ≤ €510k, PT ≤ 80 min or car ≤ 40 min, score ≥ 50); mention in the digest when the set
  changes.
- `archive_listings.py` fetches live pages at 2 s spacing, which is faster than the willhaben rule. Normal runs add only a
  few rows, which is fine. For a batch of new rows, first write their detail pages from the fetched JSON into
  `/workspace/property-research/scrape-YYYY-MM-DD-*/details/detail_<adid>.html` (canonical link + `__NEXT_DATA__`
  `{"props":{"pageProps":{"advertDetails":…}}}`; see `expand-2026-10-08/synth_cache.py`) and pre-run
  `archive_listings.py --no-live --max-photos 3 --ids <new ids>` so the hook finds the work done.
- Never deploy with Vercel MCP uploads. Vercel builds `main` automatically.
- If Auto-review blocks `git commit`/`git push`, retry the **same command unchanged** with
  `request_smart_mode_approval: true` and `smart_mode_block_reason` = the exact block text. If that is denied, stop and
  report.
- After pushing, fetch https://tulln-property-watch.vercel.app (cache-busting `?v=<commit>`) and confirm the new ids
  and the row count appear.
- Commit message pattern: `Evening watch 2026-10-08: add MF12 Strasshof €389k; S2W3 disappeared (N rows / M active)`.

## Commute zones overlay

`commute-zones.geojson` is a hex grid (~3.8 km cells) over all watch regions, built by
`tools/sample_grid.py` (cached in `tools/grid_cache.json`, resumable) and `tools/build_geojson.py`:
`PT_DEST_LID=… CAR_DEST_LATLON=… python3 tools/sample_grid.py && python3 tools/build_geojson.py`.
To cover a new region add a box to `BOXES` in `sample_grid.py` (same lattice, existing cells stay valid).
Not part of the twice-daily run. Rural cells with no stop in walking range (HAFAS `H9220`) have no PT band and show
car bands only; that is expected.

## Changelog + digest

Changelog (`changelogs/…md`): summary table (active before/after, total rows, new, price drops/ups, disappeared,
sold, URL refreshes), then New listings, Price changes, Disappeared, Republishes, Intentionally not added, Liveness,
Sources (searches + row counts per region), Not fetched (429), Files.

Digest for the owner (chat message from the watch run):
- Warm, concise, plain English. **Lead with the news** (new listing, price drop, a favourite gone), one line per item:
  id, town, type, price, rooms/m², plot, fit label, "~X min by car to the 21st district / ~Y min by public transport
  to the 8th district", one honest caveat, link to the live page `#id=<ID>`.
- Group by region only when several regions have news.
- **If nothing material changed, stay quiet** (no digest, or a single line if the run is expected to report).
  last_seen bumps, skip-list noise and unchanged liveness are not news.
- Mention a willhaben 429 / unfetched pages only when it means something may have been missed.
- Never name the owner or show addresses; never suggest contacting anyone on the owner's behalf.
