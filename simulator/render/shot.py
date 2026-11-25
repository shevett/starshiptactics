"""Turn a finished run into a shot: everything Blender needs for each output frame, as plain JSON.

Scale. Real space is unfilmable: ships hundreds of metres long sit hundreds of kilometres apart, and a missile crosses
that in a fraction of a frame. So the shot uses a "cinematic scale":
  - positions stay true, in scene units of `unit_m` metres, relative to the target ship (a floating origin: Blender
    stores single-precision coordinates, which cannot hold solar-system distances);
  - ship hulls are enlarged by `ship_scale`, so a formation reads as a formation;
  - a detonation's stand-off from its target is enlarged by the same factor, so it does not end up inside the hull;
  - a missile is drawn as the streak it covers during one frame (honest motion blur);
  - bodies beyond `far` scene units are drawn on a shell at `far` with the same apparent size.

A clip is a sequence of shots. Each shot has its own scene origin (a ship or a missile), its own camera rig, and its
own mapping from frame to simulated time, so one shot can run in slow motion and the next can compress minutes.
Lifetimes of effects (flashes, glow) are in seconds of film, so they look the same at any playback speed.
"""
import json
import math
from pathlib import Path

import numpy as np
import yaml

from . import hulls
from .interp import Run

FAR = 2.0e6               # scene units; anything farther is projected onto a shell at this distance
FACTION_RGB = {"red": [1.0, 0.25, 0.2], "blue": [0.3, 0.55, 1.0]}


def _wxyz(q):             # simulator quaternions are xyzw, Blender's are wxyz
    return [float(q[3]), float(q[0]), float(q[1]), float(q[2])]


def default_window(run, target=None, t0=None, t1=None, length_s=150.0, lead_s=20.0):
    """Pick a target and time window when none is given: the first ship hit, from just before its first hit."""
    dets = [e for e in run.events if e["type"] == "detonation" and (target is None or e["data"]["target_id"] == target)]
    if target is None:
        target = dets[0]["data"]["target_id"] if dets else run.ships[0]
    if t0 is None:
        t0 = max(0.0, dets[0]["t"] - lead_s) if dets else 0.0
    if t1 is None:
        t1 = min(float(run.t[-1]), t0 + length_s)
    return target, float(t0), float(t1)


def _nose(q):                                     # heading of a ship's nose in the ecliptic, from its xyzw quaternion
    x, y, z, w = q
    return np.array([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 0.0])


def orbit_shot(run, ship, t0, t1, speed, fps, name="orbit", dist=4.0, azimuth_deg=(50.0, 110.0), elevation_deg=14.0, ref=None):
    """A slow orbit of a ship at constant playback speed. dist is in hull lengths. Azimuth is measured from `ref`
    (a direction; default the ship's nose): 0 = looking back along it, 90 = side on."""
    n = max(1, int(round((t1 - t0) / speed * fps)))
    return {"name": name, "origin": ("ship", ship), "times": [t0 + f * speed / fps for f in range(n)],
            "rig": {"type": "orbit", "dist": dist, "azimuth_deg": azimuth_deg, "elevation_deg": elevation_deg, "ref": ref}}


def chase_shot(run, missile, t_start, film_s, fps, end_range_m, name="chase", back=8.0, lift=1.0):
    """Ride behind a missile to its target. The range to the target shrinks by the same factor every frame, so the
    target grows at a steady rate: the cruise is compressed and the last moments run in extreme slow motion.
    back and lift place the camera, in missile lengths."""
    target = run.m_launch[missile][2]
    rng = lambda t: float(np.linalg.norm(run.missile_pos(missile, t) - run.state(target, t)["p"]))
    t_end = run.m_span[missile][1]
    r0, n, times = rng(t_start), max(2, int(round(film_s * fps))), []
    for f in range(n):
        want = r0 * (end_range_m / r0) ** (f / (n - 1))
        lo, hi = (times[-1] if times else t_start), t_end
        for _ in range(60):                               # range falls steadily: bisect for the time it equals `want`
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if rng(mid) > want else (lo, mid)
        times.append(lo)
    return {"name": name, "origin": ("missile", missile), "times": times,
            "rig": {"type": "chase", "target": target, "back": back, "lift": lift}}


