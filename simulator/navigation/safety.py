"""Navigation safety: predicted collisions and exclusion zones. Hard rules override objectives."""
import numpy as np


def _perp(v):
    """Deterministic unit vector perpendicular to v."""
    p = np.cross(v, [0, 0, 1.0])
    if np.linalg.norm(p) < 1e-9 * max(1.0, np.linalg.norm(v)):
        p = np.cross(v, [0, 1.0, 0])
    return p / np.linalg.norm(p)


def stopping_path(pos, vel, amax, react_s, n_seg):
    """Points and times along the 'coast while turning, then brake to rest' path."""
    speed = float(np.linalg.norm(vel))
    t_stop = react_s + speed / amax
    ts = np.linspace(0.0, t_stop, n_seg + 1)
    if speed < 1e-9:
        return ts, np.repeat(pos[None, :], n_seg + 1, axis=0)
    vhat = vel / speed
    tb = np.clip(ts - react_s, 0.0, None)
    along = speed * np.minimum(ts, react_s) + speed * tb - 0.5 * amax * tb * tb
    return ts, pos[None, :] + vhat[None, :] * along[:, None]


def constant_accel_path(pos, vel, acc, ts):
    """Positions at times ts for constant acceleration; a decelerating body stops when its velocity hits zero.

    pos/vel/acc are (...,3); returns (len(ts), ..., 3).
    """
    pos, vel, acc = (np.asarray(x, float) for x in (pos, vel, acc))
    a2 = np.einsum("...i,...i->...", acc, acc)
    va = np.einsum("...i,...i->...", vel, acc)
    t_stop = np.where((a2 > 1e-12) & (va < 0), -va / np.where(a2 > 1e-12, a2, 1.0), np.inf)
    tc = np.minimum(ts.reshape((-1,) + (1,) * t_stop.ndim), t_stop[None])                  # (K+1,...)
    return pos[None] + vel[None] * tc[..., None] + 0.5 * acc[None] * (tc * tc)[..., None]


def _closest_approach(ts, pts, target_pts):
    """Closest approach of a sampled path to sampled target paths.

    ts (K+1,), pts (K+1,3), target_pts (K+1,B,3). Each segment is treated as straight relative motion, so a
    fast ship cannot skip over a small target between samples. Returns (dist (B,), closest_vec (B,3), time (B,)).
    """
    rel = pts[:, None, :] - target_pts                                                       # (K+1,B,3)
    r0, w = rel[:-1], rel[1:] - rel[:-1]
    ww = np.einsum("kbi,kbi->kb", w, w)
    s = np.clip(-np.einsum("kbi,kbi->kb", r0, w) / np.where(ww > 0, ww, 1.0), 0.0, 1.0)
    closest = r0 + s[..., None] * w
    dist = np.linalg.norm(closest, axis=2)                                                   # (K,B)
    kmin = np.argmin(dist, axis=0)
    idx = np.arange(dist.shape[1])
    tmin = ts[:-1][kmin] + s[kmin, idx] * (ts[1:] - ts[:-1])[kmin]
    return dist[kmin, idx], closest[kmin, idx], tmin


def body_conflict(pos, vel, amax, react_s, body_pos, body_vel, body_excl, n_seg):
    """Check the safe-stop path against every body's exclusion sphere (moving bodies).

    Returns None if the ship could still stop clear of every zone, else
    (body_index, vector from body to ship at closest approach).
    """
    ts, pts = stopping_path(pos, vel, amax, react_s, n_seg)
    dmin, closest, _ = _closest_approach(ts, pts, body_pos[None] + body_vel[None] * ts[:, None, None])
    viol = dmin < body_excl
    if not viol.any():
        return None
    b = int(np.argmax(np.where(viol, body_excl - dmin, -np.inf)))
    return b, closest[b]


def body_avoidance(pos, vel, amax, body_pos, conflict_vec):
    """Brake, with a lateral push away from the body; if nearly stopped, push straight away."""
    speed = float(np.linalg.norm(vel))
    if speed < 1.0:
        away = conflict_vec if np.linalg.norm(conflict_vec) > 1e-6 else pos - body_pos
        return away / np.linalg.norm(away) * amax
    vhat = vel / speed
    lat = conflict_vec - np.dot(conflict_vec, vhat) * vhat
    lat = lat / np.linalg.norm(lat) if np.linalg.norm(lat) > 1e-6 else _perp(vhat)
    d = -vhat + 0.5 * lat
    return d / np.linalg.norm(d) * amax


def ship_conflict(pos, vel, thrust_accel, others_pos, others_vel, others_accel, min_sep, window_s, n_seg=24):
    """Predicted closest approach to other ships within the window.

    Every ship is extrapolated with its current velocity and thrust acceleration (a braking ship is predicted
    to stop, not to fly on at cruise speed). Other factions' accelerations are unknown and passed as zero.
    Returns None or (index, miss_vector_from_other_to_self, time_to_closest).
    """
    if len(others_pos) == 0:
        return None
    ts = np.linspace(0.0, window_s, n_seg + 1)
    pts = constant_accel_path(pos, vel, thrust_accel, ts)
    tgt = constant_accel_path(others_pos, others_vel, others_accel, ts)
    dist, closest, tmin = _closest_approach(ts, pts, tgt)
    viol = dist < min_sep
    if not viol.any():
        return None
    k = int(np.argmin(np.where(viol, dist, np.inf)))
    return k, closest[k], float(tmin[k])


def ship_avoidance(miss, rel_vel, amax, tie_sign):
    d = float(np.linalg.norm(miss))
    if d > 1.0:
        return miss / d * amax
    p = _perp(rel_vel if np.linalg.norm(rel_vel) > 0 else np.array([1.0, 0, 0]))
    return p * tie_sign * amax
