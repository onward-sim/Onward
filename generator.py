"""Turns 'where am I and what do I feel like' into three flights worth taking."""
import math
import threading
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import geo
import highlights
import story

TARGET = 12  # flights offered per search
BANDS = 10   # distance bands, so offers range from short hops to the longest legs

MARGIN = {"easy": 1.4, "normal": 1.1, "hard": 0.95, "any": 1.0}
BAND = {"easy": (1, 2), "normal": (2, 3), "hard": (4, 5), "any": (1, 5)}
FEATURED_KINDS = ["msfs", "historic:castle", "man_made:lighthouse", "natural:volcano", "waterway:waterfall",
                  "natural:glacier", "waterway:dam", "historic:fort", "historic:monastery", "natural:cape",
                  "tourism:viewpoint", "building:cathedral", "historic:ruins", "bridge", "natural:peak"]


UNKNOWN_RW = {"id": "?", "length": 0, "width": 0, "surface": "unknown", "surface_raw": "unknown", "lighted": False,
              "le": "?", "he": "?", "le_hdg": None, "he_hdg": None, "le_lat": None, "le_lon": None,
              "he_lat": None, "he_lon": None}


# Typical straight-in minimums, used because free data has no procedures: (ceiling ft AGL, visibility sm)
MINIMA = {"precision": (200, 0.5), "non-precision": (500, 1.25)}
ALT_MINIMA = (800, 2.0)  # simplified alternate weather requirement


def wx_at_least(wx, ceil_ft, vis_sm):
    if not wx:
        return None  # unknown
    c = wx.get("ceiling")
    v = wx.get("vis")
    return (c is None or c >= ceil_ft) and (v is None or v >= vis_sm)


def is_imc(wx):
    return bool(wx) and wx.get("cat") in ("IFR", "LIFR")


def usable_runways(a, prof, prefs, margin):
    if a.get("ultralight") and prof["min_runway_ft"] > 1100:
        return []
    if a.get("rw_unknown"):
        if prefs.get("difficulty") == "easy" or prefs.get("surface") == "paved" or not prefs.get("uncharted", True):
            return []
        return [UNKNOWN_RW] if prof.get("unpaved", True) else []
    out = []
    for rw in a["runways"]:
        s = rw["surface"]
        if s == "water":
            continue
        if s != "paved" and not prof.get("unpaved", True):
            continue
        if prefs.get("surface") == "paved" and s != "paved":
            continue
        if prefs.get("surface") == "unpaved" and s == "paved":
            continue
        if rw["length"] < prof["min_runway_ft"] * margin:
            continue
        out.append(rw)
    return out


def best_runway(rws, wx):
    best = None
    for rw in rws:
        for end, hdg in ((rw["le"], rw["le_hdg"]), (rw["he"], rw["he_hdg"])):
            hw, xw = geo.wind_components(wx.get("wdir") if wx else None, wx.get("wspd") if wx else None, hdg)
            key = (xw if xw is not None else 0) - (min(hw, 0) * 2 if hw is not None else 0) - rw["length"] / 100000
            if best is None or key < best[0]:
                best = (key, rw, end, hw, xw)
    _, rw, end, hw, xw = best
    gust_xw = None
    if wx and wx.get("wgst") and xw is not None and wx.get("wspd"):
        gust_xw = xw * wx["wgst"] / wx["wspd"]
    return {"id": rw["id"], "end": end, "length": round(rw["length"]), "width": round(rw["width"]),
            "surface": rw["surface"], "surface_raw": rw["surface_raw"], "lighted": rw["lighted"],
            "headwind": None if hw is None else round(hw), "crosswind": None if xw is None else round(xw),
            "gust_crosswind": None if gust_xw is None else round(gust_xw)}


