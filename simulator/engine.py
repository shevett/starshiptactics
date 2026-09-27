"""Fixed-step simulation loop."""
import json
import math
from pathlib import Path

import numpy as np

from . import __version__
from .navigation import guidance, orbits, safety
from .physics.dynamics import FORWARD, gravity, normalize, quat_between, rotate, slew_toward
from .physics.orbits import Ephemeris
from .units import G0
from .sensors.lightdelay import History, SensorNet
from .weapons.missiles import MissileSwarm


def _r(v, nd):
    return [round(float(x), nd) for x in v]


class Simulation:
    def __init__(self, cfg):
        self.cfg = cfg
        self.eph = Ephemeris(cfg.bodies, cfg.seed, cfg.phase_strategy, cfg.phase_overrides, cfg.start_time_s)
        simd = cfg.sim["simulation"]
        nav = cfg.nav["navigation"]
        nd = simd["navigation_defaults"]
        self.min_sep = nav["ship_separation"]["minimum_km"] * 1000.0
        self.avoid_on = nav["collision_avoidance"]["enabled"]
        self.window = float(nav["collision_avoidance"]["prediction_window_s"])
        self.margin = nd["braking_margin"]
        self.dest_margin = nd["exclusion_destination_margin"]
        self.n_seg = int(nd["body_path_segments"])
        self.tau = max(float(nd["velocity_gain_time_s"]), cfg.time_step_s)
        self.orbit_tol = {"radius": nd["orbit_radius_tolerance_frac"], "ecc": nd["orbit_eccentricity_tolerance"]}
        self.tol_pos = nd["arrival_position_tolerance_km"] * 1000.0
        self.tol_vel = nd["arrival_speed_tolerance_m_s"]
        self.align_tol = math.radians(simd["attitude_control"]["alignment_tolerance_deg"])
        types = set(cfg.gravity_types)
        gm = np.array([b.type in types if types else True for b in cfg.bodies])
        self.grav_mask = gm if gm.any() else np.ones(len(cfg.bodies), bool)
        self.excl = np.array([b.radius_m + b.clearance_m for b in cfg.bodies])
        self.events = []
        self.swarm = MissileSwarm()
        self.doc = cfg.doctrine.get("doctrine", {}).get("engagement", {})
        self.missile_specs = (cfg.missile_classes.get("missiles") or {})
        self.stats = {"launched": 0, "detonated": 0, "burned_out": 0, "expired": 0}
        self._build_ships()

    # ------------------------------------------------------------------ setup
    def _build_ships(self):
        cfg = self.cfg
        ids, fac, cls, grp, objs, offs, eng, bats = [], [], [], [], [], [], [], []
        P, V, Q = [], [], []
        bp0, bv0 = self.eph.at(0.0)
        min_sep = cfg.nav["navigation"]["ship_separation"]["minimum_km"] * 1000.0
        default_slot = cfg.sim["simulation"]["navigation_defaults"]["default_orbit_slot_spacing_km"] * 1000.0   # placement only
        for g in cfg.groups:
            if g.orbit:                                   # start on a circular orbit around a body
                b = self.eph.index[g.orbit["body"]]
                slots = orbits.initial_orbit_states(g.orbit, self.eph.mu[b], g.count, cfg.seed, g.id, default_slot)
                slot_pos = [bp0[b] + rel for rel, _ in slots]
                slot_vel = [bv0[b] + w for _, w in slots]
                offs_g = [np.zeros(3)] * g.count
                fwd_g = [normalize(np.array(g.forward, float)) if g.forward else normalize(w) for _, w in slots]
            else:
                f = normalize(np.array(g.forward, float))
                v = np.array(g.velocity, float)
                lateral = np.cross(f, [0, 0, 1.0])
                if np.linalg.norm(lateral) < 1e-9:
                    lateral = np.array([0, 1.0, 0])
                lateral = normalize(lateral)
                offs_g = [lateral * ((k - (g.count - 1) / 2.0) * g.spacing_m if g.formation == "line_abreast" else 0.0)
                          for k in range(g.count)]
                slot_pos = [np.array(g.position, float) + o for o in offs_g]
                slot_vel = [v.copy()] * g.count
                fwd_g = [f] * g.count
            for k in range(g.count):
                ids.append(f"{g.id}-{k + 1:03d}")
                fac.append(g.faction); cls.append(g.class_id); grp.append(g.id)
                objs.append({**g.objective, "complete": False, "phase": "approach"})
                eng.append(g.engagement)
                bats.append([{"id": b["id"], "tubes": b["tubes"], "cycle": float(b["cycle_time_s"]),
                              "compatible": list(b.get("compatible_missiles", [])),
                              "magazine": int(b["magazine_capacity"]), "next_ready": 0.0}
                             for b in ((cfg.ship_classes[g.class_id].raw.get("weapons") or {}).get("missile_batteries") or [])]
                            if g.engagement and g.engagement["mode"] == "fire_at_will" else [])
                offs.append(offs_g[k])
                P.append(slot_pos[k]); V.append(np.array(slot_vel[k], float)); Q.append(quat_between(FORWARD, fwd_g[k]))
        self.ids, self.fac, self.cls, self.grp, self.objs = ids, fac, cls, grp, objs
        self.engage, self.batteries = eng, bats
        self.integrity = np.ones(len(ids))          # 1 = undamaged, 0 = complete structural failure
        self.P, self.V, self.Q = np.array(P).reshape(-1, 3), np.array(V).reshape(-1, 3), np.array(Q).reshape(-1, 4)
        self.offset = np.array(offs).reshape(-1, 3)   # formation slot relative to group centre
        self.A = np.zeros_like(self.P)
        self.thrust_prev = np.zeros_like(self.P)      # last step's thrust acceleration (for path prediction)
        self.amax = np.array([cfg.ship_classes[c].max_accel for c in cls])
        self.rate = np.array([cfg.ship_classes[c].max_rotation_rate for c in cls])
        self.mode = [None] * len(ids)
        self.anchors = {}
        self.avoid_reason = [None] * len(ids)
        self.adjusted = [set() for _ in ids]

    def emit(self, t, etype, actor, data):
        self.events.append({"t": round(float(t), 6), "type": etype, "actor_id": actor,
                            "event_id": f"evt-{len(self.events) + 1:08d}", "data": data})

    # ------------------------------------------------------------------ navigation
    def _effective_destination(self, i, dest, bp):
        """Push a group destination lying inside a body's exclusion zone out to its boundary.

        Works on the formation centre so every ship in a group agrees on the adjusted point.
        """
        d = dest.copy()
        centre = self.P[i] - self.offset[i]
        for b in range(len(self.excl)):
            off = d - bp[b]
            dist = np.linalg.norm(off)
            limit = self.excl[b] * self.dest_margin
            if dist < limit:
                direction = normalize(off) if dist > 1 else normalize(centre - bp[b])
                d = bp[b] + direction * limit
                key = self.eph.bodies[b].id
                if key not in self.adjusted[i]:
                    self.adjusted[i].add(key)
                    self.pending_adjust.append((i, key, d.copy()))
        return d

    def _navigate(self, i, t, bp, bv, others, grav_i):
        """Return (desired_acceleration, mode, avoidance_reason)."""
        amax, pos, vel = self.amax[i], self.P[i], self.V[i]
        f = rotate(self.Q[i], FORWARD)
        speed = np.linalg.norm(vel)
        angle_to_brake = math.acos(np.clip(np.dot(f, -vel / speed), -1, 1)) if speed > 1e-6 else 0.0
        react = angle_to_brake / self.rate[i]

        # 1. hard safety rules take precedence over the objective
        if self.avoid_on:
            hit = safety.body_conflict(pos, vel, amax, react, bp, bv, self.excl, self.n_seg)
            if hit is not None:
                b, vec = hit
                return (safety.body_avoidance(pos, vel, amax, bp[b], vec), "avoidance",
                        f"body:{self.eph.bodies[b].id}")
            if len(others[0]):
                c = safety.ship_conflict(pos, vel, self.thrust_prev[i], others[0], others[1], others[3], self.min_sep, self.window)
                if c is not None:
                    k, miss, tca = c
                    rel_v = vel - others[1][k]
                    sign = 1.0 if self.ids[i] < others[2][k] else -1.0
                    a_av = safety.ship_avoidance(miss, rel_v, amax, sign)
                    # act at the last responsible moment: other ships may well change course before then
                    need = max(1.5 * self.min_sep - float(np.linalg.norm(miss)), 0.0)
                    turn = math.acos(np.clip(np.dot(f, a_av / amax), -1, 1)) / self.rate[i]
                    if tca <= 2.0 * (math.sqrt(2.0 * need / amax) + turn) + 2.0 * self.dt:
                        return a_av, "avoidance", f"ship:{others[2][k]}"

        # 2. ordinary objective
        obj = self.objs[i]
        otype = obj.get("type")
        if otype == "transit_to_point":
            dest = self._effective_destination(i, np.array(obj["destination_m"], float), bp) + self.offset[i]
            a = guidance.transit_command(pos, vel, dest, amax, self.tau, math.pi / self.rate[i],
                                         self.margin, obj.get("max_speed_m_s"))
            if not obj["complete"] and np.linalg.norm(dest - pos) < self.tol_pos and speed < self.tol_vel:
                obj["complete"] = True
                self.emit(t, "objective_complete", self.ids[i], {"objective": "transit_to_point",
                                                                  "position_m": _r(pos, 3)})
        elif otype == "transit_to_orbit":
            return self._orbit_command(i, t, bp, bv, grav_i)
        elif otype == "hold_position":
            a = guidance.velocity_tracking(vel, np.zeros(3), self.tau, amax)
            if not obj["complete"] and speed < self.tol_vel:
                obj["complete"] = True
                self.emit(t, "objective_complete", self.ids[i], {"objective": "hold_position",
                                                                  "position_m": _r(pos, 3)})
        else:
            return np.zeros(3), "coast", None
        an = np.linalg.norm(a)
        # small corrections are 'trim' and not reported as maneuvers (avoids event spam on final approach)
        mode = "trim" if an < 0.05 * amax else ("accelerate" if np.dot(a, vel) >= 0 else "brake")
        return a - grav_i, mode, None      # feed-forward: cancel local gravity so ships can hold a position

    def _orbit_command(self, i, t, bp, bv, grav_i):
        """Two phases: brake to rest relative to the body at the orbit radius, then burn to circular speed."""
        obj = self.objs[i]
        if obj["complete"]:
            return np.zeros(3), "coast", None                 # in orbit: gravity does the rest
        o = obj["orbit"]
        b = self.eph.index[o["body"]]
        amax, tol = self.amax[i], self.orbit_tol
        rel, w = self.P[i] - bp[b], self.V[i] - bv[b]
        n = orbits.plane_normal(o["inclination_rad"], o["node_rad"])
        u = orbits.capture_direction(rel, n)
        mu = self.eph.mu[b]
        r_orb, rmag = o["radius_m"], float(np.linalg.norm(rel))
        vc = orbits.circular_speed(mu, r_orb)
        mu_others = self.eph.mu.copy(); mu_others[b] = 0.0
        g_frame = gravity(bp[b:b + 1], bp, mu_others * self.grav_mask)[0]   # how the body itself is accelerating
        if obj["phase"] == "approach":
            # The group aims at an anchor point on the orbit sphere, fixed when the group first navigates (so
            # ships do not chase a target that moves with them); each ship keeps its formation offset.
            key = (self.grp[i], o["body"])
            if key not in self.anchors:
                members = [j for j in range(len(self.ids)) if self.grp[j] == self.grp[i]]
                mean_rel = np.mean([self.P[j] for j in members], axis=0) - bp[b]
                self.anchors[key] = orbits.capture_direction(mean_rel, n)
            u_c = self.anchors[key]
            slot = self.offset[i] - np.dot(self.offset[i], u_c) * u_c        # keep the offset tangential
            a = guidance.transit_command(rel, w, u_c * r_orb + slot, amax, self.tau, math.pi / self.rate[i], self.margin)
            if abs(rmag - r_orb) < max(self.tol_pos, 0.5 * tol["radius"] * r_orb) and np.linalg.norm(w) < 0.1 * vc:
                obj["ready"] = True
            # the whole group burns together: a ship that started early would drive into its neighbour
            members = [j for j in range(len(self.ids)) if self.grp[j] == self.grp[i]]
            if all(self.objs[j].get("ready") or self.objs[j]["complete"] for j in members):
                for j in members:
                    if self.objs[j]["phase"] == "approach" and not self.objs[j]["complete"]:
                        self.objs[j]["phase"] = "insertion"
                        self.emit(t, "maneuver_command", self.ids[j], {"mode": "orbit_insertion", "body": o["body"]})
            an = np.linalg.norm(a)
            mode = "trim" if an < 0.05 * amax else ("accelerate" if np.dot(a, w) >= 0 else "brake")
            return a - (grav_i - g_frame), mode, None
        v_circ = orbits.circular_speed(mu, max(rmag, 1.0)) * orbits.tangent(n, u, o["direction"])
        v_des = v_circ - u * float(np.clip((rmag - r_orb) / 60.0, -0.1 * vc, 0.1 * vc))      # trim radius toward r_orb
        a = guidance.velocity_tracking(w, v_des, self.tau, amax)
        # done when the orbit we are actually on is close to the wanted one (semi-major axis and eccentricity)
        sma = 1.0 / (2.0 / rmag - float(np.dot(w, w)) / mu) if 2.0 / rmag > np.dot(w, w) / mu else float("inf")
        ecc = float(np.linalg.norm(np.cross(w, np.cross(rel, w)) / mu - rel / rmag))
        if abs(sma - r_orb) < tol["radius"] * r_orb and ecc < tol["ecc"]:
            obj["complete"] = True
            self.emit(t, "objective_complete", self.ids[i], {
                "objective": "transit_to_orbit", "body": o["body"], "radius_m": round(rmag, 3),
                "speed_m_s": round(float(np.linalg.norm(w)), 3), "circular_speed_m_s": round(vc, 3),
                "semi_major_axis_m": round(sma, 3), "eccentricity": round(ecc, 6)})
        return a, "orbit_insertion", None

    def _others_for(self, i, t):
        """Ships this ship may reason about: own faction (truth) + light-delayed tracks of others."""
        pos, vel, acc, ids = [], [], [], []
        for j in range(len(self.ids)):
            if j != i and self.fac[j] == self.fac[i]:
                # Formation slots already guarantee separation within a group, and extrapolating two ships'
                # slightly different thrust axes over minutes produces phantom crossings. So groupmates only
                # count as obstacles once they are actually close.
                if self.grp[j] == self.grp[i] and np.linalg.norm(self.P[j] - self.P[i]) > 1.5 * self.min_sep:
                    continue
                pos.append(self.P[j]); vel.append(self.V[j]); acc.append(self.thrust_prev[j]); ids.append(self.ids[j])
        for j, p, v in self.sensors.known_others(self.fac[i], t):
            pos.append(p); vel.append(v); acc.append(np.zeros(3)); ids.append(self.ids[j])     # other factions' thrust is unknown
        return np.array(pos).reshape(-1, 3), np.array(vel).reshape(-1, 3), ids, np.array(acc).reshape(-1, 3)

    # ------------------------------------------------------------------ main loop
    def run(self, out_dir):
        cfg = self.cfg
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.dt = dt = cfg.time_step_s
        n_steps = int(round(cfg.duration_s / dt))
        snap_every = int(round(cfg.snapshot_interval_s / dt))
        stop_at = [x.get("at_s") for x in cfg.terminations if x.get("type") == "scripted_stop"]
        all_complete = any(x.get("type") == "all_objectives_complete" for x in cfg.terminations)
        nships = len(self.ids)
        hist = History(n_steps, nships, dt, self.P, self.V)
        interval = cfg.sim["simulation"]["sensors"]["observation_interval_s"]
        obs_every = max(1, int(round(interval / dt)))
        self.sensors = SensorNet(hist, self.ids, self.fac, cfg.factions, interval, self.emit)
        bmu = self.eph.mu * self.grav_mask
        self.pending_adjust = []
        state_f = open(out_dir / "state.jsonl", "w")
        missile_f = open(out_dir / "missiles.jsonl", "w")
        missile_every = max(1, int(round(cfg.sim["simulation"]["output"]["missile_output_interval_s"] / dt)))
        t_end = 0.0
        next_bodies, grav = None, None
        try:
            for step in range(n_steps + 1):
                t = step * dt
                t_end = t
                bp, bv = next_bodies if next_bodies is not None else self.eph.at(t)
                hist.record(step, self.P, self.V)
                if nships and (step % obs_every == 0 or len(self.swarm)):     # missiles need fresh tracks every step
                    self.sensors.update(t, self.P, emit_events=(step % obs_every == 0))
                terminate = (step == n_steps) or any(s is not None and t >= s for s in stop_at)
                if all_complete:
                    active = [o for o in self.objs if o.get("type") in ("transit_to_point", "hold_position", "transit_to_orbit")]
                    terminate = terminate or (bool(active) and all(o["complete"] for o in active))
                if step % snap_every == 0 or terminate:
                    state_f.write(json.dumps(self._snapshot(t, bp, bv), separators=(",", ":")) + "\n")
                if terminate:
                    break

                # decisions
                thrust = np.zeros_like(self.P)
                if grav is None:
                    grav = gravity(self.P, bp, bmu)
                for i in range(nships):
                    a_cmd, mode, reason = self._navigate(i, t, bp, bv, self._others_for(i, t), grav[i])
                    while self.pending_adjust:
                        k, body, d = self.pending_adjust.pop(0)
                        self.emit(t, "maneuver_command", self.ids[k],
                                  {"mode": "destination_adjusted", "reason": f"inside exclusion zone of {body}",
                                   "destination_m": _r(d, 3)})
                    if reason != self.avoid_reason[i]:
                        if reason:
                            self.emit(t, "avoidance_maneuver", self.ids[i], {"reason": reason})
                        self.avoid_reason[i] = reason
                    if mode not in ("avoidance", "trim") and mode != self.mode[i]:
                        self.emit(t, "maneuver_command", self.ids[i],
                                  {"mode": mode, "objective": self.objs[i].get("type")})
                    if mode not in ("avoidance", "trim"):
                        self.mode[i] = mode
                    an = float(np.linalg.norm(a_cmd))
                    if an > self.amax[i]:                       # class acceleration limit is a hard rule
                        a_cmd = a_cmd / an * self.amax[i]; an = self.amax[i]
                    if an > 1e-9:
                        # orientation first: slew toward the requested thrust direction, thrust only when aligned
                        q, remaining = slew_toward(self.Q[i], a_cmd / an, self.rate[i] * dt)
                        self.Q[i] = q
                        f = rotate(q, FORWARD)
                        if remaining <= self.align_tol:
                            thrust[i] = f * max(float(np.dot(a_cmd, f)), 0.0)

                self._fire(t)
                if len(self.swarm):
                    if step % missile_every == 0:
                        missile_f.write(json.dumps({"t": round(t, 6), "missiles": self.swarm.snapshot()}, separators=(",", ":")) + "\n")
                    mthrust = self.swarm.thrust(t, lambda fac, j: self.sensors.known_target(fac, j, t))
                    a_ship = thrust + grav
                    det, burnouts, expired = self.swarm.advance(t, dt, mthrust, self.P, self.V, a_ship, self.fac,
                                                                np.ones(nships, bool))
                    self._resolve_missile_events(det, burnouts, expired)

                # integrate (constant acceleration over the step)
                # velocity Verlet: gravity is re-evaluated at the new positions and body ephemeris time
                self.thrust_prev = thrust
                a_old = thrust + grav
                P_new = self.P + self.V * dt + 0.5 * a_old * dt * dt
                next_bodies = self.eph.at(t + dt)
                grav = gravity(P_new, next_bodies[0], bmu)
                self.A = thrust + grav
                self.V = self.V + 0.5 * (a_old + self.A) * dt
                self.P = P_new
        finally:
            state_f.close()
            missile_f.close()
        self._write_outputs(out_dir, t_end, n_steps)
        return out_dir

    # ------------------------------------------------------------------ output
    def _snapshot(self, t, bp, bv):
        objs = []
        for i, b in enumerate(self.eph.bodies):
            objs.append({"id": b.id, "kind": "body", "position_m": _r(bp[i], 1), "velocity_m_s": _r(bv[i], 6)})
        for i, sid in enumerate(self.ids):
            objs.append({"id": sid, "kind": "ship", "position_m": _r(self.P[i], 3),
                         "velocity_m_s": _r(self.V[i], 6), "acceleration_m_s2": _r(self.A[i], 6),
                         "orientation_xyzw": _r(self.Q[i], 9), "integrity": round(float(self.integrity[i]), 6)})
        row = {"t": round(t, 6), "objects": objs}
        tracks = {}
        for f, tr in self.sensors.tracks.items():
            if tr:
                tracks[f] = [{"id": x["id"], "observed_t": round(x["observed_t"], 6),
                              "position_m": _r(x["position_m"], 3), "velocity_m_s": _r(x["velocity_m_s"], 6)}
                             for x in tr.values()]
        if tracks:
            row["tracks"] = tracks
        return row

    def _fire(self, t):
        """fire_at_will: each ready battery fires a salvo at the nearest hostile track inside its reach."""
        for i, eng in enumerate(self.engage):
            if not eng or eng["mode"] != "fire_at_will":
                continue
            tracks = None
            for bat in self.batteries[i]:
                if bat["magazine"] <= 0 or t < bat["next_ready"] - 1e-9 or eng["missile"] not in bat["compatible"]:
                    continue
                spec = self.missile_specs[eng["missile"]]
                reach = 0.5 * spec["acceleration_g"] * G0 * spec["powered_duration_s"] ** 2 * self.doc["fire_range_fraction"]
                if tracks is None:
                    tracks = self.sensors.known_others(self.fac[i], t)
                best, room = None, 0
                for j, p, _v in tracks:
                    d = float(np.linalg.norm(p - self.P[i]))
                    if d > reach or self.integrity[j] <= self.doc["assumed_dead_integrity"]:
                        continue
                    aimed = sum(1 for f, tg in zip(self.swarm.faction, self.swarm.target) if f == self.fac[i] and tg == j)
                    free = int(self.doc["max_missiles_in_flight_per_target"]) - aimed
                    if free > 0 and (best is None or d < best[0]):
                        best, room = (d, j), free
                if best is None:
                    continue
                n = min(eng["salvo_size"], bat["tubes"], bat["magazine"], room)
                for _ in range(n):
                    mid = self.swarm.launch(t, self.ids[i], self.fac[i], i, best[1], self.P[i], self.V[i],
                                            eng["missile"], spec, self.doc["ballistic_expiry_s"])
                    self.stats["launched"] += 1
                    self.emit(t, "missile_launch", self.ids[i], {
                        "missile_id": mid, "missile_class": eng["missile"], "battery_id": bat["id"],
                        "target_id": self.ids[best[1]], "track_range_m": round(best[0], 1)})
                bat["magazine"] -= n
                bat["next_ready"] = t + max(bat["cycle"], float(self.doc["salvo_interval_s"]))

    def _resolve_missile_events(self, det, burnouts, expired):
        for b in burnouts:
            self.stats["burned_out"] += 1
            self.emit(b["t"], "missile_burnout", b["missile_id"], {"launcher_id": b["launcher_id"]})
        for d in det:
            k = d["ship_index"]
            before = float(self.integrity[k])
            self.integrity[k] = before * (1.0 - d["damage"])
            self.stats["detonated"] += 1
            self.emit(d["t"], "detonation", d["missile_id"], {
                "launcher_id": d["launcher_id"], "target_id": self.ids[k], "range_m": round(d["range_m"], 3),
                "damage": round(d["damage"], 6), "integrity_before": round(before, 6),
                "integrity_after": round(float(self.integrity[k]), 6), "position_m": _r(d["position_m"], 1),
                "missile_class": d["missile_class"]})
        for e in expired:
            self.stats["expired"] += 1
            self.emit(e["t"], "missile_expired", e["missile_id"], {"launcher_id": e["launcher_id"]})

    def _missile_envelope(self, class_id):
        """Powered-flight reach of the best compatible missile, from configured values only (None if unresolved).

        range = 1/2 a t^2 from rest relative to the launcher; missiles then coast, so this is the powered
        envelope, not a maximum range.
        """
        best = None
        raw = self.cfg.ship_classes[class_id].raw
        for bat in (raw.get("weapons") or {}).get("missile_batteries", []) or []:
            for name in bat.get("compatible_missiles", []):
                m = (self.cfg.missile_classes.get("missiles") or {}).get(name) or {}
                g, tb = m.get("acceleration_g"), m.get("powered_duration_s")
                if g is None or tb is None:
                    continue
                a = g * G0
                env = {"missile": name, "acceleration_g": g, "powered_duration_s": tb,
                       "powered_range_m": 0.5 * a * tb * tb, "burnout_speed_m_s": a * tb}
                if best is None or env["powered_range_m"] > best["powered_range_m"]:
                    best = env
        return best

    def _write_outputs(self, out_dir, t_end, n_steps):
        cfg = self.cfg
        with open(out_dir / "events.jsonl", "w") as f:
            for e in self.events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
        objects = [{"id": b.id, "kind": "body", "type": b.type, "parent": b.parent, "radius_m": b.radius_m,
                    "mu_m3_s2": b.mu,
                    "exclusion_radius_m": b.radius_m + b.clearance_m}
                   for b in self.eph.bodies]
        for i, sid in enumerate(self.ids):
            objects.append({"id": sid, "kind": "ship", "faction": self.fac[i], "class": self.cls[i],
                            "group": self.grp[i], "max_acceleration_m_s2": self.amax[i],
                            "missile_envelope": self._missile_envelope(self.cls[i])})
        meta = {
            "simulator_version": __version__,
            "scenario_id": cfg.scenario["id"],
            "system": cfg.scenario["system"],
            "random_seed": cfg.seed,
            "orbital_phase_strategy": cfg.phase_strategy,
            "orbital_mean_anomaly_rad": self.eph.phases,
            "units": {"position": "m", "velocity": "m/s", "acceleration": "m/s^2", "time": "s", "angle": "rad",
                      "orientation": "quaternion xyzw, body-forward = +x"},
            "time_step_s": cfg.time_step_s,
            "snapshot_interval_s": cfg.snapshot_interval_s,
            "start_time_s": cfg.start_time_s,
            "end_time_s": cfg.start_time_s + t_end,
            "elapsed_s": t_end,
            "steps": int(round(t_end / cfg.time_step_s)),
            "min_ship_separation_m": self.min_sep,
            "factions": cfg.factions,
            "hyper_limit_m": cfg.hyper_limit_m,
            "orbits": [{"group": g.id, "body": (g.orbit or g.objective.get("orbit"))["body"],
                        "radius_m": (g.orbit or g.objective.get("orbit"))["radius_m"]}
                       for g in cfg.groups if g.orbit or g.objective.get("orbit")],
            "objects": objects,
            "source_file_sha256": cfg.file_hashes,
            "warnings": cfg.warnings,
            "event_count": len(self.events),
            "missiles": dict(self.stats),
            "missile_output_interval_s": cfg.sim["simulation"]["output"]["missile_output_interval_s"],
            "final_integrity": {sid: round(float(self.integrity[i]), 6) for i, sid in enumerate(self.ids)},
        }
        with open(out_dir / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
