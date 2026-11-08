"""A2A AccuSim: read the aircraft's real condition instead of keeping Onward's own.

A2A aircraft publish their AccuSim state as local variables (the same ones A2A's own tablet reads):
condition values from 0 to 1 per part ("C_..."), failure flags ("F_..."), hours and oil. When they are there,
Onward uses them as the truth for the airplane and stops adding its own wear and failures on top.

Read-only: Onward never writes anything to AccuSim. Maintenance is done in the A2A tablet.
"""

ENGINE_PARTS = [
    ("C_Eng1_Main", "Engine (overall)"),
    ("C_Eng1_MagL", "Left magneto"), ("C_Eng1_MagR", "Right magneto"),
    ("C_Eng1_Oilsystem", "Oil system"), ("C_Eng1_OilFilter", "Oil filter"), ("C_Eng1_OilPump", "Oil pump"),
    ("C_Eng1_FuelPumpMechanical", "Engine-driven fuel pump"), ("C_Eng1_FuelPumpElectrical", "Electric fuel pump"),
    ("C_Eng1_FuelFilter", "Fuel filter"), ("C_Eng1_Fuelsystem", "Fuel system"), ("C_Eng1_AirFilter", "Air filter"),
    ("C_Eng1_Carb", "Carburetor"), ("C_Eng1_Starter", "Starter"), ("C_Eng1_Generator", "Generator"),
    ("C_Eng1_VacuumPump", "Vacuum pump"), ("C_Eng1_Baffling", "Engine baffling"), ("C_Battery1", "Battery"),
]
AIRFRAME_PARTS = [
    ("C_WingflapLeft", "Left flap"), ("C_WingflapRight", "Right flap"),
    ("C_AileronLeft", "Left aileron"), ("C_AileronRight", "Right aileron"),
    ("C_Elevator", "Elevator"), ("C_Rudder", "Rudder"),
]
GEAR_PARTS = [
    ("C_GearLeft", "Left main gear"), ("C_GearRight", "Right main gear"), ("C_GearCenter", "Nose gear"),
    ("C_TireLeft", "Left tire"), ("C_TireRight", "Right tire"), ("C_TireCenter", "Nose tire"),
    ("C_BrakesLeft", "Left brake"), ("C_BrakesRight", "Right brake"),
]
PROP_PARTS = [("C_Eng1_Prop", "Propeller")]
FAILURES = [
    ("F_StaticPort1", "Static port blocked"), ("F_StaticPort2", "Alternate static port blocked"),
    ("F_AntennaCom1", "COM 1 antenna"), ("F_AntennaCom2", "COM 2 antenna"),
    ("F_AntennaNav1", "NAV 1 antenna"), ("F_AntennaNav2", "NAV 2 antenna"), ("F_AntennaGps", "GPS antenna"),
    ("F_Com1", "COM 1 radio"), ("F_Com2", "COM 2 radio"), ("F_Nav1", "NAV 1 radio"), ("F_Nav2", "NAV 2 radio"),
    ("F_LandingLight1", "Landing light"), ("F_LandingLight2", "Second landing light"),
]
INFO = {"public": "AccusimPublic", "total_time": "TotalTime", "engine_time": "Eng1_Time",
        "oil_pct": "Eng1_OilTankQuantityPct"}
MAX_CYL = 6

# The engine is judged on its worst part and its average: one tired oil filter makes it "worn",
# a fouled plug or a weak cylinder makes it "damaged", but a single small item doesn't ground the airplane.
WORST_WEIGHT = 0.6


def cylinder_parts(n=MAX_CYL):
    out = []
    for i in range(1, n + 1):
        out.append((f"C_Eng1_Cyl[{i}]", f"Cylinder {i} compression"))
        out.append((f"C_Eng1_Cyl{i}_SparkPlugL", f"Cylinder {i} left spark plug"))
        out.append((f"C_Eng1_Cyl{i}_SparkPlugR", f"Cylinder {i} right spark plug"))
    return out


def var_names():
    """Every local variable Onward asks the sim for (without the L: prefix)."""
    names = list(INFO.values())
    for group in (ENGINE_PARTS, cylinder_parts(), AIRFRAME_PARTS, GEAR_PARTS, PROP_PARTS, FAILURES):
        names += [n for n, _ in group]
    return names


def is_a2a(title):
    return "a2a" in (title or "").lower()


def is_active(raw):
    return bool(raw) and (raw.get("AccusimPublic") or 0) >= 0.5


def summarize(raw, cylinders=None):
    """raw: {var name: value}. cylinders: how many the engine has (None: guess from the data).
    Returns None when AccuSim isn't running, else parts, failures and a 0-100 score per Onward system."""
    if not is_active(raw):
        return None

    def val(name):
        v = raw.get(name)
        return None if v is None else max(0.0, min(1.0, float(v)))

    cyl = cylinders
    if not cyl:
        # a variable the aircraft doesn't have reads as exactly 0: count the cylinders that report anything
        cyl = sum(1 for i in range(1, MAX_CYL + 1)
                  if any((raw.get(n) or 0) > 0 for n in (f"C_Eng1_Cyl[{i}]", f"C_Eng1_Cyl{i}_SparkPlugL", f"C_Eng1_Cyl{i}_SparkPlugR")))
        cyl = cyl or 4
    cyl = max(1, min(MAX_CYL, int(cyl)))

    cyl_parts = cylinder_parts(cyl)
    for side in ("SparkPlugL", "SparkPlugR"):
        # a whole set of plugs at exactly 0 means the aircraft doesn't publish them, not that they all died
        names = [n for n, _ in cyl_parts if n.endswith(side)]
        if names and all((raw.get(n) or 0) == 0 for n in names):
            cyl_parts = [p for p in cyl_parts if p[0] not in names]

    groups = {"engine": ENGINE_PARTS + cyl_parts, "airframe": AIRFRAME_PARTS, "gear": GEAR_PARTS, "prop": PROP_PARTS}
    parts, systems = [], {}
    for system, group in groups.items():
        vals = []
        for name, label in group:
            v = val(name)
            if v is None:
                continue
            # parts the aircraft doesn't have also read 0 (a fixed-gear Cub has no nose tire, a fuel-injected
            # engine has no carburetor): leave out zeros, except for the cylinders and plugs we know exist
            if v == 0 and not name.startswith("C_Eng1_Cyl") and name not in ("C_Eng1_Main", "C_Eng1_Prop"):
                continue
            pct = round(v * 100, 1)
            parts.append({"id": name, "label": label, "system": system, "pct": pct})
            vals.append(pct)
        if vals:
            systems[system] = round(WORST_WEIGHT * min(vals) + (1 - WORST_WEIGHT) * sum(vals) / len(vals), 1)
    failed = [label for name, label in FAILURES if (raw.get(name) or 0) >= 0.5]

    def num(key, digits=1):
        v = raw.get(INFO[key])
        return None if v is None else round(float(v), digits)

    return {"systems": systems, "parts": parts, "failed": failed, "cylinders": cyl,
            "total_time": num("total_time", 2), "engine_time": num("engine_time", 2), "oil_pct": num("oil_pct", 0)}


def worst(summary, n=5, below=90):
    """The parts most in need of attention, worst first."""
    if not summary:
        return []
    return sorted([p for p in summary["parts"] if p["pct"] < below], key=lambda p: p["pct"])[:n]
