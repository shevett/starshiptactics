"""Gravity, quaternion attitude helpers."""
import numpy as np


def gravity(pos, body_pos, body_mu):
    """Gravitational acceleration on each of N points from B bodies. pos (N,3) -> (N,3)."""
    d = body_pos[None, :, :] - pos[:, None, :]            # (N,B,3)
    r2 = np.einsum("nbi,nbi->nb", d, d)
    r2 = np.maximum(r2, 1.0)
    w = body_mu[None, :] / (r2 * np.sqrt(r2))
    return np.einsum("nb,nbi->ni", w, d)


def cross(a, b):
    """3-vector cross product (np.cross has large per-call overhead for single vectors)."""
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def normalize(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def quat_from_axis_angle(axis, angle):
    s = np.sin(angle / 2)
    return np.array([axis[0] * s, axis[1] * s, axis[2] * s, np.cos(angle / 2)])


def rotate(q, v):
    """Rotate vector v by unit quaternion q (xyzw)."""
    u = q[:3]
    w = q[3]
    return v + 2 * cross(u, cross(u, v) + w * v)


def quat_between(a, b):
    """Minimal rotation taking unit vector a to unit vector b."""
    d = float(np.dot(a, b))
    if d > 1 - 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0])
    if d < -1 + 1e-12:
        axis = np.cross(a, [0, 0, 1.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        return quat_from_axis_angle(normalize(axis), np.pi)
    axis = normalize(cross(a, b))
    return quat_from_axis_angle(axis, np.arccos(np.clip(d, -1, 1)))


FORWARD = np.array([1.0, 0.0, 0.0])   # body-forward axis in the body frame


def slew_toward(q, target_dir, max_angle):
    """Rotate q so its forward axis moves toward target_dir by at most max_angle (rad).

    Uses the minimal rotation, so roll is not changed by the slew. Returns (q_new, remaining_angle).
    """
    f = rotate(q, FORWARD)
    angle = float(np.arccos(np.clip(np.dot(f, target_dir), -1, 1)))
    if angle < 1e-12:
        return q, 0.0
    step = min(angle, max_angle)
    axis = cross(f, target_dir)
    if np.linalg.norm(axis) < 1e-12:               # 180 degrees: pick any perpendicular, deterministically
        axis = cross(f, [0, 0, 1.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = cross(f, [0, 1.0, 0])
    dq = quat_from_axis_angle(normalize(axis), step)
    qn = quat_mul(dq, q)
    return qn / np.linalg.norm(qn), angle - step
