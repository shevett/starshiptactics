"""Group mission control: detect -> (withdraw | engage) -> objective (docs/MISSIONS.md).

Each group has a baseline objective (handled by navigation) and an ordered list of reaction rules. Every step the
group looks at the hostile contacts its faction currently tracks, classifies each one's posture
(closing / fleeing / holding) and evaluates its rules top to bottom. The first rule whose conditions hold wins:

    engage / pursue            -> push an `engage` behaviour (steer to the target, hold a standoff range)
    withdraw                   -> push a `withdraw` behaviour (leave through the hyper limit on the safest heading)
    ignore / resume_objective  -> no behaviour; the group flies its objective

Contacts that are lost or destroyed stop matching, so the objective resumes by itself (and an orbit objective is
re-established if the group left its orbit). A committed withdrawal is never re-evaluated: it ends at the hyper limit.
The group decides as a whole, from its faction's light-delayed tracks only (destroyed-ness is the one exception:
perfect battle damage assessment, as in fire control; hostile classes are assumed identified once a contact exists).
"""
import numpy as np

from ..physics.dynamics import FORWARD, rotate
from . import escape


class MissionControl:
    def __init__(self, sim, groups, contact_cfg, escape_cfg, assumed_dead, emit):
        """groups: {group_id: mission dict}."""
        self.sim = sim
        self.missions = groups
        self.members = {g: [i for i, x in enumerate(sim.grp) if x == g] for g in groups}
        self.min_rate = float(contact_cfg["posture_min_rate_m_s"])
        self.min_dur = float(contact_cfg["posture_min_duration_s"])
        self.esc = escape_cfg
        self.dead = float(assumed_dead)
        self.emit = emit
        self.heads = escape.candidate_headings(int(escape_cfg["ring_headings"]), float(escape_cfg["elevation_deg"]))
        self.posture = {g: {} for g in groups}      # group -> target idx -> {"posture", "cand", "since"}
        self.behavior = {}                           # group -> active behaviour dict
        self.centre = {}                             # group -> (position, velocity), refreshed each update
        self.esc_cache = {}                          # group -> (time, assessment)
        self.mass0 = {g: float(np.nansum(sim.mass[m])) for g, m in self.members.items()}
        self.magazine0 = {g: sum(b["magazine"] for i in m for b in sim.batteries[i]) for g, m in self.members.items()}

    # ------------------------------------------------------------------ queries used by the engine
    def engage_target(self, group):
        b = self.behavior.get(group)
        return b["target"] if b and b["kind"] == "engage" else None

    def label(self, group, objective_type):
        b = self.behavior.get(group)
        return b["kind"] if b else objective_type

    # ------------------------------------------------------------------ per-step update
    def update(self, t, bp):
        sim = self.sim
        self.bp = bp
        for g, mission in self.missions.items():
            live = [i for i in self.members[g] if not sim.departed[i] and not sim.destroyed[i]]
            if not live:
                continue
            c, cv = sim.P[live].mean(axis=0), sim.V[live].mean(axis=0)
            self.centre[g] = (c, cv)
            tracks = {j: (p, v) for j, p, v in sim.sensors.known_others(sim.fac[live[0]], t)
                      if sim.integrity[j] > self.dead}
            post = self._classify(g, t, tracks, c, cv)
            cur = self.behavior.get(g)
            if cur and cur["kind"] == "withdraw" and cur["commit"]:
                esc = self._escape(g, t, live, c, cv, tracks)          # keep the heading fresh while a route exists
                if esc["exists"]:
                    cur["heading"] = esc["heading"]
                continue
            self._apply(g, t, self._evaluate(g, t, mission["reactions"], live, c, cv, tracks, post), post, tracks)

    # ------------------------------------------------------------------ posture
    def _classify(self, g, t, tracks, c, cv):
        ids, state = self.sim.ids, self.posture[g]
        for j in [j for j in state if j not in tracks]:
            del state[j]                                        # contact lost or destroyed: forget its posture
        out = {}
        for j, (p, v) in tracks.items():
            rel_p, rel_v = p - c, v - cv
            rng = float(np.linalg.norm(rel_p))
            rate = float(np.dot(rel_p, rel_v)) / rng if rng > 1.0 else 0.0     # >0 opening, <0 closing
            now = "closing" if rate < -self.min_rate else "fleeing" if rate > self.min_rate else "holding"
            s = state.get(j)
            if s is None:                                       # first sighting: take the posture at face value
                state[j] = {"posture": now, "cand": now, "since": t}
                self.emit(t, "posture_changed", g, {"target_id": ids[j], "posture": now, "previous": None,
                                                    "range_m": round(rng, 1), "closing_rate_m_s": round(-rate, 2)})
            else:
                if now != s["cand"]:
                    s["cand"], s["since"] = now, t
                if s["cand"] != s["posture"] and t - s["since"] >= self.min_dur:
                    self.emit(t, "posture_changed", g, {"target_id": ids[j], "posture": s["cand"],
                                                        "previous": s["posture"], "range_m": round(rng, 1),
                                                        "closing_rate_m_s": round(-rate, 2)})
                    s["posture"] = s["cand"]
            out[j] = (state[j]["posture"], rng)
        return out

    # ------------------------------------------------------------------ escape assessment (cached)
    def _escape(self, g, t, live, c, cv, tracks):
        cached = self.esc_cache.get(g)
        if cached and t - cached[0] < float(self.esc["update_interval_s"]) - 1e-9:
            return cached[1]
        sim = self.sim
        factor = float(self.esc["envelope_factor"])
        hostiles = [(p, v, float(sim.reach[j]) * factor) for j, (p, v) in tracks.items()]
        res = escape.assess(c, cv, self.bp[sim.star], sim.cfg.hyper_limit_m,
                            float(min(sim.amax[i] for i in live)), float(min(sim.rate[i] for i in live)),
                            rotate(sim.Q[live[0]], FORWARD), hostiles, self.heads,
                            float(self.esc["sample_step_s"]), float(self.esc["max_path_s"]), float(self.esc["margin_cap_s"]))
        self.esc_cache[g] = (t, res)
        return res

    # ------------------------------------------------------------------ strength accounting
    def _own_strength(self, g):
        sim = self.sim
        f = sim.fac[self.members[g][0]]
        return float(sum(sim.mass[i] for i in range(len(sim.ids))
                         if sim.fac[i] == f and not sim.departed[i] and sim.integrity[i] > self.dead))

    def _strength_lost(self, g):
        sim = self.sim
        lost = sum(sim.mass[i] for i in self.members[g] if not sim.departed[i] and sim.integrity[i] <= self.dead)
        return float(lost) / self.mass0[g] if self.mass0[g] > 0 else 0.0

    def _magazine_fraction(self, g):
        if self.magazine0[g] <= 0:
            return None
        left = sum(b["magazine"] for i in self.members[g] for b in self.sim.batteries[i])
        return left / self.magazine0[g]

    # ------------------------------------------------------------------ rule evaluation
    def _gate(self, g, name, threshold, cands, esc_fn):
        """Returns (holds, actual_value)."""
        sim = self.sim
        if name == "force_ratio_over":
            own = self._own_strength(g)
            hostile = float(sum(sim.mass[j] for _, j in cands))
            v = hostile / own if own > 0 else float("inf")
            return v > threshold, v
        if name == "own_strength_lost_over":
            v = self._strength_lost(g)
            return v > threshold, v
        if name == "own_magazine_below":
            v = self._magazine_fraction(g)
            return (v is not None and v < threshold), v
        if name == "escape_margin_below_s":
            v = esc_fn()["margin_s"]
            return v < threshold, v
        raise KeyError(name)

    def _evaluate(self, g, t, reactions, live, c, cv, tracks, post):
        """First matching rule wins. Returns None (objective) or (rule_index, rule, target_idx|None, info)."""
        sim = self.sim
        current = self.behavior.get(g)
        esc_fn = lambda: self._escape(g, t, live, c, cv, tracks)
        for k, rule in enumerate(reactions):
            w, do = rule["when"], rule["do"]
            cands = [(rng, j) for j, (posture, rng) in post.items()
                     if (w["hostile"] == "any" or w["hostile"] == posture)
                     and rng <= w.get("max_range_m", float("inf"))
                     and rng <= do.get("max_range_m", float("inf"))]
            if not cands:
                continue
            if do.get("leash_m") is not None:                    # measured from the objective's orbited body
                body = sim.eph.index[sim.objs[self.members[g][0]]["orbit"]["body"]]
                if float(np.linalg.norm(c - self.bp[body])) > do["leash_m"]:
                    continue
                if not all(sim.objs[i]["complete"] for i in live):   # a sortie starts from an established orbit, so a
                    continue                                         # recalled group gets home before it goes out again
            values, ok = {}, True
            for name, thr in w["all"]:
                holds, v = self._gate(g, name, thr, cands, esc_fn)
                values[name] = v
                ok &= holds
            if ok and w["any"]:
                any_hit = False
                for name, thr in w["any"]:
                    holds, v = self._gate(g, name, thr, cands, esc_fn)
                    values[name] = v
                    any_hit |= holds
                ok &= any_hit
            if not ok:
                continue
            info = {"gates": values}
            if do["action"] == "ignore":
                return None                                      # explicitly: carry on with the objective
            if do["action"] == "withdraw":
                esc = esc_fn()
                info.update(margin_s=esc["margin_s"], exit_s=esc["exit_s"], route=esc["exists"])
                if esc["exists"]:
                    info["heading"] = esc["heading"]
                elif do["if_no_route"] == "fight":
                    continue                                     # no safe way out: let the rules below decide
                elif do["if_no_route"] == "hold":
                    return None
                else:                                            # flee_anyway: straight out from the star
                    away = c - self.bp[sim.star]
                    info["heading"] = away / max(float(np.linalg.norm(away)), 1.0)
                return k, rule, None, info
            # engage: stay on the current target while it still matches this rule (no flip-flopping)
            if current and current["kind"] == "engage" and current["rule"] == k and any(j == current["target"] for _, j in cands):
                return k, rule, current["target"], info
            return k, rule, min(cands)[1], info                  # otherwise the nearest matching contact
        return None

    # ------------------------------------------------------------------ behaviour changes
    @staticmethod
    def _why(rule, post, target, info):
        parts = []
        if target is not None:
            parts.append(f"{post[target][0]} hostile")
        fmt = {"force_ratio_over": "force ratio {:.2f}", "own_strength_lost_over": "strength lost {:.0%}",
               "own_magazine_below": "magazine {:.0%}", "escape_margin_below_s": "escape margin {:.0f} s"}
        for name, v in info["gates"].items():
            parts.append(fmt[name].format(v) if v is not None else f"{name} n/a")
        return "; ".join(parts) or "rule matched"

    def _apply(self, g, t, choice, post, tracks):
        sim = self.sim
        cur = self.behavior.get(g)
        if choice is None:
            if cur:
                del self.behavior[g]
                sim.reset_objective(g)                           # e.g. fly back into orbit after a sortie
                self.emit(t, "mission_changed", g, {
                    "behavior": "objective", "previous": cur["kind"],
                    "previous_target_id": sim.ids[cur["target"]] if cur["target"] is not None else None,
                    "reason": "no matching rule"})
            return
        k, rule, j, info = choice
        do = rule["do"]
        kind = "withdraw" if do["action"] == "withdraw" else "engage"
        new = {"kind": kind, "target": j, "rule": k, "label": do["label"], "standoff_m": do.get("standoff_m"),
               "commit": bool(do.get("commit")), "heading": info.get("heading")}
        changed = not cur or cur["kind"] != kind or cur["target"] != j or cur["rule"] != k
        if changed:
            data = {"behavior": kind, "previous": cur["kind"] if cur else "objective", "rule_index": k,
                    "rule": do["label"], "reason": self._why(rule, post, j, info)}
            if j is not None:
                data.update(target_id=sim.ids[j], range_m=round(post[j][1], 1), standoff_m=do["standoff_m"])
            if kind == "withdraw":
                data.update(commit=new["commit"], escape_margin_s=round(info["margin_s"], 1),
                            route_exists=info["route"], heading=[round(float(x), 4) for x in info["heading"]])
                if info["exit_s"] is not None:
                    data["time_to_hyper_limit_s"] = round(info["exit_s"], 1)
            self.emit(t, "mission_changed", g, data)
        self.behavior[g] = new
