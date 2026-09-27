"""Load and validate YAML configuration; normalise to SI.

All problems found are collected and raised together as a ConfigError.
"""
import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import ConfigError
from .units import C, DAY_S, G0, KM, deg, pick_vector

OBJECTIVE_TYPES = {"coast", "hold_position", "transit_to_point", "transit_to_orbit"}
DIRECTIONS = {"prograde": 1.0, "retrograde": -1.0}
FORMATIONS = {"none", "line_abreast"}
TERMINATIONS = {"duration_reached", "all_objectives_complete", "scripted_stop"}


@dataclass
class Body:
    id: str
    type: str
    parent: str | None
    radius_m: float
    mu: float                      # m^3/s^2
    a_m: float = 0.0
    e: float = 0.0
    i_rad: float = 0.0
    node_rad: float = 0.0          # longitude of ascending node
    peri_rad: float = 0.0          # argument of periapsis
    period_s: float = 0.0
    clearance_m: float = 0.0


@dataclass
class ShipClass:
    id: str
    max_accel: float               # m/s^2
    max_rotation_rate: float       # rad/s
    raw: dict = field(default_factory=dict)


@dataclass
class Group:
    id: str
    faction: str
    class_id: str
    count: int
    position: tuple
    velocity: tuple
    forward: tuple
    formation: str
    spacing_m: float
    objective: dict
    orbit: dict | None = None      # initial circular orbit around a body (instead of position/velocity)
    engagement: dict | None = None # rules of engagement (weapons); None = hold fire


@dataclass
class Config:
    root: Path
    scenario_path: Path
    scenario: dict
    bodies: list
    ship_classes: dict
    factions: list
    groups: list
    nav: dict
    sim: dict
    doctrine: dict
    file_hashes: dict
    warnings: list
    time_step_s: float = 1.0
    duration_s: float = 0.0
    snapshot_interval_s: float = 10.0
    start_time_s: float = 0.0
    seed: int = 0
    phase_strategy: str = "random"
    phase_overrides: dict = field(default_factory=dict)   # body -> true anomaly (rad)
    terminations: list = field(default_factory=list)
    gravity_types: list = field(default_factory=list)
    missile_classes: dict = field(default_factory=dict)
    hyper_limit_m: float | None = None


def _load_yaml(path, problems):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = yaml.safe_load(raw)
    except FileNotFoundError:
        problems.append(f"{path}: file not found")
        return None, None
    except yaml.YAMLError as exc:
        problems.append(f"{path}: YAML error: {exc}")
        return None, None
    return (data if data is not None else {}), hashlib.sha256(raw).hexdigest()


def _num(d, key, where, problems, *, positive=False, nonneg=False, default=None):
    v = d.get(key, default) if isinstance(d, dict) else default
    if isinstance(v, str):
        try:                      # YAML 1.1 reads '1.5e11' (no exponent sign) as a string
            v = float(v)
        except ValueError:
            pass
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        problems.append(f"{where}.{key}: expected a number, got {v!r}")
        return None
    if positive and v <= 0:
        problems.append(f"{where}.{key}: must be > 0, got {v}")
    if nonneg and v < 0:
        problems.append(f"{where}.{key}: must be >= 0, got {v}")
    return float(v)


