import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

from simulator.config import load_config
from simulator.doctrine import escape, registry
from simulator.engine import Simulation
from simulator.errors import ConfigError

from .helpers import REPO, unit, write_scenario
from .test_core import jsonl, states

EDGE = (3.798e8, 0, 0)                      # ~90,000 km inside the hyper limit, on the +x axis
WITHDRAW_ON_ODDS = {"reactions": [{"when": {"force_ratio_over": 2.5}, "do": {"withdraw": {}}}],
                    "objective": {"type": "coast"}}


def run(d, units, duration=200, root=REPO, sensors=None):
    scenario = {"duration_s": duration}
    if sensors:
        scenario["sensors"] = sensors
    cfg = load_config(write_scenario(d, scenario=scenario, units=units), root)
    sim = Simulation(cfg)
    out = Path(d) / "o"
    sim.run(out)
    return sim, out, jsonl(out / "events.jsonl")


def of(ev, *types):
    return [e for e in ev if e["type"] in types]


def mission(**kw):
    return {"objective": {"type": "coast"}, **kw}


def temp_root(d):
    """A copy of classes/, rules/ and systems/ that a test may edit."""
    root = Path(d) / "root"
    for sub in ("classes", "rules", "systems"):
        shutil.copytree(REPO / sub, root / sub)
    return root


class Registry(unittest.TestCase):
    def test_vocabulary_is_json_and_documented(self):
        v = registry.describe()
        json.dumps(v)
        for group in ("filters", "gates"):
            for name, spec in v[group].items():
                self.assertTrue(spec["summary"], name)
        for name, spec in v["actions"].items():
            self.assertTrue(spec["summary"], name)
            for p, ps in spec["params"].items():
                self.assertTrue(ps["summary"], f"{name}.{p}")

    def test_validate_value(self):
        g = registry.GATES["own_strength_lost_over"]
        self.assertEqual(registry.validate_value(g, 0.5, "x"), 0.5)
        for bad in (1.5, -0.1, "lots", True):
            with self.assertRaises(ValueError):
                registry.validate_value(g, bad, "x")


