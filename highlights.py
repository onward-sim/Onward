"""Why a flight is worth taking: sights, terrain, the runway, the weather. Built from real data."""
import random

import geo

NOTABLE = {"historic:castle", "man_made:lighthouse", "natural:volcano", "waterway:waterfall", "natural:glacier",
           "waterway:dam", "historic:fort", "historic:monastery", "natural:cape", "building:cathedral",
           "historic:ruins", "bridge", "natural:arch", "tourism:viewpoint"}


def terrain_points(dep, a, dist_nm):
    """Points to sample: a ring around the destination, and the route itself."""
    ring = []
    for r in (2.5, 5.0):
        for b in range(0, 360, 45):
            ring.append(geo.destination(a["lat"], a["lon"], b, r))
    route = []
    brg = geo.bearing(dep["lat"], dep["lon"], a["lat"], a["lon"])
    n = max(2, min(16, int(dist_nm / 5)))
    for k in range(1, n):
        dd = dist_nm * k / n
        if 3 < dd < dist_nm - 3:
            route.append(geo.destination(dep["lat"], dep["lon"], brg, dd))
    return ring, route


SURFACES = {"GRE": "Grass", "GRS": "Grass", "GRASS": "Grass", "TURF": "Grass", "GRV": "Gravel", "GRAVEL": "Gravel",
            "DIRT": "Dirt", "DRT": "Dirt", "SAND": "Sand", "SAN": "Sand", "CLA": "Clay", "CLAY": "Clay", "SNOW": "Snow",
            "ICE": "Ice", "WATER": "Water", "COR": "Coral", "LAT": "Laterite", "SOIL": "Dirt", "EARTH": "Dirt"}


def surface_word(raw):
    """OurAirports surface codes ("GRE", "TURF-G", "Unk") as a word for a sentence; 'Unpaved' when unknown."""
    r = (raw or "").upper().strip()
    for code, word in SURFACES.items():
        if r == code or r.startswith(code + "-") or r.startswith(code + " ") or (len(code) > 3 and code in r):
            return word
    return "Unpaved"


def build(c, dep, prof, career, world, friend=None, ferry=False):
    """Returns a list of highlights, best first: {icon, text, kind, score}."""
    a, rw, wx = c["a"], c["rw"], c.get("wx")
    out = []

    def add(kind, icon, text, score):
        out.append({"kind": kind, "icon": icon, "text": text, "score": round(score, 2)})

    if friend:
        add("friend", "🧳", f"Taking {friend['name']} home to {friend['town']}", 5)
    if ferry:
        add("ferry", "🔧", "Maintenance shop at the other end", 5)

    # sights
    route = c.get("route_pois", [])
    msfs = [p for p in route if p.get("source") == "msfs" and p.get("label") != "MSFS photogrammetry city"]
    cities = [p for p in route + c.get("dest_pois", []) if p.get("label") == "MSFS photogrammetry city"]
    notable = [p for p in route if p.get("kind") in NOTABLE]
    other = [p for p in route if p not in msfs and p not in notable and p not in cities]
    if cities:
        cname = cities[0]["name"]
        at_dest = cities[0] in c.get("dest_pois", []) or cname.lower() in (a.get("town") or "").lower()
        add("city", "🏙", (f"Arrive over {cname}, a photogrammetry city in the sim" if at_dest
                          else f"Fly over {cname}, a photogrammetry city in the sim"), 3.2)
    picks = msfs[:2] + notable[: max(0, 2 - len(msfs[:2]))]
    if picks:
        names = " and ".join(p["name"] for p in picks)
        more = len(msfs) + len(notable) + len(other) - len(picks)
        tag = (f"a {msfs[0]['note']} POI" if msfs and msfs[0].get("note") else "an MSFS POI") if msfs else ""
        extra = f" (+{more} more)" if more > 0 else ""
        add("sights", "📸", f"{names} along the way{extra}" + (f", {tag}" if msfs and len(picks) == 1 else ""),
            2.4 + 0.6 * len(msfs) + 0.3 * len(notable) + min(1.0, 0.15 * more))
    elif other:
        add("sights", "📸", f"{other[0]['name']} along the way" + (f" (+{len(other) - 1} more)" if len(other) > 1 else ""),
            1.6 + min(0.8, 0.15 * len(other)))
    route_names = {p["name"] for p in route}
    near = [p for p in c.get("dest_pois", []) if p not in cities and p["name"] not in route_names]
    if near:
        p = sorted(near, key=lambda p: p.get("source") != "msfs")[0]
        add("near", "📍", f"{p['name']} close to the field", 1.4 + (0.6 if p.get("source") == "msfs" else 0))

    # terrain (only when we managed to read it)
    t = c.get("terrain")
    if t:
        ring = [e for e in t["ring"] if e is not None]
        if ring:
            rise = max(ring) - a["elev"]
            sea = sum(1 for e in ring if e <= 5)
            if rise >= 2500:
                add("mountain", "⛰", f"Mountain approach: terrain rises {rise:,.0f} ft within 5 nm of the runway", 3.6)
            elif rise >= 1400:
                add("hills", "⛰", f"Hilly approach: high ground {rise:,.0f} ft above the field nearby", 2.5)
            if sea >= 4 and a["elev"] < 400:
                add("coast", "🌊", "Coastal arrival, part of the circuit over the water", 2.1 + 0.1 * sea)
        route_e = [e for e in t["route"] if e is not None]
        if route_e:
            top = max(route_e)
            if top >= max(dep["elev"], a["elev"]) + 3000:
                add("crossing", "🏔", f"Crosses high ground up to {top:,.0f} ft on the way", 2.6)

    # the runway
    if a.get("rw_unknown") or not rw.get("length"):
        add("strip", "❓", "Runway details unknown: overfly and inspect the strip before landing", 3.3)
    else:
        ratio = rw["length"] / prof["min_runway_ft"]
        if ratio < 1.15:
            add("short", "📏", f"Short runway: {rw['length']:,} ft, tight for the {prof['name'].split(' (')[0]}", 3.7)
        elif ratio < 1.35:
            add("short", "📏", f"Snug runway: {rw['length']:,} ft", 2.2)
        if rw["surface"] == "unpaved":
            add("grass", "🌾", f"{surface_word(rw.get('surface_raw'))} strip, not tarmac", 2.6)
        if rw.get("width") and 0 < rw["width"] <= 45:
            add("narrow", "↔", f"Narrow runway, only {rw['width']} ft wide", 2.0)
    if a["elev"] > 4000:
        add("high", "🗻", f"High-altitude field at {a['elev']:,.0f} ft: watch the density altitude", 2.8)

    # conditions
    if c.get("rules") == "IFR":
        cig = wx.get("ceiling") if wx else None
        add("ifr", "☁", "Real IFR: instrument approach" + (f" with the ceiling at {cig:,} ft" if cig else ""), 3.6)
    elif wx and wx.get("cat") == "MVFR":
        add("weather", "🌥", "Marginal weather: low cloud and reduced visibility", 2.2)
    xw = rw.get("gust_crosswind") or rw.get("crosswind")
    if xw and xw >= 11:
        g = f" (gusts to {wx['wgst']:.0f} kt)" if wx and wx.get("wgst") else ""
        add("wind", "💨", f"Crosswind landing, about {xw} kt across the runway{g}", 2.4 + min(1.5, (xw - 11) * 0.15))
    if c.get("night"):
        add("night", "🌙", "Night landing" + ("" if rw.get("lighted") else " on an unlit strip"), 2.3 if rw.get("lighted") else 3.5)

    # the journey itself
    if a["ident"] in world.handcrafted:
        add("handcrafted", "✨", "Handcrafted airport in the sim", 2.6)
    if a["country"] not in career.s["countries"]:
        add("country", "🛂", f"First landing in {world.country(a['country'])}", 2.3)
    elif a["ident"] not in career.s["visited"]:
        add("new", "🆕", "Somewhere you haven't been yet", 0.6)
    if a["type"] == "large_airport":
        add("big", "🛫", "Busy airport: big runway, long taxi, airliners", 1.3)
    if not a.get("code"):
        add("uncharted", "🧭", "No sim code: find it by its coordinates", 1.0)

    out.sort(key=lambda h: -h["score"])
    return out


