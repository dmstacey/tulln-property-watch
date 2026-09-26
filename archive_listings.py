#!/usr/bin/env python3
"""archive_listings.py — house photos + static per-listing archive for Tulln Property Watch.

USAGE (run from anywhere; paths are resolved relative to this file):

    python3 archive_listings.py                 # process every listing that is missing image/archive
    python3 archive_listings.py --ids SL1,NEW7  # only these ids
    python3 archive_listings.py --dry-run       # show what would be fetched, write nothing
    python3 archive_listings.py --no-live       # only use cached scrape pages (offline)
    python3 archive_listings.py --index-only    # just rebuild archive/index.html

Run it in the twice-daily watch AFTER the new data has been copied into index.html
(and /workspace/property-research/properties.json) and BEFORE `git commit`:

    python3 archive_listings.py && git add -A img archive index.html properties.json properties.csv

What it does, per listing (active listings first, then sold/disappeared):
  * image   – if the listing has no `image` (or the file is missing) it fetches the listing
              page (url_primary / url_alt; willhaben preferred via __NEXT_DATA__), takes the
              main photo, saves img/<id>.jpg (640 px wide JPEG, ~60–120 KB) and sets
              `image = "img/<id>.jpg"`.
  * archive – if the listing has no `archive` it writes archive/<id>.html (self-contained
              static page, no original JS/trackers) with facts, description, attribute
              tables, source URL, archive date and up to 8 photos in archive/<id>/NN.jpg
              (~1000 px JPEG). Sets `archive = "archive/<id>.html"` and `archived_at`.
  * Existing archive files are NEVER overwritten (so a listing that has since been sold
    or disappeared keeps its snapshot). Existing images are kept too.
  * When a live page is gone (e.g. willhaben redirects expired ads to a search page) it
    falls back to cached pages under /workspace/property-research/scrape-*/.
  * Writes the new fields into: index.html (const PROPERTIES; keeps `const BUDGET`),
    /workspace/property-research/dashboard.html, properties.json and properties.csv in
    both the repo and /workspace/property-research/ (whichever exist).
  * Rebuilds archive/index.html.
  * Sites that block bots (immowelt/DataDome, remax/Turnstile) are skipped – never bypassed.

Requires: Python 3.9+, Pillow, beautifulsoup4  (pip install Pillow beautifulsoup4)
"""
import argparse, csv, glob, html, io, json, os, re, sys, time, urllib.parse, urllib.request, ssl
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from PIL import Image, ImageOps
    from bs4 import BeautifulSoup
except ImportError as e:  # pragma: no cover
    sys.exit(f"missing dependency: {e}. Run: pip install Pillow beautifulsoup4")

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = "/workspace/property-research"
CACHE_GLOBS = [RESEARCH + "/scrape-*"]
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
DELAY = 2.0            # seconds between page requests (polite)
IMG_DELAY = 0.4        # seconds between image downloads
MAX_PHOTOS = 8
TODAY = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
BLOCKED_HOSTS = {"www.immowelt.at", "immowelt.at", "www.remax.at", "remax.at"}  # bot walls
PRIVACY_SCRUB = [("David Stacey", "—"), ("Lange Gasse 50", "8th district"),
                 ("Anton-Böck-Gasse", "21st district"), ("Anton-Boeck-Gasse", "21st district")]

log_lines = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log_lines.append(s)

# ------------------------------------------------------------------ HTTP
_last = [0.0]
def http_get(url, referer=None, is_image=False, timeout=30):
    wait = (IMG_DELAY if is_image else DELAY) - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    hdr = {"User-Agent": UA, "Accept-Language": "de-AT,de;q=0.9,en;q=0.7",
           "Accept": ("image/avif,image/webp,image/*,*/*;q=0.8" if is_image else
                      "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")}
    if referer:
        hdr["Referer"] = referer
    req = urllib.request.Request(url, headers=hdr)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if "UNRECOGNIZED_NAME" in str(e) or "CERTIFICATE" in str(e):
            ctx = ssl._create_unverified_context()   # misconfigured TLS on a few small sites
            r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            raise
    finally:
        _last[0] = time.time()
    data = r.read()
    return r.status, r.geturl(), data

# ------------------------------------------------------------------ helpers
def esc(s):
    return html.escape("" if s is None else str(s), quote=True)

def scrub(s):
    for a, b in PRIVACY_SCRUB:
        s = s.replace(a, b)
    return s

ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h3", "h4", "h5",
                "table", "thead", "tbody", "tr", "td", "th", "span", "div", "sup", "sub"}
def sanitize_html(fragment):
    """Keep only simple formatting tags, strip every attribute/script."""
    soup = BeautifulSoup(fragment or "", "html.parser")
    for t in soup(["script", "style", "iframe", "noscript", "img", "svg", "form", "button", "a"]):
        if t.name == "a":
            t.unwrap()
        else:
            t.decompose()
    for t in soup.find_all(True):
        if t.name not in ALLOWED_TAGS:
            t.unwrap()
        else:
            t.attrs = {}
    out = str(soup)
    out = re.sub(r"(<br/?>\s*){3,}", "<br/><br/>", out)
    return out.strip()

def text_to_html(txt):
    paras = [p.strip() for p in re.split(r"\n\s*\n", txt or "") if p.strip()]
    return "".join("<p>" + esc(p).replace("\n", "<br/>") + "</p>" for p in paras)

