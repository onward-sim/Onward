"""Pre-flight checks: the weather along the route and takeoff/landing distances."""
import json
import math
from datetime import datetime, timedelta, timezone

import data
import geo

WX_CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
            51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
            61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
            71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
            82: "violent showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
            99: "thunderstorms with hail"}


LEVELS = (1000, 925, 850, 700, 600, 500, 400)   # hPa: roughly the surface to 24,000 ft
LEVEL_VARS = ("temperature", "relative_humidity", "wind_speed", "wind_direction", "geopotential_height", "cloud_cover")


def _column(h, i, ground_ft):
    """Temperature, humidity and wind at each pressure level above the ground, lowest first."""
    out = []
    for L in LEVELS:
        try:
            z, t, rh = h[f"geopotential_height_{L}hPa"][i], h[f"temperature_{L}hPa"][i], h[f"relative_humidity_{L}hPa"][i]
            ws, wd = h[f"wind_speed_{L}hPa"][i], h[f"wind_direction_{L}hPa"][i]
            cc = (h.get(f"cloud_cover_{L}hPa") or [None] * (i + 1))[i]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (z, t, rh):
            continue
        ft = z * 3.28084
        if ft < ground_ft - 200:
            continue
        out.append({"ft": round(ft), "t": t, "rh": rh, "ws": ws, "wd": wd, "cc": cc})
    return out


def _interp(col, ft):
    for a, b in zip(col, col[1:]):
        if a["ft"] <= ft <= b["ft"]:
            f = (ft - a["ft"]) / max(1, b["ft"] - a["ft"])
            uv = []
            for c in (a, b):
                ws, wd = c["ws"] or 0, math.radians(c["wd"] or 0)
                uv.append((ws * math.sin(wd), ws * math.cos(wd)))
            return {"t": a["t"] + (b["t"] - a["t"]) * f, "rh": a["rh"] + (b["rh"] - a["rh"]) * f,
                    "u": uv[0][0] + (uv[1][0] - uv[0][0]) * f, "v": uv[0][1] + (uv[1][1] - uv[0][1]) * f}
    return None


def layers(col, ground_ft):
    """Cloud layers from the forecast cloud cover at each height: [[base, top, cover], ...] in feet, lowest first."""
    pts = [(c["ft"], c["cc"]) for c in col if c.get("cc") is not None]
    if len(pts) < 2:
        return []
    out, cur = [], None
    for ft in range(int(math.ceil(max(ground_ft, pts[0][0]) / 500) * 500), int(pts[-1][0]) + 1, 500):
        cc = None
        for (fa, ca), (fb, cb) in zip(pts, pts[1:]):
            if fa <= ft <= fb:
                cc = ca + (cb - ca) * (ft - fa) / max(1, fb - fa)
                break
        if cc is not None and cc >= 30:
            if cur is None:
                cur = [ft, ft, cc]
            cur[1], cur[2] = ft, max(cur[2], cc)
        elif cur is not None:
            out.append(cur); cur = None
    if cur is not None:
        out.append(cur)
    return [[max(round(ground_ft), lo - 250), hi + 250, round(cc)] for lo, hi, cc in out]


def low_layer(col, ground_ft, t, td, low, lay):
    """Scattered or broken low cloud the pressure levels miss: cumulus is often thinner than the gap between
    two levels, so the cloud cover at each level stays under 30% even when the sky is a quarter to half full.
    Base from the temperature/dew point spread at the surface, top where the air above dries out.
    Returns [base, top, cover] in feet, or None when there is no such cloud or a layer already covers it."""
    if t is None or td is None or (low or 0) < 25:
        return None
    base = ground_ft + max(300, (t - td) * 400)
    if any(lo - 500 <= base <= hi + 500 for lo, hi, cc in lay):
        return None
    top, ft = base + 1000, base
    while col and ft <= col[-1]["ft"]:
        a = _interp(col, ft)
        if a is not None:
            if a["rh"] < 70:
                break
            top = max(top, ft)
        ft += 500
    return [round(base / 100) * 100, round(min(top, base + 10000) / 100) * 100, round(low)]


