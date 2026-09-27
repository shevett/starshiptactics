"""Objective guidance: turns an objective into a desired acceleration vector (before safety checks)."""
import numpy as np


def clamp(a, amax):
    n = np.linalg.norm(a)
    return a * (amax / n) if n > amax else a


def velocity_tracking(vel, v_des, tau, amax):
    """First-order velocity tracking with time constant tau, limited by capability.

    tau must be well above the attitude-slew time scale of small corrections, otherwise a
    thrust-axis-constrained ship chatters (the commanded direction outruns the ship's turning).
    """
    return clamp((v_des - vel) / tau, amax)


def transit_command(pos, vel, dest, amax, tau, t_flip_worst, margin, max_speed=None):
    """Fly to `dest` and stop there.

    Desired speed toward the destination follows a braking curve sqrt(2*a*d*margin), where d is
    reduced by the distance covered while turning the ship around (worst case 180 degrees).
    """
    r = dest - pos
    d = float(np.linalg.norm(r))
    if d < 1e-6:
        return velocity_tracking(vel, np.zeros(3), tau, amax)
    dhat = r / d
    v_close = float(np.dot(vel, dhat))
    d_eff = max(d - max(v_close, 0.0) * t_flip_worst, 0.0)
    speed = np.sqrt(2.0 * amax * d_eff * margin)
    if max_speed is not None:
        speed = min(speed, max_speed)
    return velocity_tracking(vel, dhat * speed, tau, amax)