def fmt_eur(v):
    if v in (None, ""):
        return None
    try:
        return "€ " + f"{float(v):,.0f}".replace(",", ".")
    except Exception:
        return str(v)

def fmt_num(v, unit=""):
    if v in (None, ""):
        return None
    try:
        f = float(v)
        s = (f"{f:,.0f}" if f == int(f) else f"{f:,.1f}").replace(",", "X").replace(".", ",").replace("X", ".")
        return s + unit
    except Exception:
        return str(v)

# ------------------------------------------------------------------ images
def to_jpeg(data, width, max_kb, start_q=82, min_q=45):
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        try:
            bg.paste(im, mask=im.convert("RGBA").split()[-1])
        except Exception:
            bg.paste(im.convert("RGB"))
        im = bg
    im = im.convert("RGB")
    if im.width < 300:
        raise ValueError(f"image too small ({im.width}px)")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    if im.height > im.width * 1.6:   # very tall scans/floorplans: crop height
        im = im.crop((0, 0, im.width, int(im.width * 1.6)))
    q = start_q
    while True:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= max_kb * 1024 or q <= min_q:
            return buf.getvalue()
        q -= 6

BAD_IMG_HOSTS = set()
def fetch_image(url, referer, width, max_kb, start_q):
    host = urllib.parse.urlparse(url).netloc
    if host in BAD_IMG_HOSTS:
        raise ValueError(f"{host} unreachable earlier – skipped")
    try:
        st, _, data = http_get(url, referer=referer, is_image=True)
    except urllib.error.HTTPError:
        raise
    except Exception:
        BAD_IMG_HOSTS.add(host)
        raise
    if st != 200 or len(data) < 2000:
        raise ValueError(f"HTTP {st} / {len(data)} bytes")
    return to_jpeg(data, width, max_kb, start_q)

# ------------------------------------------------------------------ parsers
def listing_template():
    return {"title": None, "price": None, "location": None, "seller": None,
            "sections": [], "tables": [], "images": [], "source": None, "source_url": None,
            "fetched": None, "site_status": None}

WH_LABELS = [
    ("PRICE_FOR_DISPLAY", "Kaufpreis"), ("OLD_PRICE_FOR_DISPLAY", "Vorheriger Preis"),
    ("PRICE/SQUARE_METER_FOR_DISPLAY_WITH_UNIT", "Preis pro m²"),
    ("ESTATE_SIZE/LIVING_AREA", "Wohnfläche (m²)"), ("ESTATE_SIZE/USEABLE_AREA", "Nutzfläche (m²)"),
    ("PLOT/AREA", "Grundfläche (m²)"), ("NO_OF_ROOMS", "Zimmer"), ("FLOOR_SURFACE", "Boden"),
    ("PROPERTY_TYPE", "Objekttyp"), ("BUILDING_TYPE", "Bautyp"), ("BUILDING_CONDITION", "Zustand"),
    ("CONSTRUCTION_YEAR", "Baujahr"), ("HEATING", "Heizung"),
    ("ENERGY_HWB", "HWB (kWh/m²a)"), ("ENERGY_HWB_CLASS", "HWB-Klasse"),
    ("ENERGY_FGEE", "fGEE"), ("ENERGY_FGEE_CLASS", "fGEE-Klasse"),
    ("ESTATE_PRICE/MONTHCOSTS_GROSS", "Monatliche Kosten brutto (€)"),
    ("ESTATE_PRICE/HEATINGCOSTSNET", "Heizkosten netto (€)"),
    ("ADDITIONAL_COST/FEE", "Provision"), ("FREE_AREA/FREE_AREA_TYPE_AND_AREA", "Freifläche"),
    ("ESTATE_PREFERENCE", "Ausstattung / Merkmale"), ("AVAILABLE_NOW", "Verfügbar"),
    ("AVAILABLE_DATE_FREETEXT", "Verfügbar (Text)"), ("OWNAGETYPE", "Angebot"),
    ("PRICE_REDUCTION_SET_DATE", "Preisreduktion am"),
]
WH_SKIP = re.compile(r"^(CONTACT/|IMPORT_|PROPERTY_TYPE_|SHOW_|ISPRIVATE|DEALER|ORG_TYPE|AREA_ID|"
                     r"REGION_AREA_ID|POSITION_RADIUS|COORDINATES|GENERAL_TEXT_ADVERT/|DESCRIPTION|"
                     r"INFOLINK/|PRICE$|PRICE/SQUARE_METER$|PRICE/SQUARE_METER_FOR_DISPLAY$|"
                     r"ESTATE_PRICE/PRICE_SUGGESTION|ESTATE_SIZE$|FREE_AREA/FREE_AREA_(AREA|TYPE)$|"
                     r"OLD_PRICE$|AVAILABLE_DATE$|LOCATION/)")

def next_data(s):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