TITLES = {
    "friend": ["A ride for {friend}", "Taking {friend} to {town}"],
    "ferry": ["Limp to the shop at {name}", "Maintenance run to {town}"],
    "city": ["Over {poi} to {town}", "{poi} from above, then {town}"],
    "city_dest": ["Into {town}, photogrammetry and all", "{town} in full 3D"],
    "sights": ["Past {poi} to {town}", "{poi} and on to {town}", "The scenic way to {town}"],
    "near": ["A look at {town}", "Onward to {town}"],
    "mountain": ["Mountain approach into {town}", "Down into the valley at {town}"],
    "hills": ["Into the hills at {town}", "Over the ridges to {town}"],
    "coast": ["Coastal arrival at {town}", "Along the water into {town}"],
    "crossing": ["Over the mountains to {town}", "Across the high ground to {town}"],
    "strip": ["Scouting the strip at {name}", "Mystery strip at {town}"],
    "short": ["Short field at {town}", "Every foot counts at {name}"],
    "grass": ["Grass strip at {town}", "Off the tarmac at {name}"],
    "narrow": ["Threading the runway at {town}"],
    "high": ["High and thin at {town}", "Up to {town}"],
    "ifr": ["Instruments to {town}", "Through the grey to {town}", "IFR to {name}"],
    "weather": ["Under the clouds to {town}"],
    "wind": ["Crosswind at {town}", "Wrestling the wind at {town}"],
    "night": ["Night landing at {town}", "Lights on at {town}"],
    "handcrafted": ["Handcrafted {name}", "A proper airport: {name}"],
    "country": ["Across the border to {town}", "First stop in {country}: {town}"],
    "big": ["Big-airport visit: {town}"],
    "new": ["Onward to {town}", "A look at {town}"],
    "uncharted": ["Finding {name}"],
}
CHALLENGE = {"mountain", "hills", "strip", "short", "high", "ifr", "wind", "narrow", "grass"}
SCENIC = {"city", "sights", "coast", "crossing"}


def title(headline, fmt):
    key = headline["kind"] if headline else "new"
    if key == "city" and headline["text"].startswith("Arrive over"):
        key = "city_dest"
    return random.choice(TITLES.get(key, TITLES["new"])).format(**fmt)


def kind_for(hl, ferry=False, friend=False):
    if friend:
        return "ride"
    if ferry:
        return "ferry"
    top = hl[0]["kind"] if hl else ""
    if top in CHALLENGE:
        return "challenge"
    if top in SCENIC or any(h["kind"] in SCENIC for h in hl[:2]):
        return "scenic"
    return "tour"
