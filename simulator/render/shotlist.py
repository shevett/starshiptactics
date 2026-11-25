"""Shot lists: a film described as data (YAML), resolved against a finished run. See docs/RENDER.md.

A shot list has three parts:
  film     frame rate, resolution, hull enlargement;
  anchors  named moments found in the run's events (a ship's destruction, the missile that caused it), so shots do not
           hard-code times and the list still works after the scenario is changed and re-run;
  shots    in order: what the scene follows, the camera rig, and how film time maps to simulated time.
"""
import re
from pathlib import Path

import numpy as np
import yaml

from . import shot as shot_mod

BUILTIN = Path(__file__).resolve().parent / "sequences"

FILM_KEYS = {"fps", "resolution", "ship_scale", "engine"}
SHOT_KEYS = {"name", "follow", "camera", "start", "end", "duration", "speed", "pacing", "end_range"}
RIG_KEYS = {"orbit": {"distance", "azimuth", "elevation", "relative_to"}, "chase": {"back", "lift", "swing"}}
ANCHOR_KINDS = ("event", "missile_that_killed", "missile", "time")

_EXPR = re.compile(r"^\s*(\$?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?|\d+(?:\.\d*)?)\s*(?:([+-])\s*(\d+(?:\.\d*)?))?\s*$")


class ShotListError(ValueError):
    pass


def load(path):
    path = Path(path)
    if not path.exists():
        raise ShotListError(f"shot list not found: {path}")
    spec = yaml.safe_load(path.read_text()) or {}
    unknown = set(spec) - {"film", "anchors", "shots"}
    if unknown:
        raise ShotListError(f"{path.name}: unknown section(s) {sorted(unknown)} (expected film, anchors, shots)")
    if not spec.get("shots"):
        raise ShotListError(f"{path.name}: no shots")
    unknown = set(spec.get("film") or {}) - FILM_KEYS
    if unknown:
        raise ShotListError(f"{path.name}: unknown film setting(s) {sorted(unknown)} (expected {sorted(FILM_KEYS)})")
    return spec


def _lookup(ref, anchors, variables, where):
    if ref.startswith("$"):
        return variables.get(ref[1:])
    head, _, field = ref.partition(".")
    if head not in anchors:
        raise ShotListError(f"{where}: unknown anchor '{head}' (anchors: {', '.join(anchors) or 'none'})")
    a = anchors[head]
    if not field:
        return a
    if field not in a:
        raise ShotListError(f"{where}: anchor '{head}' has no '{field}' (it has: {', '.join(a)})")
    return a[field]


def _value(v, anchors, variables, where):
    """A literal, or a reference to a variable ($target) or an anchor field (killer.launcher)."""
    if isinstance(v, str) and (v.startswith("$") or v.partition(".")[0] in anchors):
        return _lookup(v, anchors, variables, where)
    return v


def _time(expr, anchors, variables, where):
    """Simulated seconds from a number, an anchor, or an anchor plus or minus an offset ('kill - 0.45')."""
    if expr is None:
        return None
    if isinstance(expr, (int, float)):
        return float(expr)
    m = _EXPR.match(str(expr))
    if not m:
        raise ShotListError(f"{where}: cannot read time '{expr}' (use a number, an anchor, or 'anchor + seconds')")
    term, sign, off = m.groups()
    if term[0].isdigit():
        base = float(term)
    else:
        base = _lookup(term, anchors, variables, where)
        base = base["t"] if isinstance(base, dict) else base
        if not isinstance(base, (int, float)):
            raise ShotListError(f"{where}: '{term}' is not a time")
    return float(base) + (float(off) if off else 0.0) * (-1.0 if sign == "-" else 1.0)


def _missile_anchor(run, mid, where):
    if mid not in run.m_launch or mid not in run.m_first:
        raise ShotListError(f"{where}: no launch or track recorded for missile '{mid}'")
    tl, launcher, target = run.m_launch[mid]
    end = float(run.m_end.get(mid, run.m_span[mid][1]))
    t = min(max(float(run.mt[run.m_first[mid]]), tl + 0.5), end)          # a moment after launch, to see which way it left
    away = run.missile_pos(mid, t) - run.state(launcher, t)["p"]
    return {"t": end, "missile": mid, "launcher": launcher, "target": target, "launch": float(tl), "impact": end,
            "heading": (away / max(np.linalg.norm(away), 1e-9)).tolist()}