def _build_bodies(system, nav, sim, problems, warnings):
    bodies_raw = system.get("bodies") or {}
    if not bodies_raw:
        problems.append("system: no bodies defined")
        return []
    clr = (nav.get("navigation", {}).get("celestial_exclusion") or {})
    clearance_key = {
        "star": "star_surface_clearance_km", "planet": "planet_surface_clearance_km",
        "moon": "moon_surface_clearance_km", "dwarf_planet": "dwarf_planet_surface_clearance_km",
        "asteroid": "asteroid_surface_clearance_km",
    }
    miss_inc = sim.get("simulation", {}).get("orbit_initialization", {}).get("missing_inclination_deg", 0.0)
    missing_inc, missing_angles, missing_ecc, neg_period = [], [], [], []
    bodies = []
    for bid, b in bodies_raw.items():
        w = f"system.bodies.{bid}"
        btype = b.get("type")
        if btype not in clearance_key:
            problems.append(f"{w}.type: unsupported type {btype!r}")
            continue
        ck = clearance_key[btype]
        if ck not in clr:
            problems.append(f"rules/navigation.yaml celestial_exclusion.{ck}: missing (needed for {bid})")
            continue
        radius = _num(b, "radius_km", w, problems, positive=True)
        mu = b.get("mu_km3_s2")
        if mu is None and b.get("mass_kg") is not None:
            mu = b["mass_kg"] * 6.6743e-11 / 1e9
        mu = _num({"mu_km3_s2": mu}, "mu_km3_s2", w, problems, positive=True)
        body = Body(bid, btype, b.get("parent"), (radius or 0) * KM, (mu or 0) * 1e9,
                    clearance_m=float(clr[ck]) * KM)
        if body.parent is not None:
            if body.parent not in bodies_raw:
                problems.append(f"{w}.parent: unknown body {body.parent!r}")
            orb = b.get("orbit")
            if not isinstance(orb, dict):
                problems.append(f"{w}.orbit: required for a body with a parent")
                continue
            body.a_m = (_num(orb, "semi_major_axis_km", w + ".orbit", problems, positive=True) or 0) * KM
            if orb.get("eccentricity") is None:
                missing_ecc.append(bid)
            else:
                body.e = _num(orb, "eccentricity", w + ".orbit", problems, nonneg=True) or 0.0
            if body.e >= 1:
                problems.append(f"{w}.orbit.eccentricity: must be < 1")
            if "inclination_deg" in orb:
                body.i_rad = deg(_num(orb, "inclination_deg", w + ".orbit", problems) or 0)
            else:
                body.i_rad = deg(miss_inc)
                missing_inc.append(bid)
            for key, attr in (("longitude_of_ascending_node_deg", "node_rad"),
                              ("argument_of_periapsis_deg", "peri_rad")):
                if key in orb:
                    setattr(body, attr, deg(_num(orb, key, w + ".orbit", problems) or 0))
                elif bid not in missing_angles:
                    missing_angles.append(bid)
            pd = _num(orb, "orbital_period_days", w + ".orbit", problems)
            if pd == 0:
                problems.append(f"{w}.orbit.orbital_period_days: must be non-zero")
            if pd is not None and pd < 0:
                neg_period.append(bid)      # retrograde is expressed by inclination > 90 deg; use |period|
            body.period_s = abs(pd or 0) * DAY_S
        bodies.append(body)
    # order: parents before children
    ids = {b.id for b in bodies}
    ordered, placed = [], set()
    pending = list(bodies)
    while pending:
        progressed = False
        for b in list(pending):
            if b.parent is None or b.parent in placed:
                ordered.append(b); placed.add(b.id); pending.remove(b); progressed = True
        if not progressed:
            problems.append("system: cyclic or unresolved parent links among " + ", ".join(b.id for b in pending))
            break
    if sim.get("simulation", {}).get("orbit_initialization", {}).get("warn_on_missing_optional_elements", True):
        if missing_inc:
            warnings.append(f"missing inclination (assumed {miss_inc} deg) for: {', '.join(missing_inc)}")
        if missing_ecc:
            warnings.append(f"missing eccentricity (assumed 0) for: {', '.join(missing_ecc)}")
        if neg_period:
            warnings.append(f"negative orbital period for {', '.join(neg_period)}: using |period|; "
                            "retrograde motion comes from inclination > 90 deg")
        if missing_angles:
            warnings.append("longitude of ascending node / argument of periapsis not given; assumed 0 for "
                            f"{len(missing_angles)} bodies")
    return ordered


