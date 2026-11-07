"""Quick checks before a release: the pure calculations, then every endpoint of a demo server.

    python tests/smoke.py            (starts its own demo server on port 8699 with a temporary data folder)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ONWARD_DATA", tempfile.mkdtemp(prefix="onward-test-"))
fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# ---------- calculations ----------
import briefing  # noqa: E402
import planner  # noqa: E402
import updater  # noqa: E402

check("gfp coordinates", planner._gfp_coord(67.50333, -133.78167) == "N67302W133469" and planner._gfp_coord(-33.9461, 151.1772) == "S33568E151106")
job = {"dep": {"code": "KHAF", "lat": 37.5134, "lon": -122.501}, "dest": {"code": "CA-0001", "lat": 38.3, "lon": -121.0},
       "plan": {"route": [{}, {"lat": 37.7, "lon": -122.2}, {}]}}
check("gfp file", planner.gfp(job) == "FPN/RI:F:KHAF:F:N37420W122120:F:N38180W121000", planner.gfp(job))
check("version compare", updater.newer("0.26.0", "0.25.2") and not updater.newer("0.25.2", "0.25.10"))


def _frozen_check(manifest):  # the packaged app reads stable.json and points to flightsim.to, never GitHub
    import data as _d
    u, was = updater.Updater({}), _d.FROZEN
    asked = []
    u._get = lambda url, **k: (asked.append(url), json.dumps(manifest).encode())[1]
    _d.FROZEN = True
    try:
        st = dict(u.check())
        st["msg"] = u.apply()
    finally:
        _d.FROZEN = was
    return st, asked


st, asked = _frozen_check({"version": "99.0.0", "url": "https://flightsim.to/file/1/onward"})
check("packaged app checks stable.json", asked and asked[0].split("?")[0].endswith("/main/stable.json"), asked)
check("packaged app points to flightsim.to", st["available"] and st["url"].startswith("https://flightsim.to")
      and "flightsim.to" in st["msg"] and "github" not in st["msg"].lower(), st)
st, _ = _frozen_check({"version": "", "url": ""})
check("no stable version yet means no update", not st["available"], st)


# right after loading into the cockpit MSFS sends the position before the rest: that half sample must wait
import tracker  # noqa: E402


class _Val:
    def __init__(self, v):
        self.value = v


_src = tracker.SimConnectSource(log=lambda m: None)
_src.try_connect = lambda: True
_src.connected, _src.connected_at, _src.sm = True, time.time(), type("Sm", (), {"quit": 0})()
_src.reqs = {k: _Val(None) for k in tracker.FAST}
_src.reqs["lat"].value, _src.reqs["lon"].value = 37.5, -122.5
_src.slow, _src.title_req = {k: _Val(None) for k in tracker.SLOW}, _Val(b"Cessna 172")
half = _src.read()
for _k in tracker.FAST:
    if _src.reqs[_k].value is None:
        _src.reqs[_k].value = 1.0
check("half sample from MSFS waits", half is None and (_src.read() or {}).get("lat") == 37.5, half)
sys.path.insert(0, ROOT)
import server  # noqa: E402
check("log hides the user folder", server.HOME not in server._private(os.path.join(server.HOME, "Onward", "data")))
col = [{"ft": 300, "t": 8, "rh": 70, "ws": 10, "wd": 270, "cc": 0}, {"ft": 2500, "t": 4, "rh": 80, "ws": 18, "wd": 270, "cc": 0},
       {"ft": 5000, "t": 0, "rh": 95, "ws": 25, "wd": 280, "cc": 90}, {"ft": 10000, "t": -8, "rh": 92, "ws": 35, "wd": 290, "cc": 80},
       {"ft": 14000, "t": -16, "rh": 50, "ws": 40, "wd": 290, "cc": 0}]
hz = briefing.hazards(col, 300, 30, 1500, 3)
check("icing band found", bool(hz["ice"]) and hz["ice"][0][0] <= 5500, hz["ice"])
check("gusts and storms", hz["low_turb"] is not None and hz["storm"] == "possible")
check("cloud layers", bool(briefing.layers(col, 300)) and briefing.layers(col, 300)[0][2] >= 80)
# scattered cumulus the pressure levels miss (thinner than the gap between levels): drawn from the surface forecast
cu = [{"ft": 2600, "t": 14, "rh": 55, "ws": 5, "wd": 220, "cc": 5}, {"ft": 5000, "t": 9, "rh": 62, "ws": 8, "wd": 230, "cc": 10},
      {"ft": 7300, "t": 3, "rh": 85, "ws": 12, "wd": 240, "cc": 22}, {"ft": 9900, "t": -3, "rh": 78, "ws": 18, "wd": 250, "cc": 20},
      {"ft": 13900, "t": -12, "rh": 35, "ws": 25, "wd": 260, "cc": 40}]
sct = briefing.low_layer(cu, 3000, 14, 4, 40, briefing.layers(cu, 3000))
check("scattered cumulus shown", sct is not None and 6500 <= sct[0] <= 7500 and sct[1] >= 9500 and sct[2] == 40, sct)
check("no cumulus on a clear day", briefing.low_layer(cu, 3000, 14, 4, 10, []) is None)
try:
    import terrain
    h = terrain.elevation_m(39.09, -120.04)
    check("terrain tiles (Lake Tahoe ~1897 m)", 1850 < h < 1950, h)
except Exception as e:  # offline is not a code failure
    print("skip terrain tiles:", e)

# ---------- endpoints ----------
port = 8699
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--demo", "--port", str(port), "--no-browser", "--start", "KHAF"],
                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
B = f"http://localhost:{port}"


def get(p, raw=False):
    with urllib.request.urlopen(B + p, timeout=120) as r:
        body = r.read()
        return body if raw else json.loads(body)


def post(p, b):
    req = urllib.request.Request(B + p, data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read())


try:
    for _ in range(180):
        try:
            if get("/api/state").get("ready"):
                break
        except Exception:
            pass
        time.sleep(2)
    st = get("/api/state")
    check("server ready", st.get("ready"), st.get("error"))
    check("page", b"ONWARD" in get("/", raw=True))
    check("cockpit page", b"<html" in get("/cockpit.html", raw=True).lower())
    check("credits", "OurAirports" in get("/api/credits").get("credits", ""))
    post("/api/generate", {})
    for _ in range(90):
        st = get("/api/state")
        if st["offers"] and not st["gen"]["running"]:
            break
        time.sleep(2)
    check("offers", len(st["offers"]) >= 8, len(st["offers"]))
    oid = st["offers"][0]["id"]
    plan = get(f"/api/plan?id={oid}")["plan"]
    check("plan profile", plan["profile"]["msa"] > 0 and plan["profile"]["terrain"])
    check("plan weather", plan["profile"].get("wx") in ("ok", "none"))
    check("pln", b"<FlightPlan.FlightPlan>" in get(f"/api/pln?id={oid}", raw=True))
    check("gfp download", get(f"/api/gfp?id={oid}", raw=True).startswith(b"FPN/RI:"))
    check("navlog", len(get(f"/api/navlog?id={oid}", raw=True)) > 500)
    check("route ideas", len(get(f"/api/ideas?id={oid}").get("ideas", [])) >= 3)
    r = post("/api/replan", {"id": oid, "opts": {"cruise_alt": 6500}})
    check("replan", not r.get("error"), r.get("error"))
    s, w, n, e = 40.5, -9.5, 42.5, -7.0
    check("metars", "metars" in get(f"/api/wx/metars?s={s}&w={w}&n={n}&e={e}"))
    check("sigmets", "sigmets" in get("/api/wx/sigmets"))
    g = get(f"/api/wx/grid?s={s}&w={w}&n={n}&e={e}&alt=5000")
    check("winds grid", "points" in g, g.get("error"))
    c = get(f"/api/wx/cond?s={s}&w={w}&n={n}&e={e}")
    check("flying conditions", "grid" in c, c.get("error"))
    check("satellite time", "eumetsat" in get("/api/wx/sat"))
    check("pois", "pois" in get(f"/api/pois?s={s}&w={w}&n={n}&e={e}"))
    home = get("/api/trip/home")
    check("trip home", "stats" in home and "ranges" in home)
    check("trip map", "highlights" in get(f"/api/trip/map?s={s}&w={w}&n={n}&e={e}&z=9"))
    check("trip place", "place" in get("/api/trip/place?lat=37.5&lon=-122.5"))
    t = post("/api/trip", {"action": "preview", "ident": "KSBA"})
    check("trip preview", t.get("trip") and len(t["trip"]["legs"]) >= 2, t.get("error"))
    check("airport search", get("/api/apsearch?q=half%20moon")["results"])
    # fly a demo leg end to end
    check("accept", not post("/api/accept", {"id": oid}).get("error"))
    get(f"/api/plan?id={oid}")
    post("/api/demo", {"action": "fly", "fpm": 150, "rough": False, "duration": 25})
    for _ in range(60):
        f = (get("/api/state").get("live") or {}).get("flight") or {}
        if f.get("landings") and f.get("phase") != "air":
            break
        time.sleep(2)
    time.sleep(4)
    r = post("/api/finish", {})
    check("finish leg", not r.get("error"), r.get("error"))
    res = get("/api/state").get("last_result") or {}
    check("debrief with landing grade", (res.get("landing") or {}).get("grade") in ("A", "B", "C", "D"))
    j = get("/api/journal")
    check("journal entry", any(e.get("type") == "leg" for e in j["journal"]))
    check("history", get("/api/history")["legs"])
finally:
    srv.terminate()
    try:
        out = srv.communicate(timeout=10)[0].decode("utf-8", "replace")
        errs = [l for l in out.splitlines() if "Traceback" in l or "error:" in l.lower()]
        check("no server tracebacks", not any("Traceback" in l for l in errs), "\n".join(errs[-5:]))
    except Exception:
        pass

print("\nALL OK" if not fails else f"\n{len(fails)} FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
