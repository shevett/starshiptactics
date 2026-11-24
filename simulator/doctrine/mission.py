"""Group mission control: detect -> engage -> objective (docs/MISSIONS.md).

Each group has a baseline objective (handled by navigation) and an ordered list of reaction rules. Every step the
group looks at the hostile contacts its faction currently tracks, classifies each one's posture
(closing / fleeing / holding) and evaluates its rules top to bottom. The first rule with a matching contact wins:

    engage / pursue -> push an `engage` behaviour on top of the objective (steer to the target, hold a standoff range)
    ignore / resume_objective -> no behaviour; the group flies its objective

Contacts that are lost or destroyed stop matching, so the objective resumes by itself. The group decides as a
whole (one picture, one decision), which keeps the behaviour deterministic and easy to follow in the event log.
All decisions use only the faction's light-delayed tracks, never ground truth about other factions
(destroyed-ness is the one exception: perfect battle damage assessment, as in fire control).
"""
import numpy as np


class MissionControl:
    def __init__(self, groups, ship_group, contact_cfg, assumed_dead, emit):
        """groups: {group_id: mission dict}; ship_group: group id per ship index."""
        self.missions = groups
        self.members = {g: [i for i, x in enumerate(ship_group) if x == g] for g in groups}
        self.min_rate = float(contact_cfg["posture_min_rate_m_s"])
        self.min_dur = float(contact_cfg["posture_min_duration_s"])
        self.dead = float(assumed_dead)
        self.emit = emit
        self.posture = {g: {} for g in groups}      # group -> target idx -> {"posture", "cand", "since"}
        self.behavior = {}                           # group -> {"target", "standoff_m", "rule", ...} while engaged
        self.centre = {}                             # group -> (position, velocity) of the group, refreshed each update

    # ------------------------------------------------------------------ queries used by the engine
    def engage_target(self, group):
        b = self.behavior.get(group)
        return b["target"] if b else None

    def label(self, group, objective_type):
        return "engage" if group in self.behavior else objective_type

    # ------------------------------------------------------------------ per-step update
    def update(self, t, P, V, fac, ids, integrity, sensors):
        for g, mission in self.missions.items():
            m = self.members[g]
            c = P[m].mean(axis=0)
            cv = V[m].mean(axis=0)
            self.centre[g] = (c, cv)
            tracks = {}
            for j, p, v in sensors.known_others(fac[m[0]], t):
                if integrity[j] > self.dead:
                    tracks[j] = (p, v)
            post = self._classify(g, t, tracks, c, cv, ids)
            choice = self._evaluate(g, mission["reactions"], tracks, post, c)
            self._apply(g, t, choice, ids, tracks, c, post)

    def _classify(self, g, t, tracks, c, cv, ids):
        state = self.posture[g]
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

    def _evaluate(self, g, reactions, tracks, post, c):
        """First matching rule wins. Returns (rule_index, rule, target_idx) or None."""
        current = self.behavior.get(g)
        for k, rule in enumerate(reactions):
            w = rule["when"]
            cands = [(rng, j) for j, (posture, rng) in post.items()
                     if (w["hostile"] == "any" or w["hostile"] == posture)
                     and rng <= w.get("max_range_m", float("inf"))
                     and rng <= rule["do"].get("max_range_m", float("inf"))]
            if not cands:
                continue
            if rule["do"]["action"] in ("ignore", "resume_objective"):
                return None                                      # explicitly: carry on with the objective
            # stay on the current target while it still matches this rule (no flip-flopping between near-equal targets)
            if current and current["rule"] == k and any(j == current["target"] for _, j in cands):
                return k, rule, current["target"]
            return k, rule, min(cands)[1]                        # otherwise the nearest matching contact
        return None

    def _apply(self, g, t, choice, ids, tracks, c, post):
        cur = self.behavior.get(g)
        if choice is None:
            if cur:
                del self.behavior[g]
                self.emit(t, "mission_changed", g, {"behavior": "objective", "previous": "engage",
                                                     "previous_target_id": ids[cur["target"]],
                                                     "reason": "no matching hostile contact"})
            return
        k, rule, j = choice
        new = {"target": j, "standoff_m": rule["do"]["standoff_m"], "rule": k, "label": rule["do"]["label"]}
        if not cur or cur["target"] != j or cur["rule"] != k:
            self.emit(t, "mission_changed", g, {
                "behavior": "engage", "previous": "engage" if cur else "objective", "target_id": ids[j],
                "reason": f"{post[j][0]} hostile", "rule_index": k, "rule": rule["do"]["label"],
                "standoff_m": rule["do"]["standoff_m"], "range_m": round(post[j][1], 1)})
        self.behavior[g] = new
