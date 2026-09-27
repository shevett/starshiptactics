"""Keplerian celestial-body propagation (parent-relative two-body motion)."""
import math
import random

import numpy as np


def true_to_mean_anomaly(nu, e):
    E = 2.0 * math.atan2(math.sqrt(1 - e) * math.sin(nu / 2), math.sqrt(1 + e) * math.cos(nu / 2))
    return E - e * math.sin(E)


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


class Ephemeris:
    """Positions/velocities (m, m/s) of all bodies at any time, in body list order."""

    def __init__(self, bodies, seed, strategy="random", overrides=None, start_time_s=0.0):
        self.bodies = bodies
        self.index = {b.id: i for i, b in enumerate(bodies)}
        n = len(bodies)
        self.parent = np.array([self.index[b.parent] if b.parent else -1 for b in bodies])
        self.orbiting = self.parent >= 0
        self.a = np.array([b.a_m for b in bodies])
        self.e = np.array([b.e for b in bodies])
        with np.errstate(divide="ignore"):
            self.n = np.where(self.orbiting, 2 * math.pi / np.where(self.orbiting, [b.period_s or 1 for b in bodies], 1), 0.0)
        self.R = np.stack([_rot_z(b.node_rad) @ _rot_x(b.i_rad) @ _rot_z(b.peri_rad) for b in bodies])
        self.mu = np.array([b.mu for b in bodies])
        self.radius = np.array([b.radius_m for b in bodies])
        self.start = float(start_time_s)
        self.phases = {}
        rng = random.Random(seed)
        M0 = np.zeros(n)
        for i, b in sorted(enumerate(bodies), key=lambda p: p[1].id):   # sorted -> stable draw order
            if not self.orbiting[i]:
                continue
            draw = rng.random() * 2 * math.pi
            if b.id in (overrides or {}):
                M0[i] = true_to_mean_anomaly(overrides[b.id], b.e)
            elif strategy == "random":
                M0[i] = draw
            self.phases[b.id] = float(M0[i])
        self.M0 = M0
        self.depth = self._max_depth()

    def _max_depth(self):
        d = 0
        for i in range(len(self.bodies)):
            k, j = 0, i
            while self.parent[j] >= 0:
                j = self.parent[j]; k += 1
            d = max(d, k)
        return d

    def at(self, t):
        """Return (pos, vel) arrays of shape (B,3) at scenario time t (seconds after start)."""
        M = self.M0 + self.n * (self.start + t)
        e = self.e
        E = M.copy()
        for _ in range(50):
            dE = (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
            E -= dE
            if np.max(np.abs(dE)) < 1e-13:
                break
        cE, sE = np.cos(E), np.sin(E)
        rate = 1 - e * cE
        b = self.a * np.sqrt(1 - e * e)
        p = np.stack([self.a * (cE - e), b * sE, np.zeros_like(E)], axis=1)
        v = np.stack([-self.a * self.n * sE / rate, b * self.n * cE / rate, np.zeros_like(E)], axis=1)
        p = np.einsum("bij,bj->bi", self.R, p)
        v = np.einsum("bij,bj->bi", self.R, v)
        p = np.where(self.orbiting[:, None], p, 0.0)
        v = np.where(self.orbiting[:, None], v, 0.0)
        ap, av = p.copy(), v.copy()
        has_parent = self.orbiting[:, None]
        safe_parent = np.where(self.orbiting, self.parent, 0)
        for _ in range(self.depth):       # resolve parent chains level by level
            ap = p + np.where(has_parent, ap[safe_parent], 0.0)
            av = v + np.where(has_parent, av[safe_parent], 0.0)
        return ap, av