class WithdrawConfig(unittest.TestCase):
    def load(self, m, **kw):
        with tempfile.TemporaryDirectory() as d:
            return load_config(write_scenario(d, units=[unit("r", "red", mission=m, **kw)]), REPO)

    def bad(self, m, needle, **kw):
        with self.assertRaises(ConfigError) as cm:
            self.load(m, **kw)
        self.assertIn(needle, str(cm.exception))

    def test_flat_when_list_form_and_any(self):
        flat = self.load(mission(reactions=[{"when": {"hostile": "closing", "force_ratio_over": 2, "max_range_km": 5e6},
                                             "do": {"withdraw": {"if_no_route": "hold"}}}])).groups[0].mission
        w = flat["reactions"][0]["when"]
        self.assertEqual((w["hostile"], w["all"], w["max_range_m"]), ("closing", [("force_ratio_over", 2.0)], 5e9))
        self.assertEqual(flat["reactions"][0]["do"]["if_no_route"], "hold")
        self.assertTrue(flat["needs"]["mass"] and flat["needs"]["hyper_limit"])
        lst = self.load(mission(reactions=[{"when": [{"force_ratio_over": 2}, {"own_magazine_below": .2}],
                                            "do": "ignore"}])).groups[0].mission
        self.assertEqual([g for g, _ in lst["reactions"][0]["when"]["all"]], ["force_ratio_over", "own_magazine_below"])
        anyr = self.load(mission(reactions=[{"when": {"any": [{"own_magazine_below": .1}, {"own_strength_lost_over": .5}]},
                                             "do": {"withdraw": {}}}])).groups[0].mission
        self.assertEqual(len(anyr["reactions"][0]["when"]["any"]), 2)

    def test_withdraw_defaults(self):
        do = self.load(mission(reactions=[{"when": {"force_ratio_over": 2}, "do": {"withdraw": {}}}])
                       ).groups[0].mission["reactions"][0]["do"]
        self.assertEqual((do["action"], do["commit"], do["if_no_route"], do["to"]), ("withdraw", True, "fight", "hyper_limit"))

    def test_errors_name_the_problem(self):
        r = lambda when, do="ignore": mission(reactions=[{"when": when, "do": do}])
        self.bad(r({"mood": 1}), "unknown condition")
        self.bad(r({"own_strength_lost_over": 2}), "out of range")
        self.bad(r({"any": []}), "non-empty")
        self.bad(r({"any": [{"hostile": "closing"}]}), "expected one condition")
        self.bad(r({"force_ratio_over": 2}, {"withdraw": {"to": "mars"}}), "not in")
        self.bad(r({"force_ratio_over": 2}, {"withdraw": {"commit": "yes"}}), "true or false")
        self.bad(r({}), "mapping of conditions")
        self.bad(r({"hostile": "any"}, {"engage": {"leash_km": 1000}}), "leash_km needs a transit_to_orbit")

    def test_profile_parameters_are_substituted_and_overridable(self):
        m = self.load({"profile": "cautious_defender", "objective": {"type": "transit_to_orbit", "body": "earth",
                                                                     "altitude_km": 20000},
                       "profile_params": {"force_ratio": 4.0}}).groups[0].mission
        self.assertEqual(m["reactions"][0]["when"]["all"][0], ("force_ratio_over", 4.0))
        self.assertEqual(m["reactions"][2]["do"]["leash_m"], 1e8)
        self.bad({"profile": "cautious_defender", "profile_params": {"warp": 1}}, "unknown parameter")
        self.bad({"profile_params": {"x": 1}}, "needs a profile")

    def test_mass_is_required_for_force_ratio(self):
        with tempfile.TemporaryDirectory() as d:
            root = temp_root(d)
            ships = yaml.safe_load((root / "classes/ships.yaml").read_text())
            ships["classes"]["battlecruiser"]["mass_kg"] = None
            (root / "classes/ships.yaml").write_text(yaml.safe_dump(ships))
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[unit("r", "red", mission=WITHDRAW_ON_ODDS)]), root)
            self.assertIn("no mass_kg", str(cm.exception))

    def test_hyper_limit_is_required_for_withdraw(self):
        with tempfile.TemporaryDirectory() as d:
            root = temp_root(d)
            sysdoc = yaml.safe_load((root / "systems/solar_system.yaml").read_text())
            del sysdoc["hyper_limit"]
            (root / "systems/solar_system.yaml").write_text(yaml.safe_dump(sysdoc))
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[unit("r", "red", mission=WITHDRAW_ON_ODDS)]), root)
            self.assertIn("need a hyper_limit", str(cm.exception))


class EscapeMath(unittest.TestCase):
    star, limit = np.zeros(3), 3.8e11
    p0 = np.array([1.47e11, 0, 0])

    def assess(self, hostiles):
        return escape.assess(self.p0, np.zeros(3), self.star, self.limit, 4800.0, np.radians(2), np.array([1.0, 0, 0]),
                             hostiles, escape.candidate_headings())

    def test_no_threat_gives_full_margin(self):
        r = self.assess([])
        self.assertTrue(r["exists"])
        self.assertEqual(r["margin_s"], 3600.0)

    def test_inside_an_envelope_there_is_no_route(self):
        r = self.assess([(self.p0 + [-1e9, 0, 0], np.zeros(3), 1.9e9)])
        self.assertFalse(r["exists"])
        self.assertEqual(r["margin_s"], 0.0)

    def test_margin_matches_the_time_until_the_hostile_reaches_its_envelope(self):
        # hostile 5e9 m behind, closing at 1.2e6 m/s, envelope 1.9e9: it reaches the envelope in (5e9-1.9e9)/1.2e6 s
        r = self.assess([(self.p0 + [-5e9, 0, 0], np.array([1.2e6, 0, 0]), 1.9e9)])
        self.assertTrue(r["exists"])
        self.assertAlmostEqual(r["margin_s"], 3.1e9 / 1.2e6, delta=0.15 * 3.1e9 / 1.2e6)

    def test_margin_shrinks_as_the_hostile_closes(self):
        m = [self.assess([(self.p0 + [-d, 0, 0], np.array([1.2e6, 0, 0]), 1.9e9)])["margin_s"] for d in (8e9, 5e9, 3e9)]
        self.assertGreater(m[0], m[1]); self.assertGreater(m[1], m[2])