def parse_willhaben(s):
    nd = next_data(s)
    ad = ((nd or {}).get("props") or {}).get("pageProps", {}).get("advertDetails")
    if not ad:
        return None
    attrs = {}
    for a in (ad.get("attributes") or {}).get("attribute", []):
        attrs[a["name"]] = a.get("values") or []
    g = lambda k: (attrs.get(k) or [None])[0]
    L = listing_template()
    L["title"] = ad.get("description")
    L["price"] = g("PRICE_FOR_DISPLAY") or g("ESTATE_PRICE/PRICE_SUGGESTION_FOR_DISPLAY")
    loc = [g("LOCATION/POSTCODE"), g("LOCATION/ADDRESS_2"), g("LOCATION/ADDRESS_3"), g("LOCATION/ADDRESS_4")]
    addr = ad.get("advertAddressDetails") or {}
    L["location"] = ", ".join(x for x in [addr.get("postCode") and f'{addr.get("postCode")} {addr.get("postalName") or ""}'.strip()]
                               + [x for x in loc[1:] if x] if x) or None
    st = (ad.get("advertStatus") or {}).get("description")
    L["site_status"] = st
    private = g("ISPRIVATE") == "1"
    org = (ad.get("organisationDetails") or {}).get("orgName")
    if private:
        L["seller"] = "Privat (private seller)"
    else:
        bits = [g("CONTACT/COMPANYNAME") or org, g("CONTACT/NAME"), g("CONTACT/URL")]
        seen, out = set(), []
        for b in bits:
            if b and b not in seen:
                seen.add(b); out.append(b)
        L["seller"] = "Makler: " + " · ".join(out) if out else "Makler"
    rows = []
    for k, lab in WH_LABELS:
        if attrs.get(k):
            rows.append((lab, ", ".join(attrs[k])))
    known = {k for k, _ in WH_LABELS}
    for k, v in attrs.items():
        if k in known or WH_SKIP.match(k) or not v:
            continue
        rows.append((k.replace("_", " ").title(), ", ".join(v)))
    if L["location"]:
        rows.insert(0, ("Ort", L["location"]))
    L["tables"].append(("Objektdaten (willhaben)", rows))
    if attrs.get("DESCRIPTION"):
        L["sections"].append(("Beschreibung", sanitize_html(attrs["DESCRIPTION"][0])))
    for k, v in attrs.items():
        if k.startswith("GENERAL_TEXT_ADVERT/") and v:
            L["sections"].append((k.split("/", 1)[1], sanitize_html(v[0])))
    for im in (ad.get("advertImageList") or {}).get("advertImage", []):
        u = im.get("referenceImageUrl") or im.get("mainImageUrl")
        if u:
            L["images"].append(u)
    return L

def ld_objects(soup):
    out = []
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        stack = [d]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                out.append(x)
                if "@graph" in x:
                    stack.append(x["@graph"])
    return out

def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def site_images(host, s, soup, lds):
    imgs = []
    if "ir.at" in host:
        keys = uniq(re.findall(r"files\.ir\.at/public/pic/t(?:1|4)/([A-Za-z0-9_\-]+)\.jpg", s))
        imgs = [f"https://files.ir.at/public/pic/t4/{k}.jpg" for k in keys]
    elif "raiffeisen-immobilien" in host:
        big = uniq(u.replace("&amp;", "&") for u in re.findall(
            r"https://www\.raiffeisen-immobilien\.at/realty-media/\d+/[0-9a-f]+\.jpg\?fit=max&(?:amp;)?h=1080&(?:amp;)?w=1440&(?:amp;)?signature=[0-9a-f]+", s))
        imgs = big or uniq(re.findall(r"https://www\.raiffeisen-immobilien\.at/realty-media/\d+/[0-9a-f]+\.(?:jpg|jpeg|png)\?signature=[0-9a-f]+", s))
    elif "findheim" in host:
        og = re.search(r'og:image(?::url)?"\s+content="[^"]*/([0-9a-f]{32})\.jpg', s)
        hashes = uniq(re.findall(r"fify-dynamo-assets\.sos-at-vie-1\.exo\.io/([0-9a-f]{32})_hd\.webp", s))
        if og and og.group(1) in hashes:
            hashes = hashes[hashes.index(og.group(1)):]
        elif og:
            hashes = [og.group(1)] + hashes
        imgs = [f"https://fify-dynamo-assets.sos-at-vie-1.exo.io/{h}_2xl.webp" for h in hashes]
    elif "dibeo" in host:
        imgs = uniq(re.findall(r"https://asset\.dibeo\.at/\d+/[0-9a-f]+/big-jpeg/[^\"'\s]+?\.jpe?g", s))
    elif "gesucht-gefunden" in host:
        imgs = uniq(re.findall(r"https://gesucht-gefunden\.at/wp-content/uploadstorage/[^\"'\s]+?\.(?:jpg|jpeg|png)", s))
        imgs = [u for u in imgs if not re.search(r"-\d+x\d+\.", u)]
    for d in lds:
        im = d.get("image")
        if isinstance(im, str):
            imgs.append(im)
        elif isinstance(im, list):
            imgs.extend(i if isinstance(i, str) else (i or {}).get("url") for i in im)
        elif isinstance(im, dict):
            imgs.append(im.get("url"))
    for m in soup.find_all("meta", attrs={"property": re.compile(r"^og:image(:url|:secure_url)?$")}):
        imgs.append(m.get("content"))
    imgs = [urllib.parse.urljoin("https://" + host + "/", u) for u in imgs if u]
    imgs = [u for u in imgs if not re.search(r"logo|icon|avatar|/pic/t12/|placeholder|sprite", u, re.I)]
    return uniq(imgs)

