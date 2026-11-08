"""A realistic VFR plan for a leg: terrain-safe altitude, winds aloft, navlog, fuel, frequencies,
and an MSFS .pln file. Terrain and winds come from Open-Meteo (free, no key)."""
import json
import math
import os
import threading
import time
import urllib.parse
import xml.sax.saxutils as sx
from datetime import datetime, timezone

import briefing
import data
import geo

LEVELS = [(925, 2500), (850, 4800), (700, 9900)]  # hPa and rough altitude in ft


def _ifr_alt(minimum, mc):
    """Smallest IFR semicircular altitude (odd thousands eastbound, even westbound) at or above minimum."""
    east = (mc % 360) < 180
    t = max(2, math.ceil(minimum / 1000))
    while (t % 2 == 1) != east:
        t += 1
    return t * 1000


def _hemi_alt(minimum, mc):
    """Smallest VFR semicircular altitude (odd+500 eastbound, even+500 westbound) at or above minimum."""
    east = (mc % 360) < 180
    t = max(1, math.floor((minimum - 500) / 1000))
    while True:
        alt = t * 1000 + 500
        if alt >= minimum and (t % 2 == 1) == east:
            return alt
        t += 1


class Planner:
    def __init__(self, world, log=print, landmarks=None, weather=None):
        self.world, self.log, self.landmarks, self.weather = world, log, landmarks, weather
        self.elev_path = os.path.join(data.DATA, "terrain_cache.json")
        self.elev_cache = {}
        try:
            with open(self.elev_path, encoding="utf-8") as f:
                self.elev_cache = {tuple(map(float, k.split(","))): v for k, v in json.load(f).items()}
        except Exception:
            pass
        self.wind_cache = {}
        self.lock = threading.Lock()

    # ---------- free data ----------
    def elevations(self, pts):
        """pts: [(lat, lon)] -> feet or None (list), using a 0.01 degree cache."""
        keys = [(round(a, 2), round(b, 2)) for a, b in pts]
        todo = sorted({k for k in keys if k not in self.elev_cache})
        if todo:  # terrain tiles first: no per-point web requests, cached on disk
            try:
                import terrain
                for k, m in zip(todo, terrain.elevations_m(todo)):
                    self.elev_cache[k] = round(m * 3.28084)
                todo_left = []
            except Exception as e:
                self.log(f"Terrain tiles failed: {e}. Using the elevation web services instead.")
                todo_left = todo
        else:
            todo_left = []
        for i in range(0, len(todo_left), 100):
            chunk = todo_left[i:i + 100]
            res = None
            try:
                url = ("https://api.open-meteo.com/v1/elevation?latitude=" + ",".join(f"{a:.2f}" for a, _ in chunk)
                       + "&longitude=" + ",".join(f"{b:.2f}" for _, b in chunk))
                res = json.loads(data.http_get(url, timeout=15))["elevation"]
            except Exception as e:
                self.log(f"Terrain lookup (Open-Meteo) failed: {e}. Trying OpenTopoData...")
                try:
                    url = "https://api.opentopodata.org/v1/srtm90m?locations=" + "|".join(f"{a:.2f},{b:.2f}" for a, b in chunk)
                    res = [r.get("elevation") for r in json.loads(data.http_get(url, timeout=15))["results"]]
                    time.sleep(1.1)  # their limit is one request per second
                except Exception as e2:
                    self.log(f"Terrain lookup (OpenTopoData) failed: {e2}")
                    return [None] * len(pts)
            for k, m in zip(chunk, res):
                self.elev_cache[k] = None if m is None else round(m * 3.28084)
        if todo:
            try:
                with open(self.elev_path, "w", encoding="utf-8") as f:
                    json.dump({f"{a},{b}": v for (a, b), v in self.elev_cache.items()}, f)
            except Exception:
                pass
        return [self.elev_cache.get(k) for k in keys]

    def wx_grid(self, s, w, n, e, alt_ft=5000):
        """Winds at one altitude and the low cloud on a coarse grid over the map view, from one Open-Meteo call."""
        levels = [(1000, 300), (925, 2500), (850, 5000), (700, 10000), (600, 14000), (500, 18000)]
        hpa = min(levels, key=lambda l: abs(l[1] - alt_ft))[0]
        # points on a lattice fixed to the globe, each kept for 30 minutes, so panning only asks for new ones
        span = max(n - s, (e - w) * 0.75)
        step = next((st for st in (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0) if span / st <= 6), 5.0)
        lat0, lon0 = math.floor(s / step) * step + step / 2, math.floor(w / step) * step + step / 2
        pts = [(round(lat0 + i * step, 3), round(lon0 + j * step, 3)) for i in range(int((n - lat0) / step) + 1) for j in range(int((e - lon0) / step) + 1)]
        pts = [p for p in pts if s <= p[0] <= n and w <= p[1] <= e][:49]
        slot = int(time.time() // 1800)
        cache = getattr(self, "_grid_pts", None)
        if cache is None or cache.get("slot") != slot:
            cache = self._grid_pts = {"slot": slot}
        missing = [p for p in pts if (p, hpa) not in cache]
        raw_by = {}
        if missing:
            url = ("https://api.open-meteo.com/v1/forecast?latitude=" + ",".join(f"{a:.3f}" for a, _ in missing)
                   + "&longitude=" + ",".join(f"{b:.3f}" for _, b in missing)
                   + f"&hourly=temperature_2m,dew_point_2m,cloud_cover_low,wind_speed_{hpa}hPa,wind_direction_{hpa}hPa"
                   + "&forecast_hours=1&wind_speed_unit=kn&timezone=UTC")
            try:
                got = json.loads(data.forecast_get(url, timeout=20))
            except Exception as ex:
                self.log(f"Weather grid failed: {ex}")
                return {"points": [], "level_ft": dict(levels)[hpa], "error": "Forecast not available right now."}
            if isinstance(got, dict):
                got = [got]
            for p, r in zip(missing, got):
                cache[(p, hpa)] = r
        raw = [cache[(p, hpa)] for p in pts]
        if isinstance(raw, dict):
            raw = [raw]
        out = []
        for (la, lo), r in zip(pts, raw):
            h = r.get("hourly") or {}
            c = {k: (v[0] if isinstance(v, list) and v else None) for k, v in h.items()}
            t, td, low = c.get("temperature_2m"), c.get("dew_point_2m"), c.get("cloud_cover_low") or 0
            base = round((r.get("elevation", 0) * 3.28084 + max(0, t - td) * 400) / 100) * 100 if t is not None and td is not None and low >= 40 else None
            out.append({"lat": round(la, 3), "lon": round(lo, 3), "dir": c.get(f"wind_direction_{hpa}hPa"), "kt": c.get(f"wind_speed_{hpa}hPa"),
                        "low": low, "base": base})
        return {"points": out, "level_ft": dict(levels)[hpa]}

    COND_HOURS = 7          # now and the next six hours
    COND_LEVELS = (1000, 925, 850, 700)

    def wx_conditions(self, s, w, n, e):
        """Forecast flying conditions (VFR/MVFR/IFR/LIFR from ceiling and visibility) on a lattice over the map view.

        The lattice is fixed to the globe and each point is cached for 30 minutes, so panning only asks for new points."""
        span = max(n - s, (e - w) * 0.75)
        step = next((st for st in (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0) if span / st <= 8), 5.0)
        s2, n2 = math.floor(max(-80, s) / step) * step, math.ceil(min(80, n) / step) * step
        w2, e2 = math.floor(w / step) * step, math.ceil(e / step) * step
        lats = [round(s2 + i * step, 3) for i in range(int(round((n2 - s2) / step)) + 1)]
        lons = [round(w2 + j * step, 3) for j in range(int(round((e2 - w2) / step)) + 1)]
        if len(lats) * len(lons) > 110:
            return {"error": "Zoom in a little for the forecast flying conditions.", "lats": [], "lons": [], "grid": []}
        cache = getattr(self, "_cond_cache", None)
        if cache is None:
            cache = self._cond_cache = {}
        slot = int(time.time() // 1800)
        for k in [k for k, v in cache.items() if v[0] != slot]:
            del cache[k]
        want = [(la, ((lo + 180) % 360) - 180) for la in lats for lo in lons]
        missing = [p for p in dict.fromkeys(want) if p not in cache]
        err = None
        lv = "".join(f",cloud_cover_{L}hPa,geopotential_height_{L}hPa" for L in self.COND_LEVELS)
        if time.time() < data._om_down_until and len(missing) > 60:  # the backup forecast is one request per point
            return {"error": "Zoom in a little: the backup forecast covers a smaller area.", "lats": [], "lons": [], "grid": []}
        for i in range(0, len(missing), 60):
            chunk = missing[i:i + 60]
            url = ("https://api.open-meteo.com/v1/forecast?latitude=" + ",".join(f"{a:.3f}" for a, _ in chunk)
                   + "&longitude=" + ",".join(f"{b:.3f}" for _, b in chunk)
                   + "&hourly=temperature_2m,dew_point_2m,cloud_cover_low,visibility" + lv
                   + f"&forecast_hours={self.COND_HOURS}&timezone=UTC")
            try:
                raw = json.loads(data.forecast_get(url, timeout=25))
            except Exception as ex:
                self.log(f"Flying conditions forecast failed: {ex}")
                err = "Forecast not available right now."
                break
            if isinstance(raw, dict):
                raw = [raw]
            for p, r in zip(chunk, raw):
                cache[p] = (slot, self._cond_point(r))
        times = None
        grid = []
        for p in want:
            v = cache.get(p)
            grid.append(v[1]["c"] if v else None)
            if v and not times:
                times = v[1]["t"]
        return {"lats": lats, "lons": lons, "step": step, "grid": grid, "times": times or [], "error": err}

    def _cond_point(self, r):
        """Per hour: [category 0-3, ceiling ft above ground or None, visibility km]."""
        h = r.get("hourly") or {}
        ground = (r.get("elevation") or 0) * 3.28084
        out = []
        for i in range(len(h.get("time") or [])):
            g = lambda k: (h.get(k) or [None] * (i + 1))[i]
            col = []
            for L in self.COND_LEVELS:
                z, cc = g(f"geopotential_height_{L}hPa"), g(f"cloud_cover_{L}hPa")
                if z is not None and cc is not None:
                    col.append({"ft": z * 3.28084, "cc": cc})
            ceil = None
            for lo, hi, cc in briefing.layers(col, ground):
                if cc >= 60:
                    ceil = max(0, lo - ground)
                    break
            t, td, low = g("temperature_2m"), g("dew_point_2m"), g("cloud_cover_low") or 0
            if low >= 60 and t is not None and td is not None:  # low cloud the pressure levels are too coarse to place
                est = max(0, t - td) * 400
                ceil = est if ceil is None else min(ceil, est)
            vis = g("visibility")
            vis_sm = None if vis is None else vis / 1609.34
            cat = 0
            if (ceil is not None and ceil <= 3000) or (vis_sm is not None and vis_sm <= 5):
                cat = 1
            if (ceil is not None and ceil < 1000) or (vis_sm is not None and vis_sm < 3):
                cat = 2
            if (ceil is not None and ceil < 500) or (vis_sm is not None and vis_sm < 1):
                cat = 3
            out.append([cat, None if ceil is None else int(round(ceil / 100) * 100), None if vis is None else round(vis / 1000, 1)])
        return {"t": h.get("time") or [], "c": out}

    def winds(self, lat, lon, when):
        key = (round(lat, 1), round(lon, 1), when.strftime("%Y%m%d%H"))
        if key in self.wind_cache:
            return self.wind_cache[key]
        vars_ = ",".join(f"wind_speed_{p}hPa,wind_direction_{p}hPa,temperature_{p}hPa" for p, _ in LEVELS)
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.2f}&longitude={lon:.2f}&hourly={vars_}"
               f"&wind_speed_unit=kn&forecast_days=2&timezone=UTC")
        try:
            raw = json.loads(data.forecast_get(url, timeout=15))["hourly"]
            stamp = when.strftime("%Y-%m-%dT%H:00")
            idx = raw["time"].index(stamp) if stamp in raw["time"] else 0
            out = {p: {"dir": raw[f"wind_direction_{p}hPa"][idx], "kt": raw[f"wind_speed_{p}hPa"][idx],
                       "temp": raw[f"temperature_{p}hPa"][idx], "ft": ft} for p, ft in LEVELS}
        except Exception as e:
            self.log(f"Winds aloft lookup failed: {e}")
            return None
        self.wind_cache[key] = out
        return out

    @staticmethod
    def wind_at(levels, alt):
        if not levels:
            return None
        pts = [levels[p] for p, _ in LEVELS if levels[p]["kt"] is not None and levels[p]["dir"] is not None]
        if not pts:
            return None

        def vec(w):
            r = math.radians(w["dir"])
            return w["kt"] * math.sin(r), w["kt"] * math.cos(r)
        if alt <= pts[0]["ft"]:
            lo = hi = pts[0]
        elif alt >= pts[-1]["ft"]:
            lo = hi = pts[-1]
        else:
            lo = max((w for w in pts if w["ft"] <= alt), key=lambda w: w["ft"])
            hi = min((w for w in pts if w["ft"] >= alt), key=lambda w: w["ft"])
        f = 0 if hi["ft"] == lo["ft"] else (alt - lo["ft"]) / (hi["ft"] - lo["ft"])
        (x1, y1), (x2, y2) = vec(lo), vec(hi)
        x, y = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
        temp = lo["temp"] + (hi["temp"] - lo["temp"]) * f if lo["temp"] is not None and hi["temp"] is not None else None
        return {"dir": round((math.degrees(math.atan2(x, y)) + 360) % 360), "kt": round(math.hypot(x, y)), "temp": temp}

    def _vfr_route(self, job, start, end, notes):
        """Insert the sights most worth seeing (detouring when they're worth it), then navaids on the way as checkpoints."""
        direct = geo.dist_nm(start["lat"], start["lon"], end["lat"], end["lon"])
        route = [start, end]
        if direct < 8:
            return route
        per_point = max(1.5, direct * 0.04)          # nm of detour allowed per point of interest value
        budget = max(8.0, direct * 0.30)             # total extra distance for the whole leg
        corridor = min(25.0, max(8.0, direct * 0.2))  # how far off the line we'll look

        cands, seen = [], set()
        pool = list(job.get("pois", [])) + list(job.get("dest_pois", []))
        if self.landmarks:
            try:
                pool += self.landmarks.along(start, end, corridor_nm=corridor, fetch=False, skip_ends_nm=3)
            except Exception as ex:
                self.log(f"Landmark search for the route failed: {ex}")
        for p in pool:
            key = (p["name"], round(p["lat"], 2))
            if key in seen:
                continue
            seen.add(key)
            if geo.dist_nm(start["lat"], start["lon"], p["lat"], p["lon"]) < 3 or geo.dist_nm(end["lat"], end["lon"], p["lat"], p["lon"]) < 2.5:
                continue
            cands.append((self._value(p), p))
        cands.sort(key=lambda c: -c[0])

        def cheapest(pt):
            best = None
            for i in range(len(route) - 1):
                a, b = route[i], route[i + 1]
                extra = (geo.dist_nm(a["lat"], a["lon"], pt["lat"], pt["lon"]) + geo.dist_nm(pt["lat"], pt["lon"], b["lat"], b["lon"])
                         - geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"]))
                if best is None or extra < best[0]:
                    best = (extra, i + 1)
            return best

        def too_close(pt, gap):
            return any(geo.dist_nm(r["lat"], r["lon"], pt["lat"], pt["lon"]) < gap for r in route)

        used, visuals = 0.0, 0
        for value, p in cands:
            if visuals >= 6:
                break
            extra, at = cheapest(p)
            if extra > value * per_point or used + extra > budget or too_close(p, 3):
                continue
            used += extra
            visuals += 1
            label = p.get("label") or ("MSFS POI" if p.get("source") == "msfs" else "")
            route.insert(at, {"kind": "visual", "name": p["name"], "ident": _wp_name(p["name"], visuals), "lat": p["lat"],
                              "lon": p["lon"], "label": label, "source": p.get("source", "osm"), "detour": round(extra, 1)})

        # navaids close to the planned line make good checkpoints (and something to tune)
        navs = []
        for k in range(len(route) - 1):
            a, b = route[k], route[k + 1]
            leg = geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            mid = geo.destination(a["lat"], a["lon"], geo.bearing(a["lat"], a["lon"], b["lat"], b["lon"]), leg / 2)
            for d, n in self.world.navaids_near(mid[0], mid[1], leg / 2 + 6):
                navs.append(n)
        added = 0
        for n in sorted({x["ident"] + x["type"]: x for x in navs}.values(), key=lambda n: not n["type"].startswith("VOR")):
            if added >= 3:
                break
            extra, at = cheapest(n)
            if extra > 4 or too_close(n, 4):
                continue
            if geo.dist_nm(start["lat"], start["lon"], n["lat"], n["lon"]) < 3 or geo.dist_nm(end["lat"], end["lon"], n["lat"], n["lon"]) < 3:
                continue
            added += 1
            used += extra
            route.insert(at, {"kind": "nav", "name": f"{n['name']} {n['type']} {n['freq']}", "ident": n["ident"],
                              "lat": n["lat"], "lon": n["lon"], "freq": n["freq"], "detour": round(extra, 1)})
        k = 0
        for r in route:  # number visual waypoints in flying order
            if r["kind"] == "visual":
                k += 1
                r["ident"] = _wp_name(r["name"], k)
        total = sum(geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(route, route[1:]))
        if total - direct > 1:
            sights = [r["name"] for r in route if r["kind"] == "visual" and r.get("detour", 0) >= 2]
            if sights:
                notes.append(f"The route detours {total - direct:.0f} nm to take in {', '.join(sights[:3])}.")
        return route

    @staticmethod
    def _value(p):
        label = (p.get("label") or "").lower()
        kind = p.get("kind") or ""
        if "photogrammetry" in label:
            return 5.0
        if p.get("source") == "msfs":
            return 4.0
        if kind in ("historic:castle", "man_made:lighthouse", "natural:volcano", "waterway:waterfall", "natural:glacier",
                    "waterway:dam", "natural:cape", "historic:fort", "historic:monastery", "building:cathedral", "natural:arch"):
            return 3.0
        if kind in ("tourism:viewpoint", "historic:ruins", "bridge", "historic:archaeological_site"):
            return 2.0
        return 1.2

    def _profile(self, route, samples, elev, at_nm, total, cruise, msa, climb_nm, desc_nm, dep, dest, route_wx):
        """Everything the side view needs: terrain under the route, your altitude, MSA, cloud and freezing level, and what clashes."""
        by = {}
        for (lat, lon), e, d in zip(samples, elev, at_nm):
            if e is None:
                continue
            key = round(d, 1)
            by[key] = max(by.get(key, -1e9), e)
        terrain = sorted(by.items())
        if len(terrain) > 160:
            stepk = len(terrain) / 160
            terrain = [terrain[int(i * stepk)] for i in range(160)] + [terrain[-1]]
        marks, cum = [], 0.0
        for i, r in enumerate(route):
            if i:
                cum += geo.dist_nm(route[i - 1]["lat"], route[i - 1]["lon"], r["lat"], r["lon"])
            marks.append({"nm": round(cum, 1), "ident": r.get("ident"), "name": r.get("name"), "kind": r.get("kind")})
        alt = [[0, round(dep["elev"])], [round(climb_nm, 1), cruise], [round(total - desc_nm, 1), cruise], [round(total, 1), round(dest["elev"] + 1000)]]
        clouds, aloft, frz = [], [], None
        pts = (route_wx or {}).get("points", [])
        nms = [(p.get("frac") or 0) * total for p in pts]
        for k, p in enumerate(pts):
            a = 0 if k == 0 else (nms[k - 1] + nms[k]) / 2
            b = total if k == len(pts) - 1 else (nms[k] + nms[k + 1]) / 2
            seg = {"nm": round(nms[k], 1), "from": round(a, 1), "to": round(b, 1)}
            if p.get("layers") is not None:
                for lo, hi, cc in p["layers"]:
                    clouds.append({**seg, "base": lo, "top": hi, "cover": cc})
                # grey sky with nothing low enough to draw: say so along the top of the side view
                if (p.get("sky") or 0) >= 60 and not any(lo < cruise + 4000 for lo, hi, cc in p["layers"]):
                    above = [lo for lo, hi, cc in p["layers"] if cc >= 30]
                    aloft.append({**seg, "base": min(above) if above else None, "cover": p.get("sky")})
            elif p.get("base_msl") is not None and (p.get("low_cloud") or 0) >= 50:
                clouds.append({**seg, "base": p["base_msl"], "top": p["base_msl"] + 1500, "cover": p["low_cloud"]})
            if p.get("freezing_ft") is not None:
                frz = p["freezing_ft"] if frz is None else min(frz, p["freezing_ft"])
        issues = []
        # terrain closer than 500 ft below you in the cruise portion
        close = [d for d, e in terrain if climb_nm < d < total - desc_nm and e > cruise - 500]
        if close:
            issues.append({"kind": "terrain", "from": min(close), "to": max(close),
                           "text": f"Terrain comes within 500 ft of {cruise:,} ft between {min(close):.0f} and {max(close):.0f} nm."})
        # cloud: at or above the base where it's broken or overcast
        if clouds:
            hit = [c for c in clouds if cruise > c["base"] - 500 and c["cover"] >= 50]
            if hit:
                a_nm = min(c["from"] for c in hit)
                b_nm = max(c["to"] for c in hit)
                base = min(c["base"] for c in hit)
                issues.append({"kind": "cloud", "from": round(a_nm, 1), "to": round(b_nm, 1), "base": base,
                               "text": (f"At {cruise:,} ft you'd be in cloud (base about {base:,} ft) for roughly {b_nm - a_nm:.0f} nm."
                                    if any(cruise < c["top"] + 500 for c in hit) else
                                    f"At {cruise:,} ft you'd be on top of a cloud layer (tops about {max(c['top'] for c in hit):,} ft) for roughly {b_nm - a_nm:.0f} nm, with no view of the ground.")})
        # scattered cloud at your height: you'll pass near or through some, so don't call the route clear
        if clouds and not any(i["kind"] == "cloud" for i in issues):
            near = [c for c in clouds if 25 <= c["cover"] < 50 and c["base"] - 500 < cruise < c["top"] + 500]
            if near:
                a_nm, b_nm = min(c["from"] for c in near), max(c["to"] for c in near)
                lo, hi = min(c["base"] for c in near), max(c["top"] for c in near)
                issues.append({"kind": "sct", "from": round(a_nm, 1), "to": round(b_nm, 1), "base": lo,
                               "text": f"Scattered cloud from about {lo:,} to {hi:,} ft between {a_nm:.0f} and {b_nm:.0f} nm: at {cruise:,} ft you'll pass near or through some."})
        hazards = self._hazards(route_wx, total, terrain)
        at_cruise = lambda h: h["lo"] - 300 <= cruise <= h["hi"] + 300
        ice = [h for h in hazards if h["kind"] == "ice" and at_cruise(h)]
        if ice:
            a_nm, b_nm = min(h["from"] for h in ice), max(h["to"] for h in ice)
            issues.append({"kind": "ice", "from": a_nm, "to": b_nm, "lo": min(h["lo"] for h in ice), "hi": max(h["hi"] for h in ice),
                           "text": f"Icing conditions at {cruise:,} ft between {a_nm:.0f} and {b_nm:.0f} nm: cloud or moist air between 0 and -15 \u00b0C."})
        elif frz is not None and cruise >= frz - 500 and not any(h["kind"] == "ice" for h in hazards) and \
                any(c["cover"] >= 50 and c["base"] <= frz + 3000 and c["top"] >= frz - 1500 for c in clouds):
            issues.append({"kind": "ice", "text": f"Freezing level around {frz:,} ft, close to your cruise altitude, with cloud about."})
        turb = [h for h in hazards if h["kind"] in ("turb", "wave", "low_turb") and at_cruise(h)]
        if turb:
            a_nm, b_nm = min(h["from"] for h in turb), max(h["to"] for h in turb)
            why = {"turb": "the wind changes quickly with height", "wave": "strong wind over high ground, so mountain waves and downdrafts are possible",
                   "low_turb": "gusty wind near the ground"}
            kinds = list(dict.fromkeys(h["kind"] for h in turb))
            issues.append({"kind": "turb", "from": a_nm, "to": b_nm,
                           "text": f"Bumpy at {cruise:,} ft between {a_nm:.0f} and {b_nm:.0f} nm: " + "; ".join(why[k] for k in kinds) + "."})
        storms = [h for h in hazards if h["kind"] == "storm"]
        if storms:
            a_nm, b_nm = min(h["from"] for h in storms), max(h["to"] for h in storms)
            sure = any(h["level"] == "forecast" for h in storms)
            issues.append({"kind": "storm", "from": a_nm, "to": b_nm, "level": "forecast" if sure else "possible",
                           "text": (f"Thunderstorms forecast between {a_nm:.0f} and {b_nm:.0f} nm. Keep well clear of them."
                                    if sure else f"Unstable air between {a_nm:.0f} and {b_nm:.0f} nm: showers or storms could build while you fly.")})
        fixes = []
        cloud = next((i for i in issues if i["kind"] == "cloud"), None)
        if cloud:
            under = int((cloud["base"] - 500) // 500 * 500)
            if under >= msa:
                fixes.append({"action": "alt", "value": under, "title": f"Cruise at {under:,} ft, under the cloud",
                              "detail": f"Clears the minimum safe altitude of {msa:,} ft"})
            else:
                fixes.append({"action": "shape", "value": "low", "title": "Follow the lower ground",
                              "detail": f"The cloud base is below the safe altitude of {msa:,} ft on this line"})
            later = (route_wx or {}).get("later") or []
            for l in later:
                if l.get("lowest_base") is None or l["lowest_base"] > cloud["base"] + 500:
                    fixes.append({"action": "later", "value": l["offset"], "title": f"Leave {l['offset'] // 60} hour{'s' if l['offset'] > 60 else ''} later",
                                  "detail": "The forecast clears along the route" if l.get("lowest_base") is None else f"Forecast cloud base lifts to about {l['lowest_base']:,} ft"})
                    break
        ice_i = next((i for i in issues if i["kind"] == "ice" and "lo" in i), None)
        if ice_i:
            below = int((ice_i["lo"] - 500) // 500 * 500)
            above = int(math.ceil((ice_i["hi"] + 500) / 500) * 500)
            if below >= msa and not any(f["action"] == "alt" and f["value"] <= below for f in fixes):
                fixes.append({"action": "alt", "value": below, "title": f"Cruise at {below:,} ft, below the icing", "detail": f"Clears the minimum safe altitude of {msa:,} ft"})
            elif above <= 12500:
                fixes.append({"action": "alt", "value": above, "title": f"Climb to {above:,} ft, above the icing", "detail": "Only if you can stay clear of cloud on the way up"})
        turb_i = next((i for i in issues if i["kind"] == "turb"), None)
        if turb_i and not fixes:
            for cand in sorted({int(c) for c in range(int(math.ceil(msa / 500) * 500), 12501, 500)}, key=lambda c: abs(c - cruise)):
                if cand != cruise and not any(h["kind"] in ("turb", "wave", "low_turb", "ice") and h["lo"] - 300 <= cand <= h["hi"] + 300 for h in hazards):
                    fixes.append({"action": "alt", "value": cand, "title": f"Cruise at {cand:,} ft for a smoother ride", "detail": "The forecast is calmer at that height"})
                    break
        storm_i = next((i for i in issues if i["kind"] == "storm"), None)
        if storm_i:
            for l in (route_wx or {}).get("later") or []:
                if not l.get("storms"):
                    fixes.append({"action": "later", "value": l["offset"], "title": f"Leave {l['offset'] // 60} hour{'s' if l['offset'] > 60 else ''} later",
                                  "detail": "The storm risk has passed by then, according to the forecast"})
                    break
        if any(i["kind"] == "terrain" for i in issues):
            fixes.insert(0, {"action": "alt", "value": int(math.ceil((msa) / 500) * 500), "title": f"Climb to {int(math.ceil(msa / 500) * 500):,} ft",
                             "detail": "Back above the minimum safe altitude"})
        return {"terrain": terrain, "marks": marks, "alt": alt, "cruise": cruise, "msa": msa, "clouds": clouds, "aloft": aloft,
                "freezing": frz, "total": round(total, 1), "issues": issues, "fixes": fixes, "hazards": hazards,
                "wx": "ok" if pts else "none", "wx_source": (route_wx or {}).get("source"),
                "cloud_metars": sorted({p["cloud_src"] for p in pts if p.get("cloud_src")})}

    @staticmethod
    def _hazards(route_wx, total, terrain):
        """Icing, turbulence and storm bands along the route, each over the stretch its forecast point covers."""
        pts = [p for p in (route_wx or {}).get("points", []) if p.get("haz")]
        if not pts:
            return []
        nms = [(p.get("frac") or 0) * total for p in pts]
        raw = []
        for k, (p, nm) in enumerate(zip(pts, nms)):
            a = 0 if k == 0 else (nms[k - 1] + nm) / 2
            b = total if k == len(pts) - 1 else (nm + nms[k + 1]) / 2
            hz = p["haz"]
            for lo, hi in hz.get("ice") or []:
                raw.append(("ice", a, b, lo, hi, None))
            for lo, hi in hz.get("turb") or []:
                raw.append(("turb", a, b, lo, hi, None))
            if hz.get("low_turb"):
                raw.append(("low_turb", a, b, hz["low_turb"][0], hz["low_turb"][1], None))
            if hz.get("storm"):
                raw.append(("storm", a, b, 0, 40000, hz["storm"]))
            seg = [e for d, e in terrain if a <= d <= b]
            if seg and (hz.get("wind_mid") or 0) >= 25 and max(seg) >= 2500 and max(seg) - min(seg) >= 1500:
                ridge = round(max(seg) / 100) * 100
                raw.append(("wave", a, b, ridge, ridge + 5000, None))
        # join the same hazard across neighboring stretches when the heights overlap
        out = []
        for kind, a, b, lo, hi, lvl in sorted(raw, key=lambda r: (r[0], r[1])):
            m = next((h for h in out if h["kind"] == kind and abs(h["to"] - a) < 0.5 and lo <= h["hi"] + 500 and hi >= h["lo"] - 500), None)
            if m:
                m["to"], m["lo"], m["hi"] = round(b, 1), min(m["lo"], lo), max(m["hi"], hi)
                if lvl == "forecast":
                    m["level"] = "forecast"
                m["parts"].append([round(a, 1), round(b, 1), lo, hi])
            else:
                out.append({"kind": kind, "from": round(a, 1), "to": round(b, 1), "lo": lo, "hi": hi, "level": lvl, "parts": [[round(a, 1), round(b, 1), lo, hi]]})
        return out

    def _nav_route(self, dep, dest, min_total=25, spacing=20):
        total = geo.dist_nm(dep["lat"], dep["lon"], dest["lat"], dest["lon"])
        if total < min_total:
            return []
        brg = geo.bearing(dep["lat"], dep["lon"], dest["lat"], dest["lon"])
        cands, seen = [], set()
        for k in range(0, int(total / 15) + 1):
            p = geo.destination(dep["lat"], dep["lon"], brg, min(total, k * 15))
            for d, n in self.world.navaids_near(p[0], p[1], 15):
                key = n["ident"] + n["type"]
                if key in seen:
                    continue
                seen.add(key)
                xt, frac = geo.cross_track_nm(n["lat"], n["lon"], dep["lat"], dep["lon"], dest["lat"], dest["lon"])
                if xt <= 8 and 0 <= frac <= 1:
                    da = frac * total
                    if da >= min(10, spacing) and total - da >= min(10, spacing):
                        cands.append((frac, xt, n))
        vors = [c for c in cands if c[2]["type"].startswith("VOR")]
        pool = sorted(vors if len(vors) >= 1 else cands, key=lambda c: c[0])
        out, last_nm = [], 0
        for frac, xt, n in pool:
            if frac * total - last_nm < spacing or len(out) >= 5:
                continue
            out.append({"kind": "nav", "name": f"{n['name']} {n['type']} {n['freq']}", "ident": n["ident"],
                        "lat": n["lat"], "lon": n["lon"]})
            last_nm = frac * total
        return out

    def _arrival(self, job, route, prof, var, when, freqs, ete_min):
        """What you need to know for the last ten miles."""
        from datetime import timedelta
        dest = job["dest"]
        ap = self.world.airports.get(dest["ident"])
        wx = job.get("weather")
        elev = dest["elev"]
        tpa = int(round((elev + (1500 if prof.get("engine") == "turboprop" else 1000)) / 100.0) * 100)
        compass = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]

        def dir_name(b):
            return compass[int(((b % 360) + 22.5) // 45) % 8]

        # runways, both ends, with the wind
        ends = []
        for rw in (ap["runways"] if ap else []):
            if rw["surface"] == "water":
                continue
            for end, hdg in ((rw["le"], rw["le_hdg"]), (rw["he"], rw["he_hdg"])):
                hw, xw = geo.wind_components(wx.get("wdir") if wx else None, wx.get("wspd") if wx else None, hdg)
                ends.append({"end": end, "hdg_true": hdg, "hdg_mag": None if hdg is None else round((hdg - var) % 360),
                             "length": round(rw["length"]), "width": round(rw["width"] or 0), "surface": rw["surface"],
                             "surface_raw": rw["surface_raw"], "lighted": rw["lighted"],
                             "headwind": None if hw is None else round(hw), "crosswind": None if xw is None else round(xw)})
        best = None
        if ends:
            pref = (job.get("runway") or {}).get("end")
            best = max(ends, key=lambda e: ((e["headwind"] if e["headwind"] is not None else 0) * 2 - (e["crosswind"] or 0)
                                            + (0.5 if e["end"] == pref and not wx else 0) + e["length"] / 20000))

        # terrain around the field
        ring_pts = [(r, b, geo.destination(dest["lat"], dest["lon"], b, r)) for r in (2.5, 5.0) for b in range(0, 360, 45)]
        elev_ring = self.elevations([p for _, _, p in ring_pts])
        terrain = None
        sea_dirs = []
        if any(e is not None for e in elev_ring):
            hi = max(((e, b, r) for (r, b, _), e in zip(ring_pts, elev_ring) if e is not None), key=lambda x: x[0])
            terrain = {"highest": round(hi[0]), "above_field": round(hi[0] - elev), "dir": dir_name(hi[1]), "nm": hi[2]}
            sea_dirs = sorted({dir_name(b) for (r, b, _), e in zip(ring_pts, elev_ring) if e is not None and e <= 5 and r == 2.5})

        # how you'll arrive
        prev = route[-2] if len(route) > 1 else route[0]
        inbound_true = geo.bearing(prev["lat"], prev["lon"], dest["lat"], dest["lon"])
        inbound_mag = round((inbound_true - var) % 360)
        join = None
        side_note = None
        if best and best["hdg_true"] is not None:
            rel = geo.angle_diff(inbound_true, best["hdg_true"])
            name = f"runway {best['end']}"
            if rel <= 35:
                join = f"You're arriving roughly in line with {name}: a straight-in from about 5 nm, descending to be at {tpa:,} ft by 3 nm out."
            elif rel >= 145:
                join = f"You'll arrive from the far end of {name}. Overfly the field at {tpa + 500:,} ft, look at the windsock, then descend on the dead side and join downwind."
            else:
                join = f"You'll come in from the side of {name}: join crosswind or midfield downwind at {tpa:,} ft."
            # which side is higher? suggest the circuit on the lower side
            if any(e is not None for e in elev_ring):
                rs, ls = [], []
                for (r, b, _), e in zip(ring_pts, elev_ring):
                    if e is None or r != 2.5:
                        continue
                    rel_b = (b - best["hdg_true"]) % 360
                    if 20 < rel_b < 160:
                        rs.append(e)
                    elif 200 < rel_b < 340:
                        ls.append(e)
                if rs and ls and abs(max(rs) - max(ls)) > 700:
                    high, low = ("right", "left") if max(rs) > max(ls) else ("left", "right")
                    circuit = "left-hand" if low == "left" else "right-hand"
                    side_note = (f"Terrain is higher on the {high} side of {name} ({max(max(rs), max(ls)):,.0f} ft). "
                                 f"Expect a {circuit} circuit to stay clear of it, but check the local procedure.")

        # sunset
        eta = when + timedelta(minutes=ete_min or 0)
        sunset = None
        t = when
        prev_up = geo.solar_elevation(dest["lat"], dest["lon"], t) > -0.833
        for _ in range(int(20 * 60 / 5)):
            t2 = t + timedelta(minutes=5)
            up = geo.solar_elevation(dest["lat"], dest["lon"], t2) > -0.833
            if prev_up and not up:
                sunset = t2.strftime("%H:%MZ")
                break
            t, prev_up = t2, up

        tips = []
        if ap and ap.get("rw_unknown"):
            tips.append(f"Nobody published this strip's details. Overfly at about {elev + 1000:,.0f} ft first and check the surface, length and obstacles before committing.")
        if best:
            if best["surface"] != "paved":
                tips.append("Grass or dirt: find the windsock and the mown strip early, and use soft-field technique on landing and takeoff.")
            if best["length"] and best["length"] < prof["min_runway_ft"] * 1.3:
                tips.append(f"Short runway ({best['length']:,} ft): fly the approach speed exactly, aim for the threshold and decide early on a go-around.")
            if best["width"] and best["width"] <= 45:
                tips.append(f"Only {best['width']} ft wide: it will look long and thin on final. Keep it centered.")
            if best["crosswind"] and best["crosswind"] >= 10:
                tips.append(f"About {best['crosswind']} kt of crosswind: wing low into the wind, and keep the upwind aileron in as you slow down.")
        if terrain and terrain["above_field"] >= 1500:
            tips.append(f"High ground rises to {terrain['highest']:,} ft to the {terrain['dir']}. Expect sink on the lee side and plan your go-around away from it.")
        if sea_dirs:
            tips.append("Water close to the field: sea breezes can swing the wind on short final.")
        if elev > 3000:
            tips.append(f"Field elevation {elev:,} ft: lean the mixture for taxi and expect a longer takeoff roll on the way out.")
        if job.get("night"):
            tips.append("Night arrival: if the lights are pilot-controlled, click the mic on the CTAF.")
        if job.get("rules") == "IFR":
            tips.append("Brief the approach plate, minimums and missed approach before the descent.")

        return {
            "field": dest["name"], "elev": elev, "tpa": tpa, "inbound_mag": inbound_mag,
            "runway": best, "runways": ends, "terrain": terrain, "sea": sea_dirs,
            "join": join, "circuit_note": side_note, "freqs": freqs, "sunset": sunset,
            "weather": wx and {"raw": wx.get("raw"), "station": wx.get("station"), "dist": wx.get("dist"), "cat": wx.get("cat")},
            "tips": tips[:5],
        }

    def mag_var(self, lat, lon):
        for d, n in self.world.navaids_near(lat, lon, 250):
            if n.get("var") is not None:
                return n["var"]
        return 0.0

    # ---------- the plan ----------
    # ---------- route shapes ----------
    def _low_route(self, start, end):
        """Follow the lowest ground between two points: a small grid of terrain samples and a minimax path through it."""
        L = geo.dist_nm(start["lat"], start["lon"], end["lat"], end["lon"])
        if L < 12:
            return [start, end]
        brg = geo.bearing(start["lat"], start["lon"], end["lat"], end["lon"])
        cols = [i / 8 for i in range(1, 8)]
        spread = min(14.0, L * 0.3)
        offs = [spread * k / 4 for k in range(-4, 5)]
        grid = []
        for f in cols:
            c = geo.destination(start["lat"], start["lon"], brg, L * f)
            grid.append([geo.destination(c[0], c[1], (brg + 90) % 360, o) if o else c for o in offs])
        flat = [p for col in grid for p in col]
        elev = self.elevations(flat)
        if not any(e is not None for e in elev):
            return [start, end]
        E = [[(elev[i * len(offs) + j] or 0) for j in range(len(offs))] for i in range(len(cols))]
        n = len(offs)
        # best[j] = (worst terrain so far, sideways moves) ending in row j; step at most 2 rows per column
        best = [(E[0][j], abs(j - n // 2), [j]) for j in range(n)]
        for i in range(1, len(cols)):
            nxt = []
            for j in range(n):
                opts = [(max(best[k][0], E[i][j]), best[k][1] + abs(j - k), best[k][2] + [j]) for k in range(max(0, j - 2), min(n, j + 3))]
                nxt.append(min(opts, key=lambda x: (round(x[0] / 150), x[1])))
            best = nxt
        worst, moves, path = min(best, key=lambda x: (round(x[0] / 150), x[1] + abs(x[2][-1] - n // 2)))
        direct_worst = max(E[i][n // 2] for i in range(len(cols)))
        if direct_worst - worst < 300:
            return [start, end]  # the straight line is already about as low as it gets
        # keep only the bends that matter (Ramer-Douglas-Peucker on the grid path, about 2.5 nm tolerance)
        pts = [(start["lat"], start["lon"])] + [grid[i][j] for i, j in enumerate(path)] + [(end["lat"], end["lon"])]

        def rdp(seq):
            if len(seq) < 3:
                return seq
            a, b = seq[0], seq[-1]
            far, idx = 0.0, 0
            for i in range(1, len(seq) - 1):
                xt, _ = geo.cross_track_nm(seq[i][0], seq[i][1], a[0], a[1], b[0], b[1])
                if xt > far:
                    far, idx = xt, i
            if far < 2.5:
                return [a, b]
            return rdp(seq[: idx + 1])[:-1] + rdp(seq[idx:])

        keep = rdp(pts)[1:-1][:3]
        route = [start] + [{"kind": "user", "name": "Low ground", "ident": f"LOW{k + 1}", "lat": p[0], "lon": p[1]} for k, p in enumerate(keep)]
        return route + [end]

    def route_for(self, job, shape, notes=None):
        """The route for a shape: direct, scenic (the default tour), low (lowest ground) or navaids."""
        notes = notes if notes is not None else []
        dep, dest = job["dep"], job["dest"]
        start = {"kind": "airport", "name": dep["name"], "ident": dep["code"] or dep["ident"], "lat": dep["lat"], "lon": dep["lon"]}
        end = {"kind": "airport", "name": dest["name"], "ident": dest["code"] or dest["ident"], "lat": dest["lat"], "lon": dest["lon"]}
        if shape == "direct":
            return [start, end]
        if shape == "navaids":
            return [start] + self._nav_route(dep, dest, min_total=12, spacing=8) + [end]
        if shape == "low":
            return self._low_route(start, end)
        return self._vfr_route(job, start, end, notes)

    def ideas(self, job, prof):
        """The four route shapes side by side, quickly (no weather)."""
        out = []
        tas = prof["cruise_kt"]
        for shape in ("direct", "scenic", "low", "navaids"):
            try:
                r = self.route_for(job, shape)
            except Exception as e:
                self.log(f"Route idea {shape} failed: {e}")
                continue
            nm = sum(geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(r, r[1:]))
            mins = nm / tas * 60 + 4
            out.append({"shape": shape, "nm": round(nm, 1), "min": round(mins), "gal": round(prof.get("gph", 10) * mins / 60, 1),
                        "sights": sum(1 for x in r if x["kind"] == "visual"), "navaids": sum(1 for x in r if x["kind"] == "nav"),
                        "route": [{k: x[k] for k in ("kind", "name", "ident", "lat", "lon") if k in x} for x in r]})
        return out

    def detours(self, job, route, limit=6):
        """Sights near the route that aren't in it yet, with what each would add."""
        if len(route) < 2:
            return []
        dep, dest = route[0], route[-1]
        L = geo.dist_nm(dep["lat"], dep["lon"], dest["lat"], dest["lon"])
        corridor = min(20.0, max(8.0, L * 0.25))
        pool = list(job.get("pois", [])) + list(job.get("dest_pois", []))
        if self.landmarks:
            try:
                pool += self.landmarks.along(dep, dest, corridor_nm=corridor, fetch=False, skip_ends_nm=2)
            except Exception:
                pass
        mid = geo.destination(dep["lat"], dep["lon"], geo.bearing(dep["lat"], dep["lon"], dest["lat"], dest["lon"]), L / 2)
        try:
            pool += [p for p in self.world.msfs_near(mid[0], mid[1], L / 2 + corridor)]
        except Exception:
            pass
        have = {(r.get("name"), round(r["lat"], 2)) for r in route}
        seen, out = set(), []
        for p in pool:
            key = (p["name"], round(p["lat"], 2))
            if key in seen or key in have:
                continue
            seen.add(key)
            if any(geo.dist_nm(r["lat"], r["lon"], p["lat"], p["lon"]) < 2 for r in route):
                continue
            if geo.dist_nm(dep["lat"], dep["lon"], p["lat"], p["lon"]) < 3 or geo.dist_nm(dest["lat"], dest["lon"], p["lat"], p["lon"]) < 2.5:
                continue
            best = None
            for i in range(len(route) - 1):
                a, b = route[i], route[i + 1]
                extra = (geo.dist_nm(a["lat"], a["lon"], p["lat"], p["lon"]) + geo.dist_nm(p["lat"], p["lon"], b["lat"], b["lon"])
                         - geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"]))
                if best is None or extra < best[0]:
                    best = (extra, i + 1)
            if best[0] > max(15.0, L * 0.4):
                continue
            v = self._value(p)
            label = p.get("label") or ("MSFS POI" if p.get("source") == "msfs" else "")
            out.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "label": label, "source": p.get("source", "osm"),
                        "kind": p.get("kind"), "wiki": p.get("wiki", ""), "extra_nm": round(best[0], 1), "at": best[1],
                        "score": v / max(1.0, best[0])})
        out.sort(key=lambda d: -d["score"])
        return out[:limit]

    def build(self, job, prof, when=None, pilot="", opts=None):
        opts = opts or {}
        when = when or datetime.now(timezone.utc)
        if opts.get("depart_offset_min"):
            from datetime import timedelta
            when = when + timedelta(minutes=int(opts["depart_offset_min"]))
        w = self.world
        dep, dest = job["dep"], job["dest"]
        notes = []
        ifr = job.get("rules") == "IFR"
        pick_alt = _ifr_alt if ifr else _hemi_alt
        corridor = 4.0 if ifr else 2.5

        # 1. route: VFR tours sights worth a detour and uses navaids as checkpoints; IFR goes navaid to navaid
        start = {"kind": "airport", "name": dep["name"], "ident": dep["code"] or dep["ident"], "lat": dep["lat"], "lon": dep["lon"]}
        end = {"kind": "airport", "name": dest["name"], "ident": dest["code"] or dest["ident"], "lat": dest["lat"], "lon": dest["lon"]}
        shape = opts.get("shape") or ("navaids" if ifr else "scenic")
        if opts.get("waypoints") is not None:
            mids = []
            for i, p in enumerate(opts["waypoints"]):
                kind = p.get("kind") if p.get("kind") in ("visual", "nav", "user", "airport") else "user"
                wp = {"kind": kind, "name": p.get("name") or p.get("ident") or f"Point {i + 1}", "lat": float(p["lat"]), "lon": float(p["lon"])}
                for k in ("label", "source", "freq", "wiki"):
                    if p.get(k):
                        wp[k] = p[k]
                wp["ident"] = p.get("ident") or _wp_name(wp["name"], i + 1)
                mids.append(wp)
            route = [start] + mids + [end]
            shape = "custom"
        elif ifr:
            route = [start] + self._nav_route(dep, dest) + [end]
        else:
            route = self.route_for(job, shape, notes)
        total = 0.0
        for a, b in zip(route, route[1:]):
            b["leg_nm"] = geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            b["tc"] = geo.bearing(a["lat"], a["lon"], b["lat"], b["lon"])
            total += b["leg_nm"]

        # 2. terrain along a 5 nm wide corridor
        step = max(2.0, total / 30)  # about 90 samples keeps it to one free API call
        samples, owner, at_nm = [], [], []
        cum0 = 0.0
        for i, (a, b) in enumerate(zip(route, route[1:])):
            n = max(1, int(b["leg_nm"] / step))
            for k in range(n + 1):
                lat, lon = geo.destination(a["lat"], a["lon"], b["tc"], b["leg_nm"] * k / n)
                for off in (-corridor, 0, corridor):
                    samples.append(geo.destination(lat, lon, (b["tc"] + 90) % 360, off) if off else (lat, lon))
                    owner.append(i)
                    at_nm.append(cum0 + b["leg_nm"] * k / n)
            cum0 += b["leg_nm"]
        elev = self.elevations(samples)
        have_terrain = any(e is not None for e in elev)
        leg_max = [None] * (len(route) - 1)
        top = (None, None)
        for (lat, lon), e, i in zip(samples, elev, owner):
            if e is None:
                continue
            if leg_max[i] is None or e > leg_max[i]:
                leg_max[i] = e
            if top[0] is None or e > top[0]:
                top = (e, (lat, lon))
        if not have_terrain:
            notes.append("Terrain data was unavailable, so the minimum altitude is only based on the airfields. Check the map before you go.")
            leg_max = [max(dep["elev"], dest["elev"])] * (len(route) - 1)
            top = (max(dep["elev"], dest["elev"]), None)
        mountainous = (top[0] or 0) >= 5000
        buffer = 2000 if mountainous else 1000
        leg_msa = [math.ceil(((m or 0) + buffer) / 100) * 100 for m in leg_max]
        msa = max(leg_msa)

        # 3. magnetic course and cruise altitude
        mid = geo.destination(dep["lat"], dep["lon"], route[1]["tc"], total / 2) if total else (dep["lat"], dep["lon"])
        var = self.mag_var(*mid)
        overall_tc = geo.bearing(dep["lat"], dep["lon"], dest["lat"], dest["lon"])
        overall_mc = (overall_tc - var) % 360
        ceiling = min(prof.get("ceiling_ft", 13000), 12500 if prof.get("engine") == "piston" else 25000)
        desired = max(msa, dep["elev"] + 1500, dest["elev"] + 1500)
        if ifr and total > 40:
            desired = max(desired, 4000)
        if total > 70:
            desired = max(desired, 4500)
        if total > 150:
            desired = max(desired, 6500)
        if shape == "low":
            desired = max(msa, dep["elev"] + 1500, dest["elev"] + 1500)
        cruise = pick_alt(desired, overall_mc)
        # short hops: don't plan a climb you'll never finish
        climb_fpm, climb_kt = prof.get("climb_fpm", 700), prof.get("climb_kt", 80)
        desc_fpm = prof.get("descent_fpm", 500)
        tas = prof["cruise_kt"]
        while True:
            need = (cruise - dep["elev"]) / climb_fpm * climb_kt / 60 + max(0, cruise - dest["elev"] - 1000) / desc_fpm * tas / 60
            lower = pick_alt(max(msa, dep["elev"] + 1500, dest["elev"] + 1500), overall_mc)
            if need <= total * 0.85 or cruise <= lower:
                break
            cruise -= 2000
        if cruise > ceiling:
            notes.append(f"Terrain pushes the cruise altitude to {cruise:,} ft, above what's sensible for this aircraft without oxygen. Consider following the valleys instead.")
        if msa > cruise:
            cruise = pick_alt(msa, overall_mc)
        chosen_alt = opts.get("cruise_alt")
        if chosen_alt:
            cruise = max(500, min(int(chosen_alt), 25000))
            cruise = int(round(cruise / 100) * 100)
            if cruise < msa:
                notes.insert(0, f"Your cruise altitude of {cruise:,} ft is below the minimum safe altitude of {msa:,} ft. Check the side view.")

        # 4. winds aloft
        levels = self.winds(mid[0], mid[1], when)
        wind = self.wind_at(levels, cruise)
        if not wind:
            notes.append("Winds aloft weren't available, so the times assume no wind.")
        wd, wk = (wind["dir"], wind["kt"]) if wind else (None, 0)

        # 5. navlog with top of climb and top of descent
        _, gs_climb = geo.wind_triangle(route[1]["tc"], climb_kt, wd, wk)
        climb_nm = (cruise - dep["elev"]) / climb_fpm * gs_climb / 60
        _, gs_desc = geo.wind_triangle(route[-1]["tc"], tas, wd, wk)
        desc_nm = max(0, cruise - dest["elev"] - 1000) / desc_fpm * gs_desc / 60
        if climb_nm + desc_nm > total:
            k = total / (climb_nm + desc_nm)
            climb_nm, desc_nm = climb_nm * k, desc_nm * k
            notes.append("Short hop: you'll start down almost as soon as you level off.")
        marks = [(climb_nm, "TOC", "Top of climb"), (total - desc_nm, "TOD", "Top of descent")]
        legs, cum, fuel_trip, time_trip = [], 0.0, 0.0, 0.0
        gph = prof.get("gph", 10)
        for i, (a, b) in enumerate(zip(route, route[1:])):
            start, end = cum, cum + b["leg_nm"]
            cuts = [start] + [m for m, _, _ in marks if start < m < end] + [end]
            for s0, s1 in zip(cuts, cuts[1:]):
                d = s1 - s0
                midp = (s0 + s1) / 2
                phase = "climb" if midp < climb_nm else ("descent" if midp > total - desc_nm else "cruise")
                spd = climb_kt if phase == "climb" else tas
                th, gs = geo.wind_triangle(b["tc"], spd, wd, wk)
                mins = d / gs * 60
                burn = gph * {"climb": 1.2, "cruise": 1.0, "descent": 0.7}[phase] * mins / 60
                mark = next(((code, label) for m, code, label in marks if abs(m - s1) < 1e-6), None)
                if mark:
                    lat, lon = geo.destination(a["lat"], a["lon"], b["tc"], s1 - start)
                    to = {"ident": mark[0], "name": mark[1], "kind": "marker", "lat": lat, "lon": lon}
                else:
                    to = b
                alt = cruise if phase == "cruise" or mark and mark[0] == "TOC" else None
                legs.append({
                    "to": to["ident"], "name": to["name"], "kind": to["kind"], "phase": phase,
                    "alt": alt if alt else (f"↗ {cruise:,}" if phase == "climb" else (f"↘ {dest['elev'] + 1000:,.0f}" if to is route[-1] else f"↘")),
                    "tc": round(b["tc"]), "mc": round((b["tc"] - var) % 360), "mh": round((th - var) % 360),
                    "dist": round(d, 1), "cum": round(s1, 1), "gs": round(gs), "ete": round(mins, 1),
                    "fuel": round(burn, 1), "msa": leg_msa[i],
                })
                fuel_trip += burn
                time_trip += mins
            cum = end

        # 6. fuel
        night = bool(job.get("night"))
        taxi = round(gph * 0.15, 1)
        reserve = round(gph * (0.75 if (night or ifr) else 0.5), 1)
        altn = job.get("alternate") if ifr else None
        alt_fuel = round(gph * (altn["dist_nm"] / tas + 0.15), 1) if altn else 0.0
        total_fuel = round(fuel_trip + taxi + reserve + alt_fuel, 1)
        usable = prof.get("fuel_gal")
        if usable and total_fuel > usable:
            notes.append(f"Fuel is tight: {total_fuel} gal needed with reserves, {usable} gal usable. Plan a fuel stop.")

        # 7. runways, frequencies, navaids
        dep_ap, dest_ap = w.airports.get(dep["ident"]), w.airports.get(dest["ident"])
        dep_rw = _runway_for_wind(dep_ap, job.get("dep_weather"))
        arr_rw = job.get("runway") if job.get("runway") and job["runway"].get("length") else None
        navs, seen = [], set()
        for p in (geo.destination(dep["lat"], dep["lon"], overall_tc, total * f / 6) for f in range(7)):
            for d, n in w.navaids_near(p[0], p[1], 25):
                if n["ident"] + n["type"] in seen:
                    continue
                xt, frac = geo.cross_track_nm(n["lat"], n["lon"], dep["lat"], dep["lon"], dest["lat"], dest["lon"])
                if xt <= 15 and -0.1 <= frac <= 1.1:
                    seen.add(n["ident"] + n["type"])
                    navs.append({"ident": n["ident"], "name": n["name"], "type": n["type"], "freq": n["freq"],
                                 "at_nm": round(max(0, min(1, frac)) * total), "off_nm": round(xt, 1)})
        navs.sort(key=lambda n: (n["off_nm"] > 6, n["at_nm"]))
        navs = sorted(navs[:6], key=lambda n: n["at_nm"])

        def freqs(ident):
            order = ["ATIS", "AFIS", "CTAF", "UNIC", "A/G", "TWR", "GND", "APP", "CLR DLVR", "DEP"]
            fl = w.freqs.get(ident, [])
            return sorted(fl, key=lambda f: order.index(f["type"]) if f["type"] in order else 99)[:6]

        # 8. density altitude at destination
        wx = job.get("weather")
        if wx and wx.get("temp") is not None:
            isa = 15 - 2 * dest["elev"] / 1000
            da = dest["elev"] + 120 * (wx["temp"] - isa)
            if da > dest["elev"] + 1500:
                notes.append(f"Density altitude at the destination is about {da:,.0f} ft ({wx['temp']:.0f} °C). Expect a longer landing roll and a lazy climb out.")
        if top[1] and have_terrain:
            notes.insert(0, f"Highest terrain within {corridor:g} nm of the route: {top[0]:,.0f} ft. "
                            f"{'Minimum IFR altitude' if ifr else 'Minimum safe altitude'} {msa:,} ft ({buffer:,} ft clearance).")
        if ifr:
            mins = job.get("minimums") or (500, 1.25)
            notes.insert(0, f"IFR. Expect a {job.get('approach') or 'instrument'} approach at {dest['name']} (typical minimums around {mins[0]} ft and {mins[1]} sm). "
                            "Check the actual procedures in the sim before you go.")
            notes.insert(1, "Onward's route goes navaid to navaid at a safe altitude. For airways, departures, arrivals and approaches, use Plan in SimBrief and import it in MSFS.")
            if altn:
                aw = altn.get("weather") or {}
                notes.insert(2, f"Alternate: {altn['code']} {altn['name']}, {altn['dist_nm']} nm away ({aw.get('cat', 'no report')}). Fuel for it is included.")
            else:
                notes.insert(2, "No alternate with good enough weather was found within 70 nm. Carry extra fuel and keep an eye on the weather.")
        if wind:
            notes.append(f"Winds aloft at {cruise:,} ft: {wind['dir']:03d}° at {wind['kt']} kt (Open-Meteo forecast).")

        arrival = self._arrival(job, route, prof, var, when, freqs(dest["ident"]), (time_trip or 0))
        try:
            route_wx = briefing.route_weather(route, time_trip, when, cruise, ifr, self.weather, dest, msa, self.log)
        except Exception as e:
            self.log(f"Route weather failed: {e}")
            route_wx = None
        dwx, awx = job.get("dep_weather") or {}, job.get("weather") or {}
        dep_ap = self.world.airports.get(dep["ident"])
        perf = {"takeoff": briefing.distances(prof, dep["elev"], dwx.get("temp"), (dep_rw or {}).get("headwind"),
                                               (dep_rw or {}).get("surface"), (dep_rw or {}).get("length"), "takeoff") if dep_rw else None,
                "landing": briefing.distances(prof, dest["elev"], awx.get("temp"), (arrival.get("runway") or {}).get("headwind"),
                                              (arrival.get("runway") or {}).get("surface"), (arrival.get("runway") or {}).get("length"), "landing")
                if arrival.get("runway") else None}
        for k, v in perf.items():
            if v and v.get("status") == "no":
                notes.insert(0, f"{'Takeoff' if k == 'takeoff' else 'Landing'} distance is about {v['need_ft']:,} ft over 50 ft, "
                                f"and the runway is only {v['runway_ft']:,} ft. Think hard about this one.")
        profile = self._profile(route, samples, elev, at_nm, total, cruise, msa, climb_nm, desc_nm, dep, dest, route_wx)
        try:
            detours = self.detours(job, route)
        except Exception as e:
            self.log(f"Detour search failed: {e}")
            detours = []
        return {
            "shape": shape, "opts": {k: v for k, v in opts.items() if k != "waypoints"}, "profile": profile, "detours": detours,
            "route_weather": route_wx, "performance": perf,
            "built_at": when.strftime("%Y-%m-%d %H:%MZ"), "cruise_alt": cruise, "msa": msa,
            "max_terrain": round(top[0]) if top[0] is not None else None, "mag_var": round(var, 1),
            "wind": wind, "route": [{k: r[k] for k in ("kind", "name", "ident", "lat", "lon", "label", "source", "detour", "freq", "wiki") if k in r} for r in route],
            "direct_nm": round(geo.dist_nm(dep["lat"], dep["lon"], dest["lat"], dest["lon"]), 1),
            "legs": legs, "total_nm": round(total, 1), "ete_min": round(time_trip),
            "fuel": {"taxi": taxi, "trip": round(fuel_trip, 1), "reserve": reserve, "total": total_fuel,
                     "usable": usable, "gph": gph, "reserve_min": 45 if (night or ifr) else 30, "alternate": alt_fuel},
            "rules": "IFR" if ifr else "VFR", "alternate": altn,
            "simbrief": simbrief_url(job, prof, cruise, dep_rw, arr_rw, pilot, when),
            "dep_runway": dep_rw, "arr_runway": arr_rw,
            "freqs": {"dep": freqs(dep["ident"]), "arr": freqs(dest["ident"])},
            "navaids": navs, "notes": notes, "mc": round(overall_mc),
            "arrival": arrival,
        }


def simbrief_url(job, prof, cruise, dep_rw, arr_rw, pilot, when):
    dep, dest = job["dep"], job["dest"]
    if not dep.get("code") or not dest.get("code"):
        return None
    q = {"orig": dep["code"], "dest": dest["code"], "type": prof.get("simbrief_type") or "C172",
         "fl": str(cruise), "units": "LBS", "resvrule": "45" if job.get("rules") == "IFR" else "30",
         "deph": when.strftime("%H"), "depm": when.strftime("%M"), "date": when.strftime("%d%b%y").upper(),
         "manualrmk": f"Onward: {job['title']}", "find_sidstar": "1" if job.get("rules") == "IFR" else "0"}
    if pilot:
        q["cpt"] = pilot
    if job.get("alternate"):
        q["altn"] = job["alternate"]["code"]
    if dep_rw and dep_rw.get("end"):
        q["origrwy"] = dep_rw["end"]
    if arr_rw and arr_rw.get("end"):
        q["destrwy"] = arr_rw["end"]
    return "https://dispatch.simbrief.com/options/custom?" + urllib.parse.urlencode(q)


def _wp_name(name, i):
    s = "".join(ch for ch in name.upper() if ch.isalnum() and ch.isascii())
    return (s[:6] or "VIS") + f"{i}"


def _runway_for_wind(ap, wx):
    if not ap or not ap["runways"]:
        return None
    best = None
    for rw in ap["runways"]:
        if rw["surface"] == "water":
            continue
        for end, hdg in ((rw["le"], rw["le_hdg"]), (rw["he"], rw["he_hdg"])):
            hw, xw = geo.wind_components(wx.get("wdir") if wx else None, wx.get("wspd") if wx else None, hdg)
            key = (-(hw or 0), xw or 0, -rw["length"])
            if best is None or key < best[0]:
                best = (key, {"end": end, "length": round(rw["length"]), "surface": rw["surface"],
                              "headwind": None if hw is None else round(hw), "crosswind": None if xw is None else round(xw)})
    return best[1] if best else None


def _rwy_tags(end):
    if not end:
        return ""
    digits = "".join(ch for ch in end if ch.isdigit())
    if not digits:
        return ""
    des = {"L": "LEFT", "R": "RIGHT", "C": "CENTER"}.get(end[-1].upper(), "NONE")
    return f"<RunwayNumberFP>{int(digits)}</RunwayNumberFP><RunwayDesignatorFP>{des}</RunwayDesignatorFP>"


def _gfp_coord(lat, lon):
    """Garmin user waypoint: N/S + 2 digits of degrees + 3 of minutes and tenths, E/W + 3 + 3 (67\u00b030.2'N -> N67302)."""
    def part(v, width, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = round((v - d) * 60, 1)
        if m >= 60:
            d, m = d + 1, 0.0
        return f"{h}{d:0{width}d}{int(round(m * 10)):03d}"
    return part(lat, 2, "N", "S") + part(lon, 3, "E", "W")


def _garmin_ident(code):
    """An airport code a Garmin database is likely to know (ICAO style), else None."""
    import re
    code = (code or "").strip().upper()
    return code if re.fullmatch(r"[A-Z0-9]{3,4}", code) else None


def gfp(job):
    """Garmin GTN flight plan (TDS GTNXi and other GTN units): one line, airports by code, everything else by lat/lon.

    Waypoints go in as coordinates on purpose: named ones that exist twice in the Garmin database import as "locked"."""
    dep, dest = job["dep"], job["dest"]
    plan = job.get("plan")
    mids = plan["route"][1:-1] if plan else [{"lat": p["lat"], "lon": p["lon"]} for p in job.get("pois", [])[:5]]
    parts = [_garmin_ident(dep.get("code")) or _gfp_coord(dep["lat"], dep["lon"])]
    parts += [_gfp_coord(w["lat"], w["lon"]) for w in mids]
    parts.append(_garmin_ident(dest.get("code")) or _gfp_coord(dest["lat"], dest["lon"]))
    return "FPN/RI:" + ":".join("F:" + x for x in parts)


def gfp_name(job):
    return f'{job["dep"].get("code") or "STRIP"}-{job["dest"].get("code") or "STRIP"}.gfp'.replace("/", "-")


def pln(job):
    """MSFS flight plan file. Uses the built plan when there is one."""
    dep, dest = job["dep"], job["dest"]
    plan = job.get("plan")
    alt = plan["cruise_alt"] if plan else job["cruise_alt"]
    visuals = plan["route"][1:-1] if plan else \
        [{"ident": _wp_name(p["name"], i + 1), "lat": p["lat"], "lon": p["lon"], "name": p["name"]} for i, p in enumerate(job.get("pois", [])[:5])]
    dep_rw = (plan or {}).get("dep_runway") or {}
    arr_rw = (plan or {}).get("arr_runway") or job.get("runway") or {}

    def ap_wp(a, elev, rw_end):
        if a["code"]:
            return (f'\n        <ATCWaypoint id="{sx.escape(a["code"])}">\n            <ATCWaypointType>Airport</ATCWaypointType>\n'
                    f'            <WorldPosition>{geo.lla(a["lat"], a["lon"], elev)}</WorldPosition>\n'
                    f'            {_rwy_tags(rw_end)}\n'
                    f'            <ICAO><ICAOIdent>{sx.escape(a["code"])}</ICAOIdent></ICAO>\n        </ATCWaypoint>')
        return (f'\n        <ATCWaypoint id="STRIP">\n            <ATCWaypointType>User</ATCWaypointType>\n'
                f'            <WorldPosition>{geo.lla(a["lat"], a["lon"], elev)}</WorldPosition>\n        </ATCWaypoint>')

    wps = [ap_wp(dep, dep["elev"], dep_rw.get("end"))]
    for v in visuals:
        wps.append(f'\n        <ATCWaypoint id="{sx.escape(v["ident"])}">\n            <ATCWaypointType>User</ATCWaypointType>\n'
                   f'            <WorldPosition>{geo.lla(v["lat"], v["lon"], alt)}</WorldPosition>\n        </ATCWaypoint>')
    wps.append(ap_wp(dest, dest["elev"], arr_rw.get("end")))
    dep_id, dest_id = dep["code"] or "STRIP", dest["code"] or "STRIP"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SimBase.Document Type="AceXML" version="1,0">
    <Descr>AceXML Document</Descr>
    <FlightPlan.FlightPlan>
        <Title>{sx.escape(dep_id)} to {sx.escape(dest_id)}</Title>
        <FPType>{"IFR" if job.get("rules") == "IFR" else "VFR"}</FPType>
        <RouteType>VOR</RouteType>
        <CruisingAlt>{alt}</CruisingAlt>
        <DepartureID>{sx.escape(dep_id)}</DepartureID>
        <DepartureLLA>{geo.lla(dep["lat"], dep["lon"], dep["elev"])}</DepartureLLA>
        <DestinationID>{sx.escape(dest_id)}</DestinationID>
        <DestinationLLA>{geo.lla(dest["lat"], dest["lon"], dest["elev"])}</DestinationLLA>
        <Descr>{sx.escape(job["title"])}</Descr>
        <DepartureName>{sx.escape(dep["name"])}</DepartureName>
        <DestinationName>{sx.escape(dest["name"])}</DestinationName>
        <AppVersion>
            <AppVersionMajor>11</AppVersionMajor>
            <AppVersionBuild>282174</AppVersionBuild>
        </AppVersion>{"".join(wps)}
    </FlightPlan.FlightPlan>
</SimBase.Document>
"""


def navlog_html(job, prof):
    """A printable kneeboard page."""
    p = job["plan"]
    e = sx.escape
    def alt_txt(a):
        return a if isinstance(a, str) else f"{a:,}"
    rows = "".join(
        f"<tr class='{l['phase']}'><td><b>{e(l['to'])}</b><br><small>{e(l['name'])}</small></td><td>{alt_txt(l['alt'])}</td>"
        f"<td>{l['mc']:03d}°</td><td>{l['mh']:03d}°</td><td>{l['dist']}</td><td>{l['cum']}</td><td>{l['gs']}</td><td>{l['ete']:.0f}</td><td>{l['fuel']}</td><td>{l['msa']:,}</td><td class='blank'></td></tr>"
        for l in p["legs"])
    fq = lambda fl: ", ".join(f"{e(f['type'])} {f['mhz']}" for f in fl) or "none listed"
    navs = "".join(f"<li>{e(n['ident'])} {e(n['type'])} {e(n['freq'])}, {e(n['name'])} (at {n['at_nm']} nm, {n['off_nm']} nm off track)</li>" for n in p["navaids"]) or "<li>none nearby</li>"
    wind = f"{p['wind']['dir']:03d}°/{p['wind']['kt']} kt" if p.get("wind") else "n/a"
    def rw(r):
        if not r:
            return "pick on arrival"
        xw = f", X-wind {r['crosswind']} kt" if r.get("crosswind") is not None else ""
        return f"RWY {e(r['end'])} ({r['length']:,} ft {e(r['surface'])}{xw})"
    f = p["fuel"]
    usable = f" of {f['usable']} usable" if f["usable"] else ""
    altf = f" + alternate {f['alternate']}" if f.get("alternate") else ""
    an = p.get("alternate")
    altn_txt = f" &nbsp; <b>Alternate</b> {e(an['code'])} {e(an['name'])} ({an['dist_nm']} nm)" if an else ""
    var_txt = f"{abs(p['mag_var'])}°{'W' if p['mag_var'] < 0 else 'E'}"
    notes = "".join(f"<li>{e(n)}</li>" for n in p["notes"])
    title_ids = f"{e(job['dep']['code'] or job['dep']['ident'])}-{e(job['dest']['code'] or job['dest']['ident'])}"
    wx = lambda w: e(w["raw"]) if w else "no report nearby"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Navlog {title_ids}</title>
<style>body{{font:13px/1.4 -apple-system,Segoe UI,sans-serif;margin:24px;color:#111}}h1{{font-size:20px;margin:0}}table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #999;padding:4px 6px;text-align:center}}th{{background:#eee;font-size:11px}}td:first-child{{text-align:left}}tr.climb td,tr.descent td{{color:#444;background:#fafafa}}
.blank{{width:60px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px}}small{{color:#555}}ul{{margin:4px 0;padding-left:18px}}code{{font-size:12px}}
@media print{{button{{display:none}} body{{margin:8mm}}}}</style></head><body>
<button onclick="print()" style="float:right">Print</button>
<h1>{e(job['title'])}</h1><p>{e(job['dep']['name'])} → {e(job['dest']['name'])} · {p['total_nm']} nm · {p['ete_min']} min · {e(prof['name'])} · planned {e(p['built_at'])}</p>
<div class="grid"><div><b>Cruise</b> {p['cruise_alt']:,} ft &nbsp; <b>MSA</b> {p['msa']:,} ft &nbsp; <b>Wind</b> {wind} &nbsp; <b>Var</b> {var_txt}</div>
<div><b>Departure</b> {rw(p['dep_runway'])}</div><div><b>Dep freq</b> {fq(p['freqs']['dep'])}</div><div><b>Arrival</b> {rw(p['arr_runway'])}</div>
<div><b>Arr freq</b> {fq(p['freqs']['arr'])}</div><div><b>Fuel</b> trip {f['trip']} + taxi {f['taxi']}{altf} + reserve {f['reserve']} ({f['reserve_min']} min) = <b>{f['total']} gal</b>{usable}</div>
<div><b>Rules</b> {p.get('rules', 'VFR')}{altn_txt}</div></div>
<table><tr><th>To</th><th>Alt</th><th>MC</th><th>MH</th><th>Dist</th><th>Total</th><th>GS</th><th>ETE</th><th>Fuel</th><th>MSA</th><th>ATA</th></tr>{rows}</table>
<b>Navaids near the route</b><ul>{navs}</ul>
<b>Weather</b><ul><li>Departure: <code>{wx(job.get('dep_weather'))}</code></li><li>Destination: <code>{wx(job.get('weather'))}</code></li></ul>
<b>Notes</b><ul>{notes}</ul>
<p><small>For simulator use only. Terrain: Copernicus DEM via Open-Meteo. Winds: Open-Meteo forecast. Navaids and frequencies: OurAirports. Sim data can differ.</small></p>
</body></html>"""