class WithdrawRuntime(unittest.TestCase):
    def big_red(self, gap_m=3e10, **kw):
        # three Majestics (~2e10 kg) a long way behind the blue ship, coasting
        return {**unit("r", "red", count=3, pos=(3.798e8 - gap_m / 1e3, 0, 0), vel=(1000, 0, 0)), "class": "majestic",
                "formation": {"type": "line_abreast", "spacing_km": 500}}

    def blue(self, m, **kw):
        return {**unit("b", "blue", pos=EDGE, vel=(1000, 0, 0), mission=m, **kw), "class": "crusader"}

    def test_outnumbered_group_withdraws_through_the_hyper_limit(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = run(d, [self.big_red(), self.blue(WITHDRAW_ON_ODDS)])
            ch = of(ev, "mission_changed")
            self.assertEqual([c["data"]["behavior"] for c in ch], ["withdraw"])
            self.assertTrue(ch[0]["data"]["route_exists"])
            self.assertIn("force ratio", ch[0]["data"]["reason"])
            self.assertGreater(ch[0]["data"]["escape_margin_s"], 0)
            dep = of(ev, "hyper_departure")
            self.assertEqual([e["actor_id"] for e in dep], ["b-001"])
            self.assertGreater(dep[0]["t"], ch[0]["t"])

    def test_an_even_match_does_not_withdraw(self):
        with tempfile.TemporaryDirectory() as d:
            units = [{**unit("r", "red", pos=(3.798e8 - 3e7, 0, 0), vel=(1000, 0, 0)), "class": "crusader"}, self.blue(WITHDRAW_ON_ODDS)]
            _, _, ev = run(d, units)
            self.assertFalse(of(ev, "mission_changed", "hyper_departure"))

    def run_close(self, d, if_no_route, then_engage=True):
        reactions = [{"when": {"force_ratio_over": 2.5}, "do": {"withdraw": {"if_no_route": if_no_route}}}]
        if then_engage:
            reactions.append({"when": {"hostile": "any"}, "do": {"engage": {"standoff_km": 100000}}})
        return run(d, [self.big_red(gap_m=3e8), self.blue(mission(reactions=reactions))], duration=100)

    def test_no_safe_route_means_fight_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = self.run_close(d, "fight")                       # red is already inside its own missile envelope
            self.assertEqual([c["data"]["behavior"] for c in of(ev, "mission_changed")], ["engage"])
            self.assertFalse(of(ev, "hyper_departure"))

    def test_no_safe_route_hold_flies_the_objective(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = self.run_close(d, "hold")
            self.assertFalse(of(ev, "mission_changed"))

    def test_flee_anyway_goes_without_a_safe_route(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = self.run_close(d, "flee_anyway", then_engage=False)
            ch = of(ev, "mission_changed")
            self.assertEqual((ch[0]["data"]["behavior"], ch[0]["data"]["route_exists"]), ("withdraw", False))
            self.assertTrue(of(ev, "hyper_departure"))

    def far_scenario(self, commit):
        # blue is far from the limit (so it flees for a long time); the rule only applies while red is within 4e7 km,
        # which stops being true once blue has pulled away
        rule = {"when": {"force_ratio_over": 2.5, "max_range_km": 4e7}, "do": {"withdraw": {"commit": commit}}}
        red = {**unit("r", "red", count=3, pos=(2.5e8 - 3e7, 0, 0), vel=(1000, 0, 0)), "class": "majestic",
               "formation": {"type": "line_abreast", "spacing_km": 500}}
        blue = {**unit("b", "blue", pos=(2.5e8, 0, 0), vel=(1000, 0, 0), mission=mission(reactions=[rule])), "class": "crusader"}
        return [red, blue]

    def test_committed_withdrawal_continues_after_the_rule_stops_matching(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _, ev = run(d, self.far_scenario(True), duration=3000)
            self.assertEqual([c["data"]["behavior"] for c in of(ev, "mission_changed")], ["withdraw"])
            self.assertGreater(sim.V[3][0], 1e7)                          # still burning outward

    def test_uncommitted_withdrawal_ends_when_the_rule_stops_matching(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = run(d, self.far_scenario(False), duration=3000)
            ch = of(ev, "mission_changed")
            self.assertEqual([c["data"]["behavior"] for c in ch], ["withdraw", "objective"])

    def test_strength_lost_and_magazine_conditions(self):
        with tempfile.TemporaryDirectory() as d:
            fire = {"mode": "fire_at_will", "missile": "standard_nuclear"}
            rule = {"when": {"own_magazine_below": 0.999}, "do": {"withdraw": {"if_no_route": "flee_anyway"}}}
            units = [{**unit("r", "red", pos=(3.798e8 - 3e5, 0, 0), vel=(1000, 0, 0)), "class": "crusader"},
                     {**unit("b", "blue", pos=EDGE, vel=(1000, 0, 0), mission=mission(reactions=[rule]), engagement=fire),
                      "class": "reliant"}]
            _, _, ev = run(d, units, duration=100)
            ch = of(ev, "mission_changed")
            self.assertTrue(ch and ch[0]["data"]["behavior"] == "withdraw")
            self.assertIn("magazine", ch[0]["data"]["reason"])
            self.assertTrue(of(ev, "missile_launch"))                       # it fired first, which is what emptied the magazine

    def test_leash_returns_the_group_to_orbit_and_reestablishes_it(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"objective": {"type": "transit_to_orbit", "body": "earth", "altitude_km": 20000},
                 "reactions": [{"when": {"hostile": "any"}, "do": {"engage": {"standoff_km": 1000, "leash_km": 60000}}}]}
            units = [{"group": "b", "faction": "blue", "class": "reliant", "count": 1,
                      "orbit": {"body": "earth", "altitude_km": 20000, "phase_deg": 0}, "mission": m},
                     {**unit("r", "red", pos=(1.47098449e8 + 150000, 0, 0), vel=(0, 30.28, 0)), "class": "crusader"}]
            sim, out, ev = run(d, units, duration=1500)
            kinds = [c["data"]["behavior"] for c in of(ev, "mission_changed")]
            self.assertEqual(kinds[:2], ["engage", "objective"])             # sortie, then called back at the leash
            first_back = of(ev, "mission_changed")[1]["t"]
            self.assertGreater(first_back, 0)
            # the orbit objective was reset, so the ship re-approaches the orbit rather than coasting on
            self.assertTrue(any(e["type"] == "maneuver_command" and e["t"] > first_back and e["data"]["objective"] == "transit_to_orbit"
                                for e in ev))


if __name__ == "__main__":
    unittest.main()


class BodiesAreObstacles(unittest.TestCase):
    def test_escape_heading_avoids_a_planets_exclusion_zone(self):
        from simulator.doctrine import escape
        heads = escape.candidate_headings(32, 45.0)
        args = dict(p0=np.zeros(3), v0=np.zeros(3), star=np.array([-1.0e10, 0, 0]), limit=1.0e10 + 1.0e7, amax=4000.0,
                    rate=1.0, fwd=np.array([1.0, 0, 0]), hostiles=[], heads=heads)
        free = escape.assess(**args)
        blocked = escape.assess(**args, bodies=(np.array([[3.0e6, 0, 0]]), np.zeros((1, 3)), np.array([1.0e6])))
        self.assertTrue(free["exists"] and blocked["exists"])
        self.assertGreater(free["heading"][0], 0.95)                              # straight out when nothing is in the way
        off_axis = float(np.linalg.norm(np.cross(blocked["heading"], [1.0, 0, 0])))   # sine of the angle to the planet
        self.assertGreater(off_axis * 3.0e6, 1.0e6)                               # the chosen line clears the exclusion zone