LABEL_RE = re.compile(r"^\s*([A-Za-zÄÖÜäöüß0-9 .,/()\-²]{2,40}):\s*(.{1,200})$")
def generic_tables(soup):
    rows = []
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                rows.append((dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)))
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            rows.append((cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True)))
    for li in soup.find_all("li"):
        t = li.get_text(" ", strip=True)
        m = LABEL_RE.match(t)
        if m and len(li.find_all("li")) == 0:
            rows.append((m.group(1).strip(), m.group(2).strip()))
    out, seen = [], set()
    for k, v in rows:
        k = re.sub(r"\s+", " ", k).strip(" :"); v = re.sub(r"\s+", " ", v).strip()
        if not k or not v or len(k) > 45 or len(v) > 220 or (k.lower(), v) in seen:
            continue
        if re.search(r"cookie|datenschutz|newsletter|login|passwort|telefon|e-mail|mail", k + v, re.I):
            continue
        seen.add((k.lower(), v)); out.append((k, v))
    return out[:90]

def parse_markdown_listing(md):
    """raiffeisen-immobilien serves a markdown version of each listing (<url>.md)."""
    L = listing_template()
    m = re.search(r"^#\s+(.+)$", md, re.M)
    L["title"] = m.group(1).strip() if m else None
    for sec in re.split(r"^##\s+", md, flags=re.M)[1:]:
        head, _, body = sec.partition("\n")
        if re.match(r"\s*(Bilder|Downloads|Dokumente|Videos?|Kontakt|Ansprechpartner)", head, re.I):
            continue
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
        items = re.findall(r"^-\s+([^:\n]{1,60}):\s*(.+)$", body, re.M)
        if items and len(items) >= len([l for l in body.splitlines() if l.strip()]) * 0.6:
            L["tables"].append((head.strip(), [(k.strip(), v.strip()) for k, v in items]))
        else:
            txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body).strip()
            if txt:
                L["sections"].append((head.strip(), text_to_html(txt)))
    for t, rows in L["tables"]:
        for k, v in rows:
            if k.lower().startswith("kaufpreis") and not L["price"]:
                L["price"] = v
            if k.lower() == "adresse":
                L["location"] = v
    L["images"] = uniq(re.findall(r"https://www\.raiffeisen-immobilien\.at/realty-media/\d+/[0-9a-f]+\.(?:jpg|jpeg|png)\?signature=[0-9a-f]+", md))
    return L

def parse_generic(host, s):
    if s.lstrip().startswith("# ") and "raiffeisen" in host:
        return parse_markdown_listing(s)
    soup = BeautifulSoup(s, "html.parser")
    lds = ld_objects(soup)
    L = listing_template()
    ogt = soup.find("meta", attrs={"property": "og:title"})
    h1 = soup.find("h1")
    L["title"] = (h1.get_text(" ", strip=True) if h1 else None) or (ogt.get("content") if ogt else None) \
        or (soup.title.get_text(strip=True) if soup.title else None)
    L["images"] = site_images(host, s, soup, lds)
    # description: LD description, then likely description containers (longest text)
    desc = None
    for d in lds:
        if d.get("@type") in ("Product", "RealEstateListing", "Offer", "House", "SingleFamilyResidence") and d.get("description"):
            desc = d["description"]; break
        if d.get("offers") and isinstance(d["offers"], dict) and d["offers"].get("price") and not L["price"]:
            L["price"] = fmt_eur(d["offers"]["price"])
    for t in soup.find_all(True, attrs={"class": re.compile(r"cookie|consent|cmplz|gdpr|borlabs|newsletter", re.I)}) + \
             soup.find_all(True, attrs={"id": re.compile(r"cookie|consent|cmplz|gdpr|borlabs", re.I)}) + \
             soup.find_all(["nav", "footer", "script", "style", "noscript"]):
        t.decompose()
    DESC = re.compile(r"desc|beschreib|freitext|expose-?text|object-?text|objekttext", re.I)
    cands = [t for t in soup.find_all(True) if t.get("itemprop") == "description"
             or DESC.search(" ".join(t.get("class") or [])) or DESC.search(t.get("id") or "")]
    picked, texts = [], []
    for t in cands:
        txt = t.get_text(" ", strip=True)
        if len(txt) < 80 or len(txt) > 30000:
            continue
        if any(txt in x for x in texts):
            continue
        # drop earlier picks that are contained in this one? keep the smaller (leaf) blocks instead
        if any(x in txt for x in texts):
            continue
        picked.append(t); texts.append(txt)
    body = "".join(sanitize_html(str(t)) for t in picked)
    if sum(map(len, texts)) > 200:
        L["sections"].append(("Beschreibung", body))
    elif desc:
        L["sections"].append(("Beschreibung", sanitize_html(html.unescape(desc)) if "<" in desc else text_to_html(html.unescape(desc))))
    else:
        ogd = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
        if ogd and ogd.get("content"):
            L["sections"].append(("Beschreibung (Kurztext)", text_to_html(ogd["content"])))
    for d in lds:
        if d.get("@type") == "Organization" and d.get("name") and not L["seller"]:
            L["seller"] = "Makler: " + d["name"]
        if d.get("@type") == "PostalAddress" and not L["location"]:
            L["location"] = " ".join(x for x in [d.get("postalCode"), d.get("addressLocality")] if x)
    rows = generic_tables(soup)
    if rows:
        L["tables"].append(("Objektdaten (" + host.replace("www.", "") + ")", rows))
    return L

def is_blocked_page(s):
    return bool(re.search(r"challenges\.cloudflare\.com/turnstile|Please enable JS and disable any ad blocker|"
                          r"captcha-delivery|Security Verification", s[:6000]))

