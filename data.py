"""World data: airports and runways (OurAirports), live METARs (aviationweather.gov),
landmarks (OpenStreetMap via Overpass + your own MSFS POI CSV files). All free."""
import csv
import glob
import io
import json
import math
import os
import pickle
import threading
import re
import time
import urllib.parse
import urllib.request

import sys

import geo
import version

FROZEN = getattr(sys, "frozen", False)  # running as a packaged Onward.exe
BASE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """Your data lives next to the program when you run from source, and in %LOCALAPPDATA%\\Onward
    for the packaged app (so updates never touch it). ONWARD_DATA overrides both."""
    if os.environ.get("ONWARD_DATA"):
        return os.environ["ONWARD_DATA"]
    if FROZEN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Onward")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


DATA = _data_dir()
RAW = os.path.join(DATA, "raw")
POI_CACHE = os.path.join(DATA, "poi_cache")
MSFS_POI_DIR = os.path.join(DATA, "msfs_pois")
# Community map of MSFS points of interest, handcrafted airports and photogrammetry cities (Google My Maps)
MSFS_MAP_MID = "1KUg5jwyT_9k2A9n5IZ99UChlhfVUfO5S"
MSFS_MAP_FILE = os.path.join(MSFS_POI_DIR, "msfs-world-map.kml")
WORLD_PICKLE = os.path.join(DATA, "world.pickle")
OA = "https://davidmegginson.github.io/ourairports-data/"
HEADERS = {"User-Agent": f"Onward/{version.VERSION} (flight sim companion; https://github.com/onward-sim/Onward)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
WORLD_VERSION = 9

for d in (DATA, RAW, POI_CACHE, MSFS_POI_DIR):
    os.makedirs(d, exist_ok=True)
if not os.path.exists(os.path.join(MSFS_POI_DIR, "README.txt")):
    try:
        with open(os.path.join(MSFS_POI_DIR, "README.txt"), "w", encoding="utf-8") as _f:
            _f.write("MSFS points of interest\n\nOnward downloads the community \"Microsoft Flight Simulator Map\" (MSFS POIs, photogrammetry cities and\n"
                     "handcrafted airports) here and refreshes it about once a week.\n\nYou can drop extra Little Navmap userpoint CSV files here too, "
                     "then press \"Reload POI files\" in Onward's Settings.\n")
    except OSError:
        pass


_om_down_until = 0.0
_om_cache = {}
BUDGET_FILE = os.path.join(DATA, "open_meteo_usage.json")
DAILY_BUDGET = 5000  # Open-Meteo's free limit is 10,000 a day per address; keep well under it and leave the rest to MET Norway


def _om_count(url):
    """Open-Meteo counts each location, and more for long variable lists; roughly the same here."""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    locs = len((q.get("latitude") or [""])[0].split(","))
    nvars = len((q.get("hourly") or [""])[0].split(","))
    return locs * max(1.0, nvars / 10)


def _om_usage(add=0.0):
    day = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        with open(BUDGET_FILE, encoding="utf-8") as f:
            u = json.load(f)
    except Exception:
        u = {}
    if u.get("day") != day:
        u = {"day": day, "used": 0.0}
    if add:
        u["used"] = round(u["used"] + add, 1)
        try:
            with open(BUDGET_FILE, "w", encoding="utf-8") as f:
                json.dump(u, f)
        except Exception:
            pass
    return u["used"]


def forecast_get(url, timeout=30):
    """An Open-Meteo forecast, or the same answer from MET Norway when Open-Meteo refuses, is down, or today's budget is spent.

    Answers are kept for 15 minutes, so replanning the same route doesn't ask again."""
    global _om_down_until
    hit = _om_cache.get(url)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    raw = None
    cost = _om_count(url)
    if time.time() >= _om_down_until and _om_usage() + cost <= DAILY_BUDGET:
        try:
            raw = http_get(url, timeout=timeout)
            _om_usage(cost)
        except Exception as e:
            _om_down_until = time.time() + (3600 if "429" in str(e) else 300)  # after a refusal, leave it alone for a while
            print(f"Open-Meteo forecast failed ({e}). Using MET Norway instead.")
    if raw is None:
        import metno
        raw = metno.forecast(url)
    if len(_om_cache) > 300:
        _om_cache.clear()
    _om_cache[url] = (time.time(), raw)
    return raw


def _ssl_context():
    """The system's trusted certificates plus certifi's bundle. Windows only fetches some newer roots
    (HARICA/GEANT, used by MET Norway) on demand for browsers, so Python alone can fail to verify them."""
    import ssl
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


SSL_CTX = _ssl_context()


def http_get(url, timeout=30, data=None):
    req = urllib.request.Request(url, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


PAVED = ("ASP", "ASF", "CON", "PEM", "BIT", "BET", "TAR", "PAV", "CEM", "MAC", "SEAL", "BRICK", "HARD", "HORMIG")
WATER = ("WATER", "WAT")


def surface_class(s):
    s = (s or "").upper()
    if not s:
        return "unknown"
    if any(w in s for w in WATER):
        return "water"
    if any(p in s for p in PAVED) and "UNPAV" not in s and "GRAV" not in s:
        return "paved"
    return "unpaved"


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class World:
    """All airports in memory, with a 1x1 degree grid index for fast radius searches."""

    def __init__(self, log=print):
        self.log = log
        self.airports = {}
        self.grid = {}
        self.countries = {}
        self.msfs_pois = []
        self.msfs_grid = {}
        self.handcrafted = set()
        self._load()
        self.load_msfs_files()

    # ---------- airports ----------
    def _download(self, name):
        path = os.path.join(RAW, name)
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            self.log(f"Downloading {name} from OurAirports...")
            with open(path, "wb") as f:
                f.write(http_get(OA + name, timeout=120))
        return path

    def refresh(self):
        for n in ("airports.csv", "runways.csv", "countries.csv", "navaids.csv", "airport-frequencies.csv"):
            p = os.path.join(RAW, n)
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(WORLD_PICKLE):
            os.remove(WORLD_PICKLE)
        self._load()

    def _load(self):
        if os.path.exists(WORLD_PICKLE):
            try:
                with open(WORLD_PICKLE, "rb") as f:
                    blob = pickle.load(f)
                if blob.get("v") == WORLD_VERSION:
                    self.airports, self.countries = blob["airports"], blob["countries"]
                    self.navaids, self.freqs = blob["navaids"], blob["freqs"]
                    self._index()
                    return
            except Exception:
                pass
        ap_path, rw_path, c_path, nv_path, fq_path = (self._download(n) for n in (
            "airports.csv", "runways.csv", "countries.csv", "navaids.csv", "airport-frequencies.csv"))
        self.navaids, self.freqs = [], {}
        with open(nv_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                lat, lon = _f(r["latitude_deg"]), _f(r["longitude_deg"])
                if lat is None or lon is None or r["type"] not in ("VOR", "VOR-DME", "VORTAC", "NDB", "NDB-DME"):
                    continue
                khz = _f(r["frequency_khz"])
                self.navaids.append({"ident": r["ident"], "name": r["name"], "type": r["type"], "lat": lat, "lon": lon,
                                     "freq": (f"{khz / 1000:.2f}" if khz and khz > 10000 else f"{khz:.0f} kHz") if khz else "",
                                     "var": _f(r["magnetic_variation_deg"]), "assoc": r["associated_airport"]})
        with open(fq_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                mhz = _f(r["frequency_mhz"])
                if mhz:
                    self.freqs.setdefault(r["airport_ident"], []).append(
                        {"type": r["type"], "desc": r["description"], "mhz": f"{mhz:.3f}"})
        self.log("Building the airport database (first run only)...")
        with open(c_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.countries[r["code"]] = r["name"]
        keep = {"small_airport", "medium_airport", "large_airport", "seaplane_base"}
        with open(ap_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["type"] not in keep:
                    continue
                lat, lon = _f(r["latitude_deg"]), _f(r["longitude_deg"])
                if lat is None or lon is None:
                    continue
                gps, local = r.get("gps_code") or "", r.get("local_code") or ""
                ident = r["ident"]
                # MSFS generally uses the ICAO/GPS code; private strips often have no code it knows
                code = ident if (len(ident) == 4 and ident.isalnum()) else (gps if len(gps) in (3, 4) and gps.isalnum() else (local if local.isalnum() and 3 <= len(local) <= 4 else ""))
                self.airports[ident] = {
                    "ident": ident, "code": code, "type": r["type"], "name": r["name"],
                    "lat": lat, "lon": lon, "elev": _f(r["elevation_ft"], 0) or 0,
                    "country": r["iso_country"], "town": r["municipality"],
                    "scheduled": r["scheduled_service"] == "yes", "runways": [],
                }
        with open(rw_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = self.airports.get(r["airport_ident"])
                if not a or r["closed"] == "1":
                    continue
                length = _f(r["length_ft"])
                if not length or length < 300:
                    continue
                if (r["le_ident"] or "").upper().startswith("H"):
                    continue  # helipad
                a["runways"].append({
                    "id": f'{r["le_ident"]}/{r["he_ident"]}'.strip("/"),
                    "length": length, "width": _f(r["width_ft"], 0) or 0,
                    "surface": surface_class(r["surface"]), "surface_raw": r["surface"],
                    "lighted": r["lighted"] == "1",
                    "le": r["le_ident"], "he": r["he_ident"],
                    "le_hdg": _f(r["le_heading_degT"]), "he_hdg": _f(r["he_heading_degT"]),
                    "le_lat": _f(r["le_latitude_deg"]), "le_lon": _f(r["le_longitude_deg"]),
                    "he_lat": _f(r["he_latitude_deg"]), "he_lon": _f(r["he_longitude_deg"]),
                    "le_disp": _f(r.get("le_displaced_threshold_ft"), 0) or 0, "he_disp": _f(r.get("he_displaced_threshold_ft"), 0) or 0,
                    "le_elev": _f(r.get("le_elevation_ft")), "he_elev": _f(r.get("he_elevation_ft")),
                })
        for a in self.airports.values():
            for rw in a["runways"]:
                if rw["le_hdg"] is None:
                    rw["le_hdg"] = _hdg_from_ident(rw["le"])
                if rw["he_hdg"] is None and rw["le_hdg"] is not None:
                    rw["he_hdg"] = (rw["le_hdg"] + 180) % 360
        # many small strips have no runway data in OurAirports: keep them, flagged as unknown
        for a in self.airports.values():
            a["rw_unknown"] = not a["runways"] and a["type"] != "seaplane_base"
            n = a["name"].lower()
            a["military"] = any(w in n for w in ("air base", "base aérea", "base aerea", "afb", "military", "naval air", "raf ", "army", "air force", "luftwaffe", "militar", "base aérienne", "aeronautica militare", "air station"))
            a["ultralight"] = any(w in n for w in (" ul", "ultralight", "ulm", "ultra-light", "microlight", "ultraleve"))
        with open(WORLD_PICKLE, "wb") as f:
            pickle.dump({"v": WORLD_VERSION, "airports": self.airports, "countries": self.countries,
                         "navaids": self.navaids, "freqs": self.freqs}, f)
        self._index()

    def _index(self):
        self.nav_assoc = {n.get("assoc") for n in self.navaids if n.get("assoc")}
        self.nav_grid = {}
        for n in self.navaids:
            self.nav_grid.setdefault((math.floor(n["lat"]), math.floor(n["lon"])), []).append(n)
        self.grid = {}
        for a in self.airports.values():
            self.grid.setdefault((math.floor(a["lat"]), math.floor(a["lon"])), []).append(a)
        self.log(f"{len(self.airports):,} airfields loaded.")

    def near(self, lat, lon, radius_nm):
        dlat = radius_nm / 60 + 1
        dlon = radius_nm / (60 * max(0.1, math.cos(math.radians(lat)))) + 1
        out = []
        for i in range(math.floor(lat - dlat), math.floor(lat + dlat) + 1):
            for j in range(math.floor(lon - dlon), math.floor(lon + dlon) + 1):
                jj = ((j + 180) % 360) - 180
                for a in self.grid.get((i, jj), ()):
                    d = geo.dist_nm(lat, lon, a["lat"], a["lon"])
                    if d <= radius_nm:
                        out.append((d, a))
        out.sort(key=lambda x: x[0])
        return out

    def nearest(self, lat, lon, radius_nm=30, pred=None):
        for d, a in self.near(lat, lon, radius_nm):
            if pred is None or pred(a):
                return d, a
        return None, None

    def navaids_near(self, lat, lon, radius_nm):
        span = int(radius_nm / 50) + 1
        out = []
        for i in range(math.floor(lat) - span, math.floor(lat) + span + 1):
            for j in range(math.floor(lon) - span, math.floor(lon) + span + 1):
                for n in self.nav_grid.get((i, j), ()):
                    d = geo.dist_nm(lat, lon, n["lat"], n["lon"])
                    if d <= radius_nm:
                        out.append((d, n))
        out.sort(key=lambda x: x[0])
        return out

    def ifr_approach(self, a):
        """Rough guess at the best instrument approach an airfield has. Free data has no procedures,
        so: long lit paved runway plus a big airport (likely ILS), or an airfield with scheduled
        service or its own navaid (likely RNAV or VOR/NDB approach)."""
        if not a or a.get("military"):
            return None
        if not any(r["surface"] == "paved" and r["lighted"] and r["length"] >= 3000 for r in a["runways"]):
            return None
        if a["type"] == "large_airport":
            return "precision"
        if a.get("scheduled") or a["ident"] in self.nav_assoc:
            return "non-precision"
        return None

    def country(self, code):
        return self.countries.get(code, code)

    # ---------- MSFS POI / handcrafted files (Little Navmap userpoint CSVs) ----------
    def load_msfs_files(self):
        self.msfs_pois, self.msfs_grid, self.handcrafted = [], {}, set()
        for path in glob.glob(os.path.join(MSFS_POI_DIR, "*.csv")):
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as f:
                    for row in csv.reader(f):
                        if len(row) < 5:
                            continue
                        lat, lon = _f(row[3]), _f(row[4])
                        if lat is None or lon is None:
                            continue
                        typ, name, ident = row[0].strip(), row[1].strip(), row[2].strip()
                        if ident and ident.upper() in self.airports and ("airport" in typ.lower() or "airport" in name.lower() or len(ident) == 4):
                            self.handcrafted.add(ident.upper())
                            continue
                        desc = row[8].strip() if len(row) > 8 else ""
                        p = {"name": name or desc or typ, "kind": typ or "MSFS POI", "lat": lat, "lon": lon,
                             "source": "msfs", "note": (row[7].strip() if len(row) > 7 else "")}
                        self.msfs_pois.append(p)
                        self.msfs_grid.setdefault((math.floor(lat), math.floor(lon)), []).append(p)
            except Exception as e:
                self.log(f"Could not read {os.path.basename(path)}: {e}")
        for path in glob.glob(os.path.join(MSFS_POI_DIR, "*.kml")):
            try:
                self._load_kml(path)
            except Exception as e:
                self.log(f"Could not read {os.path.basename(path)}: {e}")
        if self.msfs_pois or self.handcrafted:
            self.log(f"MSFS points of interest: {len(self.msfs_pois):,} places, {len(self.handcrafted)} handcrafted airports.")

    def _load_kml(self, path):
        import re
        import xml.etree.ElementTree as ET
        ns = "{http://www.opengis.net/kml/2.2}"
        root = ET.parse(path).getroot()
        seen = {(round(p["lat"], 3), round(p["lon"], 3)) for p in self.msfs_pois}

        def walk(el, folder):
            for child in el:
                tag = child.tag.replace(ns, "")
                if tag == "Folder":
                    walk(child, child.findtext(ns + "name") or folder)
                elif tag == "Document":
                    walk(child, folder)
                elif tag == "Placemark":
                    coords = (child.findtext(f".//{ns}coordinates") or "").strip().split()
                    if not coords:
                        continue
                    try:
                        lon, lat = map(float, coords[0].split(",")[:2])
                    except ValueError:
                        continue
                    name = (child.findtext(ns + "name") or "").strip()
                    f = folder.lower()
                    if "airport" in f:
                        _, ap = self.nearest(lat, lon, 1.5)
                        if ap:
                            self.handcrafted.add(ap["ident"])
                        continue
                    update = ""
                    m = re.match(r"^\s*(WU|CU)\s*#?\s*(\d+)\s*:?\s*(.*)$", name, re.I)
                    if m:
                        update = f"{'World' if m.group(1).upper() == 'WU' else 'City'} Update {m.group(2)}"
                        name = m.group(3).strip() or name
                    if "photogrammetry" in f or "3d cit" in f:
                        label = "MSFS photogrammetry city"
                    elif "national park" in f:
                        label = "National park"
                    else:
                        label = "MSFS POI" + (f" · {update}" if update else "")
                    key = (round(lat, 3), round(lon, 3))
                    if key in seen or not name:
                        continue
                    seen.add(key)
                    p = {"name": name, "kind": "msfs", "label": label, "lat": lat, "lon": lon, "source": "msfs", "note": update}
                    self.msfs_pois.append(p)
                    self.msfs_grid.setdefault((math.floor(lat), math.floor(lon)), []).append(p)
        walk(root, "")

    def refresh_msfs_map(self, max_age_days=7):
        """Download the community MSFS POI map if our copy is missing or old. Returns True if updated."""
        if os.path.exists(MSFS_MAP_FILE) and time.time() - os.path.getmtime(MSFS_MAP_FILE) < max_age_days * 86400:
            return False
        try:
            raw = http_get(f"https://www.google.com/maps/d/kml?mid={MSFS_MAP_MID}&forcekml=1", timeout=60)
            if b"<kml" not in raw[:500]:
                raise ValueError("unexpected response")
            tmp = MSFS_MAP_FILE + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, MSFS_MAP_FILE)
            self.log("Updated the MSFS points of interest map.")
            self.load_msfs_files()
            return True
        except Exception as e:
            self.log(f"Couldn't update the MSFS points of interest map ({e}). Using the saved copy.")
            return False

    def msfs_near(self, lat, lon, radius_nm):
        out = []
        span = int(radius_nm / 50) + 1
        for i in range(math.floor(lat) - span, math.floor(lat) + span + 1):
            for j in range(math.floor(lon) - span, math.floor(lon) + span + 1):
                for p in self.msfs_grid.get((i, j), ()):
                    if geo.dist_nm(lat, lon, p["lat"], p["lon"]) <= radius_nm:
                        out.append(p)
        return out


def _hdg_from_ident(ident):
    try:
        n = int("".join(ch for ch in (ident or "") if ch.isdigit())[:2])
        return (n * 10) % 360 if 1 <= n <= 36 else None
    except ValueError:
        return None


# ---------------- Weather ----------------
class Weather:
    def __init__(self, log=print):
        self.log = log
        self.cache = {}  # bbox key -> (time, list)
        self.lock = threading.Lock()

    def around(self, lat, lon, radius_nm):
        dlat = radius_nm / 60 + 0.7
        dlon = radius_nm / (60 * max(0.2, math.cos(math.radians(lat)))) + 0.7
        key = (round(lat, 0), round(lon, 0), int(radius_nm // 50))
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
        bbox = f"{lat - dlat:.2f},{lon - dlon:.2f},{lat + dlat:.2f},{lon + dlon:.2f}"
        url = f"https://aviationweather.gov/api/data/metar?bbox={bbox}&format=json&hours=2"
        try:
            raw = json.loads(http_get(url, timeout=25) or b"[]")
        except Exception as e:
            self.log(f"Weather download failed: {e}")
            raw = []
        latest = {}
        for m in raw:
            sid = m.get("icaoId")
            if sid and (sid not in latest or (m.get("obsTime") or 0) > (latest[sid].get("obsTime") or 0)):
                latest[sid] = m
        out = [parse_metar(m) for m in latest.values()]
        out = [m for m in out if m]
        with self.lock:
            self.cache[key] = (time.time(), out)
        return out

    def in_box(self, s, w, n, e):
        """METARs inside a map view (capped so a whole continent doesn't come down at once)."""
        s, n = max(-85, s), min(85, n)
        if (n - s) * (e - w) > 400:  # roughly 20 x 20 degrees
            return []
        key = ("box", round(s), round(w), round(n), round(e))
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
        url = f"https://aviationweather.gov/api/data/metar?bbox={s:.2f},{w:.2f},{n:.2f},{e:.2f}&format=json&hours=2"
        try:
            raw = json.loads(http_get(url, timeout=25) or b"[]")
        except Exception as ex:
            self.log(f"Weather download failed: {ex}")
            return []
        latest = {}
        for m in raw:
            sid = m.get("icaoId")
            if sid and (sid not in latest or (m.get("obsTime") or 0) > (latest[sid].get("obsTime") or 0)):
                latest[sid] = m
        out = [x for x in (parse_metar(m) for m in latest.values()) if x]
        with self.lock:
            self.cache[key] = (time.time(), out)
        return out

    def satellite(self):
        """Time of the latest Meteosat cloud picture (EUMETSAT), so every map tile shows the same moment. Cached for 5 minutes."""
        with self.lock:
            hit = self.cache.get("satellite")
            if hit and time.time() - hit[0] < 300:
                return hit[1]
        out = {"eumetsat": None}
        try:
            xml = http_get("https://view.eumetsat.int/geoserver/mtg_fd/rgb_geocolour/ows?service=WMS&version=1.3.0&request=GetCapabilities",
                           timeout=20).decode("utf-8", "replace")
            m = re.search(r'<Dimension name="time"[^>]*default="([^"]+)"', xml)
            if m:
                out["eumetsat"] = m.group(1)
        except Exception as e:
            self.log(f"Satellite time lookup failed: {e}")
        with self.lock:
            self.cache["satellite"] = (time.time(), out)
        return out

    def sigmets(self):
        """International SIGMETs plus US SIGMETs/AIRMETs as polygons. Cached for 10 minutes."""
        with self.lock:
            hit = self.cache.get("sigmets")
            if hit and time.time() - hit[0] < 600:
                return hit[1]
        out = []
        for url in ("https://aviationweather.gov/api/data/isigmet?format=json", "https://aviationweather.gov/api/data/airsigmet?format=json"):
            try:
                raw = json.loads(http_get(url, timeout=25) or b"[]")
            except Exception as ex:
                self.log(f"SIGMET download failed: {ex}")
                continue
            for s in raw if isinstance(raw, list) else []:
                coords = [[c["lat"], c["lon"]] for c in (s.get("coords") or []) if "lat" in c and "lon" in c]
                if len(coords) < 3:
                    continue
                kind = s.get("airSigmetType") or ("SIGMET" if "isigmet" in url else "SIGMET")
                lo = s.get("base") if s.get("base") is not None else s.get("altitudeLow1")
                hi = s.get("top") if s.get("top") is not None else s.get("altitudeHi1")
                out.append({"kind": kind, "hazard": s.get("hazard") or "", "qualifier": s.get("qualifier") or s.get("severity") or "",
                            "base": lo, "top": hi, "fir": s.get("firName") or s.get("icaoId") or "",
                            "to": s.get("validTimeTo"), "raw": s.get("rawSigmet") or s.get("rawAirSigmet") or "", "coords": coords})
        with self.lock:
            self.cache["sigmets"] = (time.time(), out)
        return out

    def taf(self, lat, lon, max_nm=60):
        """Nearest TAF to a point, parsed. Cached for 30 minutes."""
        key = ("taf", round(lat, 1), round(lon, 1))
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < 1800:
                return hit[1]
        d = max_nm / 60 + 0.3
        dl = max_nm / (60 * max(0.2, math.cos(math.radians(lat)))) + 0.3
        url = f"https://aviationweather.gov/api/data/taf?bbox={lat - d:.2f},{lon - dl:.2f},{lat + d:.2f},{lon + dl:.2f}&format=json"
        try:
            raw = json.loads(http_get(url, timeout=25) or b"[]")
        except Exception as e:
            self.log(f"TAF download failed: {e}")
            return None
        best = None
        for t in raw:
            try:
                dist = geo.dist_nm(lat, lon, float(t["lat"]), float(t["lon"]))
            except (KeyError, TypeError, ValueError):
                continue
            if dist <= max_nm and (best is None or dist < best[0]):
                best = (dist, t)
        out = None
        if best:
            t = best[1]
            groups = []
            for g in t.get("fcsts", []):
                vis = g.get("visib")
                vis = 10.0 if isinstance(vis, str) and "+" in vis else _f(vis)
                ceil = None
                for c in g.get("clouds") or []:
                    if c.get("cover") in ("BKN", "OVC", "OVX", "VV") and c.get("base") is not None:
                        ceil = c["base"] if ceil is None else min(ceil, c["base"])
                if g.get("vertVis"):
                    ceil = _f(g["vertVis"]) if ceil is None else min(ceil, _f(g["vertVis"]))
                groups.append({"from": g.get("timeFrom"), "to": g.get("timeTo"), "change": g.get("fcstChange"),
                               "prob": g.get("probability"), "wdir": g.get("wdir"), "wspd": g.get("wspd"), "wgst": g.get("wgst"),
                               "vis": vis, "ceiling": ceil, "wx": g.get("wxString") or ""})
            out = {"station": t.get("icaoId"), "dist": round(best[0]), "raw": t.get("rawTAF", ""), "groups": groups}
        with self.lock:
            self.cache[key] = (time.time(), out)
        return out

    @staticmethod
    def taf_at(taf, when_ts):
        """Prevailing forecast at a time, plus any TEMPO/PROB worsening."""
        if not taf:
            return None
        main, temp = None, []
        for g in taf["groups"]:
            if g["from"] is None or not (g["from"] <= when_ts < (g["to"] or 1e12)):
                continue
            if g["change"] in ("TEMPO", "PROB") or g["prob"]:
                temp.append(g)
            else:
                main = g
        def cat(g):
            c, v = g.get("ceiling"), g.get("vis")
            if (c is not None and c < 500) or (v is not None and v < 1):
                return "LIFR"
            if (c is not None and c < 1000) or (v is not None and v < 3):
                return "IFR"
            if (c is not None and c <= 3000) or (v is not None and v <= 5):
                return "MVFR"
            return "VFR"
        if not main:
            return None
        return {"main": dict(main, cat=cat(main)), "tempo": [dict(g, cat=cat(g)) for g in temp]}

    @staticmethod
    def nearest(metars, lat, lon, max_nm=60):
        best = None
        for m in metars:
            d = geo.dist_nm(lat, lon, m["lat"], m["lon"])
            if d <= max_nm and (best is None or d < best[0]):
                best = (d, m)
        if not best:
            return None
        return dict(best[1], dist=round(best[0]))


def parse_metar(m):
    try:
        lat, lon = float(m["lat"]), float(m["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    wdir = m.get("wdir")
    wdir = None if wdir in (None, "VRB") else _f(wdir)
    wspd, wgst = _f(m.get("wspd"), 0), _f(m.get("wgst"))
    vis = m.get("visib")
    vis_sm = 10.0 if isinstance(vis, str) and "+" in vis else _f(vis, None)
    ceiling = None
    for c in m.get("clouds") or []:
        if c.get("cover") in ("BKN", "OVC", "OVX", "VV") and c.get("base") is not None:
            ceiling = c["base"] if ceiling is None else min(ceiling, c["base"])
    cat = m.get("fltCat")
    if not cat:
        cat = "VFR"
        if (ceiling is not None and ceiling < 3000) or (vis_sm is not None and vis_sm < 5):
            cat = "MVFR"
        if (ceiling is not None and ceiling < 1000) or (vis_sm is not None and vis_sm < 3):
            cat = "IFR"
        if (ceiling is not None and ceiling < 500) or (vis_sm is not None and vis_sm < 1):
            cat = "LIFR"
    return {"station": m.get("icaoId"), "lat": lat, "lon": lon, "raw": m.get("rawOb", ""),
            "wdir": wdir, "wspd": wspd, "wgst": wgst, "vis": vis_sm, "ceiling": ceiling,
            "cat": cat, "temp": _f(m.get("temp")), "vrb": m.get("wdir") == "VRB",
            "wx": m.get("wxString") or ""}


# ---------------- Landmarks ----------------
OVERPASS_Q = """[out:json][timeout:25];
(
 nwr["historic"~"^(castle|fort|monument|ruins|archaeological_site|monastery|city_gate|tower)$"]["name"]["wikidata"]({s},{w},{n},{e});
 nwr["man_made"="lighthouse"]["name"]({s},{w},{n},{e});
 nwr["waterway"~"^(dam|waterfall)$"]["name"]["wikidata"]({s},{w},{n},{e});
 nwr["natural"~"^(volcano|glacier|arch|cape|peak|bay|beach|cliff|gorge)$"]["name"]["wikidata"]({s},{w},{n},{e});
 nwr["tourism"~"^(attraction|viewpoint|theme_park)$"]["name"]["wikidata"]({s},{w},{n},{e});
 nwr["building"~"^(cathedral)$"]["name"]["wikidata"]({s},{w},{n},{e});
 nwr["amenity"="place_of_worship"]["wikipedia"]["heritage"]({s},{w},{n},{e});
 nwr["bridge:name"]["wikidata"]({s},{w},{n},{e});
);
out center tags;"""

KIND_ORDER = ["historic", "man_made", "waterway", "natural", "tourism", "building", "amenity"]

# Landmarks the sim shows well anywhere: terrain and big structures it builds from map data.
# Everything else (castles, churches, lighthouses, towers...) only counts inside a photogrammetry area,
# where the scan shows it as it really is. MSFS's own handcrafted POIs come from the MSFS map, not from here.
# Outside photogrammetry cities, only major geographic features: the satellite-based scenery shows them well.
# Everything else (castles, churches, towers, small peaks, beaches, bridges...) only counts inside a
# photogrammetry area, where the scan shows it as it really is. MSFS's own POIs come from the MSFS map, not from here.
MAJOR_ALWAYS = {"natural:volcano", "natural:glacier"}
MAJOR_IF_NOTABLE = {"natural:gorge", "natural:cape", "natural:bay", "natural:cliff"}  # with a Wikipedia article
PEAK_MIN_M = 1000      # peaks with a Wikipedia article from this height...
PEAK_ANY_M = 2000      # ...and any named peak from this height
PHOTOGRAMMETRY_NM = 6


def is_major_feature(p):
    k, notable = p.get("kind"), bool(p.get("wiki"))
    if k in MAJOR_ALWAYS:
        return True
    if k in MAJOR_IF_NOTABLE:
        return notable
    if k == "natural:peak":
        ele = p.get("ele") or 0
        return ele >= PEAK_ANY_M or (notable and ele >= PEAK_MIN_M)
    return False


class Landmarks:
    """Landmarks from OpenStreetMap, cached on disk in 0.5 degree tiles. Downloads happen one at a
    time in a background worker, so nothing ever waits on a slow server for long."""
    TILE = 0.5

    def __init__(self, world, log=print):
        self.world, self.log = world, log
        self.mem = {}
        self.lock = threading.Lock()
        self.queue = []
        self.failed = {}
        self.offline_until = 0
        self.downloaded = 0
        self.last_error = ""
        self.last_ok = 0
        self.wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    @classmethod
    def tile_of(cls, lat, lon):
        return (math.floor(lat / cls.TILE) * cls.TILE, math.floor(lon / cls.TILE) * cls.TILE)

    @staticmethod
    def _path(t):
        return os.path.join(POI_CACHE, f"t{t[0]:.1f}_{t[1]:.1f}.json")

    def have(self, t):
        with self.lock:
            if t in self.mem:
                return True
        return os.path.exists(self._path(t))

    def request(self, tiles, front=False):
        with self.lock:
            for t in tiles:
                if t in self.mem or os.path.exists(self._path(t)):
                    continue
                if t in self.queue:
                    if not front:
                        continue
                    self.queue.remove(t)
                if front:
                    self.queue.insert(0, t)
                else:
                    self.queue.append(t)
        self.wake.set()

    def _in_photogrammetry(self, lat, lon):
        return any(p.get("label") == "MSFS photogrammetry city" for p in self.world.msfs_near(lat, lon, PHOTOGRAMMETRY_NM))

    def sim_visible(self, pois):
        """Only landmarks worth seeing in MSFS: anything inside a photogrammetry area, major geographic features elsewhere."""
        out = []
        for p in pois:
            if is_major_feature(p):
                out.append(p)
            elif self._in_photogrammetry(p["lat"], p["lon"]):
                out.append(dict(p, photogrammetry=True))
        return out

    def tile(self, t, fetch=True):
        """Cached landmarks for a tile, or None. With fetch=True a missing tile is queued for download."""
        with self.lock:
            if t in self.mem:
                return self.mem[t]
        path = self._path(t)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    pois = json.load(f)
            except Exception:
                pois = []
            pois = self.sim_visible(pois)
            with self.lock:
                self.mem[t] = pois
            return pois
        if fetch:
            self.request([t])
        return None

    def _worker(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            while True:
                if time.time() < self.offline_until:
                    time.sleep(2)
                    continue
                with self.lock:
                    t = self.queue.pop(0) if self.queue else None
                if t is None:
                    break
                if self.have(t) or time.time() - self.failed.get(t, 0) < 600:
                    continue
                self._download(t)
                time.sleep(1.0)  # be polite to the public servers

    def _download(self, t):
        s, w = t
        q = OVERPASS_Q.format(s=s, w=w, n=s + self.TILE, e=w + self.TILE)
        for url in OVERPASS:
            try:
                raw = json.loads(http_get(url, timeout=30, data=urllib.parse.urlencode({"data": q}).encode()))
            except Exception as e:
                msg = str(e)
                if time.time() - getattr(self, "fail_logged", 0) > 600:  # the public servers are often busy: say so every 10 minutes, not every try
                    self.fail_logged = time.time()
                    self.log(f"Landmark lookup slow or failed on {url.split('/')[2]} ({msg[:80]}). Onward keeps trying quietly.")
                if "429" in msg:
                    time.sleep(10)
                continue
            pois = []
            for el in raw.get("elements", []):
                tags = el.get("tags", {})
                c = el.get("center") or el
                if "lat" not in c:
                    continue
                kind = next((f"{k}:{tags[k]}" for k in KIND_ORDER if k in tags), "landmark")
                if "bridge:name" in tags and kind == "landmark":
                    kind = "bridge"
                pois.append({"name": tags.get("name:en") or tags.get("name") or tags.get("bridge:name"),
                             "kind": kind, "lat": round(c["lat"], 5), "lon": round(c["lon"], 5),
                             "ele": _f(tags.get("ele")), "wiki": tags.get("wikipedia", ""), "source": "osm"})
            with open(self._path(t), "w", encoding="utf-8") as f:
                json.dump(pois, f)  # the disk cache keeps everything, the filter applies on reading
            with self.lock:
                self.mem[t] = self.sim_visible(pois)
            self.downloaded += 1
            self.last_ok = time.time()
            self.last_error = ""
            return True
        self.last_error = "The OpenStreetMap landmark servers aren't answering right now. Onward will keep trying."
        self.failed[t] = time.time()
        self.offline_until = time.time() + 120  # both servers struggling: take a break
        return False

    # ---------- which tiles a place or a route needs ----------
    def tiles_near(self, lat, lon, radius_nm):
        dlat = radius_nm / 60
        dlon = radius_nm / (60 * max(0.2, math.cos(math.radians(lat))))
        out = set()
        la = lat - dlat
        while la <= lat + dlat + self.TILE:
            lo = lon - dlon
            while lo <= lon + dlon + self.TILE:
                out.add(self.tile_of(min(la, lat + dlat), min(lo, lon + dlon)))
                lo += self.TILE
            la += self.TILE
        return out

    def tiles_along(self, a, b, corridor_nm=5):
        d = geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"])
        steps = max(1, int(d / 15))
        out = set()
        for k in range(steps + 1):
            f = k / steps
            lat = a["lat"] + (b["lat"] - a["lat"]) * f
            lon = a["lon"] + (b["lon"] - a["lon"]) * f
            out |= self.tiles_near(lat, lon, corridor_nm)
        return out

    def wait_for(self, tiles, seconds, progress=None):
        """Queue tiles at the front and wait up to `seconds` for them. Returns how many are ready."""
        tiles = list(tiles)
        self.request(tiles, front=True)
        end = time.time() + seconds
        while True:
            ready = sum(1 for t in tiles if self.have(t))
            if progress:
                progress(f"Scouting landmarks along the way ({ready} of {len(tiles)} areas)...")
            if ready == len(tiles) or time.time() > end or time.time() < self.offline_until:
                return ready
            time.sleep(0.5)

    def in_box(self, s, w, n, e, fetch=True, limit=800):
        """Cached landmarks inside a lat/lon box; queues missing tiles (nearest the middle first)."""
        out, missing = list(), []
        mid = ((s + n) / 2, (w + e) / 2)
        la = math.floor(s / self.TILE) * self.TILE
        tiles = []
        while la < n:
            lo = math.floor(w / self.TILE) * self.TILE
            while lo < e:
                tiles.append((la, lo))
                lo += self.TILE
            la += self.TILE
        tiles.sort(key=lambda t: (t[0] + self.TILE / 2 - mid[0]) ** 2 + (t[1] + self.TILE / 2 - mid[1]) ** 2)
        for t in tiles[:60]:
            pois = self.tile(t, fetch=False)
            if pois is None:
                missing.append(t)
                continue
            out.extend(p for p in pois if s <= p["lat"] <= n and w <= p["lon"] <= e)
        for i in range(math.floor(s), math.floor(n) + 1):
            for j in range(math.floor(w), math.floor(e) + 1):
                out.extend(p for p in self.world.msfs_grid.get((i, j), ()) if s <= p["lat"] <= n and w <= p["lon"] <= e)
        if fetch and missing:
            self.request(missing[:20])
        out.sort(key=lambda p: p.get("source") != "msfs")
        return out[:limit], len(missing)

    def status(self):
        with self.lock:
            queued = len(self.queue)
        return {"queued": queued, "downloaded": self.downloaded, "error": self.last_error,
                "paused": time.time() < self.offline_until, "msfs": len(self.world.msfs_pois)}

    # ---------- queries (cache only, never blocking) ----------
    def near(self, lat, lon, radius_nm, fetch=True):
        out = list(self.world.msfs_near(lat, lon, radius_nm))
        for t in self.tiles_near(lat, lon, radius_nm):
            for p in self.tile(t, fetch=fetch) or ():
                if geo.dist_nm(lat, lon, p["lat"], p["lon"]) <= radius_nm:
                    out.append(p)
        return out

    def along(self, a, b, corridor_nm=5, fetch=True, skip_ends_nm=6):
        """Landmarks within a corridor along the route a->b (dicts with lat/lon)."""
        cands = []
        for t in self.tiles_along(a, b, corridor_nm):
            cands.extend(self.tile(t, fetch=fetch) or ())
        cands.extend(self.world.msfs_near((a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2,
                                          geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"]) / 2 + corridor_nm))
        out, seen = [], set()
        for p in cands:
            key = (p["name"], round(p["lat"], 3))
            if key in seen:
                continue
            xt, frac = geo.cross_track_nm(p["lat"], p["lon"], a["lat"], a["lon"], b["lat"], b["lon"])
            if xt <= corridor_nm and 0 <= frac <= 1:
                da = geo.dist_nm(a["lat"], a["lon"], p["lat"], p["lon"])
                db = geo.dist_nm(b["lat"], b["lon"], p["lat"], p["lon"])
                if da > skip_ends_nm and db > 2:
                    seen.add(key)
                    out.append(dict(p, off_nm=round(xt, 1), frac=round(frac, 2)))
        return out
