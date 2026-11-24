"""Escape assessment: can a group still reach the hyper limit without entering a hostile's missile envelope?

The group's path to the limit is a coast (while it turns onto the heading), then a constant-acceleration burn along that
heading. Hostiles are extrapolated at constant velocity from their tracks; each has an envelope radius (its missile
powered reach times a factor). A heading is *safe* if the path reaches the hyper limit and, up to that moment, never
comes within any hostile's envelope.

`assess` returns the safe heading that exits soonest, and the **escape margin**: how long the group could still wait
before no safe heading exists. A margin of 0 means no safe route exists right now.

Approximations (deliberate; see docs/MISSIONS.md): gravity and the bodies' exclusion zones are ignored on the planned
path (flight-time safety still avoids bodies), hostiles never change course or accelerate, and the group is treated as
a single point accelerating at its slowest ship's limit.
"""
import numpy as np


def candidate_headings(n_ring=32, elevation_deg=45.0):
    """Unit vectors: a ring in the ecliptic plane plus two rings tilted +/- out of it."""
    out = []
    for el in (0.0, elevation_deg, -elevation_deg):
        e = np.radians(el)
        for k in range(n_ring if el == 0.0 else n_ring // 2):
            a = 2 * np.pi * k / (n_ring if el == 0.0 else n_ring // 2)
            out.append([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    return np.array(out)


def _paths(p0, v0, star, limit, amax, rate, fwd, hostiles, delay, heads, step_s, max_s):
    """Evaluate every heading for a given start delay. Returns (safe(H,), exit_time(H,))."""
    t = np.arange(0.0, max_s + step_s, step_s)
    cosang = np.clip(heads @ fwd, -1.0, 1.0)
    t_start = delay + np.arccos(cosang) / max(rate, 1e-9)                      # coast until the nose is on heading
    dt = np.maximum(t[None, :] - t_start[:, None], 0.0)                         # (H, n)
    p = p0 + v0 * t[None, :, None] + 0.5 * amax * (dt ** 2)[..., None] * heads[:, None, :]
    r = np.linalg.norm(p - star, axis=2)
    exited = r >= limit
    has_exit = exited.any(axis=1)
    exit_idx = np.where(has_exit, exited.argmax(axis=1), len(t) - 1)
    safe = has_exit.copy()
    if len(hostiles):
        hp = np.array([h[0] for h in hostiles]); hv = np.array([h[1] for h in hostiles]); hr = np.array([h[2] for h in hostiles])
        hpath = hp[None, :, :] + hv[None, :, :] * t[:, None, None]               # (n, K, 3)
        d = np.linalg.norm(p[:, :, None, :] - hpath[None, :, :, :], axis=3)      # (H, n, K)
        inside = (d < hr[None, None, :]).any(axis=2)                             # (H, n)
        upto = np.arange(len(t))[None, :] <= exit_idx[:, None]
        safe &= ~(inside & upto).any(axis=1)
    return safe, np.where(has_exit, t[exit_idx], np.inf)


def assess(p0, v0, star, limit, amax, rate, fwd, hostiles, heads, step_s=60.0, max_s=21600.0, cap_s=3600.0):
    """hostiles: list of (position, velocity, envelope_radius). Returns dict(exists, margin_s, heading, exit_s)."""
    hostiles = [h for h in hostiles if h[2] > 0.0]
    kw = dict(p0=p0, v0=v0, star=star, limit=limit, amax=amax, rate=rate, fwd=fwd, hostiles=hostiles, heads=heads,
              step_s=step_s, max_s=max_s)

    def best(delay):
        safe, te = _paths(delay=delay, **kw)
        if not safe.any():
            return None
        k = int(np.argmin(np.where(safe, te, np.inf)))
        return heads[k], float(te[k])

    now = best(0.0)
    if now is None:
        return {"exists": False, "margin_s": 0.0, "heading": None, "exit_s": None}
    if not hostiles or best(cap_s) is not None:
        return {"exists": True, "margin_s": float(cap_s), "heading": now[0], "exit_s": now[1]}
    lo, hi = 0.0, float(cap_s)                                                   # route exists at lo, not at hi
    for _ in range(10):
        mid = 0.5 * (lo + hi)
        if best(mid) is not None:
            lo = mid
        else:
            hi = mid
    return {"exists": True, "margin_s": lo, "heading": now[0], "exit_s": now[1]}