# ------------------------------------------------------------------ sources
def wh_id(url):
    m = re.search(r"-(\d{6,})/?(?:\?.*)?$", url or "")
    return m.group(1) if (m and "willhaben" in (url or "")) else None

def live_source(url):
    host = urllib.parse.urlparse(url).netloc
    if host in BLOCKED_HOSTS:
        return None, f"{host}: skipped (bot wall)"
    fetch_url = url
    if "raiffeisen-immobilien" in host and not url.endswith(".md"):
        fetch_url = url + ".md"
    try:
        st, final, data = http_get(fetch_url)
    except Exception as e:
        return None, f"{host}: {e}"
    s = data.decode("utf-8", "replace")
    if is_blocked_page(s):
        return None, f"{host}: bot wall"
    if "willhaben" in host:
        if "fromExpiredAdId" in final:
            return None, f"{host}: expired (redirected to search)"
        L = parse_willhaben(s)
        if not L:
            return None, f"{host}: no advertDetails"
    else:
        L = parse_generic(host, s)
    L["source"], L["source_url"], L["fetched"] = "live", url, TODAY
    return L, f"{host}: live ok ({len(L['images'])} imgs)"

def cache_files(p):
    files = []
    for base in CACHE_GLOBS:
        for d in glob.glob(base):
            for u in (p.get("url_primary"), p.get("url_alt")):
                i = wh_id(u)
                if i:
                    files += glob.glob(f"{d}/detail_{i}.html") + glob.glob(f"{d}/details/detail_{i}.html")
            pid = p["id"]
            files += glob.glob(f"{d}/v_{pid}wh.html") + glob.glob(f"{d}/v_{pid}[a-z]*.html") + \
                glob.glob(f"{d}/v_{pid}.html") + glob.glob(f"{d}/portal_{pid}.html") + glob.glob(f"{d}/details/{pid}.html")
    files = uniq(files)
    files.sort(key=lambda f: (0 if ("detail_" in f or "wh" in os.path.basename(f)) else 1, -os.path.getmtime(f)))
    return files

