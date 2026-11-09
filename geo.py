"""Small geography helpers: distances, bearings, sun position. No dependencies."""
import math
from datetime import datetime, timezone

EARTH_NM = 3440.065


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def cross_track_nm(lat, lon, lat1, lon1, lat2, lon2):
    """Distance of a point from the great-circle segment 1->2, and how far along it is (0..1)."""
    d13 = dist_nm(lat1, lon1, lat, lon) / EARTH_NM
    d12 = dist_nm(lat1, lon1, lat2, lon2)
    if d12 < 0.01:
        return d13 * EARTH_NM, 0.0
    b13 = math.radians(bearing(lat1, lon1, lat, lon))
    b12 = math.radians(bearing(lat1, lon1, lat2, lon2))
    xt = math.asin(max(-1, min(1, math.sin(d13) * math.sin(b13 - b12))))
    at = math.acos(max(-1, min(1, math.cos(d13) / max(1e-9, math.cos(xt))))) * EARTH_NM
    if math.cos(b13 - b12) < 0:
        at = -at
    frac = at / d12
    if frac < 0:
        return dist_nm(lat1, lon1, lat, lon), frac
    if frac > 1:
        return dist_nm(lat2, lon2, lat, lon), frac
    return abs(xt) * EARTH_NM, frac


def destination(lat, lon, brg, dist):
    """Point reached from lat/lon going dist nm on true bearing brg."""
    d = dist / EARTH_NM
    b = math.radians(brg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def wind_triangle(tc, tas, wdir, wkt):
    """True course, TAS, wind FROM direction/speed -> (true heading, ground speed)."""
    if not wkt or wdir is None:
        return tc, tas
    a = math.radians(wdir - tc)
    s = max(-1.0, min(1.0, wkt * math.sin(a) / max(tas, 1)))
    wca = math.asin(s)
    gs = tas * math.cos(wca) - wkt * math.cos(a)
    return (tc + math.degrees(wca)) % 360, max(gs, 20)


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def solar_elevation(lat, lon, when=None):
    """Sun elevation in degrees (NOAA approximation). Below about -4 runway lights are needed."""
    when = when or datetime.now(timezone.utc)
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    tst = hour * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1, min(1, cz))))


def wind_components(wind_dir, wind_kt, runway_heading):
    """Returns (headwind, crosswind) in knots. Negative headwind = tailwind."""
    if wind_dir is None or wind_kt is None or runway_heading is None:
        return None, None
    a = math.radians(wind_dir - runway_heading)
    return wind_kt * math.cos(a), abs(wind_kt * math.sin(a))


def lla(lat, lon, alt_ft=0):
    """MSFS .pln coordinate format."""
    def part(v, pos, neg):
        h = pos if v >= 0 else neg
        total = round(abs(v) * 360000)  # hundredths of a second, so rounding never gives 60 seconds
        d, rest = divmod(total, 360000)
        m, cs = divmod(rest, 6000)
        return f"{h}{d}° {m}' {cs // 100:02d}.{cs % 100:02d}\""
    return f"{part(lat, 'N', 'S')},{part(lon, 'E', 'W')},{'+' if alt_ft >= 0 else '-'}{abs(alt_ft):09.2f}"
