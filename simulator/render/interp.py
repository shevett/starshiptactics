"""Read a run directory and evaluate ship, body and missile states at any time (between the stored snapshots)."""
import json
from pathlib import Path

import numpy as np


def hermite(s, h, p0, v0, p1, v1):
    """Cubic Hermite position between two samples h seconds apart; s in [0, 1]. Uses the stored velocities, so a ship
    under hard acceleration does not cut corners the way linear interpolation would."""
    s2, s3 = s * s, s * s * s
    return ((2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * h * v0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * h * v1)


def slerp(q0, q1, s):
    """Spherical interpolation between unit quaternions (any component order, as long as both use the same)."""
    q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
    d = float(q0 @ q1)
    if d < 0.0:                       # take the short way round
        q1, d = -q1, -d
    if d > 0.9995:                    # nearly parallel: lerp and renormalise
        q = q0 + s * (q1 - q0)
    else:
        th = np.arccos(d)
        q = (np.sin((1 - s) * th) * q0 + np.sin(s * th) * q1) / np.sin(th)
    return q / np.linalg.norm(q)


def _jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class Run:
    def __init__(self, run_dir):
        d = Path(run_dir)
        self.dir = d
        self.meta = json.loads((d / "metadata.json").read_text())
        rows = _jsonl(d / "state.jsonl")
        self.t = np.array([r["t"] for r in rows])
        self.ids = [o["id"] for o in rows[0]["objects"]]
        self.info = {o["id"]: o for o in self.meta["objects"]}
        n, m = len(rows), len(self.ids)
        self.pos, self.vel = np.zeros((m, n, 3)), np.zeros((m, n, 3))
        self.quat = np.tile([0.0, 0.0, 0.0, 1.0], (m, n, 1))                 # xyzw, as written by the simulator
        self.dead, self.gone = np.zeros((m, n), bool), np.zeros((m, n), bool)
        for k, r in enumerate(rows):
            for i, o in enumerate(r["objects"]):
                self.pos[i, k], self.vel[i, k] = o["position_m"], o["velocity_m_s"]
                if "orientation_xyzw" in o:
                    self.quat[i, k] = o["orientation_xyzw"]
                self.dead[i, k], self.gone[i, k] = bool(o.get("destroyed")), bool(o.get("departed"))
        self.index = {s: i for i, s in enumerate(self.ids)}
        self.ships = [s for s in self.ids if self.info.get(s, {}).get("kind") == "ship"]
        self.bodies = [s for s in self.ids if self.info.get(s, {}).get("kind") == "body"]
        self.events = _jsonl(d / "events.jsonl")
        mp = d / "missiles.jsonl"
        mrows = _jsonl(mp) if mp.exists() else []
        self.mt = np.array([r["t"] for r in mrows])
        self.mrows = [{x["id"]: x for x in r["missiles"]} for r in mrows]
        self.m_interval = float(self.meta.get("missile_output_interval_s") or self.meta.get("time_step_s") or 1.0)
        # a missile stops existing at its detonation; without that it would be drawn up to the next stored record
        self.m_end = {e["actor_id"]: e["t"] for e in self.events if e["type"] in ("detonation", "missile_expired")}

    def _bracket(self, t):
        k = int(np.clip(np.searchsorted(self.t, t, side="right") - 1, 0, len(self.t) - 1))
        k2 = min(k + 1, len(self.t) - 1)
        h = self.t[k2] - self.t[k]
        return k, k2, h, ((t - self.t[k]) / h if h > 0 else 0.0)

    def state(self, obj_id, t):
        """dict(p, v, q (xyzw), dead, gone) for a ship or body at time t."""
        i = self.index[obj_id]
        k, k2, h, s = self._bracket(t)
        s = float(np.clip(s, 0.0, 1.0))
        p = hermite(s, h, self.pos[i, k], self.vel[i, k], self.pos[i, k2], self.vel[i, k2])
        v = self.vel[i, k] * (1 - s) + self.vel[i, k2] * s
        return {"p": p, "v": v, "q": slerp(self.quat[i, k], self.quat[i, k2], s),
                "dead": bool(self.dead[i, k]), "gone": bool(self.gone[i, k])}

    def missiles_alive(self, t):
        """ids of missiles in flight at time t (from the last stored record at or before t)."""
        if not len(self.mt) or t < self.mt[0]:
            return []
        k = int(np.searchsorted(self.mt, t, side="right") - 1)
        if t - self.mt[k] > self.m_interval + 1e-9:
            return []
        return [i for i in self.mrows[k] if self.m_end.get(i, np.inf) > self.mt[k]]

    def missile_pos(self, mid, t):
        """Position of a missile at t, or None if it has no record near t. Clamped to its end (detonation) time."""
        t = min(t, self.m_end.get(mid, np.inf))
        k = int(np.clip(np.searchsorted(self.mt, t, side="right") - 1, 0, len(self.mt) - 1))
        a = self.mrows[k].get(mid)
        if a is None:
            return None
        p0, v0, tau = np.array(a["position_m"]), np.array(a["velocity_m_s"]), t - self.mt[k]
        b = self.mrows[k + 1].get(mid) if k + 1 < len(self.mrows) else None
        if b is None or tau < 0:
            return p0 + v0 * tau
        h = self.mt[k + 1] - self.mt[k]
        return hermite(tau / h, h, p0, v0, np.array(b["position_m"]), np.array(b["velocity_m_s"]))