def cache_sources(p):
    for f in cache_files(p):
        try:
            s = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if len(s) < 3000 or is_blocked_page(s):
            continue
        L = parse_willhaben(s)
        if L:
            L["_host"] = "www.willhaben.at"
        else:
            if "__NEXT_DATA__" in s and "willhaben" in s[:200000]:
                continue          # expired willhaben ad (search page) – useless
            host = ""
            m = re.search(r'<link rel="canonical" href="https?://([^/"]+)', s) or \
                re.search(r'og:url"\s+content="https?://([^/"]+)', s)
            if m:
                host = m.group(1)
            if not host:
                for u in (p.get("url_primary"), p.get("url_alt")):
                    h = urllib.parse.urlparse(u or "").netloc
                    if h and h.replace("www.", "") in s:
                        host = h; break
            if not host or "willhaben" in host:
                continue
            L = parse_generic(host, s)
            L["_host"] = host
        m = re.search(r'<link rel="canonical" href="([^"]+)"', s)
        L["source"] = "cache"
        L["source_url"] = (m.group(1) if m else None) or p.get("url_primary") or p.get("url_alt")
        L["fetched"] = datetime.fromtimestamp(os.path.getmtime(f), ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
        L["_file"] = f
        yield L

def search_cache_image(p):
    """willhaben search result pages (…_nextdata.json) keep the main image of ads."""
    ids = {wh_id(p.get("url_primary")), wh_id(p.get("url_alt"))} - {None}
    if not ids:
        return None
    files = sorted(glob.glob(RESEARCH + "/scrape-*/*nextdata*.json"), key=os.path.getmtime, reverse=True)
    for f in files:
        try:
            s = open(f, encoding="utf-8").read()
        except Exception:
            continue
        if not any(i in s for i in ids):
            continue
        try:
            d = json.loads(s)
        except Exception:
            continue
        stack = [d]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if str(x.get("id")) in ids and x.get("advertImageList"):
                    for im in x["advertImageList"].get("advertImage", []):
                        u = im.get("referenceImageUrl") or im.get("mainImageUrl")
                        if u:
                            return u
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return None

def gather(p, use_live=True):
    """Return (listing, notes). Order: live willhaben, live other, cache."""
    urls = [u for u in (p.get("url_primary"), p.get("url_alt")) if u]
    urls.sort(key=lambda u: 0 if "willhaben" in u else 1)
    notes, cands = [], []
    if use_live:
        for u in urls:
            L, note = live_source(u)
            notes.append(note)
            if L and len(L["images"]) >= 3:
                return L, notes
            if L:
                cands.append(L)
    for L in cache_sources(p):
        notes.append(f"cache {os.path.relpath(L['_file'], RESEARCH)} ({len(L['images'])} imgs)")
        if len(L["images"]) >= 3:
            return L, notes
        cands.append(L)
    cands = [c for c in cands if c["images"]] or cands
    if cands:
        return max(cands, key=lambda c: len(c["images"])), notes
    return None, notes

# ------------------------------------------------------------------ archive page
CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
color:#1f2933;background:#f5f7fa;line-height:1.5}main{max-width:1000px;margin:0 auto;padding:18px}
a{color:#1d4ed8}header.top{background:#1f2933;color:#fff;padding:10px 18px;font-size:14px}
header.top a{color:#cbd5e1;margin-right:14px}h1{font-size:1.5rem;margin:.4em 0 .2em}
.price{font-size:1.4rem;font-weight:700;color:#0f766e}.meta{color:#52606d;font-size:.95rem}
.notice{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:8px 12px;margin:12px 0;font-size:.9rem}
.card{background:#fff;border:1px solid #e4e7eb;border-radius:10px;padding:14px 16px;margin:14px 0}
.card h2{font-size:1.1rem;margin:.1em 0 .6em}.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.fact{background:#f8fafc;border-radius:8px;padding:6px 10px}.fact b{display:block;font-size:.75rem;color:#7b8794;font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}
.gallery img{width:100%;height:170px;object-fit:cover;border-radius:6px;display:block;background:#e4e7eb}
.gallery a:first-child{grid-column:1/-1}.gallery a:first-child img{height:auto;max-height:520px}
table{border-collapse:collapse;width:100%;font-size:.92rem}td,th{border-bottom:1px solid #edf0f3;padding:5px 8px;text-align:left;vertical-align:top}
td:first-child{color:#52606d;width:38%}.desc{font-size:.95rem}.desc ul{padding-left:1.2em}
footer{color:#7b8794;font-size:.85rem;margin:20px 0 40px}code{word-break:break-all}
@media(max-width:600px){main{padding:10px}.gallery{grid-template-columns:1fr 1fr}.gallery img{height:120px}}
"""

def facts_from_record(p):
    f = [("Preis", fmt_eur(p.get("price_eur"))), ("Ort", " ".join(x for x in [p.get("plz"), p.get("location_town")] if x) or None),
         ("Region", p.get("district_or_region")), ("Typ", p.get("type")),
         ("Wohnfläche", fmt_num(p.get("living_m2"), " m²")), ("Grundfläche", fmt_num(p.get("plot_m2"), " m²")),
         ("Zimmer", fmt_num(p.get("rooms"))), ("Baujahr", p.get("year_built")),
         ("Energie (HWB)", " ".join(x for x in [p.get("energy_hwb_class") and f"Klasse {p['energy_hwb_class']}",
                                                p.get("energy_hwb_value") is not None and f"{p['energy_hwb_value']} kWh/m²a" or None] if x) or None),
         ("Heizung", p.get("heating")),
         ("Betriebskosten / Monat", fmt_eur(p.get("betriebskosten_monat_eur")) if p.get("betriebskosten_monat_eur") is not None else None),
         ("Verkäufer", {"makler": "Makler", "private": "Privat"}.get(p.get("seller_type"), p.get("seller_type"))),
         ("Makler / Agent", p.get("makler_name")), ("Preis pro m²", fmt_eur(p.get("price_per_m2"))),
         ("All-in inkl. Nebenkosten (Schätzung)", fmt_eur(p.get("all_in_price_eur"))),
         ("Status (Watch)", p.get("listing_status")), ("Zuerst gesehen", p.get("first_seen"))]
    return [(k, v) for k, v in f if v not in (None, "", False)]

def render_archive(p, L, photos):
    title = L.get("title") or p.get("title") or p["id"]
    price = L.get("price") or fmt_eur(p.get("price_eur")) or "—"
    loc = L.get("location") or " ".join(x for x in [p.get("plz"), p.get("location_town")] if x)
    src_note = ("Snapshot taken from the live listing page" if L["source"] == "live"
                else f"Snapshot reconstructed from a cached copy of the listing saved on {L['fetched']}")
    status_note = ""
    if p.get("listing_status") in ("sold", "disappeared"):
        status_note = f'<div class="notice">Listing status at archive time: <b>{esc(p["listing_status"])}</b>.</div>'
    parts = [f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{esc(p['id'])} · {esc(title)} — Archiv</title><style>{CSS}</style></head><body>
<header class="top"><a href="../">← Dashboard</a><a href="./">Archive index</a>Tulln Property Watch · archived listing {esc(p['id'])}</header>
<main><h1>{esc(title)}</h1><div class="price">{esc(price)}</div>
<div class="meta">{esc(loc)} · archived {esc(L['fetched'])} · <a href="{esc(L['source_url'] or p.get('url_primary') or '')}" rel="noopener nofollow" target="_blank">original listing</a></div>
{status_note}"""]
    if photos:
        g = "".join(f'<a href="{esc(ph)}" target="_blank"><img src="{esc(ph)}" loading="lazy" alt="Foto {i+1}"></a>'
                    for i, ph in enumerate(photos))
        parts.append(f'<section class="card"><h2>Fotos</h2><div class="gallery">{g}</div></section>')
    facts = facts_from_record(p)
    if L.get("seller"):
        facts.append(("Anbieter (laut Inserat)", L["seller"]))
    parts.append('<section class="card"><h2>Eckdaten</h2><div class="facts">' +
                 "".join(f'<div class="fact"><b>{esc(k)}</b>{esc(v)}</div>' for k, v in facts) + "</div></section>")
    for head, body in L.get("sections", []):
        if body and len(re.sub(r"<[^>]+>", "", body).strip()) > 3:
            parts.append(f'<section class="card desc"><h2>{esc(head)}</h2>{body}</section>')
    for head, rows in L.get("tables", []):
        if rows:
            parts.append(f'<section class="card"><h2>{esc(head)}</h2><table>' +
                         "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows) + "</table></section>")
    parts.append(f"""<footer>{esc(src_note)}.<br>Original URL: <code>{esc(L['source_url'] or p.get('url_primary') or '')}</code><br>
Archived: {esc(L['fetched'])} (page generated {esc(TODAY)}) · Content and photos © the original advertiser; kept as a private reference copy.
</footer></main></body></html>""")
    return scrub("\n".join(parts))

def render_index(props):
    rows = []
    order = {"active": 0, "sold": 1, "disappeared": 2}
    for p in sorted([p for p in props if p.get("archive")], key=lambda p: (order.get(p.get("listing_status"), 3), p["id"])):
        thumb = f'<img src="../{esc(p["image"])}" alt="" loading="lazy">' if p.get("image") else ""
        rows.append(f"""<tr><td class="th">{thumb}</td><td><a href="{esc(os.path.basename(p['archive']))}"><b>{esc(p['id'])}</b> · {esc(p.get('title'))}</a>
<div class="meta">{esc(p.get('location_town') or '')} · {esc(fmt_num(p.get('living_m2'), ' m²') or '—')} · {esc(fmt_num(p.get('plot_m2'), ' m² Grund') or '—')}</div></td>
<td>{esc(fmt_eur(p.get('price_eur')) or '—')}</td><td><span class="st st-{esc(p.get('listing_status'))}">{esc(p.get('listing_status'))}</span></td><td>{esc(p.get('archived_at') or '')}</td></tr>""")
    css = CSS + """.th img{width:96px;height:64px;object-fit:cover;border-radius:6px}.th{width:104px}
.st{font-size:.8rem;padding:1px 7px;border-radius:9px;background:#e0f2f1}.st-sold{background:#fee2e2}.st-disappeared{background:#e5e7eb}
@media(max-width:600px){.th img{width:64px;height:44px}.th{width:70px}}"""
    return scrub(f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Archived listings — Tulln Property Watch</title><style>{css}</style></head><body>
<header class="top"><a href="../">← Dashboard</a>Tulln Property Watch · listing archive</header><main>
<h1>Archived listings</h1><p class="meta">{len(rows)} static snapshots of listing pages (facts, description, photos). Updated {esc(TODAY)}.</p>
<div class="card"><table><thead><tr><th></th><th>Listing</th><th>Price</th><th>Status</th><th>Archived</th></tr></thead><tbody>
{''.join(rows)}</tbody></table></div></main></body></html>""")

# ------------------------------------------------------------------ data files
PROPS_RE = re.compile(r"const PROPERTIES = (\[.*?\n\]);\n", re.S)

class HtmlStore:
    def __init__(self, path):
        self.path = path
        self.s = open(path, encoding="utf-8").read()
        m = PROPS_RE.search(self.s)
        if not m:
            raise SystemExit(f"{path}: const PROPERTIES not found")
        self.m = m
        self.arr = json.loads(m.group(1))
    def save(self):
        s = self.s[:self.m.start(1)] + json.dumps(self.arr, indent=2, ensure_ascii=False) + self.s[self.m.end(1):]
        if "const BUDGET" not in s:
            raise SystemExit(f"{self.path}: refusing to write – const BUDGET line missing")
        open(self.path, "w", encoding="utf-8").write(s)

def apply_update(p, u):
    for k, v in u.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v

def update_json(path, updates):
    if not os.path.exists(path):
        return
    s = open(path, encoding="utf-8").read()
    data = json.loads(s)
    lst = data if isinstance(data, list) else data.get("properties", [])
    for p in lst:
        u = updates.get(p.get("id"))
        if u:
            apply_update(p, u)
    out = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if s.endswith("\n") else "")
    open(path, "w", encoding="utf-8").write(out)

def update_csv(path, updates, fields=("image", "archive", "archived_at")):
    if not os.path.exists(path):
        return
    raw = open(path, encoding="utf-8", newline="").read()
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return
    cols = list(rows[0].keys())
    for f in fields:
        if f not in cols:
            cols.append(f)
    for r in rows:
        u = updates.get(r.get("id")) or {}
        for f in fields:
            if f in u:
                r[f] = "" if u[f] is None else str(u[f])
            else:
                r.setdefault(f, "")
                if r[f] is None:
                    r[f] = ""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\r\n" if "\r\n" in raw else "\n")
    w.writeheader(); w.writerows(rows)
    open(path, "w", encoding="utf-8", newline="").write(out.getvalue())

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ids", help="comma-separated ids to process")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-live", action="store_true", help="use cached pages only")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--max-photos", type=int, default=MAX_PHOTOS)
    ap.add_argument("--redo", action="store_true",
                    help="with --ids: delete existing image/archive of those ids first and rebuild them")
    ap.add_argument("--no-inactive-archive", action="store_true",
                    help="only create archives for active listings (images are still fetched for all)")
    a = ap.parse_args()

    store = HtmlStore(os.path.join(HERE, "index.html"))
    props = store.arr
    img_dir, arc_dir = os.path.join(HERE, "img"), os.path.join(HERE, "archive")
    updates = {}
    if a.redo:
        if not a.ids:
            sys.exit("--redo needs --ids")
        import shutil
        for p in props:
            if p["id"] in a.ids.split(","):
                for f in (os.path.join(img_dir, f"{p['id']}.jpg"), os.path.join(arc_dir, f"{p['id']}.html")):
                    if os.path.exists(f):
                        os.remove(f)
                shutil.rmtree(os.path.join(arc_dir, p["id"]), ignore_errors=True)
                for k in ("image", "archive", "archived_at"):
                    p.pop(k, None)
                updates[p["id"]] = {"image": None, "archive": None, "archived_at": None}
    # adopt files that already exist on disk (idempotent / repair missing fields)
    for p in props:
        u = {}
        if not p.get("image") and os.path.exists(os.path.join(img_dir, f"{p['id']}.jpg")):
            u["image"] = f"img/{p['id']}.jpg"
        if not p.get("archive") and os.path.exists(os.path.join(arc_dir, f"{p['id']}.html")):
            u["archive"] = f"archive/{p['id']}.html"
            u["archived_at"] = p.get("archived_at") or datetime.fromtimestamp(
                os.path.getmtime(os.path.join(arc_dir, f"{p['id']}.html")), ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
        if u:
            p.update(u); updates[p["id"]] = dict(u)

    ids = set(a.ids.split(",")) if a.ids else None
    order = {"active": 0, "sold": 1, "disappeared": 2}
    todo = []
    for p in sorted(props, key=lambda p: order.get(p.get("listing_status"), 3)):
        if ids and p["id"] not in ids:
            continue
        if not (p.get("url_primary") or p.get("url_alt")):
            continue
        need_img = not (p.get("image") and os.path.exists(os.path.join(HERE, p["image"])))
        inactive = p.get("listing_status") in ("sold", "disappeared")
        need_arc = not p.get("archive") and not (inactive and a.no_inactive_archive)
        if need_img or need_arc:
            todo.append((p, need_img, need_arc))
    log(f"{len(todo)} listing(s) need work" + (" (dry run)" if a.dry_run else ""))
    res = {"img_ok": [], "img_fail": [], "arc_ok": [], "arc_fail": [], "notes": {}}
    if a.index_only:
        todo = []
    for p, need_img, need_arc in todo:
        pid = p["id"]
        if a.dry_run:
            log(f"  {pid} [{p.get('listing_status')}] image={need_img} archive={need_arc}")
            continue
        L, notes = gather(p, use_live=not a.no_live)
        res["notes"][pid] = notes
        log(f"{pid} [{p.get('listing_status')}]: " + " | ".join(notes))
        images = list(L["images"]) if L else []
        referer = (L or {}).get("source_url") or p.get("url_primary")
        if need_img and not images:
            u = search_cache_image(p)
            if u:
                images = [u]; log(f"  {pid}: main image from cached willhaben search results")
        u = {}
        if need_img:
            ok = False
            for iu in images[:4]:
                try:
                    jpg = fetch_image(iu, referer, 640, 120, 82)
                    os.makedirs(img_dir, exist_ok=True)
                    open(os.path.join(img_dir, f"{pid}.jpg"), "wb").write(jpg)
                    u["image"] = f"img/{pid}.jpg"; ok = True
                    log(f"  {pid}: image saved ({len(jpg)//1024} KB)")
                    break
                except Exception as e:
                    log(f"  {pid}: image fail {iu[:90]}: {e}")
            (res["img_ok"] if ok else res["img_fail"]).append(pid)
        if need_arc:
            path = os.path.join(arc_dir, f"{pid}.html")
            if os.path.exists(path):          # never overwrite an existing snapshot
                u["archive"] = f"archive/{pid}.html"
            elif L and (L["images"] or sum(len(re.sub(r"<[^>]+>", "", b)) for _, b in L["sections"]) > 200):
                photos, pdir = [], os.path.join(arc_dir, pid)
                os.makedirs(pdir, exist_ok=True)
                for iu in L["images"]:
                    if len(photos) >= a.max_photos:
                        break
                    fn = f"{len(photos)+1:02d}.jpg"
                    try:
                        jpg = fetch_image(iu, referer, 1000, 150, 74)
                        open(os.path.join(pdir, fn), "wb").write(jpg)
                        photos.append(f"{pid}/{fn}")
                    except Exception as e:
                        log(f"  {pid}: archive photo fail {iu[:90]}: {e}")
                if not photos:
                    try: os.rmdir(pdir)
                    except OSError: pass
                open(path, "w", encoding="utf-8").write(render_archive(p, L, photos))
                u["archive"] = f"archive/{pid}.html"
                u["archived_at"] = L["fetched"]
                res["arc_ok"].append(pid)
                log(f"  {pid}: archive written ({len(photos)} photos, source={L['source']})")
            else:
                res["arc_fail"].append(pid)
        if u:
            apply_update(p, u)
            updates.setdefault(pid, {}).update(u)

    if a.dry_run:
        return
    if updates:
        store.save()
        dash = os.path.join(RESEARCH, "dashboard.html")
        if os.path.exists(dash):
            d = HtmlStore(dash)
            for q in d.arr:
                if q.get("id") in updates:
                    apply_update(q, updates[q["id"]])
            d.save()
        for base in (HERE, RESEARCH):
            update_json(os.path.join(base, "properties.json"), updates)
            update_csv(os.path.join(base, "properties.csv"), updates)
    if any(p.get("archive") for p in props):
        os.makedirs(arc_dir, exist_ok=True)
        open(os.path.join(arc_dir, "index.html"), "w", encoding="utf-8").write(render_index(props))
    summary = {k: v for k, v in res.items() if k != "notes"}
    log("SUMMARY " + json.dumps(summary))
    with open(os.path.join(HERE, ".archive_last_run.json"), "w") as f:
        json.dump({"date": TODAY, **res}, f, indent=1, ensure_ascii=False)

if __name__ == "__main__":
    main()