def kill_sequence(run, target=None, fps=24, launch_s=2.5, chase_s=7.0, kill_s=2.0, kill_speed=3.0, ship_scale=40.0):
    """Launch, chase, kill: the missile that destroyed `target` (default: the last ship destroyed), from the ship that
    fired it, ridden to impact, then the target breaking up seen from outside."""
    kills = [e for e in run.events if e["type"] == "ship_destroyed" and (target is None or e["actor_id"] == target)]
    if not kills:
        raise ValueError(f"no ship_destroyed event for {target or 'any ship'} in this run")
    kill = kills[-1]
    target = kill["actor_id"]
    dets = [e for e in run.events if e["type"] == "detonation" and e["data"]["target_id"] == target and e["t"] <= kill["t"] + 1e-6
            and e["actor_id"] in run.m_launch]
    if not dets:
        raise ValueError(f"no recorded missile hit on {target} before it was destroyed")
    missile = dets[-1]["actor_id"]
    # several warheads can go off in the same instant: follow the first of that group (the one that started the kill)
    missile = next(e["actor_id"] for e in dets if abs(e["t"] - dets[-1]["t"]) < 1e-6)
    tl, launcher, _ = run.m_launch[missile]
    td = run.m_end[missile]
    first = float(run.mt[run.m_first[missile]])
    # launch: seen side-on to the direction the missile leaves in, in slow motion, until it is a few hull lengths out
    away = run.missile_pos(missile, first) - run.state(launcher, first)["p"]
    lead = 0.35 * (first - tl)
    shots = [orbit_shot(run, launcher, tl - lead, first + 0.1 * (first - tl), (first - tl) * 1.45 / launch_s, fps, name="launch",
                        dist=2.6, azimuth_deg=(108.0, 82.0), elevation_deg=10.0, ref=(away / np.linalg.norm(away)).tolist())]
    hull = _class_dims(run)[run.info[target]["class"]]["length"] * ship_scale
    shots.append(chase_shot(run, missile, first, chase_s, fps, end_range_m=0.75 * hull))
    shots.append(orbit_shot(run, target, td - 0.15 * kill_speed, td + (kill_s - 0.15) * kill_speed, kill_speed, fps, name="kill",
                            dist=2.4, azimuth_deg=(50.0, 75.0)))
    return {"target": target, "missile": missile, "launcher": launcher, "launch_t": tl, "impact_t": td, "shots": shots}


_DIMS = {}


def _class_dims(run, repo=None):
    if repo is not None:
        classes = yaml.safe_load((Path(repo) / "classes" / "ships.yaml").read_text())["classes"]
        for c in sorted({run.info[s]["class"] for s in run.ships}):
            d = classes[c].get("dimensions") or {}
            # classes without dimensions in the database get a generic hull size
            _DIMS[c] = {"length": float(d.get("length_m") or 500.0), "beam": float(d.get("beam_m") or 80.0), "height": float(d.get("height_m") or 80.0)}
    return _DIMS


def build(run_dir, out_dir, repo, target=None, t0=None, t1=None, speed=15.0, fps=24, cam_dist=4.0,
          cam_azimuth_deg=(50.0, 110.0), cam_elevation_deg=14.0, **kw):
    """A single orbit shot of one ship. speed = simulated seconds per second of film. See `build_sequence`."""
    run = Run(run_dir)
    target, t0, t1 = default_window(run, target, t0, t1)
    if target not in run.ships:
        raise ValueError(f"unknown ship '{target}' (ships: {', '.join(run.ships)})")
    spec = orbit_shot(run, target, t0, t1, speed, fps, dist=cam_dist, azimuth_deg=cam_azimuth_deg, elevation_deg=cam_elevation_deg)
    return build_sequence(run, out_dir, repo, [spec], fps=fps, target=target, **kw)


