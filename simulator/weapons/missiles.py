"""Missile flight and detonation (Milestone 2, plus the simple damage rule from the design).

Missiles are simulated as vectorised arrays. Each missile:
  * burns at its class acceleration for its powered duration, steering toward the predicted intercept of its
    target's *track* (light-delayed faction information, extrapolated at constant velocity);
  * then coasts ballistically until it detonates or expires.

Gravity on missiles is ignored: at thousands of g it is negligible over a 300 s flight.

A missile detonates at its point of closest approach to a hostile ship if that approach is inside the warhead's
damage radius. Damage is linear in range:  d = max_damage * (1 - range / damage_radius), and
integrity *= (1 - d). Because closing speeds are millions of m/s, closest approach is found exactly within each
step (missile and ship both follow constant-acceleration paths across a step), not by testing end positions.
"""
import numpy as np

from ..units import G0


class MissileSwarm:
    def __init__(self):
        self.n_launched = 0
        self.ids, self.faction, self.launcher, self.target, self.mclass = [], [], [], [], []
        self.P = np.zeros((0, 3)); self.V = np.zeros((0, 3))
        self.acc = np.zeros(0); self.born = np.zeros(0); self.burn_end = np.zeros(0)
        self.expire_at = np.zeros(0); self.radius = np.zeros(0); self.max_damage = np.zeros(0)
        self.burnt_out = np.zeros(0, bool)

    def __len__(self):
        return len(self.ids)

    def launch(self, t, launcher_id, faction, launcher_idx, target_idx, pos, vel, mname, spec, ballistic_expiry_s):
        self.n_launched += 1
        mid = f"msl-{self.n_launched:06d}"
        a = spec["acceleration_g"] * G0
        tb = spec["powered_duration_s"]
        self.ids.append(mid); self.faction.append(faction); self.launcher.append(launcher_id)
        self.target.append(target_idx); self.mclass.append(mname)
        self.P = np.vstack([self.P, pos]); self.V = np.vstack([self.V, vel])
        for name, val in (("acc", a), ("born", t), ("burn_end", t + tb), ("expire_at", t + tb + ballistic_expiry_s),
                          ("radius", spec["warhead"]["damage_radius_km"] * 1000.0),
                          ("max_damage", spec["warhead"]["max_damage"])):
            setattr(self, name, np.append(getattr(self, name), val))
        self.burnt_out = np.append(self.burnt_out, False)
        return mid

    def _drop(self, keep):
        keep = np.asarray(keep, bool)
        for name in ("ids", "faction", "launcher", "target", "mclass"):
            setattr(self, name, [x for x, k in zip(getattr(self, name), keep) if k])
        for name in ("P", "V", "acc", "born", "burn_end", "expire_at", "radius", "max_damage", "burnt_out"):
            setattr(self, name, getattr(self, name)[keep])

    # ------------------------------------------------------------------ guidance
    def thrust(self, t, target_state):
        """Thrust acceleration (M,3). target_state(faction, target_idx) -> (pos, vel) at time t, or None."""
        M = len(self)
        out = np.zeros((M, 3))
        if not M:
            return out
        tp = np.zeros((M, 3)); tv = np.zeros((M, 3)); ok = np.zeros(M, bool)
        for m in range(M):
            st = target_state(self.faction[m], self.target[m])
            if st is not None:
                tp[m], tv[m] = st; ok[m] = True
        powered = ok & (t < self.burn_end - 1e-9)
        r = tp - self.P
        w = tv - self.V
        a = self.acc
        rn = np.linalg.norm(r, axis=1)
        tau = np.sqrt(2.0 * np.maximum(rn, 1.0) / a)
        for _ in range(20):        # solve |r + w*tau| = 1/2 a tau^2 for the time to intercept at full thrust
            q = r + w * tau[:, None]
            qn = np.maximum(np.linalg.norm(q, axis=1), 1e-9)
            f = qn - 0.5 * a * tau * tau
            fp = np.einsum("mi,mi->m", q, w) / qn - a * tau
            step = np.where(np.abs(fp) > 1e-12, f / np.where(np.abs(fp) > 1e-12, fp, 1.0), 0.0)
            tau = np.clip(tau - step, 1e-6, 1e6)
        zem = r + w * tau[:, None]
        zn = np.linalg.norm(zem, axis=1)
        direction = np.where(zn[:, None] > 1e-9, zem / np.maximum(zn, 1e-9)[:, None], r / np.maximum(rn, 1e-9)[:, None])
        out[powered] = direction[powered] * a[powered, None]
        return out

    # ------------------------------------------------------------------ step
    def advance(self, t, dt, thrust, ship_P, ship_V, ship_A, ship_faction, alive_mask):
        """Move missiles one step and resolve detonations.

        ship_* describe ships at the START of the step (ship path across the step: P + V*tau + A*tau^2/2).
        Returns a list of detonation dicts. Missiles that detonate or expire are removed; burnout and
        expiry are reported in the returned lists too.
        """
        det, burnouts, expired = [], [], []
        M, S = len(self), len(ship_P)
        if M == 0:
            return det, burnouts, expired
        hostile = np.array([[f != sf for sf in ship_faction] for f in self.faction]) & alive_mask[None, :]
        best_r = np.full(M, np.inf); best_s = np.full(M, -1); best_tau = np.zeros(M)
        if S:
            K = 33
            taus = np.linspace(0.0, dt, K)

            def dist(tau):         # tau: (M,S) -> distance (M,S)
                tt = tau[..., None]
                pm = self.P[:, None, :] + self.V[:, None, :] * tt + 0.5 * thrust[:, None, :] * tt * tt
                ps = ship_P[None] + ship_V[None] * tt + 0.5 * ship_A[None] * tt * tt
                return np.linalg.norm(pm - ps, axis=2)

            samples = np.stack([dist(np.full((M, S), x)) for x in taus])                 # (K,M,S)
            k = np.argmin(samples, axis=0)
            step = taus[1] - taus[0]
            lo = np.clip(taus[k] - step, 0.0, dt); hi = np.clip(taus[k] + step, 0.0, dt)
            for _ in range(45):    # ternary search on the bracket around the sampled minimum
                m1 = lo + (hi - lo) / 3.0; m2 = hi - (hi - lo) / 3.0
                left = dist(m1) < dist(m2)
                hi = np.where(left, m2, hi); lo = np.where(left, lo, m1)
            tau_star = 0.5 * (lo + hi)
            d_star = np.minimum(dist(tau_star), np.min(samples, axis=0))
            d_star = np.where(hostile, d_star, np.inf)
            s_best = np.argmin(d_star, axis=1)
            best_r = d_star[np.arange(M), s_best]; best_s = s_best; best_tau = tau_star[np.arange(M), s_best]
        hit = best_r < self.radius
        for m in np.nonzero(hit)[0]:
            r = float(best_r[m]); d = float(self.max_damage[m] * (1.0 - r / self.radius[m]))
            tau = float(best_tau[m])
            pos = self.P[m] + self.V[m] * tau + 0.5 * thrust[m] * tau * tau
            det.append({"missile_id": self.ids[m], "launcher_id": self.launcher[m], "faction": self.faction[m],
                        "t": t + tau, "ship_index": int(best_s[m]), "range_m": r, "damage": d,
                        "position_m": pos, "missile_class": self.mclass[m]})
        # kinematics
        self.P = self.P + self.V * dt + 0.5 * thrust * dt * dt
        self.V = self.V + thrust * dt
        t_new = t + dt
        for m in range(M):
            if not hit[m] and not self.burnt_out[m] and t_new >= self.burn_end[m] - 1e-9:
                burnouts.append({"missile_id": self.ids[m], "launcher_id": self.launcher[m], "t": float(self.burn_end[m])})
                self.burnt_out[m] = True
        gone = hit | (t_new >= self.expire_at - 1e-9)
        for m in np.nonzero(gone & ~hit)[0]:
            expired.append({"missile_id": self.ids[m], "launcher_id": self.launcher[m], "t": t_new})
        self._drop(~gone)
        return det, burnouts, expired

    def snapshot(self):
        return [{"id": self.ids[m], "faction": self.faction[m], "target_index": self.target[m],
                 "position_m": [round(float(x), 1) for x in self.P[m]],
                 "velocity_m_s": [round(float(x), 3) for x in self.V[m]]} for m in range(len(self))]