COVER_PCT = {"FEW": 20, "SCT": 40, "BKN": 70, "OVC": 100, "OVX": 100, "VV": 100}
METAR_NM = 20        # a point this close to a reporting airport uses its METAR for the low cloud
METAR_HOURS = 3      # ...if you'll be there within this many hours
METAR_AGE_S = 2 * 3600


def metar_layers(m, col):
    """Cloud layers from a METAR, [[base, top, cover], ...] in feet above sea level, plus how high the report
    reaches (a METAR only covers the lower sky: CAVOK and NSC mean nothing below 5,000 ft above the field).
    MSFS live weather is built largely from these reports, so near an airport they beat the forecast.
    The top of each layer comes from the forecast humidity above it. None when the report says nothing usable."""
    elev = m.get("elev_ft")
    clouds = m.get("clouds") or []
    if elev is None or (not clouds and not m.get("clear")):
        return None
    out = []
    for cover, base in clouds:
        b = elev + base
        top, ft = b + 1000, b + 500
        while col and ft <= min(col[-1]["ft"], b + 5000):
            a = _interp(col, ft)
            if a is not None:
                if a["rh"] < 70:
                    break
                top = max(top, ft)
            ft += 500
        out.append([round(b / 100) * 100, round(top / 100) * 100, COVER_PCT.get(cover, 50)])
    reach = elev + max(5000, max((base for _, base in clouds), default=0) + 2000)
    return out, reach


def _bands(flags, step):
    """[(ft, bool)] in 500 ft steps -> [[lo, hi], ...] where the flag is set."""
    out, lo = [], None
    for ft, on in flags:
        if on and lo is None:
            lo = ft
        if not on and lo is not None:
            out.append([lo, ft - step]); lo = None
    if lo is not None:
        out.append([lo, flags[-1][0]])
    return [b for b in out if b[1] >= b[0]]


def hazards(col, ground_ft, gust_kt, cape, code):
    """Icing, turbulence and storm risk in one column of air, as altitude bands in feet."""
    step, top = 500, 14000
    alts = [a for a in range(int(math.ceil((ground_ft + 500) / step) * step), top + 1, step)]
    ice, turb = [], []
    prev = None
    for ft in alts:
        p = _interp(col, ft)
        ice.append((ft, bool(p) and -15 <= p["t"] <= 1 and p["rh"] >= 85))
        sh = False
        if p and prev and ft - ground_ft > 1500:
            sh = math.hypot(p["u"] - prev["u"], p["v"] - prev["v"]) / (step / 1000) >= 8  # 8+ kt of wind change per 1,000 ft
        turb.append((ft, sh))
        prev = p
    out = {"ice": _bands(ice, step), "turb": _bands(turb, step), "low_turb": None, "storm": None}
    if gust_kt is not None and gust_kt >= 25:
        out["low_turb"] = [round(ground_ft), round(ground_ft + 3000)]
    if code in (95, 96, 99):
        out["storm"] = "forecast"
    elif cape is not None and cape >= 1000:
        out["storm"] = "possible"
    # strongest wind between 3,000 and 10,000 ft, for mountain waves over high ground
    mids = [c for c in col if 3000 <= c["ft"] <= 10000 and c["ws"] is not None]
    out["wind_mid"] = max((c["ws"] for c in mids), default=None)
    return out


def _near(name):
    return name if str(name).startswith("between") else f"near {name}"


