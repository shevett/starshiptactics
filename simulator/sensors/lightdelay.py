"""Light-delayed observation of ships. Perfect detection once light arrives (Milestone 1)."""
import numpy as np

from ..units import C


class History:
    """Authoritative per-step ship state, used to reconstruct what light from the past shows."""

    def __init__(self, n_steps, n_ships, dt, p0, v0):
        self.dt = dt
        self.pos = np.zeros((n_steps + 1, n_ships, 3))
        self.vel = np.zeros((n_steps + 1, n_ships, 3))
        self.p0, self.v0 = p0.copy(), v0.copy()
        self.count = 0

    def record(self, step, pos, vel):
        self.pos[step] = pos
        self.vel[step] = vel
        self.count = step + 1

    def state_at(self, j, t):
        """Position/velocity of ship j at time t (interpolated; extrapolated before t=0)."""
        if t <= 0:
            return self.p0[j] + self.v0[j] * t, self.v0[j]
        x = t / self.dt
        k = min(int(x), self.count - 1)
        if k >= self.count - 1:
            return self.pos[k, j] + self.vel[k, j] * (t - k * self.dt), self.vel[k, j]
        f = x - k
        return (self.pos[k, j] * (1 - f) + self.pos[k + 1, j] * f,
                self.vel[k, j] * (1 - f) + self.vel[k + 1, j] * f)


def observe(history, observer_pos, target_idx, t):
    """What an observer at observer_pos (at time t) sees of target ship `target_idx`.

    Solves  t_obs = t - |observer_pos - target(t_obs)| / c  by fixed-point iteration.
    Returns (t_obs, position, velocity, delay).
    """
    t_obs = t
    for _ in range(6):
        p, v = history.state_at(target_idx, t_obs)
        delay = float(np.linalg.norm(observer_pos - p)) / C
        new = t - delay
        if abs(new - t_obs) < 1e-12:
            t_obs = new
            break
        t_obs = new
    p, v = history.state_at(target_idx, t_obs)
    return t_obs, p, v, float(np.linalg.norm(observer_pos - p)) / C


class SensorNet:
    """Faction contact tracks built only from light-delayed observations of other factions' ships."""

    def __init__(self, history, ship_ids, ship_factions, factions, interval_s, emit,
                 detection_range_m=None, lose_range_factor=1.2):
        """detection_range_m: None = every ship is detectable at any range (once its light arrives).
        Otherwise a contact is gained when an observer is within range and lost beyond range * lose_range_factor
        (hysteresis, so a contact on the boundary does not flicker)."""
        self.h = history
        self.range = detection_range_m
        self.lose_factor = lose_range_factor
        self.stale = {f: {} for f in factions}    # last track of contacts that have been lost
        self.departed = None                      # optional bool array (engine-owned): ships that have left via the hyper limit
        self.ids = ship_ids
        self.fac = ship_factions
        self.factions = factions
        self.interval = interval_s
        self.emit = emit
        self.tracks = {f: {} for f in factions}   # faction -> target idx -> track dict

    def update(self, t, pos, emit_events=True):
        for f in self.factions:
            gone = self.departed if self.departed is not None else ()
            observers = [i for i, x in enumerate(self.fac) if x == f and not (len(gone) and gone[i])]
            for j, tf in enumerate(self.fac):
                if tf == f or not observers:
                    continue
                if len(gone) and gone[j]:                          # left the field: no longer a contact at all
                    self.stale[f].pop(j, None)
                    if self.tracks[f].pop(j, None) is not None:
                        self.emit(t, "contact_lost", f"faction:{f}", {"faction": f, "target_id": self.ids[j],
                                                                       "reason": "hyper_departure"})
                    continue
                had = j in self.tracks[f]
                limit = None if self.range is None else self.range * (self.lose_factor if had else 1.0)
                best = None
                for i in observers:
                    obs = observe(self.h, pos[i], j, t)
                    if limit is not None and float(np.linalg.norm(pos[i] - obs[1])) > limit:
                        continue                                  # outside detection range
                    if best is None or obs[0] > best[1][0]:      # freshest information wins
                        best = (i, obs)
                if best is None:
                    if had:
                        self.stale[f][j] = self.tracks[f].pop(j)
                        self.emit(t, "contact_lost", f"faction:{f}", {"faction": f, "target_id": self.ids[j],
                                  "last_observed_t": self.stale[f][j]["observed_t"]})
                    continue
                i, (t_obs, p, v, delay) = best
                if not had:
                    self.stale[f].pop(j, None)
                    self.emit(t, "contact_gained", f"faction:{f}", {
                        "faction": f, "target_id": self.ids[j], "observer_id": self.ids[i],
                        "range_m": round(float(np.linalg.norm(pos[i] - p)), 1), "observed_t": t_obs})
                self.tracks[f][j] = {"id": self.ids[j], "observed_t": t_obs, "position_m": p, "velocity_m_s": v}
                data = {"observer_id": self.ids[i], "target_id": self.ids[j], "faction": f,
                        "observed_t": t_obs, "received_t": t, "delay_s": delay,
                        "position_m": [float(c) for c in p], "velocity_m_s": [float(c) for c in v]}
                if emit_events:
                    self.emit(t_obs, "sensor_observation_generated", self.ids[j], data)
                    self.emit(t, "sensor_observation_received", self.ids[i], data)

    def known_others(self, faction, t):
        """Tracks of other factions' ships, extrapolated (constant velocity) to time t."""
        out = []
        for j, tr in self.tracks[faction].items():
            if self.departed is not None and self.departed[j]:
                continue
            dt = t - tr["observed_t"]
            out.append((j, tr["position_m"] + tr["velocity_m_s"] * dt, tr["velocity_m_s"]))
        return out

    def known_target(self, faction, j, t):
        """(position, velocity) of ship j from faction's track, extrapolated to time t; None if never observed."""
        if self.departed is not None and self.departed[j]:
            return None
        tr = self.tracks[faction].get(j) or self.stale[faction].get(j)    # a missile may outlive its contact
        if tr is None:
            return None
        return tr["position_m"] + tr["velocity_m_s"] * (t - tr["observed_t"]), tr["velocity_m_s"]
