"""The persistent journey: where you are, your planes and their condition, the journal."""
import copy
import json
import os
import threading
import time
from datetime import datetime, timezone

SYSTEMS = ["engine", "airframe", "gear", "prop"]
LABELS = {"engine": "Engine", "airframe": "Airframe", "gear": "Landing gear", "prop": "Propeller"}

DEFAULT_PREFS = {
    "min_nm": 30, "max_nm": 120, "difficulty": "any", "surface": "any", "weather": "marginal",
    "night": "lit_only", "bush": False, "max_elev": 12000, "uncharted": True, "prefer_new": True,
}
DEFAULT_SETTINGS = {
    "pilot_name": "", "failures": True, "engine_failures": True, "ifr_auto": True, "openaip_key": "",
    "auto_load_plan": True, "gtn_auto": False, "gtn_folder": r"C:\ProgramData\TDS\GTNXi\FPL", "voice": False, "photos": True, "photo_folders": "",
    "update_check": True, "update_repo": "", "update_token": "",
    "shop_min_per_pt": 1.0, "field_min_per_pt": 3.0, "crash_hours": 6.0,
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Career:
    def __init__(self, path, world, profiles, start="KHAF"):
        self.path, self.world, self.profiles = path, world, profiles
        self.lock = threading.RLock()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.s = json.load(f)
            self.s.setdefault("setup_done", True)  # careers from before the setup screen are already set up
        else:
            a = world.airports.get(start)
            self.s = {
                "version": 1, "home": start, "location": start, "started": now_iso(),
                "current_aircraft": None, "planes": {}, "offers": [], "job": None, "journal": [],
                "visited": [start], "countries": [a["country"]] if a else [], "friends": [],
                "pending_friend": None, "sights": [], "last_result": None, "setup_done": False,
            }
        self.s.setdefault("prefs", {})
        for k, v in DEFAULT_PREFS.items():
            self.s["prefs"].setdefault(k, v)
        self.s.setdefault("settings", {})
        for k, v in DEFAULT_SETTINGS.items():
            self.s["settings"].setdefault(k, v)
        self.save()

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.s, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)

    # ---------- aircraft ----------
    def profile(self, pid=None):
        pid = pid or self.s.get("current_aircraft") or "generic"
        return self.profiles.get(pid) or self.profiles["generic"]

    def plane(self, pid=None):
        pid = pid or self.s.get("current_aircraft") or "generic"
        with self.lock:
            p = self.s["planes"].setdefault(pid, {"cond": {k: 100.0 for k in SYSTEMS}, "hours": 0.0,
                                                  "repair": None, "write_offs": 0, "damage_log": []})
            return p

    def set_aircraft(self, pid):
        if pid not in self.profiles:
            return
        with self.lock:
            self.s["current_aircraft"] = pid
            self.plane(pid)
            self.save()

    def status(self, pid=None):
        p = self.plane(pid)
        self.check_repair(pid)
        if p["repair"]:
            return "in_repair"
        low = min(p["cond"].values())
        if low < 35:
            return "grounded"
        if low < 60:
            return "damaged"
        if low < 80:
            return "worn"
        return "ok"

    def accusim(self, pid=None):
        """The last AccuSim reading for this airplane, if its condition comes from A2A AccuSim."""
        return self.plane(pid).get("accusim")

    def sync_accusim(self, summary, pid=None):
        """Take the airplane's condition from AccuSim. Saves only when something visible changed, or once a minute."""
        p = self.plane(pid)
        with self.lock:
            prev = p.get("accusim") or {}
            cond = dict(p["cond"])
            for k, v in summary["systems"].items():
                if k in cond:
                    cond[k] = v
            moved = any(abs(cond[k] - p["cond"][k]) >= 0.5 for k in cond) or summary["failed"] != prev.get("failed")
            p["cond"] = cond
            p["accusim"] = dict(summary, at=time.time())
            if moved or time.time() - prev.get("saved", 0) > 60:
                p["accusim"]["saved"] = time.time()
                self.save()

    def clear_accusim(self, pid=None):
        """The same type is flying without AccuSim (a different version of it): back to Onward's own model,
        starting from the last condition AccuSim reported."""
        p = self.plane(pid)
        with self.lock:
            if p.pop("accusim", None) is not None:
                self.save()

    def apply_damage(self, events, pid=None):
        """events: list of {system, amount, reason}"""
        p = self.plane(pid)
        with self.lock:
            for e in events:
                c = p["cond"]
                c[e["system"]] = max(0.0, round(c[e["system"]] - e["amount"], 1))
                if e["amount"] >= 0.5:
                    p["damage_log"] = (p["damage_log"] + [dict(e, at=now_iso())])[-60:]
            self.save()

    # ---------- maintenance ----------
    def is_shop(self, airport):
        return bool(airport) and (airport["type"] in ("medium_airport", "large_airport") or airport.get("scheduled"))

    def repair_quote(self, kind, pid=None):
        p = self.plane(pid)
        st = self.s["settings"]
        missing = sum(100 - v for v in p["cond"].values())
        if kind == "shop":
            return round(missing * st["shop_min_per_pt"])
        if kind == "field":
            target_missing = sum(max(0, 70 - v) for v in p["cond"].values())
            return round(target_missing * st["field_min_per_pt"])
        return 0

    def start_repair(self, kind, pid=None):
        p = self.plane(pid)
        if p.get("accusim") and kind != "replacement":
            return "This airplane's condition comes from A2A AccuSim. Do the maintenance in the A2A tablet, and Onward picks it up."
        here = self.world.airports.get(self.s["location"])
        if kind == "shop" and not self.is_shop(here):
            return "There's no maintenance shop here."
        mins = self.repair_quote(kind, pid)
        with self.lock:
            p["repair"] = {"kind": kind, "until": time.time() + mins * 60, "minutes": mins}
            self.save()
        self.check_repair(pid)
        return None

    def check_repair(self, pid=None):
        p = self.plane(pid)
        r = p.get("repair")
        if not r or time.time() < r["until"]:
            return
        with self.lock:
            if r["kind"] == "shop" or r["kind"] == "replacement":
                p["cond"] = {k: 100.0 for k in SYSTEMS}
            elif r["kind"] == "field":
                p["cond"] = {k: max(v, 70.0) for k, v in p["cond"].items()}
            p["repair"] = None
            self.add_note(f"{'Replacement aircraft delivered' if r['kind'] == 'replacement' else 'Repairs finished'} at {self.loc_name()}.")
            self.save()

    def write_off(self, pid=None):
        p = self.plane(pid)
        with self.lock:
            p["cond"] = {k: 0.0 for k in SYSTEMS}
            p["write_offs"] += 1
            hours = self.s["settings"]["crash_hours"]
            p["repair"] = {"kind": "replacement", "until": time.time() + hours * 3600, "minutes": round(hours * 60)}
            self.save()

    # ---------- journey ----------
    def loc(self):
        return self.world.airports.get(self.s["location"])

    def loc_name(self):
        a = self.loc()
        return a["name"] if a else self.s["location"]

    def move_to(self, ident):
        a = self.world.airports.get(ident)
        with self.lock:
            self.s["location"] = ident
            new_country = False
            if ident not in self.s["visited"]:
                self.s["visited"].append(ident)
            if a and a["country"] not in self.s["countries"]:
                self.s["countries"].append(a["country"])
                new_country = True
            self.s["offers"] = []
            self.save()
        return new_country

    def setup(self, pilot_name, home):
        """First run: who you are and where the journey starts."""
        a = self.world.airports.get(home)
        if not a:
            return "Unknown airfield."
        with self.lock:
            if not self.s["journal"]:
                self.s.update(home=home, location=home, visited=[home], countries=[a["country"]], offers=[], job=None)
            self.s["settings"]["pilot_name"] = (pilot_name or "").strip()[:40]
            self.s["setup_done"] = True
        self.add_note(f"The journey starts at {a['name']}.") if not self.s["journal"] else None
        self.save()
        return None

    def trip_arrived(self, ident):
        """Move a planned trip along when you land where its next leg ends."""
        trip = self.s.get("trip")
        if not trip or trip["idx"] >= len(trip["legs"]):
            return
        legs = trip["legs"]
        hit = next((i for i in range(trip["idx"], len(legs)) if legs[i]["to"] == ident), None)
        if hit is None:
            return
        trip["idx"] = hit + 1
        if trip["idx"] >= len(legs):
            self.add_note(f"Trip complete: {trip['name']}. {len(legs)} legs, {sum(l['nm'] for l in legs)} nm.")
            trip["done"] = True
            self.s.setdefault("trips", []).insert(0, {"name": trip["name"], "started": trip.get("started"), "done": True, "how": "done",
                                                      "legs": len(legs), "flown": len(legs), "nm": sum(l["nm"] for l in legs),
                                                      "dest": trip.get("dest"), "via": trip.get("via") or [], "round_trip": bool(trip.get("round_trip")),
                                                      "path": [[legs[0]["from_lat"], legs[0]["from_lon"]]] + [[l["lat"], l["lon"]] for l in legs]})
            del self.s["trips"][12:]
            trip["archived"] = True
        elif legs[hit]["stop"] == "overnight":
            self.add_note(f"A night in {legs[hit]['to_name']}. Day {legs[hit]['day'] + 1} of the trip tomorrow.")

    def add_note(self, text):
        with self.lock:
            self.s["journal"].append({"type": "note", "at": now_iso(), "text": text})

    def add_entry(self, entry):
        with self.lock:
            self.s["journal"].append(dict(entry, type="leg", at=now_iso()))
            self.save()

    def stats(self):
        legs = [e for e in self.s["journal"] if e.get("type") == "leg"]
        return {
            "legs": len(legs),
            "hours": round(sum(e.get("minutes", 0) for e in legs) / 60, 1),
            "nm": round(sum(e.get("distance_nm", 0) for e in legs)),
            "airfields": len(self.s["visited"]),
            "countries": len(self.s["countries"]),
            "sights": len(self.s["sights"]),
            "friends": len(self.s["friends"]),
            "best_landing": min([e["landing_fpm"] for e in legs if e.get("landing_fpm") is not None], default=None),
        }

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.s)