def _build_ship_classes(raw, sim, problems):
    out = {}
    default_rate = sim["simulation"]["attitude_control"]["default_max_rotation_rate_deg_s"]
    for cid, c in (raw.get("classes") or {}).items():
        w = f"classes/ships.yaml classes.{cid}"
        prop = c.get("propulsion") or {}
        g = _num(prop, "max_acceleration_g", w + ".propulsion", problems, positive=True)
        axis = prop.get("primary_thrust_axis", "longitudinal")
        if axis != "longitudinal" or prop.get("lateral_primary_thrust") or prop.get("reverse_primary_thrust"):
            problems.append(f"{w}.propulsion: only longitudinal, forward-only primary thrust is implemented")
        rate = prop.get("max_rotation_rate_deg_s")
        rate = default_rate if rate is None else rate
        out[cid] = ShipClass(cid, (g or 0) * G0, deg(rate), c)
    return out


MISSILE_REQUIRED = ("acceleration_g", "powered_duration_s", "warhead.damage_radius_km", "warhead.damage_falloff",
                    "warhead.max_damage")


def _dig(d, dotted):
    for k in dotted.split("."):
        d = (d or {}).get(k)
    return d


def _missile_problems(name, spec, where):
    """Problems that stop a missile class being fired: only values the simulator actually uses are required."""
    if spec is None:
        return [f"{where}: unknown missile class {name!r}"]
    if (spec.get("warhead") or {}).get("type") != "nuclear":
        return [f"{where}: cannot fire {name!r}: only nuclear warheads are implemented; unresolved values: "
                f"{', '.join(_null_fields(spec.get('warhead') or {})) or 'none'} (see docs/OPEN_QUESTIONS.md)"]
    unresolved = [k for k in MISSILE_REQUIRED if _dig(spec, k) is None]
    out = []
    if unresolved:
        out.append(f"{where}: cannot fire missile {name!r}; unresolved values: {', '.join(unresolved)} "
                   f"(see docs/OPEN_QUESTIONS.md)")
    elif spec["warhead"]["damage_falloff"] != "linear":
        out.append(f"{where}: warhead.damage_falloff {spec['warhead']['damage_falloff']!r} not supported (only 'linear')")
    return out


def _parse_engagement(spec, cls, missiles, where, doctrine):
    """Validate a group's `engagement:` block and return SI/normalised values."""
    mode = spec.get("mode", "hold_fire")
    if mode not in ("hold_fire", "fire_at_will"):
        raise ValueError(f"{where}.mode: must be hold_fire or fire_at_will")
    batteries = (cls.raw.get("weapons") or {}).get("missile_batteries", []) or []
    compatible = sorted({m for b in batteries for m in b.get("compatible_missiles", [])})
    out = {"mode": mode, "missile": spec.get("missile"), "salvo_size": None}
    if mode == "hold_fire":
        return out
    if not batteries:
        raise ValueError(f"{where}: ship class {cls.id!r} has no missile batteries")
    if out["missile"] is None:
        out["missile"] = compatible[0]
    if out["missile"] not in compatible:
        raise ValueError(f"{where}.missile: {out['missile']!r} is not carried by {cls.id!r} (compatible: {', '.join(compatible)})")
    probs = _missile_problems(out["missile"], (missiles.get("missiles") or {}).get(out["missile"]), where)
    if probs:
        raise ValueError("; ".join(probs))
    salvo = spec.get("salvo_size", (doctrine.get("doctrine", {}).get("engagement") or {}).get("salvo_size"))
    if not isinstance(salvo, int) or salvo < 1:
        raise ValueError(f"{where}.salvo_size: must be an integer >= 1 (or set doctrine.engagement.salvo_size)")
    out["salvo_size"] = salvo
    return out


def _null_fields(d, prefix=""):
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out += _null_fields(v, f"{prefix}{k}.")
        elif v is None:
            out.append(f"{prefix}{k}")
    return out


_SPECTRAL_ORDER = "FGKM"       # the rule is only defined for these classes, ten subclasses each


