"""Onward: a solo-traveller career companion for MSFS 2024.
Run:  python server.py          (live, needs MSFS running on this PC)
      python server.py --demo   (pretend airplane, for trying it out)
Then open http://localhost:8642
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import career as career_mod  # noqa: E402
import data  # noqa: E402
import geo  # noqa: E402
import generator  # noqa: E402
import planner  # noqa: E402
import wiki  # noqa: E402
import tracker  # noqa: E402
import trips as trips_mod  # noqa: E402
import photos as photos_mod  # noqa: E402
import updater as updater_mod  # noqa: E402
import version  # noqa: E402

BASE = data.BASE
WEB = os.path.join(BASE, "web")
LOG = []
LOG_FILE = os.path.join(data.DATA, "onward.log")


HOME = os.path.expanduser("~")


def _private(text):
    """Logs get shared in bug reports: show the user folder as %USERPROFILE% instead of the Windows user name."""
    text = str(text)
    if len(HOME) > 3:
        text = text.replace(HOME, "%USERPROFILE%").replace(HOME.replace("\\", "/"), "%USERPROFILE%")
    return text


def log(msg):
    line = f"{time.strftime('%H:%M:%S')}  {_private(msg)}"
    print(line, flush=True)
    LOG.append(line)
    del LOG[:-200]
    try:  # a log file you can send when something goes wrong (trimmed at about 1 MB)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d ") + line + "\n")
    except OSError:
        pass


class App:
    def __init__(self, demo=False, start="KHAF"):
        self.demo, self.start = demo, start
        self.ready = False
        self.boot_error = None
        self.gen = {"running": False, "progress": "", "error": None}
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        try:
            self.world = data.World(log=log)
            self.weather = data.Weather(log=log)
            if self.demo and os.environ.get("ONWARD_FAKE_IMC"):  # test hook: pretend the weather is bad
                real = self.weather.around
                self.weather.around = lambda lat, lon, r: [dict(m, cat="IFR", ceiling=700, vis=3.0) for m in real(lat, lon, r)]
            self.landmarks = data.Landmarks(self.world, log=log)
            own = os.path.join(data.DATA, "aircraft.json")  # your own edited copy wins over the one that ships
            with open(own if os.path.exists(own) else os.path.join(BASE, "aircraft.json"), encoding="utf-8") as f:
                self.profiles = {p["id"]: p for p in json.load(f)["profiles"]}
            name = "career-demo.json" if self.demo else "career.json"
            self.career = career_mod.Career(os.path.join(data.DATA, name), self.world, self.profiles, start=self.start)
            if not self.career.s.get("current_aircraft"):
                self.career.set_aircraft("c172")
            if self.demo:
                self.career.s["setup_done"] = True
            self.updater = updater_mod.Updater(self.career.s["settings"], log=log)
            self.planner = planner.Planner(self.world, log=log, landmarks=self.landmarks, weather=self.weather)
            self.wiki = wiki.Wiki(log=log)
            self.gen_engine = generator.Generator(self.world, self.weather, self.landmarks, log=log, terrain=self.planner.elevations)
            self.trips = trips_mod.Trips(self.world, self.gen_engine, log=log)
            src = tracker.DemoSource(self.career, log=log) if self.demo else tracker.SimConnectSource(log=log)
            self.tracker = tracker.Tracker(self.career, self.world, self.landmarks, src, log=log, weather=self.weather)
            self.ready = True
            log("Ready. Have a good trip.")
            threading.Thread(target=self._prefetch, daemon=True).start()
            threading.Thread(target=self.world.refresh_msfs_map, daemon=True).start()
            threading.Thread(target=self._update_loop, daemon=True).start()
            log(f"Onward {version.VERSION}. Your data is in {data.DATA}")
        except Exception as e:
            self.boot_error = f"{e}"
            log("Startup failed:\n" + traceback.format_exc())

    def _update_loop(self):
        time.sleep(15)
        while True:
            if self.career.s["settings"].get("update_check", True) and not self.demo:
                self.updater.check()
            time.sleep(12 * 3600)

    def restart_soon(self):
        """Exit with the restart code; start.bat starts the new version straight away."""
        def go():
            try:
                self.career.save()
            finally:
                os._exit(updater_mod.RESTART_CODE)
        threading.Timer(1.5, go).start()

    def _prefetch(self):
        """Quietly cache landmarks around wherever you are."""
        last = None
        while True:
            try:
                here = self.career.loc()
                if here and here["ident"] != last:
                    last = here["ident"]
                    self.landmarks.request(self.landmarks.tiles_near(here["lat"], here["lon"], 50))
            except Exception as e:
                log(f"Landmark prefetch: {e}")
            time.sleep(30)

    # ---------- auto aircraft detection ----------
    def detect_aircraft(self, title):
        t = (title or "").lower()
        if not t:
            return None
        for pid, p in self.profiles.items():
            if any(m in t for m in p.get("match", [])):
                return pid
        return None

    def state(self):
        if not self.ready:
            return {"ready": False, "error": self.boot_error, "log": LOG[-8:]}
        c = self.career
        live = self.tracker.view()
        title = (live.get("sample") or {}).get("title")
        detected = self.detect_aircraft(title)
        if detected and detected != c.s["current_aircraft"] and not c.s.get("job") and c.status() != "in_repair":
            c.set_aircraft(detected)
            log(f"Aircraft detected: {self.profiles[detected]['name']}")
        # a "sim session" starts each time aircraft data appears (a flight is loaded); MSFS ignores flight plans
        # sent from the main menu or a loading screen, so send once per session, 10 s after you're in the cockpit
        has_data = bool(live.get("sample")) and not live.get("demo")
        if has_data and not getattr(self, "_had_data", False):
            self._sim_session = getattr(self, "_sim_session", 0) + 1
            self._session_t = time.time()
        self._had_data = has_data
        job = c.s.get("job")
        if (job and job.get("plan") and job.get("plan_sent_session") != getattr(self, "_sim_session", 0) and has_data
                and c.s["settings"].get("auto_load_plan") and not (self.tracker.f or {}).get("takeoff_t")
                and time.time() - getattr(self, "_session_t", 0) > 10 and time.time() - getattr(self, "_plan_try", 0) > 30):
            self._plan_try = time.time()
            threading.Thread(target=self.send_plan, args=(True,), daemon=True).start()
        here = c.loc()
        snap = c.snapshot()
        plane = c.plane()
        status = c.status()
        repair = plane.get("repair")
        return {
            "ready": True, "demo": self.demo,
            "here": generator.airport_card(self.world, here) if here else None,
            "shop_here": c.is_shop(here),
            "aircraft": c.profile(), "aircraft_detected": detected, "sim_title": title,
            "profiles": [{"id": p["id"], "name": p["name"]} for p in self.profiles.values()],
            "plane": {"cond": plane["cond"], "hours": plane["hours"], "status": status,
                      "repair": dict(repair, left_s=max(0, round(repair["until"] - time.time()))) if repair else None,
                      "quotes": {"shop": c.repair_quote("shop"), "field": c.repair_quote("field")},
                      "damage_log": plane["damage_log"][-12:], "write_offs": plane["write_offs"]},
            "offers": snap["offers"], "job": snap["job"], "prefs": snap["prefs"], "settings": dict(snap["settings"], update_token="••••" if snap["settings"].get("update_token") else ""),
            "pending_friend": snap.get("pending_friend"), "last_result": snap.get("last_result"), "trip": snap.get("trip"),
            "version": version.VERSION, "update": dict(self.updater.status, backups=self.updater.backups()),
            "needs_setup": not c.s.get("setup_done", True), "data_dir": data.DATA,
            "stats": c.stats(), "live": live, "gen": self.gen, "log": LOG[-6:],
        }

    # ---------- actions ----------
    def generate(self, prefs):
        if self.gen["running"]:
            return
        c = self.career
        c.s["prefs"].update({k: v for k, v in prefs.items() if k in career_mod.DEFAULT_PREFS})
        c.save()
        self.gen = {"running": True, "progress": "Finding flights...", "error": None}

        def run():
            try:
                def prog(m):
                    self.gen["progress"] = m
                now = self.sim_time()
                offers, err = self.gen_engine.generate(c, c.s["prefs"], now_utc=now, progress=prog)
                c.s["offers"] = offers
                c.save()
                self.gen = {"running": False, "progress": "", "error": err}
            except Exception as e:
                log("Generator failed:\n" + traceback.format_exc())
                self.gen = {"running": False, "progress": "", "error": f"Something went wrong: {e}"}

        threading.Thread(target=run, daemon=True).start()

    def sim_time(self):
        return datetime.now(timezone.utc)

    def find_flight(self, oid):
        c = self.career
        job = c.s.get("job")
        if job and (not oid or job["id"] == oid):
            return job
        return next((o for o in c.s["offers"] if o["id"] == oid), None)

    def ensure_plan(self, flight, rebuild=False, opts=None):
        if flight and (rebuild or opts is not None or not flight.get("plan")):
            prof = self.profiles.get(flight.get("aircraft")) or self.career.profile()
            if opts is not None:
                flight["plan_opts"] = opts
            flight["plan"] = self.planner.build(flight, prof, self.sim_time(), pilot=self.career.s["settings"].get("pilot_name", ""),
                                                opts=flight.get("plan_opts"))
            flight["plan_sent"] = False
            flight.pop("plan_sent_session", None)
            # sights the route now visits become part of the leg (so flying past them counts)
            if "base_pois" not in flight:
                flight["base_pois"] = list(flight.get("pois", []))
            flight["pois"] = [dict(p) for p in flight["base_pois"]]
            names = {p["name"] for p in flight.get("pois", [])}
            for r in flight["plan"]["route"]:
                if r["kind"] == "visual" and r["name"] not in names:
                    flight.setdefault("pois", []).append({"id": f"r{len(flight['pois'])}", "name": r["name"], "lat": r["lat"], "lon": r["lon"],
                                                          "label": r.get("label") or "sight", "source": r.get("source", "osm"), "off_nm": None, "frac": 0})
            self.career.save()
        return flight.get("plan") if flight else None

    def replan(self, b):
        """Change the plan: a route shape, your own waypoints, a cruise altitude or a later departure."""
        fl = self.find_flight(b.get("id"))
        if not fl:
            return None, "No such flight"
        f = self.tracker.f
        if self.career.s.get("job") is fl and f and f.get("takeoff_t"):
            return None, "You're already flying this one."
        old = fl.get("plan") or {}
        opts = dict(fl.get("plan_opts") or {})
        if b.get("reset"):
            opts = {}
        if "shape" in b:
            opts = {k: v for k, v in opts.items() if k == "depart_offset_min"}
            opts["shape"] = b["shape"]
        if "waypoints" in b:
            opts["waypoints"] = b["waypoints"]
            opts.pop("shape", None)
        if "cruise_alt" in b:
            if b["cruise_alt"]:
                opts["cruise_alt"] = int(b["cruise_alt"])
            else:
                opts.pop("cruise_alt", None)
        if "depart_offset_min" in b:
            opts["depart_offset_min"] = int(b["depart_offset_min"] or 0)
        plan = self.ensure_plan(fl, opts=opts)
        delta = None
        if old:
            delta = {"nm": round(plan["total_nm"] - old.get("total_nm", 0), 1), "min": round(plan["ete_min"] - old.get("ete_min", 0)),
                     "gal": round(plan["fuel"]["total"] - (old.get("fuel") or {}).get("total", 0), 1)}
        return {"plan": plan, "delta": delta}, None

    def wp_search(self, qtext, lat, lon):
        """Airfields, navaids, MSFS POIs and cached landmarks matching what you type, nearest first."""
        qq = qtext.strip().lower()
        if len(qq) < 2:
            return []
        w, out = self.world, []
        for d, a in w.near(lat, lon, 150):
            if qq in (a["code"] or "").lower() or qq in a["ident"].lower() or qq in a["name"].lower() or qq in (a.get("town") or "").lower():
                out.append({"kind": "airport", "ident": a["code"] or a["ident"], "name": a["name"], "lat": a["lat"], "lon": a["lon"],
                            "label": a.get("town") or "", "dist": round(d)})
            if len(out) > 40:
                break
        for d, n in w.navaids_near(lat, lon, 150):
            if n["ident"].lower().startswith(qq) or qq in n["name"].lower():
                out.append({"kind": "nav", "ident": n["ident"], "name": f"{n['name']} {n['type']} {n['freq']}", "freq": n["freq"],
                            "lat": n["lat"], "lon": n["lon"], "label": n["type"], "dist": round(d)})
        for p in w.msfs_near(lat, lon, 150):
            if qq in p["name"].lower():
                out.append({"kind": "visual", "name": p["name"], "lat": p["lat"], "lon": p["lon"], "label": p.get("label") or "MSFS POI",
                            "source": "msfs", "dist": round(geo.dist_nm(lat, lon, p["lat"], p["lon"]))})
        try:
            for p in self.landmarks.near(lat, lon, 80, fetch=False):
                if qq in (p.get("name") or "").lower():
                    out.append({"kind": "visual", "name": p["name"], "lat": p["lat"], "lon": p["lon"], "source": "osm", "wiki": p.get("wiki", ""),
                                "label": generator.story.kind_label(p.get("kind")), "dist": round(geo.dist_nm(lat, lon, p["lat"], p["lon"]))})
        except Exception:
            pass
        out = [r for r in out if r["dist"] > 0 or r["kind"] != "airport"]
        for r in out:
            if (r.get("label") or "").lower() == "msfs":
                r["label"] = "MSFS POI"
        exact = lambda r: 0 if (r.get("ident") or "").lower() == qq else 1
        out.sort(key=lambda r: (exact(r), r["dist"]))
        return out[:10]

    def ap_search(self, qtext):
        qq = qtext.strip().lower()
        if len(qq) < 2:
            return []
        here = self.career.loc()
        out = []
        for a in self.world.airports.values():
            if a.get("military") or a["type"] not in ("small_airport", "medium_airport", "large_airport"):
                continue
            hit = (a["code"] or "").lower() == qq or a["ident"].lower() == qq
            if hit or qq in a["name"].lower() or qq in (a.get("town") or "").lower():
                d = geo.dist_nm(here["lat"], here["lon"], a["lat"], a["lon"])
                rank = (0 if hit and d < 1500 else 1, d > 1500, d)
                out.append((rank, {"ident": a["ident"], "code": a["code"], "name": a["name"], "town": a.get("town") or "",
                                   "country": self.world.country(a["country"]), "dist": round(d), "lat": a["lat"], "lon": a["lon"]}))
        out.sort(key=lambda x: x[0])
        return [o for _, o in out[:10]]

    def anywhere(self, ident):
        a = self.world.airports.get(ident)
        if not a:
            return None, "Unknown airfield."
        if self.career.s.get("job"):
            return None, "Finish or cancel the current leg first."
        o, err = self.gen_engine.custom_offer(self.career, a, self.sim_time(), kind="anywhere", wait=10)
        if err:
            return None, err
        self.career.s["offers"] = [o] + [x for x in self.career.s["offers"] if x["dest"]["ident"] != ident]
        self.career.save()
        return o["id"], None

    def _archive_trip(self, how):
        """Keep finished or abandoned trips for the Plan a trip screen (past trips, fly it again)."""
        c = self.career
        t = c.s.get("trip")
        if not t or t.get("archived"):
            return
        flown = t["legs"][:t.get("idx", 0)]
        if not flown and not t.get("done"):
            return
        c.s.setdefault("trips", []).insert(0, {"name": t["name"], "started": t.get("started"), "done": bool(t.get("done")), "how": how,
                                               "legs": len(t["legs"]), "flown": len(flown), "nm": sum(l["nm"] for l in flown),
                                               "dest": t.get("dest"), "via": t.get("via") or [], "round_trip": bool(t.get("round_trip")),
                                               "path": [[t["legs"][0]["from_lat"], t["legs"][0]["from_lon"]]] + [[l["lat"], l["lon"]] for l in t["legs"]]})
        del c.s["trips"][12:]

    def trip(self, b):
        c = self.career
        act = b.get("action")
        if act in ("preview", "start"):
            t, err = self.trips.plan(c, b.get("ident"), b.get("via") or [], bool(b.get("round_trip")), b.get("leg_nm"))
            if err:
                return None, err
            if act == "preview":
                return t, None
            self._archive_trip("replaced")
            name = b.get("name") or self.trips.name_for(c, t)
            c.s["trip"] = dict(t, name=name, idx=0, started=career_mod.now_iso())
            c.add_note(f"New trip: {name}, {len(t['legs'])} legs over {t['days']} day{'s' if t['days'] > 1 else ''}.")
            c.save()
            self.generate(c.s["prefs"])
            return c.s["trip"], None
        if act == "end":
            if c.s.get("trip") and not c.s["trip"].get("done"):
                c.add_note(f"Called the trip ({c.s['trip']['name']}) off. Onward, somewhere else.")
            self._archive_trip("ended")
            c.s["trip"] = None
            c.save()
            return None, None
        if act == "skip":
            t = c.s.get("trip")
            if not t or t["idx"] + 1 >= len(t["legs"]):
                return None, "There's no stop to skip."
            a, b2 = t["legs"][t["idx"]], t["legs"][t["idx"] + 1]
            src = self.world.airports.get(a["from"])
            nm = geo.dist_nm(src["lat"], src["lon"], b2["lat"], b2["lon"])
            if nm > c.profile()["range_nm"] * 0.85:
                return None, "Skipping that stop makes the leg too long for this aircraft."
            merged = dict(b2, **{"from": a["from"], "from_name": a["from_name"], "from_lat": a["from_lat"], "from_lon": a["from_lon"],
                                 "nm": round(nm), "min": round(nm / c.profile()["cruise_kt"] * 60 + 15)})
            t["legs"][t["idx"]:t["idx"] + 2] = [merged]
            c.save()
            self.generate(c.s["prefs"])
            return t, None
        return None, "Unknown trip action."

    def accept(self, oid):
        c = self.career
        if c.s.get("job"):
            return "Finish or cancel the current leg first."
        offer = next((o for o in c.s["offers"] if o["id"] == oid), None)
        if not offer:
            return "That offer has expired."
        try:
            self.ensure_plan(offer)
        except Exception:
            log("Flight plan failed:\n" + traceback.format_exc())
        c.s["job"] = offer
        c.save()
        self.tracker.begin(offer)
        if self.demo:
            self.tracker.src.place(offer["dep"]["ident"])
        return None

    def save_gtn(self, job):
        """Save the leg as a Garmin .gfp in the GTN folder (TDS GTNXi by default). Returns (path, error)."""
        try:
            self.ensure_plan(job)
        except Exception:
            log("Flight plan failed:\n" + traceback.format_exc())
        folder = (self.career.s["settings"].get("gtn_folder") or "").strip()
        if not folder:
            return None, "Set the GTN flight plan folder in Settings first."
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, planner.gfp_name(job))
            with open(path, "w", encoding="ascii", errors="replace", newline="") as fh:
                fh.write(planner.gfp(job))
            log(f"GTN flight plan saved: {path}")
            return path, None
        except PermissionError:
            return None, f"Windows didn't allow Onward to write to {folder}. Give your user full control of that folder (right-click, Properties, Security)."
        except OSError as e:
            return None, f"Couldn't save the GTN flight plan ({e})."

    def send_plan(self, auto=False):
        """Write the .pln next to the career and ask MSFS to load it (SimConnect FlightPlanLoad)."""
        c = self.career
        job = c.s.get("job")
        if not job:
            return "No active leg."
        try:
            self.ensure_plan(job)
        except Exception:
            log("Flight plan failed:\n" + traceback.format_exc())
        if c.s["settings"].get("gtn_auto") and not self.demo:
            _, err = self.save_gtn(job)
            if err:
                self.tracker.msg(err, "warn")
        folder = os.path.abspath(os.path.join(data.DATA, "plans"))
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, f"onward-{job['dep']['ident']}-{job['dest']['ident']}")
        with open(base + ".pln", "w", encoding="utf-8") as fh:
            fh.write(planner.pln(job))
        if not self.tracker.src.connected:
            return "The sim isn't connected yet. Onward will send the plan once you're in the cockpit."
        if not self.tracker.view().get("sample"):
            return "MSFS only accepts a flight plan once a flight is loaded. Get into the cockpit and Onward sends it by itself (or press Send again)."
        ok = self.tracker.src.load_plan(base)
        job["plan_sent"] = bool(ok)
        if ok:
            job["plan_sent_session"] = getattr(self, "_sim_session", 0)
        c.save()
        if ok:
            log(f"Flight plan sent to MSFS: {base}.pln")
            self.tracker.msg("Flight plan sent to the sim. Check it in the GPS or the EFB.", "good")
            return None
        if not auto:
            return "MSFS didn't accept the flight plan. Download the .pln and load it from the world map instead."
        return None

    def cancel(self):
        c = self.career
        f = self.tracker.f
        if f and f.get("takeoff_t"):
            return "You're already on your way. Land somewhere and press Finish leg."
        self.tracker.cancel()
        c.s["job"] = None
        c.save()
        return None


APP = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/state":
                if "cockpit" in q:
                    APP.cockpit_seen = time.time()
                st = APP.state()
                if st.get("ready"):
                    st["cockpit_active"] = time.time() - getattr(APP, "cockpit_seen", 0) < 6
                return self._json(st)
            if u.path == "/api/credits":
                out = {}
                for name in ("CREDITS.md", "LICENSE.txt"):
                    try:
                        with open(os.path.join(BASE, name), encoding="utf-8") as fh:
                            out[name.split(".")[0].lower()] = fh.read()
                    except OSError:
                        out[name.split(".")[0].lower()] = ""
                return self._json(out)
            if u.path == "/api/history":
                if not APP.ready:
                    return self._json({"legs": []})
                w = APP.world
                by_code = getattr(APP, "_by_code", None)
                if by_code is None:
                    by_code = APP._by_code = {a["code"]: a for a in w.airports.values() if a["code"]}
                legs = []
                for e in APP.career.s["journal"]:
                    if e.get("type") != "leg":
                        continue
                    track = e.get("track")
                    if not track:
                        a = w.airports.get(e.get("from_ident") or "") or by_code.get(e.get("from")) or w.airports.get(e.get("from"))
                        if a and e.get("lat") is not None:
                            track = [[a["lat"], a["lon"]], [e["lat"], e["lon"]]]
                    if track:
                        legs.append({"track": track, "label": f'{e.get("from")} → {e.get("to")}', "date": e.get("at", "")[:10],
                                     "to_name": e.get("to_name"), "outcome": e.get("outcome"), "photos": e.get("photos") or [],
                                     "title": e.get("title"), "nm": e.get("distance_nm"), "grade": (e.get("landing") or {}).get("grade")})
                return self._json({"legs": legs})
            if u.path == "/api/journal":
                c = APP.career
                return self._json({"journal": c.s["journal"][::-1], "stats": c.stats(), "sights": c.s["sights"][::-1],
                                   "friends": c.s["friends"], "visited": [generator.airport_card(APP.world, APP.world.airports[i])
                                                                           for i in c.s["visited"] if i in APP.world.airports]})
            if u.path in ("/api/wx/metars", "/api/wx/grid"):
                s_, w_, n_, e_ = (float(q[k][0]) for k in ("s", "w", "n", "e"))
                if u.path == "/api/wx/metars":
                    return self._json({"metars": APP.weather.in_box(s_, w_, n_, e_)})
                return self._json(APP.planner.wx_grid(s_, w_, n_, e_, int((q.get("alt") or ["5000"])[0])))
            if u.path.startswith("/api/trip/") and not APP.ready:
                return self._json({"error": "Starting up"}, 503)
            if u.path == "/api/trip/map":
                s_, w_, n_, e_ = (float(q[k][0]) for k in ("s", "w", "n", "e"))
                unch = bool(APP.career.s.get("prefs", {}).get("uncharted"))
                return self._json(APP.trips.map_markers(APP.career, s_, w_, n_, e_, int(float((q.get("z") or ["6"])[0])), unch))
            if u.path == "/api/trip/place":
                unch = bool(APP.career.s.get("prefs", {}).get("uncharted"))
                lat, lon = (q.get("lat") or [None])[0], (q.get("lon") or [None])[0]
                p = APP.trips.place(APP.career, (q.get("ident") or [None])[0], None if lat is None else float(lat), None if lon is None else float(lon), unch)
                return self._json({"place": p} if p else {"error": "No airfield near there."})
            if u.path == "/api/trip/home":
                unch = bool(APP.career.s.get("prefs", {}).get("uncharted"))
                return self._json({"stats": APP.trips.stats(APP.career), "ranges": APP.trips.ranges(APP.career),
                                   "past": APP.career.s.get("trips", []), "ideas": APP.trips.ideas(APP.career, unch)})
            if u.path == "/api/wx/cond":
                s_, w_, n_, e_ = (float(q[k][0]) for k in ("s", "w", "n", "e"))
                return self._json(APP.planner.wx_conditions(s_, w_, n_, e_))
            if u.path == "/api/wx/sat":
                return self._json(APP.weather.satellite())
            if u.path == "/api/wx/sigmets":
                return self._json({"sigmets": APP.weather.sigmets()})
            if u.path == "/api/ideas":
                fl = APP.find_flight((q.get("id") or [""])[0])
                if not fl:
                    return self._json({"error": "No such flight"}, 404)
                prof = APP.profiles.get(fl.get("aircraft")) or APP.career.profile()
                return self._json({"ideas": APP.planner.ideas(fl, prof)})
            if u.path == "/api/wpsearch":
                return self._json({"results": APP.wp_search((q.get("q") or [""])[0], float((q.get("lat") or ["0"])[0]), float((q.get("lon") or ["0"])[0]))})
            if u.path == "/api/apsearch":
                return self._json({"results": APP.ap_search((q.get("q") or [""])[0])})
            if u.path == "/api/plan":
                fl = APP.find_flight((q.get("id") or [""])[0])
                if not fl:
                    return self._json({"error": "No such flight"}, 404)
                return self._json({"plan": APP.ensure_plan(fl, rebuild=(q.get("rebuild") or ["0"])[0] == "1")})
            if u.path == "/api/navlog":
                fl = APP.find_flight((q.get("id") or [""])[0])
                if not fl:
                    return self._json({"error": "No such flight"}, 404)
                APP.ensure_plan(fl)
                prof = APP.profiles.get(fl.get("aircraft")) or APP.career.profile()
                body = planner.navlog_html(fl, prof).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/gfp":
                job = APP.find_flight((q.get("id") or [""])[0])
                if not job:
                    return self._json({"error": "No such flight"}, 404)
                try:
                    APP.ensure_plan(job)
                except Exception:
                    log("Flight plan failed:\n" + traceback.format_exc())
                body = planner.gfp(job).encode("ascii", "replace")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Disposition", f'attachment; filename="{planner.gfp_name(job)}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/pln":
                job = APP.find_flight((q.get("id") or [""])[0])
                if not job:
                    return self._json({"error": "No such flight"}, 404)
                try:
                    APP.ensure_plan(job)
                except Exception:
                    log("Flight plan failed:\n" + traceback.format_exc())
                body = planner.pln(job).encode("utf-8")
                fname = f'{job["dep"]["code"] or "STRIP"}-{job["dest"]["code"] or "STRIP"}.pln'
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/sight":
                if not APP.ready:
                    return self._json({"error": "starting"})
                g = lambda k: (q.get(k) or [""])[0]
                lat, lon = g("lat"), g("lon")
                return self._json(APP.wiki.lookup(g("name"), float(lat) if lat else None, float(lon) if lon else None, g("wiki")))
            if u.path == "/api/pois":
                if not APP.ready:
                    return self._json({"pois": [], "missing": 0, "status": {}})
                s_, w_, n_, e_ = (float(q[k][0]) for k in ("s", "w", "n", "e"))
                pois, missing = APP.landmarks.in_box(s_, w_, n_, e_, fetch=(n_ - s_) < 4)
                if (q.get("msfs") or ["0"])[0] == "1":  # zoomed far out: only the sim's own points of interest
                    pois, missing = [p for p in pois if p.get("source") == "msfs"], 0
                pois = [dict(p, label=p.get("label") or ("MSFS POI" if p.get("source") == "msfs" else generator.story.kind_label(p.get("kind")))) for p in pois]
                return self._json({"pois": pois, "missing": missing, "status": APP.landmarks.status()})
            if u.path.startswith("/photos/"):
                base = os.path.abspath(photos_mod.PHOTO_DIR)
                full = os.path.normpath(os.path.join(base, unquote(u.path[len("/photos/"):])))
                if not full.startswith(base + os.sep) or not os.path.isfile(full):
                    self.send_error(404)
                    return
                ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
                with open(full, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/photo_folders":
                return self._json({"folders": photos_mod.folders(APP.career.s["settings"]) if APP.ready else []})
            path = u.path if u.path != "/" else "/index.html"
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not full.startswith(WEB) or not os.path.isfile(full):
                self.send_error(404)
                return
            ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml", ".png": "image/png"}.get(os.path.splitext(full)[1], "application/octet-stream")
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            log("GET error:\n" + traceback.format_exc())
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        try:
            if not APP.ready:
                return self._json({"error": "Still starting up"}, 503)
            c = APP.career
            err = None
            if u.path == "/api/generate":
                APP.generate(b.get("prefs", {}))
            elif u.path == "/api/accept":
                err = APP.accept(b.get("id"))
            elif u.path == "/api/setup":
                err = c.setup(b.get("pilot_name", ""), b.get("home", ""))
                if not err:
                    c.s["offers"] = []
                    c.save()
            elif u.path == "/api/update/check":
                APP.updater.check()
            elif u.path == "/api/update/apply":
                if c.s.get("job") and APP.tracker.f and APP.tracker.f.get("takeoff_t"):
                    err = "Finish the leg you're flying first."
                else:
                    err = APP.updater.apply()
                    if not err:
                        APP.restart_soon()
            elif u.path == "/api/update/rollback":
                err = APP.updater.rollback()
                if not err:
                    APP.restart_soon()
            elif u.path == "/api/replan":
                res, err = APP.replan(b)
                if not err:
                    return self._json(dict(res, ok=True))
            elif u.path == "/api/anywhere":
                oid, err = APP.anywhere(b.get("ident"))
                if not err:
                    return self._json({"ok": True, "id": oid})
            elif u.path == "/api/trip":
                res, err = APP.trip(b)
                if not err:
                    return self._json({"ok": True, "trip": res})
            elif u.path == "/api/gtn":
                job = APP.find_flight(b.get("id"))
                if not job:
                    return self._json({"error": "No such flight"}, 404)
                path, err = APP.save_gtn(job)
                return self._json({"error": err} if err else {"ok": True, "path": path, "name": os.path.basename(path)})
            elif u.path == "/api/load_plan":
                err = APP.send_plan()
            elif u.path == "/api/cancel":
                err = APP.cancel()
            elif u.path == "/api/finish":
                res, err = APP.tracker.finish(force=bool(b.get("force")))
            elif u.path == "/api/repair":
                err = c.start_repair(b.get("kind", "shop"))
            elif u.path == "/api/aircraft":
                if c.s.get("job"):
                    err = "Can't swap aircraft in the middle of a leg."
                else:
                    c.set_aircraft(b.get("id"))
            elif u.path == "/api/settings":
                for k, v in (b.get("settings") or {}).items():
                    if k in career_mod.DEFAULT_SETTINGS and not (k == "update_token" and v == "••••"):
                        c.s["settings"][k] = v
                c.save()
            elif u.path == "/api/dismiss":
                c.s["last_result"] = None
                c.save()
            elif u.path == "/api/decline_friend":
                fr = c.s.get("pending_friend")
                if fr:
                    c.add_note(f"Said goodbye to {fr['name']}. Maybe another time.")
                    c.s["pending_friend"] = None
                    c.save()
            elif u.path == "/api/reload_pois":
                APP.world.load_msfs_files()
                APP.landmarks.mem.clear()
            elif u.path == "/api/demo" and APP.demo:
                src = APP.tracker.src
                job = c.s.get("job")
                if b.get("action") == "fly" and job:
                    plan = job.get("plan") or {}
                    src.fly(job["dest"], path=plan.get("route"), runway=((plan.get("arrival") or {}).get("runway") or {}).get("end"), landing_fpm=int(b.get("fpm", 250)), duration=int(b.get("duration", 60)),
                            rough=bool(b.get("rough")))
                elif b.get("action") == "crash":
                    src.s["crash"] = 1
            else:
                return self._json({"error": "unknown"}, 404)
            return self._json({"ok": err is None, "error": err})
        except Exception as e:
            log("POST error:\n" + traceback.format_exc())
            self._json({"error": str(e)}, 500)


def main():
    global APP
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--start", default="KHAF")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    APP = App(demo=a.demo, start=a.start)
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    url = f"http://localhost:{a.port}"
    log(f"Onward is running at {url}  (other devices on your network: http://<this PC's IP>:{a.port})")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