def _killer(run, ship, where):
    kills = [e for e in run.events if e["type"] == "ship_destroyed" and (ship is None or e["actor_id"] == ship)]
    if not kills:
        raise ShotListError(f"{where}: {ship or 'no ship'} was {'not ' if ship else ''}destroyed in this run")
    kill = kills[-1]
    ship = kill["actor_id"]
    dets = [e for e in run.events if e["type"] == "detonation" and e["data"]["target_id"] == ship and e["t"] <= kill["t"] + 1e-6
            and e["actor_id"] in run.m_launch]
    if not dets:
        raise ShotListError(f"{where}: no recorded missile hit on {ship} before it was destroyed")
    # several warheads can go off in the same instant: take the first of that group (the one that started the kill)
    return next(e["actor_id"] for e in dets if abs(e["t"] - dets[-1]["t"]) < 1e-6)


def resolve_anchors(run, spec, variables=None):
    """name -> dict of fields. Every anchor has 't' (its moment); the rest depends on its kind."""
    variables, anchors = variables or {}, {}
    for name, a in (spec or {}).items():
        where = f"anchor '{name}'"
        if not isinstance(a, dict) or sum(k in a for k in ANCHOR_KINDS) != 1:
            raise ShotListError(f"{where}: needs exactly one of {', '.join(ANCHOR_KINDS)}")
        val = lambda k: _value(a.get(k), anchors, variables, where)
        if "time" in a:
            anchors[name] = {"t": _time(a["time"], anchors, variables, where)}
        elif "missile" in a:
            anchors[name] = _missile_anchor(run, val("missile"), where)
        elif "missile_that_killed" in a:
            anchors[name] = _missile_anchor(run, _killer(run, val("missile_that_killed"), where), where)
        else:
            unknown = set(a) - {"event", "ship", "target", "which"}
            if unknown:
                raise ShotListError(f"{where}: unknown key(s) {sorted(unknown)}")
            ship, target = val("ship"), val("target")
            ev = [e for e in run.events if e["type"] == a["event"] and (ship is None or e["actor_id"] == ship)
                  and (target is None or e.get("data", {}).get("target_id") == target)]
            if not ev:
                what = " ".join(x for x in (f"for {ship}" if ship else "", f"on {target}" if target else "") if x)
                raise ShotListError(f"{where}: no '{a['event']}' event {what} in this run".replace("  ", " "))
            e = ev[-1 if a.get("which", "first") == "last" else 0]
            d = e.get("data", {})
            anchors[name] = {"t": float(e["t"]), "ship": e["actor_id"], "target": d.get("target_id"), "missile": d.get("missile_id")}
    return anchors


def solve_time(start, end, duration, speed, where="shot"):
    """Three of start, end (simulated seconds), duration (film seconds) and speed (simulated seconds per film second)
    give the fourth. Returns (start, end, duration, speed)."""
    given = sum(x is not None for x in (start, end, duration, speed))
    if given != 3:
        raise ShotListError(f"{where}: give exactly three of start, end, duration and speed (got {given})")
    if speed is None:
        speed = (end - start) / duration
    elif duration is None:
        duration = (end - start) / speed
    elif end is None:
        end = start + duration * speed
    else:
        start = end - duration * speed
    if duration <= 0 or speed <= 0 or end <= start:
        raise ShotListError(f"{where}: the shot must run forward in time (start {start:.2f}, end {end:.2f}, duration {duration:.2f} s)")
    return start, end, duration, speed


def _pair(v, where):
    v = list(v) if isinstance(v, (list, tuple)) else [v, v]
    if len(v) != 2:
        raise ShotListError(f"{where}: expected one number or [from, to]")
    return [float(v[0]), float(v[1])]


def film_settings(spec, **overrides):
    """Film settings: explicit overrides (the command line) win over the list's film section, then the defaults."""
    film = {"fps": 24, "resolution": [1280, 720], "ship_scale": 40.0, "engine": "eevee", **(spec.get("film") or {})}
    film.update({k: v for k, v in overrides.items() if v is not None})
    return film