def difficulty(prof, a, rw, wx, night):
    score, why = 1, []
    ratio = rw["length"] / prof["min_runway_ft"]
    if not rw["length"]:
        score += 1
        why.append("Runway details unknown: scout it from overhead first")
    elif ratio < 1.1:
        score += 2
        why.append(f"Short runway ({rw['length']:,} ft)")
    elif ratio < 1.35:
        score += 1
        why.append(f"Snug runway ({rw['length']:,} ft)")
    if rw["surface"] != "paved" and rw["length"]:
        score += 1
        why.append(f"{'Unpaved' if rw['surface'] == 'unpaved' else 'Unknown'} surface ({(rw['surface_raw'] or 'n/a').lower()})")
    if a["elev"] > 8000:
        score += 2
        why.append(f"Very high field ({a['elev']:,.0f} ft)")
    elif a["elev"] > 5000:
        score += 1
        why.append(f"High field ({a['elev']:,.0f} ft)")
    if night:
        score += 1
        why.append("Night arrival" + ("" if rw["lighted"] else ", unlit runway"))
        if not rw["lighted"]:
            score += 2
    if wx:
        xw = rw.get("gust_crosswind") or rw.get("crosswind") or 0
        if xw >= 15:
            score += 2
            why.append(f"Strong crosswind (~{xw} kt)")
        elif xw >= 10:
            score += 1
            why.append(f"Crosswind (~{xw} kt)")
        if (rw.get("headwind") or 0) < -5:
            score += 1
            why.append(f"Tailwind on the into-wind runway?! ({-rw['headwind']} kt)")
        if wx.get("wgst") and wx["wgst"] >= 20:
            score += 1
            why.append(f"Gusts {wx['wgst']:.0f} kt")
        if wx["cat"] == "MVFR":
            score += 1
            why.append("Marginal VFR")
        elif wx["cat"] in ("IFR", "LIFR"):
            score += 2
            why.append(f"{wx['cat']} conditions")
    return min(5, score), why


def cruise_alt(dep, dest, course, pois):
    top = max(dep["elev"], dest["elev"])
    for p in pois:
        if p.get("ele") and p.get("off_nm", 9) < 4:
            top = max(top, p["ele"] * 3.28084)
    base = top + 2000
    thousands = math.ceil(base / 1000)
    odd = course < 180
    if (thousands % 2 == 1) != odd:
        thousands += 1
    return max(thousands, 2 if not odd else 3) * 1000 + 500


def airport_card(world, a):
    return {"ident": a["ident"], "code": a["code"], "name": a["name"], "lat": a["lat"], "lon": a["lon"],
            "elev": round(a["elev"]), "country": a["country"], "country_name": world.country(a["country"]),
            "town": a["town"] or a["name"], "type": a["type"], "uncharted": not a["code"],
            "handcrafted": a["ident"] in world.handcrafted}


