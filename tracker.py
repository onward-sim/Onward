"""Reads the sim, follows the flight, keeps score on how the airplane was treated."""
import math
import os
import random
import threading
import time
import traceback
from collections import deque

import accusim
import career as career_mod
import geo
import photos
import story

FAST = {
    "lat": ("PLANE LATITUDE", "degrees"), "lon": ("PLANE LONGITUDE", "degrees"),
    "alt": ("PLANE ALTITUDE", "feet"), "agl": ("PLANE ALT ABOVE GROUND", "feet"),
    "on_ground": ("SIM ON GROUND", "bool"), "ias": ("AIRSPEED INDICATED", "knots"),
    "gs": ("GROUND VELOCITY", "knots"), "vs": ("VERTICAL SPEED", "feet per minute"),
    "g": ("G FORCE", "GForce"), "gmax": ("MAX G FORCE", "GForce"), "gmin": ("MIN G FORCE", "GForce"),
    "td_vs": ("PLANE TOUCHDOWN NORMAL VELOCITY", "feet per second"),
    "hdg": ("PLANE HEADING DEGREES TRUE", "degrees"),
    "bank": ("PLANE BANK DEGREES", "degrees"), "pitch": ("PLANE PITCH DEGREES", "degrees"),
}
# Values every aircraft has. A sample is only used once all of them have arrived: right after loading into the
# cockpit, MSFS sends the position before the rest, and half a sample broke the tracker and the page.
CORE = ("lat", "lon", "alt", "agl", "on_ground", "ias", "gs", "vs", "hdg", "bank", "pitch")
SLOW = {
    "rpm": ("GENERAL ENG RPM:1", "rpm"), "prop_rpm": ("PROP RPM:1", "rpm"),
    "cht": ("ENG CYLINDER HEAD TEMPERATURE:1", "fahrenheit"),
    "combustion": ("GENERAL ENG COMBUSTION:1", "bool"),
    "flaps": ("FLAPS HANDLE INDEX", "number"), "gear": ("GEAR HANDLE POSITION", "bool"),
    "crash": ("CRASH FLAG", "enum"), "slew": ("IS SLEW ACTIVE", "bool"),
    # the sim's own clock, so "night" means night in the sim (which can be set to any time)
    "zulu_s": ("E:ZULU TIME", "seconds"), "zulu_doy": ("E:ZULU DAY OF YEAR", "number"), "zulu_year": ("E:ZULU YEAR", "number"),
}


def sim_time(s):
    """The sim's UTC time from a sample, or the real time when the sim doesn't say."""
    from datetime import datetime, timedelta, timezone
    try:
        y, d, sec = int(s["zulu_year"]), int(s["zulu_doy"]), float(s["zulu_s"])
        if 1990 < y < 2200 and 1 <= d <= 366:
            return datetime(y, 1, 1, tzinfo=timezone.utc) + timedelta(days=d - 1, seconds=sec)
    except (KeyError, TypeError, ValueError):
        pass
    return datetime.now(timezone.utc)

FAILURES = {
    "engine": [("TOGGLE_VACUUM_FAILURE", "Vacuum pump failure"), ("TOGGLE_ELECTRICAL_FAILURE", "Electrical failure")],
    "engine_severe": [("TOGGLE_ENGINE1_FAILURE", "Engine failure")],
    "airframe": [("TOGGLE_PITOT_BLOCKAGE", "Pitot tube blocked"), ("TOGGLE_STATIC_PORT_BLOCKAGE", "Static port blocked")],
    "gear": [("TOGGLE_LEFT_BRAKE_FAILURE", "Left brake failure"), ("TOGGLE_RIGHT_BRAKE_FAILURE", "Right brake failure")],
    "prop": [("TOGGLE_VACUUM_FAILURE", "Vacuum pump failure")],
}


def _thin(pts, limit=400):
    if len(pts) <= limit:
        return pts
    step = len(pts) / (limit - 1)
    return [pts[int(i * step)] for i in range(limit - 1)] + [pts[-1]]


def is_grey(job):
    for w in (job.get("weather"), job.get("dep_weather")):
        if w and w.get("cat") in ("IFR", "LIFR"):
            return True
    return False


class SimConnectSource:
    """Live MSFS connection through the SimConnect Python package."""

    def __init__(self, log=print):
        self.log = log
        self.sm = None
        self.reqs = {}
        self.slow = {}
        self.title_req = None
        self.connected = False
        self.last_try = 0
        self.slow_at = 0
        self.slow_vals = {}
        self.title = ""
        self.error = ""
        self.connected_at = 0
        self.last_data = 0
        self.accu = {}        # AccuSim variables, only asked for while an A2A aircraft is loaded
        self.accu_at = 0
        self.accu_vals = None

    def try_connect(self):
        if self.connected or time.time() - self.last_try < 5:
            return self.connected
        self.last_try = time.time()
        try:
            from SimConnect import SimConnect, Request
        except Exception as e:
            self.error = f"SimConnect package missing ({e}). Run start.bat again."
            self.log(self.error)
            return False
        try:
            sm = SimConnect(auto_connect=False)
            done = {}

            def _open():
                try:
                    sm.connect()
                    done["ok"] = True
                except Exception as e:  # noqa: BLE001
                    done["err"] = e
            th = threading.Thread(target=_open, daemon=True)
            self.pending = th
            th.start()
            th.join(8)
            if not done.get("ok") or not getattr(sm, "ok", False):
                # MSFS took the connection but never said it was ready (it does that while starting up).
                # The library would wait for that forever, keeping a CPU core busy and blocking every retry,
                # so let this attempt go and try again with a fresh one in a few seconds.
                self._close(sm)
                raise ConnectionError(done.get("err") or "MSFS didn't answer (still starting up?)")
            self.sm = sm
            self.reqs = {k: Request((v[0].encode(), v[1].encode()), self.sm, _time=0) for k, v in FAST.items()}
            self.slow = {k: Request((v[0].encode(), v[1].encode()), self.sm, _time=0) for k, v in SLOW.items()}
            self.title_req = Request((b"TITLE", b"String"), self.sm, _time=0)
            self.accu, self.accu_vals, self.accu_at = {}, None, 0
            self.connected = True
            self.connected_at = time.time()
            self.last_data = 0
            self.error = "Connected. Waiting for flight data (load into the cockpit)."
            self.log("Connected to Microsoft Flight Simulator.")
        except Exception as e:
            self.sm = None
            self.error = "Waiting for Microsoft Flight Simulator..."
            if time.time() - getattr(self, "last_err_log", 0) > 60:
                self.last_err_log = time.time()
                self.log(f"SimConnect: can't connect yet ({e}). Is MSFS running on this PC?")
        return self.connected

    @staticmethod
    def _close(sm):
        """End a SimConnect session cleanly: stop its threads and close the handle, so MSFS isn't left with stale connections."""
        if sm is None:
            return
        try:
            sm.quit = 1      # stops the library's dispatch thread
            sm.ok = True     # releases its "wait until MSFS is ready" loop, if it's stuck there
            t = getattr(sm, "timerThread", None)
            if t is not None and t.is_alive():
                t.join(1)
            if getattr(sm, "hSimConnect", None) is not None:
                sm.dll.Close(sm.hSimConnect)
        except Exception:
            pass

    def _drop(self, why):
        self.log(why)
        self.connected = False
        self._close(self.sm)
        self.sm = None

    def read(self):
        if not self.try_connect():
            return None
        try:
            if getattr(self.sm, "quit", 0) == 1:
                raise ConnectionError("sim closed")
            out = {k: r.value for k, r in self.reqs.items()}
            if time.time() - self.slow_at > 1.0:
                self.slow_at = time.time()
                self.slow_vals = {k: r.value for k, r in self.slow.items()}
                tt = self.title_req.value
                if isinstance(tt, bytes):
                    tt = tt.decode("utf-8", "ignore")
                self.title = (tt or "").strip("\x00 ")
            out.update(self.slow_vals)
            out["title"] = self.title
            out["accusim"] = self._read_accusim()
            if any(out.get(k) is None for k in CORE) or (out.get("lat") == 0 and out.get("lon") == 0):
                if not getattr(self, "missing_logged", False) and time.time() - self.connected_at > 10:
                    self.missing_logged = True
                    self.log("Connected, but MSFS isn't sending aircraft data yet. Missing: "
                             + ", ".join(k for k, v in out.items() if v is None)[:300])
                # connected but no aircraft data: main menu, loading, or a dead connection
                if time.time() - max(self.connected_at, self.last_data) > 45:
                    self._drop("No flight data from MSFS for a while. Reconnecting...")
                return None
            self.last_data = time.time()
            self.error = ""
            return out
        except Exception as e:
            self._drop(f"Lost the sim connection ({e}).")
            return None

    def _read_accusim(self):
        """AccuSim condition values every few seconds while an A2A aircraft is loaded (they change slowly)."""
        if not accusim.is_a2a(self.title):
            self.accu_vals = None
            return None
        if time.time() - self.accu_at < 5:
            return self.accu_vals
        self.accu_at = time.time()
        if not self.accu:
            from SimConnect import Request
            self.accu = {n: Request((f"L:{n}".encode(), b"number"), self.sm, _time=0) for n in accusim.var_names()}
        vals = {n: r.value for n, r in self.accu.items()}
        self.accu_vals = vals if accusim.is_active(vals) else None
        return self.accu_vals

    def send(self, name):
        if not self.connected:
            return False
        try:
            from SimConnect import Event
            Event(name.encode(), self.sm)()
            return True
        except Exception as e:
            self.log(f"Could not send {name}: {e}")
            return False