def plan(run, spec, repo, variables=None, fps=24, ship_scale=40.0):
    """Resolve a shot list against a run. Returns dict(anchors, shots, heroes); shots feed shot.build_sequence."""
    variables = variables or {}
    anchors = resolve_anchors(run, spec.get("anchors"), variables)
    dims = shot_mod._class_dims(run, repo)
    shots, heroes = [], []
    for i, s in enumerate(spec["shots"]):
        name = s.get("name") or f"shot{i + 1}"
        where = f"shot '{name}'"
        unknown = set(s) - SHOT_KEYS
        if unknown:
            raise ShotListError(f"{where}: unknown key(s) {sorted(unknown)}")
        obj = _value(s.get("follow"), anchors, variables, where)
        if obj in run.ships:
            origin = ("ship", obj)
        elif obj in run.m_span:
            origin = ("missile", obj)
            heroes.append(obj)
        else:
            raise ShotListError(f"{where}: follow must be a ship or a missile in this run (got {s.get('follow')!r})")
        is_missile = origin[0] == "missile"

        # time
        start, end = (_time(s.get(k), anchors, variables, where) for k in ("start", "end"))
        duration, speed = s.get("duration"), s.get("speed")
        pacing = s.get("pacing", "constant")
        if pacing == "closing":
            if not is_missile or start is None or duration is None or end is not None or speed is not None:
                raise ShotListError(f"{where}: closing pacing follows a missile and takes start and duration (not end or speed)")
            target = run.m_launch[obj][2]
            hull = dims[run.info[target]["class"]]["length"] * ship_scale
            times = shot_mod.closing_times(run, obj, start, float(duration), fps, float(s.get("end_range", 0.75)) * hull)
        elif pacing == "constant":
            start, end, duration, speed = solve_time(start, end, duration, speed, where)
            times = [start + f * speed / fps for f in range(max(1, int(round(duration * fps))))]
        else:
            raise ShotListError(f"{where}: unknown pacing '{pacing}' (constant, closing)")
        if is_missile and (times[0] < run.m_span[obj][0] - 1e-6 or times[-1] > run.m_span[obj][1] + 1e-6):
            a, b = run.m_span[obj]
            raise ShotListError(f"{where}: {obj} is only in flight from T+{a:.2f} to T+{b:.2f} s (shot runs T+{times[0]:.2f} to T+{times[-1]:.2f})")
        if times[0] < run.t[0] or times[-1] > run.t[-1]:
            raise ShotListError(f"{where}: runs T+{times[0]:.2f} to T+{times[-1]:.2f} s, outside the run (T+{run.t[0]:.0f} to T+{run.t[-1]:.0f})")

        # camera
        cam = dict(s.get("camera") or {})
        kind = cam.pop("rig", "orbit")
        if kind not in RIG_KEYS:
            raise ShotListError(f"{where}: unknown camera rig '{kind}' ({', '.join(RIG_KEYS)})")
        unknown = set(cam) - RIG_KEYS[kind]
        if unknown:
            raise ShotListError(f"{where}: unknown {kind} camera key(s) {sorted(unknown)} (expected {sorted(RIG_KEYS[kind])})")
        if kind == "orbit":
            ref = _value(cam.get("relative_to"), anchors, variables, where)
            if ref is not None and not (isinstance(ref, (list, tuple)) and len(ref) == 3):
                raise ShotListError(f"{where}: relative_to must be a direction, such as an anchor's heading")
            rig = {"type": "orbit", "dist": float(cam.get("distance", 4.0)), "azimuth_deg": _pair(cam.get("azimuth", [50.0, 110.0]), where),
                   "elevation_deg": float(cam.get("elevation", 14.0)), "ref": ref}
        else:
            if not is_missile:
                raise ShotListError(f"{where}: the chase camera follows a missile")
            rig = {"type": "chase", "target": run.m_launch[obj][2], "back": float(cam.get("back", 8.0)), "lift": float(cam.get("lift", 1.0)),
                   "swing_deg": _pair(cam.get("swing", 0.0), where)}
        shots.append({"name": name, "origin": origin, "times": times, "rig": rig})
    return {"anchors": anchors, "shots": shots, "heroes": heroes}