def _points(route, legs_total_min, when):
    """Waypoints (and midpoints on long legs) with the time you'll be there."""
    pts, cum, total = [], [0.0], 0.0
    for a, b in zip(route, route[1:]):
        total += geo.dist_nm(a["lat"], a["lon"], b["lat"], b["lon"])
        cum.append(total)
    total = total or 1
    for i, r in enumerate(route):
        if i > 0:
            a = route[i - 1]
            leg = cum[i] - cum[i - 1]
            n = int(leg // 18)  # a point every 15 to 20 nm, so the side view can show where weather changes
            brg = geo.bearing(a["lat"], a["lon"], r["lat"], r["lon"])
            for k in range(1, n + 1):
                d = leg * k / (n + 1)
                mid = geo.destination(a["lat"], a["lon"], brg, d)
                pts.append({"name": f"between {a.get('ident')} and {r.get('ident')}", "lat": mid[0], "lon": mid[1], "frac": (cum[i - 1] + d) / total})
        pts.append({"name": r.get("name") if r.get("kind") == "visual" else r.get("ident"), "lat": r["lat"], "lon": r["lon"],
                    "frac": cum[i] / total, "kind": r.get("kind")})
    if len(pts) > 12:  # keep the request small (Open-Meteo counts every point)
        inner = pts[1:-1]
        pts = [pts[0]] + [inner[round(k * (len(inner) - 1) / 9)] for k in range(10)] + [pts[-1]]
    for p in pts:
        p["eta"] = when + timedelta(minutes=10 + legs_total_min * p["frac"])
    return pts


def route_weather(route, ete_min, when, cruise_alt, ifr, weather, dest, msa, log=print):
    pts = _points(route, ete_min, when)
    hourly = "temperature_2m,dew_point_2m,cloud_cover_low,cloud_cover_mid,visibility,precipitation,wind_gusts_10m,freezing_level_height,weather_code,cape,cloud_cover,cloud_cover_high"
    hourly += "," + ",".join(f"{v}_{L}hPa" for L in LEVELS for v in LEVEL_VARS)
    url = ("https://api.open-meteo.com/v1/forecast?latitude=" + ",".join(f"{p['lat']:.3f}" for p in pts)
           + "&longitude=" + ",".join(f"{p['lon']:.3f}" for p in pts)
           + f"&hourly={hourly}&wind_speed_unit=kn&forecast_days=2&timezone=UTC")
    rows, fails = [], False
    metars = []
    if weather is not None and pts:
        try:
            mid = pts[len(pts) // 2]
            span = max(geo.dist_nm(mid["lat"], mid["lon"], p["lat"], p["lon"]) for p in pts)
            metars = weather.around(mid["lat"], mid["lon"], span + METAR_NM)
        except Exception as e:
            log(f"METARs for the side view failed: {e}")
    now = datetime.now(timezone.utc)
    try:
        raw = json.loads(data.forecast_get(url, timeout=20))
        if isinstance(raw, dict):
            raw = [raw]
        source = raw[0].get("source", "Open-Meteo") if raw else None
        for p, r in zip(pts, raw):
            h = r["hourly"]
            stamp = p["eta"].strftime("%Y-%m-%dT%H:00")
            i = h["time"].index(stamp) if stamp in h["time"] else 0
            g = lambda k: h[k][i] if h.get(k) else None
            t, td = g("temperature_2m"), g("dew_point_2m")
            low = g("cloud_cover_low") or 0
            ground = r.get("elevation", 0) * 3.28084
            base = round(max(0, (t - td)) * 400 / 100) * 100 if t is not None and td is not None and low >= 50 else None
            vis = g("visibility")
            col = _column(h, i, ground)
            lay = layers(col, ground)
            src = None
            near = data.Weather.nearest(metars, p["lat"], p["lon"], METAR_NM) if metars else None
            eta = p["eta"] if p["eta"].tzinfo else p["eta"].replace(tzinfo=timezone.utc)
            if near and (eta - now).total_seconds() < METAR_HOURS * 3600 and now.timestamp() - (near.get("obs") or 0) < METAR_AGE_S:
                ml = metar_layers(near, col)
                if ml:  # the airport's report replaces the forecast below the height it covers
                    lay = sorted([l for l in lay if l[0] >= ml[1]] + ml[0])
                    src = near["station"]
            if src is None:
                extra = low_layer(col, ground, t, td, low, lay)
                if extra:
                    lay = sorted(lay + [extra])
            solid = [l for l in lay if l[2] >= 50]
            if solid:  # the cloud cover at each height beats the temperature/dew point estimate
                base = max(0, round((solid[0][0] - ground) / 100) * 100)
            rows.append({"name": p["name"], "layers": lay, "cloud_src": src, "sky": g("cloud_cover"), "high_cloud": g("cloud_cover_high"), "eta": p["eta"].strftime("%H:%MZ"), "frac": round(p["frac"], 3), "low_cloud": low, "mid_cloud": g("cloud_cover_mid"),
                         "base_agl": base, "base_msl": None if base is None else round((ground + base) / 100) * 100,
                         "vis_km": None if vis is None else round(vis / 1000, 1), "rain_mm": g("precipitation"),
                         "gust_kt": g("wind_gusts_10m"), "freezing_ft": None if g("freezing_level_height") is None else round(g("freezing_level_height") * 3.28084 / 100) * 100,
                         "wx": WX_CODES.get(g("weather_code"), ""), "temp": t,
                         "haz": hazards(col, ground, g("wind_gusts_10m"), g("cape"), g("weather_code"))})
        # the same forecast an hour or two later, for "should I wait?"
        later = []
        for off in (60, 120):
            bases, storms = [], False
            for p, r in zip(pts, raw):
                h = r["hourly"]
                stamp = (p["eta"] + timedelta(minutes=off)).strftime("%Y-%m-%dT%H:00")
                if stamp not in h["time"]:
                    continue
                i = h["time"].index(stamp)
                if (h.get("weather_code") or [None] * (i + 1))[i] in (95, 96, 99) or ((h.get("cape") or [0] * (i + 1))[i] or 0) >= 1000:
                    storms = True
                t, td, low = h["temperature_2m"][i], h["dew_point_2m"][i], h["cloud_cover_low"][i] or 0
                if t is not None and td is not None and low >= 50:
                    bases.append(round((r.get("elevation", 0) * 3.28084 + max(0, t - td) * 400) / 100) * 100)
            later.append({"offset": off, "lowest_base": min(bases) if bases else None, "storms": storms})
    except Exception as e:
        log(f"Route weather lookup failed: {e}")
        fails = True
        later = []
        source = None

    # destination TAF at the time you arrive
    eta_dest = when + timedelta(minutes=10 + ete_min)
    taf = weather.taf(dest["lat"], dest["lon"]) if weather else None
    taf_now = weather.taf_at(taf, eta_dest.timestamp()) if taf else None

    reasons_bad, reasons_care, good = [], [], []
    if rows:
        clouds = [(r["base_msl"], r["low_cloud"]) for r in rows if r["base_msl"] is not None]
        if not ifr and clouds:
            lowest, cover = min(clouds)
            solid = cover >= 75
            if lowest < (msa or 0) and solid:
                reasons_bad.append(f"Low cloud (base around {lowest:,} ft) below the safe altitude of {msa:,} ft on part of the route: hard to stay VFR")
            elif lowest < cruise_alt:
                under = (lowest - 500) // 500 * 500
                reasons_care.append(f"{'Broken' if solid else 'Scattered'} cloud with a base around {lowest:,} ft: "
                                    + (f"cruise lower, around {under:,} ft, if terrain allows" if under >= (msa or 0) else "plan to stay clear of it"))
        vis = [r["vis_km"] for r in rows if r["vis_km"] is not None]
        if vis and min(vis) < 5:
            (reasons_bad if min(vis) < 3 else reasons_care).append(f"Visibility down to {min(vis)} km along the way")
        wet = [r for r in rows if (r["rain_mm"] or 0) >= 0.5]
        if wet:
            reasons_care.append(f"Rain expected {_near(wet[0]['name'])}")
        storms = [r for r in rows if "thunder" in r["wx"]]
        if storms:
            reasons_bad.append(f"Thunderstorms forecast {_near(storms[0]['name'])}")
        else:
            maybe = [r for r in rows if (r.get("haz") or {}).get("storm") == "possible"]
            if maybe:
                reasons_care.append(f"Unstable air {_near(maybe[0]['name'])}: showers or thunderstorms could build")
        gusts = [r["gust_kt"] for r in rows if r["gust_kt"] is not None]
        if gusts and max(gusts) >= 25:
            reasons_care.append(f"Surface gusts up to {max(gusts):.0f} kt")
        frz = [r["freezing_ft"] for r in rows if r["freezing_ft"] is not None]
        if frz and min(frz) <= cruise_alt + 1000:
            f0 = min(frz)
            if any(r.get("layers") is not None for r in rows):  # cloud close to the freezing level, not just somewhere overhead
                cloudy = any(l[2] >= 50 and l[0] <= f0 + 3000 and l[1] >= f0 - 1500 for r in rows for l in (r.get("layers") or []))
            else:
                cloudy = any((r["low_cloud"] or 0) + (r["mid_cloud"] or 0) > 80 for r in rows)
            msg = f"Freezing level around {min(frz):,} ft"
            if cloudy and ifr:
                reasons_bad.append(msg + " with cloud about: icing risk in IMC")
            elif cloudy:
                reasons_care.append(msg + ": stay out of cloud")
    if taf_now:
        m = taf_now["main"]
        if m["cat"] in ("IFR", "LIFR") and not ifr:
            reasons_bad.append(f"Destination forecast {m['cat']} when you arrive ({taf['station']} TAF)")
        elif m["cat"] == "MVFR":
            reasons_care.append(f"Destination forecast MVFR when you arrive ({taf['station']} TAF)")
        elif m["cat"] == "VFR":
            good.append(f"Destination forecast VFR when you arrive ({taf['station']} TAF)")
        for t in taf_now["tempo"]:
            if t["cat"] != "VFR":
                reasons_care.append(f"Temporary {t['cat']} spells possible at the destination{(' ' + t['wx']) if t['wx'] and t['wx'] != 'NSW' else ''}")
                break
        if m.get("wgst"):
            reasons_care.append(f"Gusts to {m['wgst']} kt forecast at the destination")
    if rows and not reasons_bad and not reasons_care:
        good.append("Nothing worrying in the forecast along the route")

    if reasons_bad:
        verdict = "think_twice"
    elif reasons_care:
        verdict = "care"
    elif rows or taf_now:
        verdict = "go"
    else:
        verdict = "unknown"
    return {"verdict": verdict, "bad": reasons_bad, "care": reasons_care, "good": good, "points": rows,
            "taf": taf and {"station": taf["station"], "dist": taf["dist"], "raw": taf["raw"]},
            "taf_eta": taf_now, "eta_dest": eta_dest.strftime("%H:%MZ"), "unavailable": fails and not taf, "later": later,
            "source": source, "route_failed": fails}


def distances(prof, field_elev, temp_c, headwind, surface, length_ft, kind):
    """Rough takeoff or landing distance over 50 ft, corrected the way a POH chart would be."""
    base = prof.get("to50_ft" if kind == "takeoff" else "ld50_ft")
    if not base:
        return None
    isa = 15 - 2 * (field_elev or 0) / 1000
    t = temp_c if temp_c is not None else isa
    da = (field_elev or 0) + 120 * (t - isa)
    factor = 1 + (0.10 if kind == "takeoff" else 0.04) * max(0, da) / 1000
    hw = headwind or 0
    if hw >= 0:
        factor *= max(0.7, 1 - 0.10 * hw / 9)
    else:
        factor *= 1 + 0.10 * (-hw) / 2
    if surface and surface != "paved":
        factor *= 1.15 if kind == "takeoff" else 1.10
    need = round(base * factor / 10) * 10
    out = {"kind": kind, "need_ft": need, "da_ft": round(da / 100) * 100, "runway_ft": length_ft, "temp": t}
    if length_ft:
        out["margin"] = round(length_ft / need, 2)
        out["status"] = "ok" if out["margin"] >= 1.5 else ("tight" if out["margin"] >= 1.15 else "no")
    return out