def demo_accusim():
    """AccuSim values like a real Comanche with about 20 hours on it (from a read of the actual aircraft)."""
    v = {n: 1.0 for n in accusim.var_names() if n.startswith("C_")}
    v.update({n: 0.0 for n in accusim.var_names() if n.startswith("F_")})
    v.update({"AccusimPublic": 1.0, "TotalTime": 21.76, "Eng1_Time": 19.24, "Eng1_OilTankQuantityPct": 59.0,
              "C_Eng1_Main": 0.899, "C_Eng1_MagL": 0.860, "C_Eng1_MagR": 0.896, "C_Eng1_Oilsystem": 0.863,
              "C_Eng1_OilFilter": 0.699, "C_Eng1_Starter": 0.945, "C_Eng1_Prop": 0.902, "C_Eng1_Generator": 0.934,
              "C_Eng1_VacuumPump": 0.907, "C_Eng1_AirFilter": 0.877, "C_Battery1": 0.935, "C_BrakesLeft": 0.981,
              "C_BrakesRight": 0.968, "C_WingflapLeft": 0.729, "C_WingflapRight": 0.729, "C_Eng1_Cyl[1]": 0.888,
              "C_Eng1_Cyl[2]": 0.925, "C_Eng1_Cyl[3]": 0.976, "C_Eng1_Cyl[4]": 0.968, "C_Eng1_Cyl[5]": 0.997,
              "C_Eng1_Cyl[6]": 0.904, "C_Eng1_Cyl3_SparkPlugL": 0.926, "C_Eng1_Cyl5_SparkPlugL": 0.888,
              "C_Eng1_Carb": 0.0, "C_GearCenter": 1.0})
    return v


class DemoSource:
    """A pretend airplane, so the whole loop can be tried without the sim."""

    def __init__(self, career, log=print):
        self.career, self.log = career, log
        self.connected = True
        self.error = ""
        a = career.loc()
        self.s = {"lat": a["lat"], "lon": a["lon"], "alt": a["elev"], "agl": 0, "on_ground": 1, "ias": 0, "gs": 0,
                  "vs": 0, "g": 1, "gmax": 1, "gmin": 1, "td_vs": 0, "hdg": 0, "rpm": 0, "prop_rpm": 0, "cht": 100,
                  "combustion": 0, "flaps": 0, "gear": 1, "crash": 0, "slew": 0, "title": "Demo Cessna 172 Skyhawk"}
        self.plan = None
        self.t = time.time()

    def place(self, ident):
        a = self.career.world.airports.get(ident)
        if a:
            self.s.update(lat=a["lat"], lon=a["lon"], alt=a["elev"], agl=0, on_ground=1, ias=0, gs=0, vs=0, crash=0, gmax=1, gmin=1, g=1)
            self.plan = None

    def fly(self, dest, landing_fpm=250, duration=75, rough=False, path=None, runway=None):
        end = [(dest["lat"], dest["lon"])]
        ap = self.career.world.airports.get(dest.get("ident")) or {}
        rws = [r for r in ap.get("runways", []) if None not in (r.get("le_lat"), r.get("le_lon"), r.get("he_lat"), r.get("he_lon"))]
        if rws:  # finish on a straight-in final to the planned runway, touching down ~800 ft past the threshold
            r = next((r for r in rws if runway in (r["le"], r["he"])), None) or max(rws, key=lambda r: r["length"])
            if runway == r["he"]:
                tl, to, ol, oo, disp = r["he_lat"], r["he_lon"], r["le_lat"], r["le_lon"], r.get("he_disp") or 0
            else:
                tl, to, ol, oo, disp = r["le_lat"], r["le_lon"], r["he_lat"], r["he_lon"], r.get("le_disp") or 0
            crs = geo.bearing(tl, to, ol, oo)
            fa = geo.destination(tl, to, (crs + 180) % 360, 3)
            td = geo.destination(tl, to, crs, (disp + 800) / 6076)
            end = [fa, td]
        pts = [(self.s["lat"], self.s["lon"])] + [(p["lat"], p["lon"]) for p in (path or [])[1:-1]] + end
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + geo.dist_nm(a[0], a[1], b[0], b[1]))
        self.plan = {"dest": dest, "t0": time.time(), "dur": duration, "fpm": landing_fpm, "rough": rough,
                     "from": (self.s["lat"], self.s["lon"]), "elev0": self.s["alt"], "pts": pts, "cum": cum}

    def read(self):
        p = self.plan
        s = self.s
        s["title"] = "Demo " + self.career.profile()["name"]
        if os.environ.get("ONWARD_DEMO_ACCUSIM"):
            # pretend to be an A2A aircraft, to try the AccuSim screens without the sim
            s["title"] = "Demo A2A " + self.career.profile()["name"]
            s["accusim"] = demo_accusim()
        if not p:
            return dict(s)
        el = time.time() - p["t0"]
        dest = p["dest"]
        f = max(0.0, min(1.0, (el - 6) / (p["dur"] - 16)))
        lat0, lon0 = p["from"]
        pts, cum = p["pts"], p["cum"]
        target = f * cum[-1]
        k = max(0, min(len(pts) - 2, next((j for j in range(len(cum) - 1) if cum[j + 1] >= target), len(pts) - 2)))
        seg = cum[k + 1] - cum[k] or 1
        g = (target - cum[k]) / seg
        (la, lo), (lb, lob) = pts[k], pts[k + 1]
        s["lat"] = la + (lb - la) * g
        s["lon"] = lo + (lob - lo) * g
        s["hdg"] = geo.bearing(la, lo, lb, lob)
        s["gs"] = cum[-1] / max(1, p["dur"] - 16) * 3600 if 0 < f < 1 else s.get("gs", 0)
        s["rpm"] = 2400 if el < p["dur"] - 4 else 0
        s["prop_rpm"] = s["rpm"]
        s["combustion"] = 1 if s["rpm"] else 0
        s["cht"] = 380
        ground = dest["elev"] if f >= 1 else p["elev0"] + (dest["elev"] - p["elev0"]) * f
        if el < 6:
            s.update(on_ground=1, ias=min(60, el * 12), gs=min(60, el * 12), agl=0, alt=p["elev0"], vs=0)
        elif f < 1:
            cruise = 3000 if 0.15 < f < 0.85 else 3000 * (f / 0.15 if f <= 0.15 else (1 - f) / 0.15)
            s.update(on_ground=0, ias=110, gs=s.get("gs", 110), agl=max(5, cruise), alt=ground + max(5, cruise),
                     vs=600 if f < 0.15 else (-p["fpm"] if f > 0.85 else 0))
            if f > 0.9:
                s["ias"] = (self.career.profile().get("vref") or 65) + (12 if f < 0.97 else 3)
            if p["rough"] and 0.4 < f < 0.45:
                s["ias"] = 175
            if p["rough"] and 0.5 < f < 0.51:
                s["g"] = 4.2
                s["gmax"] = max(s["gmax"], 4.2)
            else:
                s["g"] = 1.0
        else:
            if not s["on_ground"]:
                s["td_vs"] = -p["fpm"] / 60
            spd = max(0, (self.career.profile().get("vref") or 60) - 4 - (el - (p["dur"] - 10)) * 9)
            s.update(on_ground=1, agl=0, alt=dest["elev"], vs=0, ias=spd, gs=spd)
        return dict(s)

    def send(self, name):
        self.log(f"[demo] sim event {name}")
        return True