def spectral_index(spectral_class):
    """'G2V' -> 12 (letter F=0..M=3, times 10, plus the digit). Luminosity suffixes are ignored."""
    sc = str(spectral_class).strip().upper()
    if len(sc) < 2 or sc[0] not in _SPECTRAL_ORDER or not sc[1].isdigit():
        raise ValueError(f"spectral class {spectral_class!r}: expected a letter F, G, K or M and a digit, e.g. G2V")
    return _SPECTRAL_ORDER.index(sc[0]) * 10 + int(sc[1])


def hyper_limit_light_minutes(spectral_class, baseline_class="G0", baseline_lm=22.0, step_lm=0.44):
    """Weber's linear table: baseline at G0 (22.00 light-minutes), minus 0.44 per subclass step toward cooler stars."""
    return baseline_lm - step_lm * (spectral_index(spectral_class) - spectral_index(baseline_class))


def _hyper_limit_m(hl, system):
    given = [k for k in ("radius_km", "light_minutes", "rule") if k in hl]
    if len(given) != 1:
        raise ValueError("give exactly one of radius_km, light_minutes or rule")
    if given[0] == "radius_km":
        return float(hl["radius_km"]) * KM
    if given[0] == "light_minutes":
        return float(hl["light_minutes"]) * 60 * C
    if hl["rule"] != "spectral_class_linear":
        raise ValueError(f"unknown rule {hl['rule']!r} (supported: spectral_class_linear)")
    stars = [b for b in (system.get("bodies") or {}).values() if b.get("type") == "star" and b.get("parent") is None]
    if len(stars) != 1 or "spectral_class" not in stars[0]:
        raise ValueError("rule spectral_class_linear needs exactly one primary star with a spectral_class")
    lm = hyper_limit_light_minutes(stars[0]["spectral_class"], hl.get("baseline_class", "G0"),
                                   float(hl.get("baseline_light_minutes", 22.0)), float(hl.get("step_light_minutes", 0.44)))
    return lm * 60 * C


def _parse_orbit(spec, where, bodies, nav_margin, *, allow_phase):
    """Validate an orbit spec {body, altitude_km | radius_km, inclination_deg, ...}; return SI dict."""
    by_id = {b.id: b for b in bodies}
    body = by_id.get(spec.get("body"))
    if body is None:
        raise ValueError(f"{where}.body: unknown body {spec.get('body')!r} (see systems/*.yaml)")
    if ("altitude_km" in spec) == ("radius_km" in spec):
        raise ValueError(f"{where}: give exactly one of altitude_km (above the surface) or radius_km (from the centre)")
    radius = (body.radius_m + float(spec["altitude_km"]) * KM) if "altitude_km" in spec else float(spec["radius_km"]) * KM
    floor = (body.radius_m + body.clearance_m) * nav_margin
    if radius < floor:
        raise ValueError(f"{where}: orbit radius {radius / KM:.0f} km is inside the {body.id} exclusion zone; "
                         f"minimum is {floor / KM:.0f} km from the centre (altitude "
                         f"{(floor - body.radius_m) / KM:.0f} km)")
    direction = spec.get("direction", "prograde")
    if direction not in DIRECTIONS:
        raise ValueError(f"{where}.direction: must be prograde or retrograde")
    out = {"body": body.id, "radius_m": radius, "direction": DIRECTIONS[direction],
           "inclination_rad": deg(float(spec.get("inclination_deg", 0.0))),
           "node_rad": deg(float(spec.get("longitude_of_ascending_node_deg", 0.0)))}
    out["spacing_m"] = float(spec["spacing_km"]) * KM if "spacing_km" in spec else None
    if allow_phase:
        out["phase_rad"] = deg(float(spec["phase_deg"])) if "phase_deg" in spec else None
    return out


