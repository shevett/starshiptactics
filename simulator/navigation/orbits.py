"""Circular-orbit geometry helpers (body-centred vectors, SI)."""
import math
import random

import numpy as np

from ..physics.dynamics import cross, normalize


def plane_normal(inclination, node):
    """Unit normal of an orbit plane (right-handed: prograde motion is counter-clockwise about it)."""
    return np.array([math.sin(inclination) * math.sin(node), -math.sin(inclination) * math.cos(node), math.cos(inclination)])


def perpendicular_in_plane(n):
    """Deterministic unit vector lying in the plane with normal n (the ascending-node direction if defined)."""
    e1 = cross([0, 0, 1.0], n)
    if np.linalg.norm(e1) < 1e-9:
        e1 = np.array([1.0, 0.0, 0.0])
    return normalize(e1)


def capture_direction(rel, n):
    """Unit vector in the orbit plane pointing toward the ship's projected position."""
    u = rel - np.dot(rel, n) * n
    if np.linalg.norm(u) < 1e-6 * max(np.linalg.norm(rel), 1.0):
        return perpendicular_in_plane(n)
    return normalize(u)


def tangent(n, u, direction):
    """Unit prograde (direction=+1) or retrograde (-1) tangent at in-plane radial direction u."""
    return normalize(cross(n, u)) * direction


def circular_speed(mu, r):
    return math.sqrt(mu / r)


def initial_orbit_states(orbit, mu, count, seed, group_id, default_spacing_m):
    """Positions/velocities (relative to the body) for `count` ships evenly spaced along a circular orbit.

    The group is centred on `phase_rad`; when no phase is given a deterministic one is drawn from
    (seed, group id) so results do not depend on the order groups are declared in.
    """
    n = plane_normal(orbit["inclination_rad"], orbit["node_rad"])
    e1 = perpendicular_in_plane(n)
    e2 = cross(n, e1)
    r = orbit["radius_m"]
    phase = orbit["phase_rad"]
    if phase is None:
        phase = random.Random(f"{seed}:{group_id}").random() * 2 * math.pi
    spacing = orbit["spacing_m"] or default_spacing_m
    step = spacing / r
    vc = circular_speed(mu, r)
    out = []
    for k in range(count):
        th = phase + (k - (count - 1) / 2.0) * step * orbit["direction"]
        u = math.cos(th) * e1 + math.sin(th) * e2
        out.append((r * u, vc * tangent(n, u, orbit["direction"])))
    return out