class Tracker:
    def __init__(self, career, world, landmarks, source, log=print, weather=None):
        self.career, self.world, self.landmarks, self.src, self.log = career, world, landmarks, source, log
        self.weather = weather
        self.live = None
        self.f = None
        self.err_logged = {}
        self.lock = threading.RLock()
        self.running = True
        self.photo_dirs, self.photo_dirs_t = [], 0
        self.accu_last = None
        if career.s.get("job"):
            self.begin(career.s["job"], resume=True)
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._photo_loop, daemon=True).start()

    # ---------- lifecycle ----------
    def begin(self, job, resume=False):
        with self.lock:
            self.f = {"phase": "preflight", "job": job, "msgs": [], "events": [], "landings": [],
                      "acc": {}, "seen": [], "planned": [], "active": [], "takeoff_t": None, "air_s": 0.0,
                      "nm": 0.0, "last": None, "gpeak": 1.0, "gmin": 1.0, "cht_hist": deque(maxlen=200),
                      "shock_t": 0, "ground_slow_s": 0.0, "td_t": None, "excursion": False, "crash": False,
                      "dep_warned": False, "maxalt": 0, "exceed": {}, "go_arounds": 0, "ga_armed": None,
                      "wx_check_t": time.time(), "dest_wx": job.get("weather"), "wp_idx": 1, "prealert": [],
                      "arrival_shown": False, "track": [], "poslog": deque(maxlen=2000), "poslog_t": 0,
                      "accusim": False}
            if not resume or "photo_since" not in job:
                job["photo_since"] = time.time() - 30
                job["photos"] = []
                job["photo_dir"] = time.strftime("%Y-%m-%d") + f"-{job['dep']['ident']}-{job['dest']['ident']}-{job['id']}"
            self.msg("Leg loaded. Head to the runway when you're ready." if not resume else "Resumed the active leg.")

    def cancel(self):
        with self.lock:
            self._clear_failures()
            self.f = None

    def msg(self, text, level="info", quiet=False):
        """A line in the flight log. quiet: shown but not spoken by the callouts."""
        if self.f is not None:
            m = {"t": time.strftime("%H:%M:%S"), "text": text, "level": level}
            if quiet:
                m["quiet"] = True
            self.f["msgs"].append(m)
            self.f["msgs"] = self.f["msgs"][-40:]

    def damage(self, system, amount, reason, quiet=False):
        if amount <= 0:
            return
        f = self.f
        for e in f["events"]:
            if e["reason"] == reason and e["system"] == system:
                e["amount"] = round(e["amount"] + amount, 1)
                break
        else:
            f["events"].append({"system": system, "amount": round(amount, 1), "reason": reason})
            if not quiet:
                self.msg(f"{reason}", "warn")

    # ---------- main loop ----------
    def _loop(self):
        last = time.time()
        while self.running:
            time.sleep(0.25)
            now = time.time()
            dt = min(1.0, now - last)
            last = now
            try:
                s = self.src.read()
            except Exception as e:
                self.log(f"Sim read error: {e}")
                s = None
            with self.lock:
                self.live = s
                if s and not s.get("accusim") and not accusim.is_a2a(s.get("title")) and self.career.accusim():
                    title = (s.get("title") or "").lower()
                    if any(m in title for m in self.career.profile().get("match", [])):
                        self.career.clear_accusim()
                        self.log(f"This {self.career.profile()['name']} isn't an A2A AccuSim aircraft: Onward keeps its condition again.")
                if s and s.get("accusim") is not self.accu_last:
                    self.accu_last = s.get("accusim")
                    try:
                        self._sync_accusim(s)
                    except Exception as e:
                        self.log(f"AccuSim read failed: {e}")
                if s and self.f:
                    try:
                        self._update(s, dt)
                        if self.f and self.f["phase"] == "done":
                            self.finish()
                    except Exception as e:
                        tb = traceback.extract_tb(e.__traceback__)
                        where = f" (tracker.py line {tb[-1].lineno})" if tb else ""
                        key, now2 = f"{e}{where}", time.time()
                        if now2 - self.err_logged.get(key, 0) > 60:
                            self.err_logged[key] = now2
                            self.log(f"Tracker error: {e}{where}")

    def _sync_accusim(self, s):
        """An A2A aircraft is loaded: its AccuSim condition becomes the condition of the matching Onward airplane."""
        raw = s.get("accusim")
        prof = self.career.profile()
        title = (s.get("title") or "").lower()
        if not raw or not any(m in title for m in prof.get("match", [])):
            return
        summary = accusim.summarize(raw, prof.get("cylinders"))
        if summary:
            if not self.career.accusim():
                self.log(f"A2A AccuSim found: the {prof['name']}'s condition now comes from AccuSim.")
            self.career.sync_accusim(summary)

    def _update(self, s, dt):
        f = self.f
        job = f["job"]
        if s.get("accusim"):
            f["accusim"] = True
        prof = self.career.profile()
        on_ground = bool(s.get("on_ground"))
        lat, lon = s["lat"], s["lon"]

        # teleport / slew / back-to-menu detection
        if f["last"]:
            jump = geo.dist_nm(f["last"][0], f["last"][1], lat, lon)
            if jump > 8 and dt < 2:
                if f["phase"] in ("air", "rollout"):
                    self.msg("The aircraft jumped across the map. The flight was reset; nothing counted.", "warn")
                f.update(phase="preflight", events=[], landings=[], takeoff_t=None, air_s=0.0, nm=0.0, seen=[])
                self._clear_failures()
            elif f["phase"] in ("air", "rollout"):
                f["nm"] += jump
                tr = f["track"]
                if not tr or geo.dist_nm(tr[-1][0], tr[-1][1], lat, lon) >= 0.3:
                    tr.append([round(lat, 4), round(lon, 4)])
        f["last"] = (lat, lon)
        if time.time() - f["poslog_t"] >= 3:
            f["poslog_t"] = time.time()
            f["poslog"].append((f["poslog_t"], lat, lon, round(s.get("alt") or 0), round(s.get("agl") or 0), f["phase"]))
        if s.get("slew"):
            return

        # crash
        if s.get("crash") and f["phase"] in ("air", "rollout", "taxi_in", "preflight") and not f["crash"]:
            if f["takeoff_t"]:
                f["crash"] = True
                self.msg("Crash detected.", "bad")
                f["phase"] = "done"
                return

        if f["phase"] == "preflight":
            if not on_ground and (s.get("agl") or 0) > 40 and (s.get("ias") or 0) > 35:
                f["phase"] = "air"
                f["takeoff_t"] = time.time()
                f["gpeak"] = max(1.0, s.get("gmax") or 1.0, s.get("g") or 1.0)
                f["gmin"] = min(1.0, s.get("gmin") or 1.0)
                dep = job["dep"]
                d = geo.dist_nm(lat, lon, dep["lat"], dep["lon"])
                if d > 3:
                    self.msg(f"You took off {d:.0f} nm from {dep['name']}. The leg still counts from here.", "warn")
                else:
                    self.msg(f"Airborne from {dep['name']}. Enjoy the view.")
                self._plan_failures(job, prof)
            return

        if f["phase"] == "air":
            f["air_s"] += dt
            f["maxalt"] = max(f["maxalt"], s.get("alt") or 0)
            self._exceedances(s, dt, prof)
            self._pois(s)
            self._failures_due()
            self._approach_watch(s)
            self._stable_gate(s)
            self._advance_waypoint(s)
            if not f["arrival_shown"] and geo.dist_nm(s["lat"], s["lon"], job["dest"]["lat"], job["dest"]["lon"]) < 20:
                f["arrival_shown"] = True
                arr = (job.get("plan") or {}).get("arrival") or {}
                rw = arr.get("runway") or {}
                self.msg(f"20 nm to {job['dest']['name']}. " + (f"Expect runway {rw['end']}, circuit altitude {arr.get('tpa'):,} ft. " if rw else "")
                         + "Arrival briefing is up.", "info")
            if job.get("rules") == "IFR" and time.time() - f["wx_check_t"] > 600:
                f["wx_check_t"] = time.time()
                threading.Thread(target=self._dest_weather_update, args=(job,), daemon=True).start()
            if on_ground:
                self._touchdown(s)
            return

        if f["phase"] in ("rollout", "taxi_in"):
            f["gpeak"] = max(f["gpeak"], s.get("gmax") or 1.0, s.get("g") or 1.0)
            f["gmin"] = min(f["gmin"], s.get("gmin") or 1.0)
        if f["phase"] == "rollout":
            if not on_ground and (s.get("agl") or 0) > 15:
                if f["td_t"] and time.time() - f["td_t"] < 8:
                    f["landings"][-1]["bounces"] += 1
                    self.msg("Bounce!", "warn")
                else:
                    self.msg("Back in the air (touch-and-go).")
                    f["gate_done"] = False
                f["phase"] = "air"
                return
            if on_ground and (s.get("gs") or 0) > 25:
                self._excursion_check(lat, lon)
            if (s.get("gs") or 0) < 4:
                f["ground_slow_s"] += dt
            else:
                f["ground_slow_s"] = 0
            if f["ground_slow_s"] > 3:
                f["phase"] = "taxi_in"
                self.msg("Landed. Taxi in and shut down, or press Finish leg.")
            return

        if f["phase"] == "taxi_in":
            if not on_ground and (s.get("agl") or 0) > 40:
                f["phase"] = "air"
                self.msg("Departed again. The leg continues.")
                return
            engine_off = not s.get("combustion") and (s.get("rpm") or 0) < 100
            if engine_off and (s.get("gs") or 0) < 2:
                f["ground_slow_s"] += dt
                if f["ground_slow_s"] > 8:
                    f["phase"] = "done"
                    self.msg("Engine shut down. Leg complete.")
            return

    def _touchdown(self, s):
        f = self.f
        td = s.get("td_vs")
        fpm = abs(td) * 60 if td not in (None, 0) else abs(min(0, s.get("vs") or 0))
        if f["td_t"] and time.time() - f["td_t"] < 8 and f["landings"]:
            L = f["landings"][-1]
            L["fpm"] = max(L["fpm"], round(fpm))
        else:
            f["landings"].append({"fpm": round(fpm), "lat": s["lat"], "lon": s["lon"], "bounces": 0,
                                  "gs": round(s.get("gs") or 0)})
            try:
                f["landings"][-1]["card"] = self._scorecard(s, fpm)
            except Exception as e:
                self.log(f"Landing scorecard error: {e}")
        f["td_t"] = time.time()
        f["phase"] = "rollout"
        f["ground_slow_s"] = 0
        card = f["landings"][-1].get("card") or {}
        self.msg(f"Touchdown at {round(fpm)} fpm: {story.landing_words(fpm)}." + (f" Landing grade {card['grade']} ({card['score']})." if card.get("grade") else ""))
        for p in [p for p in f["planned"] if p["at"] == "touchdown"]:
            self._trigger(p)

    # ---------- screenshots ----------
    def _photo_loop(self):
        while self.running:
            time.sleep(4)
            try:
                self.photo_scan()
            except Exception as e:
                self.log(f"Screenshot check failed: {e}")

    def photo_scan(self):
        st = self.career.s["settings"]
        with self.lock:
            f = self.f
            if not f or not st.get("photos", True):
                return
            job = f["job"]
            since = job.get("photo_since", time.time())
            known = {p["src"] for p in job.get("photos", [])}
            poslog = list(f["poslog"])
        if time.time() - self.photo_dirs_t > 60:
            self.photo_dirs, self.photo_dirs_t = photos.folders(st), time.time()
        new = photos.scan(self.photo_dirs, since, known)
        for mtime, path in new[:20]:
            pos = min(poslog, key=lambda p: abs(p[0] - mtime)) if poslog else None
            if pos is None or abs(pos[0] - mtime) > 120:
                s = self.live or {}
                if s.get("lat") is None:
                    continue
                pos = (mtime, s["lat"], s["lon"], round(s.get("alt") or 0), round(s.get("agl") or 0), (f or {}).get("phase", "air"))
            idx = len(job.get("photos", [])) + 1
            try:
                rel, thumb = photos.store(path, job["photo_dir"], idx)
            except Exception as e:
                self.log(f"Couldn't copy screenshot {path}: {e}")
                known.add(path)
                job.setdefault("photos", []).append({"src": path, "skip": True})
                continue
            _, lat, lon, alt, agl, phase = pos
            p = {"src": path, "file": rel, "thumb": thumb, "lat": round(lat, 5), "lon": round(lon, 5), "alt": alt, "agl": agl,
                 "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)), "where": self._photo_place(lat, lon, agl, phase, job)}
            with self.lock:
                if self.f is not f:
                    return
                job.setdefault("photos", []).append(p)
                self.msg(f"Photo saved {p['where']}.", "good", quiet=True)
                self.career.save()

    def _photo_place(self, lat, lon, agl, phase, job):
        if phase in ("preflight", "taxi_in", "done") or (agl or 0) < 50:
            d, ap = self.world.nearest(lat, lon, 2)
            if ap:
                return f"at {ap['name']}"
        sights = list(job.get("pois", [])) + list(job.get("dest_pois", []))
        try:
            sights += self.world.msfs_near(lat, lon, 4)
        except Exception:
            pass
        best = min(((geo.dist_nm(lat, lon, p["lat"], p["lon"]), p) for p in sights), key=lambda x: x[0], default=(None, None))
        if best[1] and best[0] <= 3:
            return f"over {best[1]['name']}" if best[0] <= 1 else f"near {best[1]['name']}"
        d, ap = self.world.nearest(lat, lon, 12)
        if ap:
            town = ap.get("town") or ap["name"]
            return f"near {town}"
        return f"at {abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"

    def _runway_for(self, lat, lon, hdg, max_diff=40, radius=3.5):
        """The runway end we're landing on (or lined up with): heading close to ours and nearest the extended centerline."""
        best = None
        for d, ap in list(self.world.near(lat, lon, radius))[:8]:
            for rw in ap["runways"]:
                if None in (rw["le_lat"], rw["le_lon"], rw["he_lat"], rw["he_lon"]):
                    continue
                for end, tl, to, ol, oo, disp in ((rw["le"], rw["le_lat"], rw["le_lon"], rw["he_lat"], rw["he_lon"], rw.get("le_disp", 0)),
                                                  (rw["he"], rw["he_lat"], rw["he_lon"], rw["le_lat"], rw["le_lon"], rw.get("he_disp", 0))):
                    crs = geo.bearing(tl, to, ol, oo)
                    diff = geo.angle_diff(hdg or 0, crs)
                    if diff > max_diff:
                        continue
                    # flat projection around the threshold: along-track and signed cross-track, in feet
                    k = 6076 * 60
                    dx = (lon - to) * math.cos(math.radians(tl)) * k
                    dy = (lat - tl) * k
                    c = math.radians(crs)
                    along = dx * math.sin(c) + dy * math.cos(c)
                    cross = dx * math.cos(c) - dy * math.sin(c)  # + = right of centerline
                    length = geo.dist_nm(tl, to, ol, oo) * 6076
                    if along > length + 500 or along < -6 * 6076:
                        continue
                    key = diff * 20 + abs(cross)
                    if best is None or key < best[0]:
                        best = (key, {"ap": ap, "end": end, "crs": crs, "diff": diff, "along": along, "cross": cross,
                                      "length": rw["length"], "width": rw["width"] or 75, "disp": disp or 0})
        return best[1] if best else None

    def _scorecard(self, s, fpm):
        prof = self.career.profile()
        vref = prof.get("vref") or 65
        ias = s.get("ias") or 0
        bank = abs(s.get("bank") or 0)
        card = {"fpm": round(fpm), "ias": round(ias), "vref": vref, "bank": round(bank, 1), "notes": []}
        pts, out_of = 0, 0
        # sink rate
        out_of += 35
        pts += 35 if fpm <= 120 else 32 if fpm <= 200 else 27 if fpm <= 300 else 18 if fpm <= 450 else 10 if fpm <= 600 else 0
        # speed at touchdown: anywhere from a held-off, nearly stalled touchdown up to the approach speed is good.
        # Vref is the speed over the fence; a well-flared landing touches down well below it, near the stall.
        vso = prof.get("vso") or round(vref / 1.3)
        delta = ias - vref
        out_of += 15
        if ias < vso * 0.8:
            pts += 12
            card["notes"].append(f"Touched down at {round(ias)} kt, right at the stall: no margin left")
        elif delta <= 3:
            pts += 15
        elif delta <= 8:
            pts += 10
        elif delta <= 15:
            pts += 5
            card["notes"].append(f"Touched down {round(delta)} kt fast")
        else:
            card["notes"].append(f"Touched down {round(delta)} kt fast: floated or forced it on")
        # wings level
        out_of += 10
        pts += 10 if bank <= 2 else 6 if bank <= 5 else 0
        if bank > 5:
            card["notes"].append(f"Wing down {round(bank)}° at touchdown")
        rw = self._runway_for(s["lat"], s["lon"], s.get("hdg"), 45, 3)
        if rw:
            along = rw["along"] - rw["disp"]
            side = "right" if rw["cross"] >= 0 else "left"
            off = abs(rw["cross"]) if abs(rw["cross"]) <= rw["width"] * 1.5 + 50 and rw["along"] >= -100 else None
            card.update(runway=rw["end"], airport=rw["ap"]["ident"], past_threshold_ft=round(along),
                        centerline_ft=None if off is None else round(off), side=side, runway_ft=round(rw["length"]),
                        crab=round(geo.angle_diff(s.get("hdg") or 0, rw["crs"])))
            out_of += 20
            if along < 0:
                card["notes"].append(f"Landed {abs(round(along))} ft short of the threshold!")
            elif along <= 300:
                pts += 12
            elif along <= 1000:
                pts += 20
            elif along <= rw["length"] / 3:
                pts += 14
            else:
                pts += 5
                card["notes"].append(f"Landed long, {round(along):,} ft down the runway")
            out_of += 20
            if off is not None:
                pts += 20 if off <= 5 else 17 if off <= 10 else 12 if off <= 20 else 6 if off <= rw["width"] / 2 else 0
                if off > 20:
                    card["notes"].append(f"{round(off)} ft {side} of the centerline")
            if card["crab"] >= 6:
                card["notes"].append(f"Still crabbed {card['crab']}° at touchdown: kick it straight with rudder")
        card["score"] = round(pts * 100 / out_of)
        card["grade"] = "A" if card["score"] >= 85 else "B" if card["score"] >= 70 else "C" if card["score"] >= 55 else "D"
        gate = self.f.get("gate")
        if gate:
            card["stable"] = gate["stable"]
        return card

    def _stable_gate(self, s):
        """At 500 ft above the field on the way down: speed, sink, lined up, gear."""
        f = self.f
        agl, vs = s.get("agl") or 0, s.get("vs") or 0
        if f.get("gate_done") or not (420 <= agl <= 560) or vs > -150:
            return
        d, ap = self.world.nearest(s["lat"], s["lon"], 3.5)
        if not ap:
            return
        f["gate_done"] = True
        prof = self.career.profile()
        vref = prof.get("vref") or 65
        ias = s.get("ias") or 0
        issues = []
        if ias > vref + 20:
            issues.append(f"{round(ias - vref)} kt fast")
        elif ias < vref - 5:
            issues.append(f"{round(vref - ias)} kt slow")
        if vs < -1000:
            issues.append(f"sinking {abs(round(vs)):,} fpm")
        if abs(s.get("bank") or 0) > 15:
            issues.append("still turning")
        rw = self._runway_for(s["lat"], s["lon"], s.get("hdg"), 30, 4)
        mapped = any(r.get("le_lat") is not None for r in ap["runways"])
        if (not rw and mapped) or (rw and abs(rw["cross"]) > 900):
            issues.append("not lined up with a runway yet")
            rw = None
        if prof.get("retract") and not s.get("gear"):
            issues.append("GEAR UP")
        f["gate"] = {"stable": not issues, "issues": issues, "ias": round(ias), "vs": round(vs)}
        if issues:
            self.msg("Not stable at 500 ft: " + ", ".join(issues) + ". A go-around is always an option.", "warn")
        else:
            self.msg(f"Stable at 500 ft{' on runway ' + rw['end'] if rw else ''}.", "good")

    def _acc(self, key, active, dt, every, system, amount, reason):
        f = self.f
        if not active:
            return
        f["acc"][key] = f["acc"].get(key, 0) + dt
        f["exceed"][key] = time.time()
        while f["acc"][key] >= every:
            f["acc"][key] -= every
            self.damage(system, amount, reason)

    def _exceedances(self, s, dt, prof):
        f = self.f
        ias = s.get("ias") or 0
        vne = prof.get("vne")
        if vne:
            self._acc("vne", ias > vne * 1.08, dt, 3, "airframe", 4, "Well past Vne: structural stress")
            self._acc("vne_lite", vne < ias <= vne * 1.08, dt, 5, "airframe", 1.5, "Over Vne")
        flaps = s.get("flaps") or 0
        vfe = prof.get("vfe_full") if flaps >= 2 else prof.get("vfe_first")
        if flaps and vfe:
            self._acc("vfe", ias > vfe + 4, dt, 5, "airframe", 1.5, "Flaps out above their limit speed")
        if prof.get("retract") and prof.get("vle") and s.get("gear"):
            self._acc("vle", ias > prof["vle"] + 4, dt, 5, "gear", 2, "Gear down above its limit speed")
        red = prof.get("redline_rpm")
        if red:
            r = s.get("prop_rpm") if prof.get("engine") == "turboprop" else (s.get("rpm") or s.get("prop_rpm"))
            self._acc("rpm", (r or 0) > red * 1.04, dt, 5, "engine", 1.5, "Engine overspeed (RPM over redline)")
        cht = s.get("cht")
        if prof.get("cht_max_f") and cht and 150 < cht < 1200:
            self._acc("cht", cht > prof["cht_max_f"], dt, 10, "engine", 1.5, "Cylinder head temperature over the limit")
            hist = f["cht_hist"]
            hist.append((time.time(), cht))
            old = [c for t, c in hist if time.time() - t >= 55]
            if old and old[-1] - cht > 60 and time.time() - f["shock_t"] > 120:
                f["shock_t"] = time.time()
                self.damage("engine", 3, "Shock cooling (cylinders cooled too fast)")
        # G loads: use the sim's running max/min so short spikes between samples still count
        lim = prof.get("g_limit", 3.8)
        g = max(s.get("g") or 1, s.get("gmax") or 1)
        if g > f["gpeak"] + 0.05:
            if f["td_t"] and time.time() - f["td_t"] < 5:
                pass  # touchdown spike, judged by the landing itself
            elif g > lim * 1.5:
                self.damage("airframe", 35, f"Severe overload ({g:.1f} G)")
            elif g > lim:
                self.damage("airframe", 12, f"Over the G limit ({g:.1f} G)")
            elif g > lim * 0.85:
                self.damage("airframe", 3, f"Heavy G load ({g:.1f} G)")
            f["gpeak"] = max(f["gpeak"], g)
        gm = min(s.get("g") or 1, s.get("gmin") or 1)
        if gm < f["gmin"] - 0.05:
            if gm < -1.5:
                self.damage("airframe", 10, f"Negative G overload ({gm:.1f} G)")
            f["gmin"] = gm

    def _route(self):
        job = self.f["job"]
        plan = job.get("plan") or {}
        r = plan.get("route")
        if r and len(r) >= 2:
            return r, plan.get("mag_var") or 0.0
        return [dict(job["dep"], kind="airport", ident=job["dep"]["code"] or job["dep"]["ident"]),
                dict(job["dest"], kind="airport", ident=job["dest"]["code"] or job["dest"]["ident"])], 0.0

    def _advance_waypoint(self, s):
        f = self.f
        route, _ = self._route()
        i = min(f["wp_idx"], len(route) - 1)
        while i < len(route) - 1:
            a, b = route[i - 1], route[i]
            d = geo.dist_nm(s["lat"], s["lon"], b["lat"], b["lon"])
            _, frac = geo.cross_track_nm(s["lat"], s["lon"], a["lat"], a["lon"], b["lat"], b["lon"])
            if d < 1.5 or (frac > 1.0 and d < 6):
                i += 1
                nb = route[i]
                if nb.get("kind") != "visual":  # sights get their own callout as you near them
                    self.msg(f"Next: {nb.get('ident')} {('· ' + nb['name']) if nb.get('kind') != 'airport' else ''}".strip(), "info")
                continue
            break
        f["wp_idx"] = i

    def _guidance(self, s):
        f = self.f
        route, var = self._route()
        i = min(f["wp_idx"], len(route) - 1)
        a, b = route[i - 1], route[i]
        d = geo.dist_nm(s["lat"], s["lon"], b["lat"], b["lon"])
        brg = geo.bearing(s["lat"], s["lon"], b["lat"], b["lon"])
        xt, frac = geo.cross_track_nm(s["lat"], s["lon"], a["lat"], a["lon"], b["lat"], b["lon"])
        crs = geo.bearing(a["lat"], a["lon"], b["lat"], b["lon"])
        side = "right" if ((geo.bearing(a["lat"], a["lon"], s["lat"], s["lon"]) - crs) % 360) < 180 else "left"
        gs = s.get("gs") or 0
        nxt = route[i + 1] if i + 1 < len(route) else None
        return {
            "ident": b.get("ident"), "name": b.get("name"), "kind": b.get("kind"), "freq": b.get("freq"),
            "nm": round(d, 1), "brg_mag": round((brg - var) % 360), "course_mag": round((crs - var) % 360),
            "ete_min": round(d / gs * 60, 1) if gs > 30 else None,
            "xtk": round(xt, 1) if 0 <= frac <= 1 else None, "xtk_side": side,
            "then": nxt and {"ident": nxt.get("ident"), "name": nxt.get("name"), "kind": nxt.get("kind")},
            "lat": b["lat"], "lon": b["lon"], "index": i, "count": len(route) - 1,
        }

    def _approach_watch(self, s):
        """Spot a go-around / missed approach: low and close to an airfield, then climbing away."""
        f = self.f
        agl, vs = s.get("agl") or 0, s.get("vs") or 0
        if agl < 700 and time.time() - (f["takeoff_t"] or 0) > 120 and vs < -150:
            d, a = self.world.nearest(s["lat"], s["lon"], 3)
            if a:
                f["ga_armed"] = {"agl": agl, "ap": a["name"], "t": time.time()} if not f["ga_armed"] or agl < f["ga_armed"]["agl"] else f["ga_armed"]
        ga = f.get("ga_armed")
        if ga and time.time() - ga["t"] < 120 and vs > 400 and agl > ga["agl"] + 350:
            f["go_arounds"] += 1
            f["ga_armed"] = None
            f["gate_done"] = False
            self.msg(f"Go-around at {ga['ap']}{' (missed approach)' if f['job'].get('rules') == 'IFR' else ''}. No shame in that.", "warn")
        elif ga and time.time() - ga["t"] > 120:
            f["ga_armed"] = None

    def _dest_weather_update(self, job):
        if not self.weather:
            return
        try:
            d = job["dest"]
            m = self.weather.nearest(self.weather.around(d["lat"], d["lon"], 30), d["lat"], d["lon"], max_nm=30)
        except Exception:
            return
        with self.lock:
            f = self.f
            if not f or f["job"]["id"] != job["id"] or not m:
                return
            old = f.get("dest_wx") or {}
            f["dest_wx"] = m
            if m.get("raw") == old.get("raw"):
                return
            mins = job.get("minimums") or (500, 1.25)
            c, v = m.get("ceiling"), m.get("vis")
            below = (c is not None and c < mins[0]) or (v is not None and v < mins[1])
            alt = job.get("alternate")
            text = f"New weather at {m['station']}: {m['raw']}"
            if below:
                text += f". That's below typical minimums. Consider the alternate{(' ' + alt['code']) if alt else ''}."
            self.msg(text, "bad" if below else "info")

    def _excursion_check(self, lat, lon):
        f = self.f
        if f["excursion"]:
            return
        d, a = self.world.nearest(lat, lon, 2)
        if not a:
            return
        best = None
        for rw in a["runways"]:
            if None in (rw["le_lat"], rw["le_lon"], rw["he_lat"], rw["he_lon"]):
                return  # no geometry: can't judge
            xt, frac = geo.cross_track_nm(lat, lon, rw["le_lat"], rw["le_lon"], rw["he_lat"], rw["he_lon"])
            off_ft = xt * 6076
            if -0.05 <= frac <= 1.05:
                best = off_ft - (rw["width"] or 60) / 2 if best is None else min(best, off_ft - (rw["width"] or 60) / 2)
        if best is not None and best > 120:
            f["excursion"] = True
            self.damage("gear", 15, "Ran off the runway at speed")
            self.damage("prop", 10, "Prop ingested debris off the runway", quiet=True)

    @staticmethod
    def _side(s, p):
        rel = (geo.bearing(s["lat"], s["lon"], p["lat"], p["lon"]) - (s.get("hdg") or 0)) % 360
        if rel < 25 or rel > 335:
            return "dead ahead"
        if rel <= 155:
            return "ahead on the right" if rel < 60 else ("off the right wing" if rel < 120 else "behind on the right")
        if rel >= 205:
            return "ahead on the left" if rel > 300 else ("off the left wing" if rel > 240 else "behind on the left")
        return "behind you"

    def _pois(self, s):
        """One callout per sight, about a minute before you reach it; passing within 2.5 nm counts it as seen."""
        f = self.f
        agl = s.get("agl") or 0
        gs = s.get("gs") or 0
        called = f.setdefault("called", set())
        sights = f["job"].get("pois", []) + f["job"].get("dest_pois", [])
        due = []
        for p in sights:
            pid = p.get("id") or p["name"]
            key = p["name"].strip().lower()   # the same sight can be listed twice (along the route and as a waypoint)
            d = geo.dist_nm(s["lat"], s["lon"], p["lat"], p["lon"])
            if key not in called and agl < 7000 and (d <= 2.5 or (gs > 40 and d / gs * 60 <= 1.3 and d <= 4)):
                called.add(key)
                due.append((d, p))
            if pid not in f["seen"] and d <= 2.5 and agl < 7000:
                f["seen"].append(pid)
        if not due:
            return
        # sights close together (a city, a valley) go in one callout instead of a string of them
        for p in sights:
            key = p["name"].strip().lower()
            if key not in called and any(geo.dist_nm(p["lat"], p["lon"], q["lat"], q["lon"]) <= 2.0 for _, q in due):
                called.add(key)
                due.append((geo.dist_nm(s["lat"], s["lon"], p["lat"], p["lon"]), p))
        due.sort(key=lambda x: x[0])
        d, first = due[0]
        names = list(dict.fromkeys(q["name"] for _, q in due))
        if len(names) == 1:
            self.msg(f"{first['name']}, {self._side(s, first)}, {d:.1f} nm.", "good")
        else:
            shown = names[:3]
            more = len(names) - len(shown)
            lst = ", ".join(shown[:-1]) + (" and " if not more else ", ") + shown[-1] + (f" and {more} more" if more else "")
            self.msg(f"{lst}, {self._side(s, first)}, {d:.1f} nm.", "good")

    # ---------- failures ----------
    def _plan_failures(self, job, prof):
        st = self.career.s["settings"]
        f = self.f
        f["planned"] = []
        if not st.get("failures") or f.get("accusim"):
            return  # AccuSim aircraft break on their own; Onward doesn't add failures on top
        cond = self.career.plane()["cond"]
        ete_s = job["ete_min"] * 60
        for sysname, c in cond.items():
            if c >= 80:
                continue
            p = ((80 - c) / 80) ** 1.5 * 0.9
            if random.random() > p:
                continue
            pool = FAILURES[sysname]
            if sysname == "engine" and c < 45 and st.get("engine_failures") and random.random() < 0.5:
                pool = FAILURES["engine_severe"]
            ev, label = random.choice(pool)
            when = "touchdown" if sysname == "gear" else time.time() + random.uniform(0.15, 0.75) * ete_s
            f["planned"].append({"event": ev, "label": label, "at": when, "system": sysname})

    def _failures_due(self):
        f = self.f
        for p in list(f["planned"]):
            if p["at"] != "touchdown" and time.time() >= p["at"]:
                self._trigger(p)

    def _trigger(self, p):
        f = self.f
        f["planned"].remove(p)
        if self.src.send(p["event"]):
            f["active"].append(p)
        self.msg(f"{p['label']}! Probably wear and tear catching up. Fly the airplane.", "bad")

    def _clear_failures(self):
        if not self.f:
            return
        for p in self.f.get("active", []):
            self.src.send(p["event"])  # toggles back off
        self.f["active"] = []
        self.f["planned"] = []

    # ---------- end of a leg ----------
    def finish(self, force=False):
        with self.lock:
            f = self.f
            if not f:
                return None, "There's no active leg."
            if not f["takeoff_t"]:
                return None, "You haven't taken off yet. Cancel the leg instead if you changed your mind."
            if f["phase"] == "air" and not f["crash"] and not force:
                return None, "You're still flying!"
            c, w = self.career, self.world
            job = f["job"]
            try:
                self.photo_scan()
            except Exception as e:
                self.log(f"Screenshot check failed: {e}")
            s = self.live or {}
            if s.get("lat") is not None:
                pos = (s["lat"], s["lon"])
            elif f["landings"]:
                pos = (f["landings"][-1]["lat"], f["landings"][-1]["lon"])
            else:
                pos = (job["dep"]["lat"], job["dep"]["lon"])
            events = [dict(e) for e in f["events"]]

            def hit(system, amount, reason):
                events.append({"system": system, "amount": amount, "reason": reason})

            worst = max((L["fpm"] for L in f["landings"]), default=None)
            bounces = sum(L["bounces"] for L in f["landings"])
            crash = f["crash"] or (worst is not None and worst > 1400) or (f["phase"] == "air" and force)
            if worst is not None and not crash:
                if worst > 1000:
                    hit("gear", 55, f"Brutal landing ({worst} fpm)"), hit("airframe", 25, "Airframe bent by the landing"), hit("prop", 35, "Prop strike on landing")
                elif worst > 700:
                    hit("gear", 22, f"Hard landing ({worst} fpm)"), hit("airframe", 8, "Airframe stressed by the landing")
                elif worst > 500:
                    hit("gear", 7, f"Firm landing ({worst} fpm)"), hit("airframe", 2, "Rivets complained about the landing")
                elif worst > 350:
                    hit("gear", 2, f"Slightly firm landing ({worst} fpm)")
            if bounces:
                hit("gear", 3 * bounces, f"{bounces} bounce{'s' if bounces > 1 else ''} on landing")

            dest = job["dest"]
            d_dest = geo.dist_nm(pos[0], pos[1], dest["lat"], dest["lon"])
            _, near = w.nearest(pos[0], pos[1], 1.5)
            if crash:
                outcome = "crash"
            elif d_dest <= 1.5:
                outcome, near = "arrived", w.airports.get(dest["ident"])
            elif near:
                outcome = "diverted"
            else:
                outcome = "offfield"
                hit("gear", 25, "Off-field landing"), hit("prop", 30, "Prop damaged in the off-field landing"), hit("airframe", 10, "Airframe scraped in the off-field landing")
            if outcome in ("crash", "offfield"):
                _, near = w.nearest(pos[0], pos[1], 80)
            final = near or w.airports.get(job["dep"]["ident"])

            hours = f["air_s"] / 3600
            accu = f.get("accusim")
            if not accu:
                hit("engine", round(0.6 * hours, 2), "Normal engine wear")
                for sysname in ("airframe", "gear", "prop"):
                    hit(sysname, round(0.2 * hours, 2), "Normal wear")
            plane = c.plane()
            plane["hours"] = round(plane["hours"] + hours, 2)
            if crash:
                c.write_off()
            elif not accu:
                c.apply_damage(events)
            else:
                # AccuSim keeps the real score: Onward only notes what happened, the condition comes from A2A
                if s.get("accusim"):
                    self._sync_accusim(s)
                with c.lock:
                    plane["damage_log"] = (plane["damage_log"] + [dict(e, at=career_mod.now_iso(), noted=True)
                                                                for e in events if e["amount"] >= 0.5])[-60:]
            self._clear_failures()

            # who you met, what you saw
            seen = [p for p in job.get("pois", []) + job.get("dest_pois", []) if (p.get("id") or p["name"]) in f["seen"]]
            for p in seen:
                c.s["sights"].append({"name": p["name"], "kind": p.get("label", ""), "lat": p["lat"], "lon": p["lon"]})
            fmt = {"name": final["name"], "town": final["town"] or final["name"]}
            parts = []
            if outcome == "crash":
                parts.append(story.pick(story.CRASH, **fmt))
            elif outcome == "offfield":
                parts.append(f"Put it down in a field, nowhere near a runway. The plane was trucked to {final['name']}.")
            elif outcome == "diverted":
                alt = job.get("alternate") or {}
                if job.get("rules") == "IFR" and alt.get("ident") == final["ident"]:
                    parts.append(f"The weather at {dest['name']} didn't cooperate, so it was off to the alternate, {final['name']}. Exactly what alternates are for.")
                else:
                    parts.append(story.pick(story.DIVERT, **fmt))
            else:
                # dark when you actually landed, in the sim (the offer's guess can be hours old)
                night = geo.solar_elevation(final["lat"], final["lon"], sim_time(s)) < -4
                job["night"] = night
                parts.append(story.pick(story.NIGHT_ARRIVAL if night else story.ARRIVAL, **fmt))
            if job.get("rules") == "IFR" and outcome in ("arrived", "diverted"):
                parts.append("Flown IFR, mostly inside the grey." if is_grey(job) else "Flown IFR.")
            if f["go_arounds"]:
                parts.append(f"{f['go_arounds']} go-around{'s' if f['go_arounds'] > 1 else ''} along the way.")
            if worst is not None and outcome != "crash":
                parts.append(f"The landing was {story.landing_words(worst)} ({worst} fpm).")
            if seen:
                names = list(dict.fromkeys(p["name"] for p in seen))
                parts.append("Sights along the way: " + ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] + ".")
            shots = [p for p in job.get("photos", []) if not p.get("skip")]
            if shots:
                parts.append(f"{len(shots)} photo{'s' if len(shots) > 1 else ''} for the album.")
            friend_done = None
            if job.get("kind") == "ride" and job.get("friend"):
                fr = job["friend"]
                if outcome == "arrived":
                    friend_done = fr
                    parts.append(story.pick(story.RIDE_DONE, friend=fr["name"], town=fr["town"]))
                    c.s["friends"].append({"name": fr["name"], "met_at": fr["met_at"], "home": fr["town"]})
                    c.s["pending_friend"] = None
                elif outcome != "crash":
                    parts.append(f"{fr['name']} is still hoping to get to {fr['town']}.")
            real = [e for e in events if not e["reason"].startswith("Normal")]
            total = sum(e["amount"] for e in real)
            if total >= 5 and not crash:
                big = max(real, key=lambda e: e["amount"])
                parts.append(f"The plane took some punishment (worst: {big['reason'].lower()}).")
            new_country = (final["country"] not in c.s["countries"]) if outcome != "crash" else False
            if new_country:
                parts.append(f"First landing in {w.country(final['country'])}!")

            card = None
            if outcome != "crash" and f["landings"] and f["landings"][-1].get("card"):
                card = dict(f["landings"][-1]["card"])
                b = f["landings"][-1]["bounces"]
                if b:
                    card["score"] = max(0, card["score"] - 10 * b)
                    card["notes"].insert(0, f"{b} bounce{'s' if b > 1 else ''}")
                    card["grade"] = "A" if card["score"] >= 85 else "B" if card["score"] >= 70 else "C" if card["score"] >= 55 else "D"
                if card.get("stable") is False:
                    card["notes"].append("Approach wasn't stable at 500 ft")
                bests = c.s.setdefault("landing_bests", {})
                prev = bests.get(final["ident"])
                if prev is None or card["score"] > prev:
                    card["best"] = prev is not None
                    bests[final["ident"]] = card["score"]
                card["prev_best"] = prev
                parts.append(f"Landing grade {card['grade']} ({card['score']}/100)" + (", a new personal best here." if card.get("best") else "."))
            entry = {
                "from": job["dep"]["code"] or job["dep"]["ident"], "from_name": job["dep"]["name"],
                "to": final["code"] or final["ident"], "to_name": final["name"], "to_ident": final["ident"],
                "planned_to": dest["ident"], "outcome": outcome, "title": job["title"],
                "aircraft": c.profile()["name"], "minutes": round(f["air_s"] / 60), "distance_nm": round(f["nm"]),
                "landing_fpm": worst, "bounces": bounces, "sights": list(dict.fromkeys(p["name"] for p in seen)),
                "damage": [e for e in real], "text": " ".join(parts), "country": final["country"],
                "lat": final["lat"], "lon": final["lon"], "night": job.get("night", False),
                "rules": job.get("rules", "VFR"), "go_arounds": f["go_arounds"], "landing": card,
                "gate": f.get("gate"), "photos": [{k: v for k, v in p.items() if k != "src"} for p in job.get("photos", []) if not p.get("skip")],
                "accusim": bool(accu), "from_ident": job["dep"]["ident"], "track": _thin([[job["dep"]["lat"], job["dep"]["lon"]]] + f["track"] + [[final["lat"], final["lon"]]]),
            }
            c.add_entry(entry)
            c.move_to(final["ident"])
            try:
                c.trip_arrived(final["ident"])
            except Exception as e:
                self.log(f"Trip update failed: {e}")

            met = None
            if outcome in ("arrived", "diverted") and not c.s.get("pending_friend") and random.random() < 0.2:
                met = self._meet_someone(final)
            result = dict(entry, condition=dict(c.plane()["cond"]), status=c.status(), friend_done=friend_done,
                          accusim_worst=accusim.worst(c.accusim()) if accu else None,
                          accusim_failed=(c.accusim() or {}).get("failed", []) if accu else None,
                          met=met, new_country=new_country, shop_here=c.is_shop(final))
            c.s["last_result"] = result
            c.s["job"] = None
            c.save()
            self.f = None
            return result, None

    def _meet_someone(self, here):
        c, w = self.career, self.world
        prof = c.profile()
        opts = []
        for d, a in w.near(here["lat"], here["lon"], 100):
            if d < 25 or not a["code"] or not a["town"] or a["type"] == "large_airport":
                continue
            if any(r["length"] >= prof["min_runway_ft"] and (r["surface"] == "paved" or prof["unpaved"]) for r in a["runways"]):
                opts.append(a)
        if not opts:
            return None
        a = random.choice(opts)
        name = story.friend_name(here["country"])
        fr = {"name": name, "dest": a["ident"], "town": a["town"], "met_at": here["name"]}
        fr["story"] = story.pick(story.MEETINGS, friend=name, town=a["town"])
        c.s["pending_friend"] = fr
        c.add_note(fr["story"])
        c.save()
        return fr

    # ---------- for the UI ----------
    def view(self):
        with self.lock:
            s = self.live
            out = {"connected": bool(s), "error": getattr(self.src, "error", ""), "demo": isinstance(self.src, DemoSource)}
            if s:
                out["sample"] = {k: (round(v, 5) if k in ("lat", "lon") and v is not None else (round(v, 1) if isinstance(v, float) else v))
                                 for k, v in s.items() if k != "accusim"}  # AccuSim's ~80 raw values go to the Hangar summary, not every page refresh
            f = self.f
            if f:
                job = f["job"]
                out["flight"] = {
                    "phase": f["phase"], "msgs": f["msgs"][-12:], "events": f["events"], "landings": f["landings"],
                    "seen": f["seen"], "air_min": round(f["air_s"] / 60, 1), "nm": round(f["nm"], 1),
                    "exceed": [k for k, t in f["exceed"].items() if time.time() - t < 2],
                    "active_failures": [p["label"] for p in f["active"]], "accusim": f.get("accusim", False),
                }
                if s:
                    d = job["dest"]
                    out["flight"]["to_dest_nm"] = round(geo.dist_nm(s["lat"], s["lon"], d["lat"], d["lon"]), 1)
                    if f["phase"] in ("preflight", "air"):
                        try:
                            out["flight"]["guide"] = self._guidance(s)
                        except Exception as e:
                            self.log(f"Guidance error: {e}")
                    out["flight"]["near_dest"] = out["flight"]["to_dest_nm"] < 20 and f["phase"] != "preflight"
                    nxt = [p for p in job.get("pois", []) if (p.get("id") or p["name"]) not in f["seen"]]
                    if nxt:
                        n = min(nxt, key=lambda p: geo.dist_nm(s["lat"], s["lon"], p["lat"], p["lon"]))
                        out["flight"]["next_poi"] = {"name": n["name"], "nm": round(geo.dist_nm(s["lat"], s["lon"], n["lat"], n["lon"]), 1),
                                                     "brg": round(geo.bearing(s["lat"], s["lon"], n["lat"], n["lon"]))}
            return out