def load_config(scenario_path, root=None):
    scenario_path = Path(scenario_path).resolve()
    root = Path(root).resolve() if root else scenario_path.parent.parent
    problems, warnings, hashes = [], [], {}

    def load(rel):
        path = root / rel
        data, h = _load_yaml(path, problems)
        if h:
            hashes[rel] = h
        return data or {}

    sc_doc, h = _load_yaml(scenario_path, problems)
    if h:
        hashes[str(scenario_path.relative_to(root)) if scenario_path.is_relative_to(root) else scenario_path.name] = h
    sc_doc = sc_doc or {}
    nav = load("rules/navigation.yaml")
    sim = load("rules/simulation.yaml")
    doctrine = load("rules/doctrine.yaml")
    ships_raw = load("classes/ships.yaml")
    missiles = load("classes/missiles.yaml")
    load("classes/stations.yaml")   # must parse; may be empty
    if problems:
        raise ConfigError(problems)

    for req_path, d in (("navigation", nav), ("simulation", sim)):
        if req_path not in d:
            problems.append(f"rules/{req_path}.yaml: missing top-level '{req_path}' key")
    if problems:
        raise ConfigError(problems)

    sc = sc_doc.get("scenario")
    if not isinstance(sc, dict):
        raise ConfigError(f"{scenario_path.name}: missing top-level 'scenario' mapping")
    for key in ("id", "system", "random_seed"):
        if key not in sc:
            problems.append(f"scenario.{key}: required")
    if problems:
        raise ConfigError(problems)

    system = load(f"systems/{sc['system']}.yaml")
    if "bodies" not in system:
        problems.append(f"systems/{sc['system']}.yaml: not found or has no bodies")
        raise ConfigError(problems)
    simd = sim["simulation"]

    cfg = Config(root, scenario_path, sc, [], {}, [], [], nav, sim, doctrine, hashes, warnings)
    cfg.seed = int(sc["random_seed"])
    cfg.missile_classes = missiles
    hl = system.get("hyper_limit")
    if hl:
        try:
            cfg.hyper_limit_m = _hyper_limit_m(hl, system)
        except ValueError as exc:
            problems.append(f"systems/{sc['system']}.yaml hyper_limit: {exc}")
    cfg.gravity_types = list((system.get("simulation_defaults") or {}).get("include_gravity_from_types") or [])
    defaults = simd.get("defaults", {})
    cfg.time_step_s = _num(sc, "time_step_s", "scenario", problems, positive=True, default=defaults.get("time_step_s"))
    cfg.duration_s = _num(sc, "duration_s", "scenario", problems, positive=True, default=defaults.get("duration_s"))
    cfg.snapshot_interval_s = _num(sc, "snapshot_interval_s", "scenario", problems, positive=True,
                                   default=defaults.get("snapshot_interval_s"))
    cfg.start_time_s = _num(sc, "start_time_s", "scenario", problems, default=0.0)
    cfg.phase_strategy = sc.get("orbital_phase_strategy", simd.get("orbit_initialization", {}).get("phase_strategy", "random"))
    if cfg.phase_strategy not in ("random", "zero"):
        problems.append(f"scenario.orbital_phase_strategy: must be 'random' or 'zero', got {cfg.phase_strategy!r}")
    if cfg.time_step_s and cfg.snapshot_interval_s:
        ratio = cfg.snapshot_interval_s / cfg.time_step_s
        if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
            problems.append("scenario.snapshot_interval_s must be a whole multiple of time_step_s")

    cfg.bodies = _build_bodies(system, nav, sim, problems, warnings)
    body_ids = {b.id for b in cfg.bodies}
    for bid, ta in (sc.get("orbital_phase_deg") or {}).items():
        if bid not in body_ids:
            problems.append(f"scenario.orbital_phase_deg.{bid}: unknown body")
        else:
            cfg.phase_overrides[bid] = deg(float(ta))

    cfg.ship_classes = _build_ship_classes(ships_raw, sim, problems)
    cfg.factions = [f["id"] for f in (sc_doc.get("factions") or [])]
    if len(set(cfg.factions)) != len(cfg.factions):
        problems.append("factions: duplicate ids")

    seen_groups = set()
    for idx, u in enumerate(sc_doc.get("units") or []):
        w = f"units[{idx}]"
        gid = u.get("group")
        if not gid:
            problems.append(f"{w}.group: required"); continue
        w = f"units[{gid}]"
        if gid in seen_groups:
            problems.append(f"{w}: duplicate group id")
        seen_groups.add(gid)
        if u.get("faction") not in cfg.factions:
            problems.append(f"{w}.faction: {u.get('faction')!r} is not a declared faction")
        if u.get("class") not in cfg.ship_classes:
            problems.append(f"{w}.class: unknown ship class {u.get('class')!r}")
        count = u.get("count", 1)
        if not isinstance(count, int) or count < 1:
            problems.append(f"{w}.count: must be an integer >= 1")
            count = 1
        try:
            nav_margin = sim["simulation"]["navigation_defaults"]["exclusion_destination_margin"]
            orbit = None
            if "orbit" in u:
                clash = [k for k in u if k.startswith(("position_", "velocity_"))]
                if clash:
                    raise ValueError(f"{w}: 'orbit' replaces {', '.join(clash)}; give one or the other")
                orbit = _parse_orbit(u["orbit"], w + ".orbit", cfg.bodies, nav_margin, allow_phase=True)
                pos, vel = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)     # computed at start-up from the body's state
            else:
                pos = pick_vector(u, {"position_km": KM, "position_m": 1.0}, w)
                vel = pick_vector(u, {"velocity_km_s": KM, "velocity_m_s": 1.0}, w, required=False) or (0.0, 0.0, 0.0)
            fwd = (u.get("orientation") or {}).get("forward_vector")
            fwd = tuple(fwd) if fwd is not None else (None if orbit else (1, 0, 0))   # orbit: default = along velocity
            if fwd is not None and (len(fwd) != 3 or math.sqrt(sum(c * c for c in fwd)) == 0):
                raise ValueError(f"{w}.orientation.forward_vector: need a non-zero 3-vector")
            form = u.get("formation") or {"type": "none"}
            ftype = form.get("type", "none")
            if ftype not in FORMATIONS:
                raise ValueError(f"{w}.formation.type: {ftype!r} not in {sorted(FORMATIONS)}")
            spacing = float(form.get("spacing_km", 0)) * KM
            obj = dict(u.get("objective") or {"type": "coast"})
            otype = obj.get("type")
            if otype not in OBJECTIVE_TYPES:
                raise ValueError(f"{w}.objective.type: {otype!r} not in {sorted(OBJECTIVE_TYPES)}")
            if otype == "transit_to_orbit":
                obj["orbit"] = _parse_orbit(obj, w + ".objective", cfg.bodies, nav_margin, allow_phase=False)
            if otype == "transit_to_point":
                obj["destination_m"] = pick_vector(
                    obj, {"destination_km": KM, "destination_m": 1.0}, w + ".objective")
                if "max_speed_km_s" in obj:
                    obj["max_speed_m_s"] = float(obj["max_speed_km_s"]) * KM
            engagement = None
            if "engagement" in u and u.get("class") in cfg.ship_classes:
                engagement = _parse_engagement(u["engagement"] or {}, cfg.ship_classes[u["class"]], missiles, w + ".engagement", doctrine)
            cfg.groups.append(Group(gid, u.get("faction"), u.get("class"), count, pos, vel, fwd, ftype, spacing, obj, orbit,
                                    engagement))
        except ValueError as exc:
            problems.append(str(exc))

    for t in sc_doc.get("termination") or [{"type": "duration_reached"}]:
        if t.get("type") not in TERMINATIONS:
            problems.append(f"termination: unknown type {t.get('type')!r}")
        cfg.terminations.append(t)

    if problems:
        raise ConfigError(problems)
    return cfg
