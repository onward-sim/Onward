"""Ground elevation from the free AWS Terrain Tiles (Terrarium PNGs, Mapzen/Tilezen, SRTM and other sources).

One 256 px tile at zoom 11 covers about 15 km with ~75 m pixels, so a whole route needs a handful of downloads
instead of one web request per point. Tiles are kept on disk, so a second look at the same area is free."""
import io
import math
import os
import threading
import urllib.request

import data

Z = 11
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
DIR = os.path.join(data.DATA, "terrain_tiles")
_mem, _lock = {}, threading.Lock()
_failed = {}


def _tile(x, y):
    key = (x, y)
    with _lock:
        if key in _mem:
            return _mem[key]
    path = os.path.join(DIR, str(Z), str(x), f"{y}.png")
    raw = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
    else:
        raw = data.http_get(URL.format(z=Z, x=x, y=y), timeout=20)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    px = im.load()
    with _lock:
        if len(_mem) > 400:
            _mem.clear()
        _mem[key] = px
    return px


def _height(px, i, j):
    r, g, b = px[min(255, max(0, i)), min(255, max(0, j))]
    return r * 256 + g + b / 256 - 32768


def _xy(lat, lon):
    n = 2 ** Z
    lat = max(-85.0, min(85.0, lat))
    xf = (lon + 180) / 360 * n
    yf = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return xf, yf


def missing(pts):
    """The tiles these points need that aren't on disk yet."""
    n, out = 2 ** Z, set()
    for lat, lon in pts:
        xf, yf = _xy(lat, lon)
        x, y = int(xf) % n, int(yf)
        for t in ((x, y), ((x + 1) % n, y), (x, min(n - 1, y + 1))):  # neighbours, for points near a tile's edge
            if t not in _mem and not os.path.exists(os.path.join(DIR, str(Z), str(t[0]), f"{t[1]}.png")):
                out.add(t)
    return out


def prefetch(pts, progress=None, workers=8):
    """Download the tiles a set of points needs, several at a time. progress(done, total) after each one.
    The first look at a new area needs dozens of tiles; one after the other that took close to a minute."""
    todo = sorted(missing(pts))
    if not todo:
        return 0
    from concurrent.futures import ThreadPoolExecutor
    done = [0]

    def one(t):
        try:
            _tile(*t)
        except Exception:
            pass
        with _lock:
            done[0] += 1
            d = done[0]
        if progress:
            progress(d, len(todo))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    return len(todo)


def elevation_m(lat, lon):
    """Highest of the four pixels around the point (a little conservative for terrain clearance)."""
    n = 2 ** Z
    xf, yf = _xy(lat, lon)
    x, y = int(xf), int(yf)
    px = _tile(x % n, y)
    i, j = int((xf - x) * 256), int((yf - y) * 256)
    h = max(_height(px, i, j), _height(px, i + 1, j), _height(px, i, j + 1), _height(px, i + 1, j + 1))
    return max(h, 0.0)  # the sea is zero


def elevations_m(pts):
    """[(lat, lon)] -> metres, raises if the tiles can't be had (the caller falls back to the web services)."""
    return [elevation_m(a, b) for a, b in pts]
