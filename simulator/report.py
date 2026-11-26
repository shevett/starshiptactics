"""Post-action analysis report.

Reads a finished run (metadata.json, events.jsonl, state.jsonl, optional missiles.jsonl) and writes one
self-contained HTML file (inline data, inline SVG charts, no network, no extra Python packages).
The report is derived purely from simulator output; it never re-runs the simulation.
"""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

G0 = 9.80665
DEFAULT_ASSUMED_DEAD = 0.05


def _jsonl(path):
    if not path.exists():
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _r(arr, n=4):
    """Round to n significant-ish decimals for compact JSON."""
    return [None if v is None else round(float(v), n) for v in np.asarray(arr).ravel()]


def _sig(arr, n=5):
    out = []
    for v in np.asarray(arr, dtype=float).ravel():
        out.append(float(f"{v:.{n}g}") if np.isfinite(v) else None)
    return out


def _assumed_dead(root):
    try:
        import yaml
        d = yaml.safe_load(open(root / "rules" / "doctrine.yaml"))
        return float(d["doctrine"]["engagement"]["assumed_dead_integrity"])
    except Exception:
        return DEFAULT_ASSUMED_DEAD


def build(run_dir):
    run = Path(run_dir)
    if not (run / "metadata.json").exists():
        raise SystemExit(f"error: {run} is not a run directory (no metadata.json). "
                         f"Run the scenario first, e.g. python3 -m simulator run scenarios/incursion01.yaml")
    meta =json.loads((run / "metadata.json").read_text())
    events = _jsonl(run / "events.jsonl")
    states = _jsonl(run / "state.jsonl")
    mrows = _jsonl(run / "missiles.jsonl")
    if not states:
        raise SystemExit(f"{run}/state.jsonl is empty - nothing to report on")
    root = Path(__file__).resolve().parent.parent
    dead_thr = _assumed_dead(root)

    ships = [o for o in meta["objects"] if o["kind"] == "ship"]
    ids = [s["id"] for s in ships]
    fac = {s["id"]: s["faction"] for s in ships}
    factions = meta.get("factions") or sorted(set(fac.values()))
    bodies = {o["id"]: o for o in meta["objects"] if o["kind"] == "body"}
    orbits = meta.get("orbits") or []
    ref = orbits[0]["body"] if orbits else None

    n = len(states)
    T = np.array([s["t"] for s in states])
    idx = {sid: i for i, sid in enumerate(ids)}
    P = np.zeros((len(ids), n, 3)); V = np.zeros_like(P); A = np.zeros_like(P)
    I = np.ones((len(ids), n))
    DEP = np.zeros((len(ids), n), bool)             # has left the field through the hyper limit
    RP = np.zeros((n, 3)); RV = np.zeros((n, 3))
    for k, row in enumerate(states):
        for o in row["objects"]:
            if o["id"] in idx:
                i = idx[o["id"]]
                P[i, k] = o["position_m"]; V[i, k] = o["velocity_m_s"]
                A[i, k] = o.get("acceleration_m_s2", (0, 0, 0))
                I[i, k] = o.get("integrity", 1.0)
                DEP[i, k] = bool(o.get("departed", False))
            elif o["id"] == ref:
                RP[k] = o["position_m"]; RV[k] = o["velocity_m_s"]
    rel = P - RP[None]
    speed = np.linalg.norm(V - RV[None], axis=2) / 1000.0          # km/s relative to ref (or heliocentric)
    accel_g = np.linalg.norm(A, axis=2) / G0
    speed[DEP] = np.nan; accel_g[DEP] = np.nan                     # a departed ship's lines simply end
    dep_t = {sid: None for sid in ids}
    for e in events:
        if e["type"] == "hyper_departure":
            dep_t[e["actor_id"]] = float(e["t"])                    # exact event time (snapshots are coarser)

    # cross-faction range (min and mean over all hostile pairs)
    pairs = [(i, j) for i in range(len(ids)) for j in range(i + 1, len(ids)) if fac[ids[i]] != fac[ids[j]]]
    if pairs:
        D = np.stack([np.linalg.norm(P[i] - P[j], axis=1) for i, j in pairs])
        D[np.stack([DEP[i] | DEP[j] for i, j in pairs])] = np.nan  # pairs involving a departed ship drop out
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                rng_min, rng_mean = np.nanmin(D, axis=0) / 1000.0, np.nanmean(D, axis=0) / 1000.0
    else:
        rng_min = rng_mean = np.zeros(n)

    def ref_at(t):
        return np.array([np.interp(t, T, RP[:, c]) for c in range(3)])

    def ship_at(sid, t):
        i = idx[sid]
        return np.array([np.interp(t, T, P[i, :, c]) for c in range(3)])

    # ---- events ---------------------------------------------------------------------------------
    launches = [e for e in events if e["type"] == "missile_launch"]
    dets = [e for e in events if e["type"] == "detonation"]
    expiries = [e for e in events if e["type"] == "missile_expired"]
    done = [e for e in events if e["type"] == "objective_complete"]
    mfac = {}
    for e in launches:
        mfac[e["data"]["missile_id"]] = fac[e["actor_id"]]
    for e in dets:
        mfac.setdefault(e["actor_id"], fac.get(e["data"]["launcher_id"]))

    t0, t1 = float(T[0]), float(T[-1])

    def step_series(sign_events):
        """cumulative/in-flight step series per faction from (t, delta) lists"""
        out = {}
        for f in factions:
            pts = sorted(sign_events.get(f, []))
            x, y, c = [t0], [0], 0
            for t, d in pts:
                c += d
                x.append(t); y.append(c)
            x.append(t1); y.append(c)
            out[f] = {"x": _r(x, 3), "y": y}
        return out

    L = defaultdict(list); D_ = defaultdict(list); F = defaultdict(list)
    for e in launches:
        f = fac[e["actor_id"]]
        L[f].append((e["t"], 1)); F[f].append((e["t"], 1))
    for e in dets:
        f = fac.get(e["data"]["launcher_id"])
        D_[f].append((e["t"], 1)); F[f].append((e["t"], -1))
    for e in expiries:
        f = mfac.get(e["actor_id"])
        if f:
            F[f].append((e["t"], -1))
    cum_launch, cum_det, in_flight = step_series(L), step_series(D_), step_series(F)

    # kills: first time integrity <= threshold
    kills, kill_i = {}, {}
    for sid in ids:
        below = np.nonzero(I[idx[sid]] <= dead_thr)[0]
        kills[sid] = float(T[below[0]]) if len(below) else None
        kill_i[sid] = int(below[0]) if len(below) else None

    # detonations table (positions relative to ref)
    det_rows = []
    for e in dets:
        d = e["data"]
        pos = np.array(d["position_m"]) - (ref_at(e["t"]) if ref else 0)
        det_rows.append({
            "t": round(e["t"], 3), "missile": e["actor_id"], "launcher": d["launcher_id"], "target": d["target_id"],
            "range_m": d["range_m"], "damage": d["damage"], "before": d["integrity_before"],
            "after": d["integrity_after"], "x_km": round(pos[0] / 1000, 3), "y_km": round(pos[1] / 1000, 3),
            "faction": fac.get(d["launcher_id"]),
        })
    launch_rows = []
    for e in launches:
        d = e["data"]
        p = ship_at(e["actor_id"], e["t"]) - (ref_at(e["t"]) if ref else 0)
        launch_rows.append({"t": round(e["t"], 3), "missile": d["missile_id"], "launcher": e["actor_id"],
                            "target": d["target_id"], "range_m": d.get("track_range_m"),
                            "x_km": round(p[0] / 1000, 3), "y_km": round(p[1] / 1000, 3),
                            "faction": fac[e["actor_id"]]})

    # missile tracks (relative to ref)
    tracks = defaultdict(lambda: {"faction": None, "pts": []})
    for row in mrows:
        r0 = ref_at(row["t"]) if ref else np.zeros(3)
        for m in row["missiles"]:
            tr = tracks[m["id"]]
            tr["faction"] = m.get("faction")
            p = (np.array(m["position_m"]) - r0) / 1000.0
            tr["pts"].append([round(row["t"], 2), round(p[0], 2), round(p[1], 2)])
    # collapse identical tracks (salvo members fly the same path) to keep the page light
    seen, missile_tracks = {}, []
    for mid, tr in tracks.items():
        key = (tr["faction"], tuple(map(tuple, tr["pts"][::4])))
        if key in seen:
            missile_tracks[seen[key]]["n"] += 1
            continue
        seen[key] = len(missile_tracks)
        missile_tracks.append({"id": mid, "faction": tr["faction"], "pts": tr["pts"], "n": 1})

    # hit matrix launcher -> target
    cell = defaultdict(lambda: {"n": 0, "dmg": 0.0})
    for d in det_rows:
        c = cell[(d["launcher"], d["target"])]
        c["n"] += 1; c["dmg"] += d["damage"]
    matrix = [{"launcher": a, "target": b, "n": v["n"], "mean_damage": v["dmg"] / v["n"]}
              for (a, b), v in sorted(cell.items())]
    fired = sorted({r["launcher"] for r in launch_rows})
    shot = sorted({r["target"] for r in launch_rows})
    launched_by_ship = defaultdict(lambda: defaultdict(int))
    for r in launch_rows:
        launched_by_ship[r["launcher"]][r["target"]] += 1
    matrix = [{"launcher": a, "target": b, "launched": launched_by_ship[a][b], "n": cell[(a, b)]["n"],
               "mean_damage": (cell[(a, b)]["dmg"] / cell[(a, b)]["n"]) if cell[(a, b)]["n"] else 0.0}
              for a in fired for b in shot if launched_by_ship[a][b] or cell[(a, b)]["n"]]

    # key moments
    moments = []
    if launches:
        moments.append({"t": min(e["t"] for e in launches), "label": "First missile launch", "kind": "launch"})
    if dets:
        moments.append({"t": min(e["t"] for e in dets), "label": "First detonation", "kind": "det"})
    for f in factions:
        ks = sorted((t, s) for s, t in kills.items() if t is not None and fac[s] == f)
        if ks:
            moments.append({"t": ks[0][0], "label": f"First {f} ship lost ({ks[0][1]})", "kind": "kill"})
            if len(ks) == sum(1 for s in ships if s["faction"] == f):
                moments.append({"t": ks[-1][0], "label": f"{f} faction destroyed", "kind": "kill"})
    if dets:
        moments.append({"t": max(e["t"] for e in dets), "label": "Last detonation", "kind": "det"})
    seen_contact = set()
    for e in events:
        if e["type"] == "contact_gained" and e["data"]["faction"] not in seen_contact:
            seen_contact.add(e["data"]["faction"])
            moments.append({"t": e["t"], "label": f"{e['data']['faction']} gains first contact "
                            f"({e['data']['range_m'] / 1e9:.1f} Mkm)", "kind": "contact"})
        elif e["type"] == "hyper_arrival":
            if (e["t"], e["data"].get("group")) not in seen_contact:                 # one line for a group arriving together
                seen_contact.add((e["t"], e["data"].get("group")))
                moments.append({"t": e["t"], "label": f"{e['data'].get('group') or e['actor_id']} arrives from hyperspace", "kind": "depart"})
        elif e["type"] == "hyper_departure":
            moments.append({"t": e["t"], "label": f"{e['actor_id']} departs via the hyper limit", "kind": "depart"})
        elif e["type"] == "mission_changed":
            d = e["data"]
            if d["behavior"] in ("engage", "withdraw") and d["previous"] != d["behavior"]:
                on = f" on {d['target_id']}" if d.get("target_id") else ""
                moments.append({"t": e["t"], "label": f"{e['actor_id']} switches to {d['behavior']}{on} ({d['reason']})",
                                "kind": "mission"})
            elif d["behavior"] == "objective":
                moments.append({"t": e["t"], "label": f"{e['actor_id']} resumes its objective ({d['reason']})", "kind": "mission"})
    for e in done:
        moments.append({"t": e["t"], "label": f"{e['actor_id']} completed {e['data']['objective']}", "kind": "obj"})
    moments.sort(key=lambda m: m["t"])

    ev_times = [e["t"] for e in launches + dets]
    window = [max(t0, min(ev_times) - 60), min(t1, max(ev_times) + 60)] if ev_times else [t0, t1]

    ref_body = bodies.get(ref) if ref else None
    return {
        "scenario": meta.get("scenario_id"), "seed": meta.get("random_seed"), "version": meta.get("simulator_version"),
        "duration": t1 - t0, "t0": t0, "t1": t1, "window": window, "ref": ref,
        "ref_radius_km": ref_body["radius_m"] / 1000 if ref_body else None,
        "ref_exclusion_km": ref_body["exclusion_radius_m"] / 1000 if ref_body else None,
        "orbits": [{"group": o["group"], "radius_km": o["radius_m"] / 1000} for o in orbits],
        "factions": factions, "dead_threshold": dead_thr,
        "ships": [{"id": s["id"], "faction": s["faction"], "group": s["group"], "class": s["class"],
                   "integrity": float(I[idx[s["id"]], -1]), "kill_t": kills[s["id"]], "kill_i": kill_i[s["id"]], "departed_t": dep_t[s["id"]],
                   "launched": sum(1 for r in launch_rows if r["launcher"] == s["id"]),
                   "hit_by": sum(1 for d in det_rows if d["target"] == s["id"])} for s in ships],
        "warnings": meta.get("warnings", []),
        "missiles": meta.get("missiles", {}),
        "t": _r(T, 2),
        "series": {
            "speed": {s: _sig(speed[idx[s]], 5) for s in ids},
            "accel": {s: _sig(accel_g[idx[s]], 4) for s in ids},
            "integrity": {s: _sig(I[idx[s]], 6) for s in ids},
            "range_min": _sig(rng_min, 6), "range_mean": _sig(rng_mean, 6),
            "traj": {s: [[round(a / 1000, 1), round(b / 1000, 1)] for a, b in rel[idx[s], :, :2]] for s in ids},
        },
        "cum_launch": cum_launch, "cum_det": cum_det, "in_flight": in_flight,
        "detonations": det_rows, "launches": launch_rows, "missile_tracks": missile_tracks,
        "matrix": matrix, "moments": moments,
    }


def write(run_dir, out=None):
    data = build(run_dir)
    tpl = (Path(__file__).parent / "report_template.html").read_text()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", blob).replace("__TITLE__", f"{data['scenario']} - after-action report")
    out = Path(out) if out else Path(run_dir) / "report.html"
    out.write_text(html)
    return out
