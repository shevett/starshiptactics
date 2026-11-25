"""Turn a finished run into a shot: everything Blender needs for each output frame, as plain JSON.

Scale. Real space is unfilmable: ships hundreds of metres long sit hundreds of kilometres apart, and a missile crosses
that in a fraction of a frame. So the shot uses a "cinematic scale":
  - positions stay true, in scene units of `unit_m` metres, relative to the target ship (a floating origin: Blender
    stores single-precision coordinates, which cannot hold solar-system distances);
  - ship hulls are enlarged by `ship_scale`, so a formation reads as a formation;
  - a detonation's stand-off from its target is enlarged by the same factor, so it does not end up inside the hull;
  - a missile is drawn as the streak it covers during one frame (honest motion blur);
  - bodies beyond `far` scene units are drawn on a shell at `far` with the same apparent size.
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


def build(run_dir, out_dir, repo, target=None, t0=None, t1=None, speed=15.0, fps=24, ship_scale=40.0, unit_m=1000.0,
          resolution=(1280, 720), cam_dist=4.0, debris_speed=1.0, debris_spin=1.0, cam_azimuth_deg=(50.0, 110.0), cam_elevation_deg=14.0, lens_mm=35.0,
          flash_s=0.7, engine="eevee"):
    """Write <out_dir>/shot.json and return the shot dict. speed = simulated seconds per second of film."""
    run, out_dir = Run(run_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target, t0, t1 = default_window(run, target, t0, t1)
    if target not in run.ships:
        raise ValueError(f"unknown ship '{target}' (ships: {', '.join(run.ships)})")
    n = max(1, int(round((t1 - t0) / speed * fps)))
    dt = speed / fps                                                    # simulated seconds per frame
    times = [t0 + f * dt for f in range(n)]
    origin = [run.state(target, t) for t in times]                      # the target ship: scene origin, every frame

    classes = yaml.safe_load((Path(repo) / "classes" / "ships.yaml").read_text())["classes"]
    used = sorted({run.info[s]["class"] for s in run.ships})
    meshes, scale = {}, ship_scale / unit_m
    for c in used:
        d = classes[c].get("dimensions") or {}
        # classes without dimensions in the database get a generic hull size
        dims = {"length": float(d.get("length_m") or 500.0), "beam": float(d.get("beam_m") or 80.0), "height": float(d.get("height_m") or 80.0)}
        meshes[c] = hulls.hull_for(c, dims, repo, out_dir)
    tlen = meshes[run.info[target]["class"]]["length_m"] * scale       # the target's hull length in scene units

    kills = {e["actor_id"]: e["t"] for e in run.events if e["type"] == "ship_destroyed"}
    ships = []
    for s in run.ships:
        st = [run.state(s, t) for t in times]
        ships.append({"id": s, "class": run.info[s]["class"], "faction": run.info[s]["faction"], "kill_t": kills.get(s),
                      "loc": [((a["p"] - o["p"]) / unit_m).tolist() for a, o in zip(st, origin)],
                      "quat": [_wxyz(a["q"]) for a in st],
                      "dead": [a["dead"] for a in st], "gone": [a["gone"] for a in st]})

    bodies, sun_dir = [], None
    for b in run.bodies:
        radius = run.info[b]["radius_m"] / unit_m
        loc, rad = [], []
        for t, o in zip(times, origin):
            rel = (run.state(b, t)["p"] - o["p"]) / unit_m
            d = float(np.linalg.norm(rel))
            k = FAR / d if d > FAR else 1.0
            loc.append((rel * k).tolist()); rad.append(radius * k)
        if run.info[b].get("type") == "star":
            sun_dir = [(-np.array(p) / np.linalg.norm(p)).tolist() for p in loc]     # direction the sunlight travels
        if max(r / max(np.linalg.norm(p), 1e-9) for r, p in zip(rad, loc)) > 2e-4:    # skip bodies too small to see
            bodies.append({"id": b, "type": run.info[b].get("type"), "loc": loc, "radius": rad})

    # missiles: per frame, the segment each one covers during the frame (relative to the moving origin)
    streaks = []
    for f, t in enumerate(times):
        seg = []
        for mid in run.missiles_alive(t):
            a, b = run.missile_pos(mid, t - dt), run.missile_pos(mid, t)
            if a is None or b is None:
                continue
            a = (a - run.state(target, t - dt)["p"]) / unit_m if t - dt >= run.t[0] else (a - origin[f]["p"]) / unit_m
            b = (b - origin[f]["p"]) / unit_m
            # nearest point of the segment to the origin: skip streaks that pass nowhere near the scene
            ab = b - a
            s = float(np.clip(-(a @ ab) / max(ab @ ab, 1e-12), 0.0, 1.0))
            if np.linalg.norm(a + s * ab) > 400.0 * tlen:
                continue
            fac = run.mrows[int(np.searchsorted(run.mt, t, side="right") - 1)][mid]["faction"]
            seg.append({"a": a.tolist(), "b": b.tolist(), "faction": fac})
        streaks.append(seg)

    # detonations: a flash that stays with the ship it went off beside
    life = flash_s * speed
    flashes, seen = [], {}
    for e in run.events:
        if e["type"] != "detonation" or not (t0 - life <= e["t"] <= t1):
            continue
        tid = e["data"]["target_id"]
        if tid not in run.index:
            continue
        key = (tid, round(e["t"], 1))                 # warheads of one salvo going off together read as one flash
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 3:
            continue
        off = (np.array(e["data"]["position_m"]) - run.state(tid, e["t"])["p"]) * ship_scale / unit_m
        if np.linalg.norm(off) < 1e-9:
            off = np.array([0.0, 0.0, 1.0])
        # keep the flash clear of the enlarged hull
        hl = meshes[run.info[tid]["class"]]["length_m"] * scale
        off = off / np.linalg.norm(off) * max(np.linalg.norm(off), 0.35 * hl)
        frames = {}
        for f, t in enumerate(times):
            age = (t - e["t"]) / life
            if 0.0 <= age <= 1.0:
                p = (run.state(tid, t)["p"] - origin[f]["p"]) / unit_m + off
                frames[str(f)] = {"loc": p.tolist(), "radius": hl * (0.08 + 0.30 * age ** 0.5), "power": (1.0 - age) ** 2}
        if frames:
            flashes.append({"t": e["t"], "target": tid, "damage": e["data"].get("damage"), "frames": frames})

    # the kill itself: one large fireball at the ship, on top of the warheads' own flashes
    for sid, tk in kills.items():
        hl, frames, klife = meshes[run.info[sid]["class"]]["length_m"] * scale, {}, 1.6 * life
        for f, t in enumerate(times):
            age = (t - tk) / klife
            if 0.0 <= age <= 1.0:
                p = (run.state(sid, t)["p"] - origin[f]["p"]) / unit_m
                frames[str(f)] = {"loc": p.tolist(), "radius": hl * (0.25 + 0.9 * age ** 0.5), "power": (1.0 - age) ** 2}
        if frames:
            flashes.append({"t": tk, "target": sid, "kill": True, "frames": frames})

    # camera: orbits the target. Azimuth is measured from the target's nose (0 = dead ahead, 90 = broadside).
    x, y, z, w = origin[0]["q"]
    base = math.atan2(2 * (x * y + w * z), 1 - 2 * (y * y + z * z))
    cams = []
    for f in range(n):
        az = base + math.radians(cam_azimuth_deg[0] + (cam_azimuth_deg[1] - cam_azimuth_deg[0]) * f / max(n - 1, 1))
        el = math.radians(cam_elevation_deg)
        d = cam_dist * tlen
        cams.append({"loc": [d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az), d * math.sin(el)], "look": [0.0, 0.0, 0.0]})

    shot = {"scenario": run.meta.get("scenario_id"), "target": target, "t0": t0, "t1": t1, "speed": speed, "fps": fps,
            "frames": n, "times": times, "unit_m": unit_m, "ship_scale": ship_scale, "mesh_scale": scale,
            "resolution": list(resolution), "lens_mm": lens_mm, "engine": engine, "far": FAR,
            "faction_rgb": FACTION_RGB, "debris_speed": debris_speed, "debris_spin": debris_spin, "debris_cool_s": 2.5 * life, "meshes": meshes, "ships": ships, "bodies": bodies, "sun_dir": sun_dir,
            "streaks": streaks, "flashes": flashes, "camera": cams, "out_dir": str(out_dir.resolve())}
    (out_dir / "shot.json").write_text(json.dumps(shot))
    return shot