class Generator:
    def __init__(self, world, weather, landmarks, log=print, terrain=None):
        self.world, self.weather, self.landmarks, self.log = world, weather, landmarks, log
        self.terrain = terrain  # function [(lat, lon)] -> [feet or None]

    @staticmethod
    def _terrain_points(dep, cards):
        pts, owners = [], []
        for c in cards:
            ring, route = highlights.terrain_points(dep, c["a"], c["d"])
            for p in ring:
                pts.append(p)
                owners.append((c, "ring"))
            for p in route:
                pts.append(p)
                owners.append((c, "route"))
        return pts, owners

    def read_terrain(self, dep, cards, progress=lambda m: None):
        """Sample terrain around each destination and along each route in one go."""
        if not self.terrain or not cards:
            return
        progress("Finding flights: checking the terrain...")
        pts, owners = self._terrain_points(dep, cards)
        # strips with no elevation in the database read as sea level, which made the hills around them look like
        # mountains: take the field's height from the terrain instead
        for c in cards:
            if c["a"].get("elev_missing"):
                pts.append((c["a"]["lat"], c["a"]["lon"]))
                owners.append((c, "field"))
        try:
            elev = self.terrain(pts)
        except Exception as e:
            self.log(f"Terrain lookup failed: {e}")
            return
        if not any(e is not None for e in elev):
            return
        for c in cards:
            c["terrain"] = {"ring": [], "route": []}
        for (c, part), e in zip(owners, elev):
            if part == "field":
                if e is not None:
                    c["a"]["elev"], c["a"]["elev_missing"] = round(e), False
                continue
            c["terrain"][part].append(e)

    def generate(self, career, prefs, now_utc=None, progress=lambda m: None):
        now_utc = now_utc or datetime.now(timezone.utc)
        prof = career.profile()
        dep = career.loc()
        if not dep:
            return [], "Your current location isn't in the airport database."
        status = career.status()
        if status in ("grounded", "in_repair"):
            return [], "The aircraft can't fly right now. Check the hangar."
        ferry = status == "damaged"
        margin = MARGIN.get(prefs["difficulty"], 1.0)
        dmin, dmax = float(prefs["min_nm"]), float(prefs["max_nm"])
        dmax = min(dmax, prof["range_nm"] * 0.75)
        if ferry:
            dmin, dmax = 3, min(150, prof["range_nm"] * 0.5)
        visited = set(career.s["visited"][-40:])
        countries = set(career.s["countries"])
        bush = prefs.get("bush")

        progress("Finding flights: looking at the map...")
        pool = []
        for attempt in range(3):
            pool = []
            for d, a in self.world.near(dep["lat"], dep["lon"], dmax):
                if d < dmin or a["ident"] == dep["ident"]:
                    continue
                if a.get("military"):
                    continue
                if a["elev"] > prefs.get("max_elev", 99999):
                    continue
                if not a["code"] and not prefs.get("uncharted", True):
                    continue
                if ferry and not career.is_shop(a):
                    continue
                rws = usable_runways(a, prof, prefs, 1.0 if ferry else margin)
                if not rws:
                    continue
                w = {"small_airport": 1.6, "medium_airport": 1.0, "large_airport": 0.25}.get(a["type"], 0.5)
                if bush:
                    if any(r["surface"] != "paved" for r in rws):
                        w *= 4
                    if min(r["length"] for r in rws) < prof["min_runway_ft"] * 1.4 or a.get("rw_unknown"):
                        w *= 2
                    if a["type"] != "small_airport":
                        w *= 0.2
                if prefs.get("prefer_new", True) and a["ident"] in visited:
                    w *= 0.08
                if a["country"] not in countries:
                    w *= 1.6
                if a["ident"] in self.world.handcrafted:
                    w *= 2
                if not a["code"]:
                    w *= 1.4 if bush else 0.5
                if a.get("rw_unknown") and not bush:
                    w *= 0.35
                pool.append((w, d, a, rws))
            if len(pool) >= 4 or ferry:
                break
            dmax *= 1.5
            dmin *= 0.6
        if not pool:
            return [], "No suitable airfields in that range. Try widening the distance or relaxing the filters."

        progress("Finding flights: checking the weather...")
        metars = self.weather.around(dep["lat"], dep["lon"], dmax + 60)
        dep_wx = self.weather.nearest(metars, dep["lat"], dep["lon"])
        ifr_able = bool(prof.get("ifr")) and career.s["settings"].get("ifr_auto", True)
        dep_imc = is_imc(dep_wx)
        if dep_imc and not ifr_able and not ferry:
            return [], (f"The weather here is below VFR ({dep_wx['station']}: {dep_wx['cat']}), and the {prof['name']} isn't set up for IFR "
                        "in Onward. Wait for it to lift, or switch to an IFR aircraft.")

        # shortlist spread across the whole distance range: a few weighted picks per distance band
        span_lo, span_hi = (min(p[1] for p in pool), max(p[1] for p in pool)) if ferry else (dmin, dmax)
        nb = BANDS
        width = max(1.0, (span_hi - span_lo) / nb)
        band_of = lambda d: min(nb - 1, max(0, int((d - span_lo) / width)))
        by_band = {}
        for it in pool:
            by_band.setdefault(band_of(it[1]), []).append(it)
        shortlist = []
        for items in by_band.values():
            items = list(items)
            for _ in range(min(6, len(items))):
                tot = sum(i[0] for i in items)
                r = random.random() * tot
                for k, it in enumerate(items):
                    r -= it[0]
                    if r <= 0:
                        shortlist.append(items.pop(k))
                        break
                else:
                    shortlist.append(items.pop())

        cards = []
        for w, d, a, rws in shortlist:
            ete = d / prof["cruise_kt"] * 60 + 10
            eta = now_utc + timedelta(minutes=ete)
            night = geo.solar_elevation(a["lat"], a["lon"], eta) < -4
            wx = self.weather.nearest(metars, a["lat"], a["lon"])
            rw = best_runway(rws, wx)
            lit_ok = any(r["lighted"] for r in rws)
            if night and not ferry:
                if prefs["night"] == "day":
                    continue
                if prefs["night"] == "lit_only" and not lit_ok:
                    continue
            if night and lit_ok and not rw["lighted"]:
                rw = best_runway([r for r in rws if r["lighted"]], wx)
            rules, appr, alt = "VFR", None, None
            if ifr_able and (dep_imc or is_imc(wx)):
                appr = self.world.ifr_approach(a)
                if not appr:
                    continue  # IMC and no instrument approach: not today
                if wx_at_least(wx, *MINIMA[appr]) is False:
                    continue  # below minimums
                rules = "IFR"
                alt = self.alternate(a, metars)
                if is_imc(wx) or dep_imc:
                    w *= 1.5  # in bad weather, IFR legs are the interesting ones
            elif wx and not ferry:
                if prefs["weather"] == "vfr" and wx["cat"] != "VFR":
                    continue
                if prefs["weather"] == "marginal" and wx["cat"] not in ("VFR", "MVFR"):
                    continue
            score, why = difficulty(prof, a, rw, wx, night)
            if rules == "IFR":
                c_ft = wx.get("ceiling") if wx else None
                lo_mins = MINIMA[appr]
                if wx and c_ft is not None and c_ft < lo_mins[0] * 2.5:
                    score = min(5, score + 1)
                    why.append(f"Ceiling {c_ft:,} ft, close to typical {appr} minimums")
                if not alt:
                    score = min(5, score + 1)
                    why.append("No alternate with good weather nearby")
                if not wx:
                    why.append("No weather report at the destination")
                why.insert(0, f"IFR flight, {appr} approach expected")
            cards.append({"w": w, "d": d, "a": a, "rw": rw, "wx": wx, "night": night, "ete": ete, "eta": eta,
                          "score": score, "why": why, "rules": rules, "approach": appr, "alternate": alt})
        lo, hi = BAND.get(prefs["difficulty"], (1, 5))
        banded = [c for c in cards if lo <= c["score"] <= hi]
        if len(banded) >= 3 or ferry:
            cards = banded or cards
        if not cards:
            return [], "Everything in range is ruled out by the weather or darkness right now. Relax a filter or wait a bit."

        # pick one flight per distance band (shortest to longest), then fill up to the target
        target = 5 if ferry else TARGET
        chosen, used = [], set()
        bands = {}
        for c in cards:
            bands.setdefault(band_of(c["d"]), []).append(c)
        for k in sorted(bands):
            best = max(bands[k], key=lambda c: c["w"] * random.uniform(0.6, 1.4))
            chosen.append(best)
            used.add(best["a"]["ident"])
        rest = sorted([c for c in cards if c["a"]["ident"] not in used], key=lambda c: -c["w"] * random.uniform(0.6, 1.4))
        while len(chosen) < target and rest:
            # fill where the list is thinnest
            counts = {}
            for c in chosen:
                counts[band_of(c["d"])] = counts.get(band_of(c["d"]), 0) + 1
            rest.sort(key=lambda c: (counts.get(band_of(c["d"]), 0), -c["w"]))
            c = rest.pop(0)
            chosen.append(c)
            used.add(c["a"]["ident"])
        if ferry:
            chosen = sorted(cards, key=lambda c: c["d"])[:target]

        # landmarks for the chosen flights: queue their areas, wait a little, use what's ready
        need = set()
        for c in chosen:
            need |= self.landmarks.tiles_along(dep, c["a"], 5) | self.landmarks.tiles_near(c["a"]["lat"], c["a"]["lon"], 6)
        # terrain tiles download in the background meanwhile (the first search in a new area needs dozens)
        tstate = {"done": 0, "total": 0}
        pre = None
        try:
            import terrain as _terrain
            tpts = self._terrain_points(dep, chosen)[0]

            def _tp(d, n):
                tstate.update(done=d, total=n)
            pre = threading.Thread(target=_terrain.prefetch, args=(tpts, _tp), daemon=True)
            pre.start()
        except Exception as e:  # no Pillow or similar: the planner falls back to the web services
            self.log(f"Terrain tiles unavailable: {e}")
        lm = self.landmarks
        if lm.last_error and lm.failed and time.time() - max(lm.failed.values()) < 600:
            progress("Finding flights: the landmark servers are busy, using the landmarks already saved...")
        else:
            lm.wait_for(need, 20, lambda m: progress("Finding flights: " + m[0].lower() + m[1:]))
        while pre is not None and pre.is_alive():
            if tstate["total"]:
                progress(f"Finding flights: terrain for this area ({tstate['done']} of {tstate['total']} map tiles)...")
            pre.join(0.5)
        for c in chosen:
            a = c["a"]
            c["route_pois"] = self.landmarks.along(dep, a, corridor_nm=5, fetch=False)
            c["dest_pois"] = self.landmarks.near(a["lat"], a["lon"], 6, fetch=False)
        self.read_terrain(dep, chosen, progress)

        offers = []
        friend = career.s.get("pending_friend")
        if friend and not ferry:
            fa = self.world.airports.get(friend["dest"])
            if fa:
                o, _ = self.custom_offer(career, fa, now_utc, kind="ride", friend=friend, metars=metars, dep_wx=dep_wx, wait=0)
                if o:
                    offers.append(o)
        trip = career.s.get("trip")
        if trip and not ferry and trip["idx"] < len(trip["legs"]):
            leg = trip["legs"][trip["idx"]]
            ta = self.world.airports.get(leg["to"])
            if ta and leg["from"] == dep["ident"] and not any(o["dest"]["ident"] == ta["ident"] for o in offers):
                o, _ = self.custom_offer(career, ta, now_utc, kind="trip", metars=metars, dep_wx=dep_wx, wait=8)
                if o:
                    o["trip_leg"] = {"n": trip["idx"] + 1, "of": len(trip["legs"]), "name": trip["name"], "day": leg["day"], "stop": leg["stop"]}
                    o["lines"].insert(0, f"Trip: {trip['name']}, leg {trip['idx'] + 1} of {len(trip['legs'])}.")
                    if leg.get("sights"):  # via sights you asked for on this leg: the route flies over them
                        o["plan_opts"] = {"waypoints": [{"kind": "visual", "name": p["name"], "lat": p["lat"], "lon": p["lon"],
                                                         "label": p.get("label") or "sight", "source": p.get("source") or "msfs"} for p in leg["sights"]]}
                        names = [p["name"] for p in leg["sights"]]
                        o["lines"].insert(1, "Via " + (", ".join(names[:-1]) + " and " if len(names) > 1 else "") + names[-1] + ", as you asked.")
                    offers.insert(0, o)

        for c in sorted(chosen, key=lambda c: c["d"]):
            if any(o["dest"]["ident"] == c["a"]["ident"] for o in offers):
                continue
            if ferry:
                kind = "ferry"
            elif c["score"] >= 4:
                kind = "challenge"
            elif len(c["route_pois"]) >= 2 or any(p.get("source") == "msfs" for p in c["route_pois"]):
                kind = "scenic"
            else:
                kind = "tour"
            offers.append(self._offer(career, dep, dep_wx, c, kind, prof))
        return offers, None

    def custom_offer(self, career, a, now_utc=None, kind="tour", friend=None, metars=None, dep_wx=None, wait=10):
        """An offer to one airfield you chose (fly anywhere, a trip leg, a friend's ride). Returns (offer, error)."""
        now_utc = now_utc or datetime.now(timezone.utc)
        prof = career.profile()
        dep = career.loc()
        d = geo.dist_nm(dep["lat"], dep["lon"], a["lat"], a["lon"])
        if a["ident"] == dep["ident"]:
            return None, "You're already there."
        if d > prof["range_nm"] * 0.85:
            return None, f"{a['name']} is {d:.0f} nm away, too far for one leg in the {prof['name']}. Make it a trip instead."
        if metars is None:
            metars = self.weather.around(dep["lat"], dep["lon"], d + 60)
        if dep_wx is None:
            dep_wx = self.weather.nearest(metars, dep["lat"], dep["lon"])
        rws = usable_runways(a, prof, {"surface": "any"}, 1.0) or [r for r in a["runways"] if r["surface"] != "water"] or a["runways"]
        ete = d / prof["cruise_kt"] * 60 + 10
        eta = now_utc + timedelta(minutes=ete)
        wx = self.weather.nearest(metars, a["lat"], a["lon"])
        rw = best_runway(rws, wx) if rws else None
        ifr_able = bool(prof.get("ifr")) and career.s["settings"].get("ifr_auto", True)
        rules, appr, alt = "VFR", None, None
        if rw and (is_imc(dep_wx) or is_imc(wx)):
            appr = self.world.ifr_approach(a) if ifr_able else None
            if appr and wx_at_least(wx, *MINIMA[appr]) is not False:
                rules, alt = "IFR", self.alternate(a, metars)
            elif kind == "ride":
                return None, "Not in this weather."
        if not rw:
            rw = {"id": "?", "length": 0, "width": 0, "surface": "unknown", "lighted": False}
        night = geo.solar_elevation(a["lat"], a["lon"], eta) < -4
        score, why = difficulty(prof, a, rw, wx, night)
        if rules == "IFR":
            why.insert(0, f"IFR flight, {appr} approach expected")
        if wait:
            self.landmarks.wait_for(self.landmarks.tiles_along(dep, a, 5) | self.landmarks.tiles_near(a["lat"], a["lon"], 6), wait)
        c = {"w": 1, "d": d, "a": a, "rw": rw, "wx": wx, "night": night, "ete": ete, "eta": eta,
             "score": score, "why": why, "route_pois": self.landmarks.along(dep, a, corridor_nm=5, fetch=False),
             "dest_pois": self.landmarks.near(a["lat"], a["lon"], 6, fetch=False),
             "rules": rules, "approach": appr, "alternate": alt}
        self.read_terrain(dep, [c])
        o = self._offer(career, dep, dep_wx, c, "ride" if kind == "ride" else "tour", prof, friend)
        if kind in ("trip", "anywhere"):
            o["kind"] = kind
        if rw.get("id") == "?":
            o["lines"].append("Onward doesn't know this field's runways. Check them in the sim before you commit.")
        return o, None

    @staticmethod
    def trip_leg_nm(prof):
        """A comfortable leg for this aircraft, the default for trips."""
        return max(40.0, min(prof["range_nm"] * 0.4, prof["cruise_kt"] * 1.1, 160.0))

    def plan_trip(self, career, a, leg_nm=None):
        """Split a long way into legs: stops near the line at airfields worth stopping at, a fuel stop or a night between."""
        prof = career.profile()
        dep = career.loc()
        max_leg = leg_nm or self.trip_leg_nm(prof)
        stops = [dep] + self.trip_stops(career, dep, a, max_leg, {dep["ident"]})
        return self.trip_legs(career, stops, max_leg)

    def trip_stops(self, career, dep, a, max_leg, used, path=None):
        """The airfields after dep on the way to a (a included), each leg about max_leg or less.

        path: points to pass on the way (sights), so stops follow the way you'll fly, not the straight line."""
        prof = career.profile()
        pts = [(dep["lat"], dep["lon"])] + [tuple(p) for p in (path or [])] + [(a["lat"], a["lon"])]
        seg = [geo.dist_nm(x[0], x[1], y[0], y[1]) for x, y in zip(pts, pts[1:])]
        total = sum(seg)
        n = max(1, math.ceil(total / max_leg))

        def along(dist):
            for (x, y), d in zip(zip(pts, pts[1:]), seg):
                if dist <= d or d == seg[-1]:
                    return geo.destination(x[0], x[1], geo.bearing(x[0], x[1], y[0], y[1]), min(dist, d))
                dist -= d
            return pts[-1]
        stops, prev = [], dep
        used = set(used) | {dep["ident"], a["ident"]}
        for i in range(1, n):
            target = along(total * i / n)
            best = None
            # look near the line first; over water or empty country, look further out (a coast, the other side of a bay)
            for reach, stretch in ((0.35, 1.25), (0.6, 1.4), (0.9, 1.5)):
                best = self._trip_stop(career, prof, target, prev, max_leg, used, reach, stretch)
                if best:
                    break
            if best:
                stops.append(best[1])
                used.add(best[1]["ident"])
                prev = best[1]
        stops.append(a)
        # a leg far longer than planned means the line crosses open water: go around, stop by stop
        way = [dep] + stops
        if max(geo.dist_nm(x["lat"], x["lon"], y["lat"], y["lon"]) for x, y in zip(way, way[1:])) > max_leg * 1.6:
            around = self._stops_around(career, prof, dep, a, max_leg, used - {x["ident"] for x in stops[:-1]})
            if around:
                return around
        return stops

    def _stops_around(self, career, prof, dep, a, max_leg, used):
        """Hop from airfield to airfield, each time to the one within a leg's reach that gets closest to a (around a bay or a sea)."""
        stops, prev, used = [], dep, set(used) | {dep["ident"]}
        for _ in range(40):
            left = geo.dist_nm(prev["lat"], prev["lon"], a["lat"], a["lon"])
            if left <= max_leg * 1.25:
                return stops + [a]
            best = None
            for d, b in self.world.near(prev["lat"], prev["lon"], max_leg * 1.2):
                if d < max_leg * 0.3 or b["ident"] in used or b.get("military") or not b["code"] \
                        or b["type"] not in ("small_airport", "medium_airport", "large_airport") or not usable_runways(b, prof, {"surface": "any"}, 1.0):
                    continue
                gain = left - geo.dist_nm(b["lat"], b["lon"], a["lat"], a["lon"])
                if gain <= 5:
                    continue
                score = gain + (10 if b["ident"] in self.world.handcrafted else 0) + {"medium_airport": 6, "small_airport": 3}.get(b["type"], 0)
                if best is None or score > best[0]:
                    best = (score, b)
            if not best:
                return None
            prev = best[1]
            used.add(prev["ident"])
            stops.append(prev)
        return None

    def _trip_stop(self, career, prof, target, prev, max_leg, used, reach, stretch):
        """The best airfield within reach*max_leg of target for a stop, with the leg from prev at most stretch*max_leg."""
        best = None
        for d, b in self.world.near(target[0], target[1], max(15.0, max_leg * reach)):
            if b["ident"] in used or b.get("military") or not b["code"] or b["type"] not in ("small_airport", "medium_airport", "large_airport"):
                continue
            if not usable_runways(b, prof, {"surface": "any"}, 1.0):
                continue
            leg = geo.dist_nm(prev["lat"], prev["lon"], b["lat"], b["lon"])
            if leg > max_leg * stretch or leg < max_leg * 0.35:
                continue
            score = -d * 0.6
            score += 25 if b["ident"] in self.world.handcrafted else 0
            score += 18 if any(p.get("label") == "MSFS photogrammetry city" for p in self.world.msfs_near(b["lat"], b["lon"], 8)) else 0
            score += 6 * min(3, len(self.world.msfs_near(b["lat"], b["lon"], 12)))
            score += {"medium_airport": 8, "small_airport": 4, "large_airport": -6}.get(b["type"], 0)
            score += 6 if career.is_shop(b) else 0
            score -= 10 if b["ident"] in career.s.get("visited", []) else 0
            if best is None or score > best[0]:
                best = (score, b)
        return best

    def trip_legs(self, career, stops, max_leg, marks=None):
        """Legs between consecutive stops, grouped into days (two legs or about three hours a day)."""
        prof = career.profile()
        marks = marks or {}
        legs, day, day_min, day_legs = [], 1, 0.0, 0
        for i, (x, y) in enumerate(zip(stops, stops[1:])):
            way = [x] + list((marks.get(i) or {}).get("sights") or []) + [y]  # sights on this leg make it longer
            nm = sum(geo.dist_nm(p["lat"], p["lon"], q["lat"], q["lon"]) for p, q in zip(way, way[1:]))
            mins = nm / prof["cruise_kt"] * 60 + 15
            if day_legs >= 2 or (day_legs and day_min + mins > 180):
                legs[-1]["stop"] = "overnight"
                day, day_min, day_legs = day + 1, 0.0, 0
            day_min += mins
            day_legs += 1
            town = lambda ap: ap.get("municipality") or ap.get("town") or ap["name"]
            legs.append({"from": x["ident"], "to": y["ident"], "from_name": town(x), "to_name": town(y), "to_code": y["code"] or y["ident"],
                         "nm": round(nm), "min": round(mins), "day": day, "stop": "fuel", "lat": y["lat"], "lon": y["lon"],
                         "from_lat": x["lat"], "from_lon": x["lon"]})
            m = marks.get(i)
            if m:
                legs[-1].update(m)
        if legs:
            legs[-1]["stop"] = "end"
        return {"legs": legs, "total_nm": round(sum(l["nm"] for l in legs)), "total_min": round(sum(l["min"] for l in legs)),
                "days": legs[-1]["day"] if legs else 0, "max_leg": round(max_leg)}

    def alternate(self, a, metars):
        """Nearest airfield 10-70 nm away with an instrument approach and alternate-friendly weather."""
        for d, b in self.world.near(a["lat"], a["lon"], 70):
            if d < 10 or not b["code"] or not self.world.ifr_approach(b):
                continue
            wx = self.weather.nearest(metars, b["lat"], b["lon"], max_nm=25)
            if wx_at_least(wx, *ALT_MINIMA):
                card = airport_card(self.world, b)
                card.update(dist_nm=round(d), weather=wx, approach=self.world.ifr_approach(b))
                return card
        return None

    def _offer(self, career, dep, dep_wx, c, kind, prof, friend=None):
        a = c["a"]
        dest = airport_card(self.world, a)
        course = geo.bearing(dep["lat"], dep["lon"], a["lat"], a["lon"])
        route = sorted(c.get("route_pois", []), key=lambda p: (p.get("source") != "msfs", p["frac"]))
        featured = None
        for k in FEATURED_KINDS:
            m = [p for p in route if (p.get("source") == "msfs" and k == "msfs") or p.get("kind") == k]
            if m:
                featured = m[0]
                break
        if not featured and route:
            featured = route[0]
        # keep the route list readable: msfs first, then a spread of others
        shown = [p for p in route if p.get("source") == "msfs"][:6]
        others = [p for p in route if p.get("source") != "msfs"]
        others.sort(key=lambda p: (FEATURED_KINDS.index(p["kind"]) if p.get("kind") in FEATURED_KINDS else 99, p["off_nm"]))
        shown += others[: max(0, 8 - len(shown))]
        if featured and featured not in shown:
            shown.insert(0, featured)
        shown.sort(key=lambda p: p["frac"])
        for i, p in enumerate(shown):
            p["id"] = f"p{i}"
            p["label"] = story.kind_label(p.get("kind")) if p.get("source") != "msfs" else (p.get("label") or "MSFS POI")

        town = dest["town"]
        fmt = dict(name=a["name"], town=town, elev=f"{a['elev']:,.0f}", country=dest["country_name"])
        hl = highlights.build(c, dep, prof, career, self.world, friend=friend if kind == "ride" else None, ferry=kind == "ferry")
        kind = highlights.kind_for(hl, ferry=kind == "ferry", friend=kind == "ride")
        lines = []
        if kind == "ride":
            lines.append(f"{friend['name']} is waiting with a small bag. Add one passenger (about 75 kg) in the weight and balance.")
        elif kind == "ferry":
            lines.append("The plane isn't happy. Nurse it to the nearest shop gently: no aggressive maneuvers, keep the engine cool.")
        elif c.get("rules") == "IFR":
            wx = c["wx"]
            if wx and wx.get("ceiling") is not None:
                lines.append(story.pick(story.IFR_LINES, ceiling=f"{wx['ceiling']:,}", name=a["name"]))
            else:
                lines.append(story.pick(story.IFR_LINES_GENERIC, name=a["name"]))
        else:
            lines.append(story.pick(story.DEST_LINES["town"], **fmt))
        rw = c["rw"]
        head = hl[0] if hl else None
        poi = (featured or {}).get("name", "")
        if head and head["kind"] == "city":
            poi = next((p["name"] for p in c.get("route_pois", []) + c.get("dest_pois", []) if p.get("label") == "MSFS photogrammetry city"), poi)
        tfmt = dict(fmt, poi=poi, friend=friend["name"] if friend else "")
        if head and head["kind"] in ("sights", "city") and not poi:
            head = hl[1] if len(hl) > 1 else None
        title = highlights.title(head, tfmt)
        if c.get("rules") == "IFR":
            shown = []

        alt = cruise_alt(dep, a, course, route)
        return {
            "id": uuid.uuid4().hex[:8], "kind": kind, "title": title, "lines": lines,
            "dep": airport_card(self.world, dep), "dest": dest,
            "distance_nm": round(c["d"]), "bearing": round(course), "ete_min": round(c["ete"]),
            "eta_utc": c["eta"].strftime("%H:%MZ"), "cruise_alt": alt,
            "runway": rw, "weather": c["wx"], "dep_weather": dep_wx, "night": c["night"],
            "difficulty": c["score"], "difficulty_why": c["why"], "pois": shown,
            "dest_pois": [dict(p, label=story.kind_label(p.get("kind")) if p.get("source") != "msfs" else (p.get("label") or "MSFS POI")) for p in c.get("dest_pois", [])[:5]],
            "friend": friend, "aircraft": prof["id"],
            "rules": c.get("rules", "VFR"), "approach": c.get("approach"), "alternate": c.get("alternate"),
            "minimums": MINIMA.get(c.get("approach")) if c.get("rules") == "IFR" else None,
            "highlights": hl[:6],
        }



def pln(job):
    import planner
    return planner.pln(job)