def build_sequence(run, out_dir, repo, shots, fps=24, target=None, ship_scale=40.0, missile_scale=150.0, missile_length_m=15.0,
                   unit_m=1000.0, resolution=(1280, 720), lens_mm=35.0, flash_s=0.4, kill_flash_s=0.6, debris_cool_s=5.0,
                   n_sparks=80, debris_speed=1.0, debris_spin=1.0, engine="eevee", hero=None):
    """Write <out_dir>/shot.json for a list of shot specs (see orbit_shot, chase_shot) and return the shot dict.
    flash_s, kill_flash_s and debris_cool_s are in seconds of film. hero: a missile drawn without the cosmetic
    spread that separates the missiles of a salvo."""
    run = run if isinstance(run, Run) else Run(run)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dims = _class_dims(run, repo)
    meshes, scale = {c: hulls.hull_for(c, d, repo, out_dir) for c, d in dims.items()}, ship_scale / unit_m
    hull_len = lambda sid: meshes[run.info[sid]["class"]]["length_m"] * scale          # in scene units
    mlen = missile_length_m * missile_scale / unit_m                                   # missile length in scene units

    def opos(kind, oid, t):
        return run.state(oid, t)["p"] if kind == "ship" else run.missile_pos(oid, t)

    # per frame: time, the sim time the frame covers, origin, shot; and per shot, film time as a function of sim time
    F = []                                                 # frames: dict(t, dt, o (origin position), shot (index))
    for si, sp in enumerate(shots):
        ts = sp["times"]
        for f, t in enumerate(ts):
            dt = ts[f] - ts[f - 1] if f else (ts[1] - ts[0] if len(ts) > 1 else 1.0 / fps)
            F.append({"t": float(t), "dt": float(max(dt, 1e-9)), "o": opos(*sp["origin"], t), "shot": si, "k": f})
    n, times = len(F), [fr["t"] for fr in F]

    def film_age(si, t_frame, t_event):
        """Seconds of film between an event and a frame of shot si (before the shot began: at its opening speed)."""
        ts = shots[si]["times"]
        v0 = max((ts[1] - ts[0]) * fps, 1e-9) if len(ts) > 1 else 1.0
        film = lambda t: (t - ts[0]) / v0 if t < ts[0] else float(np.interp(t, ts, np.arange(len(ts)) / fps))
        return film(t_frame) - film(t_event)

    kills = {e["actor_id"]: e["t"] for e in run.events if e["type"] == "ship_destroyed"}
    ships = []
    for s in run.ships:
        st = [run.state(s, fr["t"]) for fr in F]
        tk = kills.get(s)
        ships.append({"id": s, "class": run.info[s]["class"], "faction": run.info[s]["faction"], "kill_t": tk,
                      "loc": [((a["p"] - fr["o"]) / unit_m).tolist() for a, fr in zip(st, F)],
                      "quat": [_wxyz(a["q"]) for a in st],
                      "dead": [a["dead"] for a in st], "gone": [a["gone"] for a in st],
                      # since the kill: simulated seconds (drives the drift of the pieces) and film seconds (drives the glow)
                      "kill_age": None if tk is None else [fr["t"] - tk for fr in F],
                      "kill_film": None if tk is None else [film_age(fr["shot"], fr["t"], tk) for fr in F]})

    bodies, sun_dir = [], None
    for b in run.bodies:
        radius = run.info[b]["radius_m"] / unit_m
        loc, rad = [], []
        for fr in F:
            rel = (run.state(b, fr["t"])["p"] - fr["o"]) / unit_m
            d = float(np.linalg.norm(rel))
            k = FAR / d if d > FAR else 1.0
            loc.append((rel * k).tolist()); rad.append(radius * k)
        if run.info[b].get("type") == "star":
            sun_dir = [(-np.array(p) / np.linalg.norm(p)).tolist() for p in loc]     # direction the sunlight travels
        if max(r / max(np.linalg.norm(p), 1e-9) for r, p in zip(rad, loc)) > 2e-4:    # skip bodies too small to see
            bodies.append({"id": b, "type": run.info[b].get("type"), "loc": loc, "radius": rad})

    # missiles. One that barely moves across the scene during a frame is drawn as a model; a fast one as the streak it
    # covers during the frame. The missiles of a salvo share one position in the simulation, so the models are spread
    # into a loose ring (cosmetic) around the true position; the hero missile stays on it.
    def spread(mid, along):
        if mid == hero:
            return np.zeros(3)
        h = sum(ord(c) * (i + 1) for i, c in enumerate(mid))
        side = np.cross(along, [0.0, 0.0, 1.0])
        side = side / max(np.linalg.norm(side), 1e-9)
        up = np.cross(side, along)
        a = 2 * math.pi * (h % 97) / 97.0
        return (math.cos(a) * side + math.sin(a) * up) * mlen * (1.2 + 0.5 * (h % 5))

    streaks, models = [], []
    for fr in F:
        seg, mod = [], []
        kind, oid = shots[fr["shot"]]["origin"]
        for mid in run.missiles_alive(fr["t"]):
            b = (run.missile_pos(mid, fr["t"]) - fr["o"]) / unit_m
            pa = run.missile_pos(mid, fr["t"] - fr["dt"])
            oa = opos(kind, oid, fr["t"] - fr["dt"])
            a = b if pa is None or oa is None else (pa - oa) / unit_m
            ab = b - a
            tgt = run.m_launch.get(mid, (None, None, None))[2]
            if np.linalg.norm(ab) < 12.0 * mlen:
                if np.linalg.norm(b) > 4000.0 * mlen:
                    continue
                aim = (run.state(tgt, fr["t"])["p"] - fr["o"]) / unit_m - b if tgt in run.index else ab
                aim = aim / max(np.linalg.norm(aim), 1e-9)
                mod.append({"id": mid, "loc": (b + spread(mid, aim)).tolist(), "dir": aim.tolist(), "faction": run.m_faction.get(mid)})
                continue
            s = float(np.clip(-(a @ ab) / max(ab @ ab, 1e-12), 0.0, 1.0))       # nearest point of the streak to the origin
            if np.linalg.norm(a + s * ab) > 2.0e4:
                continue
            seg.append({"a": a.tolist(), "b": b.tolist(), "faction": run.m_faction.get(mid)})
        streaks.append(seg); models.append(mod)

    # detonations: a flash that stays with the ship it went off beside
    flashes, seen = [], {}
    for e in run.events:
        if e["type"] != "detonation" or e["data"]["target_id"] not in run.index:
            continue
        tid = e["data"]["target_id"]
        hl, frames = hull_len(tid), {}
        for f, fr in enumerate(F):
            age = film_age(fr["shot"], fr["t"], e["t"]) / flash_s
            if 0.0 <= age <= 1.0 and fr["t"] >= e["t"]:
                if not frames:
                    key = (tid, round(e["t"], 1))                 # warheads of one salvo going off together read as one flash
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > 3:
                        break
                    off = (np.array(e["data"]["position_m"]) - run.state(tid, e["t"])["p"]) * ship_scale / unit_m
                    if np.linalg.norm(off) < 1e-9:
                        off = np.array([0.0, 0.0, 1.0])
                    off = off / np.linalg.norm(off) * max(np.linalg.norm(off), 0.35 * hl)      # clear of the enlarged hull
                p = (run.state(tid, fr["t"])["p"] - fr["o"]) / unit_m + off
                frames[str(f)] = {"loc": p.tolist(), "radius": hl * (0.08 + 0.30 * age ** 0.5), "power": (1.0 - age) ** 2}
        if frames:
            flashes.append({"t": e["t"], "target": tid, "frames": frames})

    # the kill itself: a fireball that swells in an instant, then collapses and fades fast, so the wreck is revealed;
    # and a shower of glowing fragments thrown out from the hull, cooling as they go
    sparks = []
    for sid, tk in kills.items():
        hl, frames = hull_len(sid), {}
        for f, fr in enumerate(F):
            age = film_age(fr["shot"], fr["t"], tk) / kill_flash_s
            if 0.0 <= age <= 1.0 and fr["t"] >= tk:
                p = (run.state(sid, fr["t"])["p"] - fr["o"]) / unit_m
                swell = min(1.0, age / 0.12)
                frames[str(f)] = {"loc": p.tolist(), "radius": hl * (0.12 + 0.5 * swell) * (1.0 - 0.6 * age),
                                  "power": float(np.exp(-5.0 * age) * (1.0 - age))}
        if frames:
            flashes.append({"t": tk, "target": sid, "kill": True, "frames": frames})
        ages = [film_age(fr["shot"], fr["t"], tk) for fr in F if fr["t"] >= tk]
        if not ages or min(ages) > debris_cool_s:
            continue
        rng = np.random.default_rng(int(tk * 1000) % (2 ** 32))
        for _ in range(n_sparks):
            d = rng.normal(size=3)
            d /= np.linalg.norm(d)
            sparks.append({"ship": sid, "start": (rng.normal(size=3) * 0.12 * hl).tolist(), "vel": (d * rng.uniform(0.05, 0.6) * hl).tolist(),
                           "size": float(hl * rng.uniform(0.003, 0.009)), "cool_s": float(debris_cool_s * rng.uniform(0.35, 1.0))})

    # cameras
    cams = []
    for fr in F:
        sp = shots[fr["shot"]]
        rig, k, m = sp["rig"], fr["k"], len(sp["times"])
        if rig["type"] == "orbit":
            ref = np.array(rig["ref"]) if rig.get("ref") is not None else _nose(run.state(sp["origin"][1], sp["times"][0])["q"])
            az0, az1 = rig["azimuth_deg"]
            az = math.atan2(ref[1], ref[0]) + math.radians(az0 + (az1 - az0) * k / max(m - 1, 1))
            el, d = math.radians(rig["elevation_deg"]), rig["dist"] * hull_len(sp["origin"][1])
            cams.append({"loc": [d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az), d * math.sin(el)], "look": [0.0, 0.0, 0.0]})
        else:                                             # chase: behind and a little above the missile, looking at its target
            to = (run.state(rig["target"], fr["t"])["p"] - fr["o"]) / unit_m
            ahead = to / max(np.linalg.norm(to), 1e-9)
            loc = -ahead * rig["back"] * mlen + np.array([0.0, 0.0, rig["lift"] * mlen])
            cams.append({"loc": loc.tolist(), "look": to.tolist()})        # the target stays centred; the missile sits below it

    v, f_, uv = hulls.lathe([[0.0, 0.55], [0.08, 0.7], [0.12, 1.0], [0.8, 1.0], [0.93, 0.6], [1.0, 0.0]], missile_length_m, missile_length_m * 0.14,
                            missile_length_m * 0.14, segments=12)
    shot = {"scenario": run.meta.get("scenario_id"), "target": target, "fps": fps, "frames": n, "times": times,
            "shots": [{"name": sp["name"], "origin": list(sp["origin"]), "frames": len(sp["times"]), "t0": sp["times"][0], "t1": sp["times"][-1]} for sp in shots],
            "shot_of": [fr["shot"] for fr in F], "unit_m": unit_m, "ship_scale": ship_scale, "mesh_scale": scale,
            "missile_mesh": {"verts": v, "faces": f_, "scale": missile_scale / unit_m, "length": mlen},
            "resolution": list(resolution), "lens_mm": lens_mm, "engine": engine, "far": FAR,
            "faction_rgb": FACTION_RGB, "debris_speed": debris_speed, "debris_spin": debris_spin, "debris_cool_s": debris_cool_s,
            "sparks": sparks, "meshes": meshes, "ships": ships, "bodies": bodies, "sun_dir": sun_dir,
            "streaks": streaks, "models": models, "flashes": flashes, "camera": cams, "out_dir": str(out_dir.resolve())}
    (out_dir / "shot.json").write_text(json.dumps(shot))
    return shot
