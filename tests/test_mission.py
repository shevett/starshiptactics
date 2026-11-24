import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError

from .helpers import REPO, unit, write_scenario
from .test_core import jsonl, states

ENGAGE_ANY = {"reactions": [{"when": {"hostile": "any"}, "do": {"engage": {"standoff_km": 100000}}}]}


def run(d, units, duration=900, sensors=None):
    scenario = {"duration_s": duration}
    if sensors is not None:
        scenario["sensors"] = sensors
    cfg = load_config(write_scenario(d, scenario=scenario, units=units), REPO)
    sim = Simulation(cfg)
    out = Path(d) / "o"
    sim.run(out)
    return sim, out, jsonl(out / "events.jsonl")


def of(ev, *types):
    return [e for e in ev if e["type"] in types]


class MissionConfig(unittest.TestCase):
    def load(self, mission, **kw):
        with tempfile.TemporaryDirectory() as d:
            return load_config(write_scenario(d, units=[unit("r", "red", mission=mission, **kw)]), REPO)

    def bad(self, mission, needle):
        with self.assertRaises(ConfigError) as cm:
            self.load(mission)
        self.assertIn(needle, str(cm.exception))

    def test_valid_mission_is_normalised_to_si(self):
        g = self.load(ENGAGE_ANY).groups[0]
        rule = g.mission["reactions"][0]
        self.assertEqual(rule["do"]["action"], "engage")
        self.assertEqual(rule["do"]["standoff_m"], 1e8)

    def test_profile_resolves_and_scenario_reactions_come_first(self):
        g = self.load({"profile": "aggressive_transit",
                       "reactions": [{"when": {"hostile": "closing"}, "do": "ignore"}]}).groups[0]
        acts = [(r["when"]["hostile"], r["do"]["action"]) for r in g.mission["reactions"]]
        self.assertEqual(acts[0], ("closing", "ignore"))              # the scenario's rule outranks the profile's
        self.assertIn(("closing", "engage"), acts[1:])

    def test_pursue_is_an_engage_capped_by_range(self):
        r = self.load({"reactions": [{"when": {"hostile": "fleeing"}, "do": {"pursue": {"max_km": 5e6}}}]}
                      ).groups[0].mission["reactions"][0]
        self.assertEqual((r["do"]["action"], r["do"]["max_range_m"]), ("engage", 5e9))

    def test_errors_are_specific(self):
        self.bad({"profile": "nope"}, "unknown doctrine profile")
        self.bad({"reactions": [{"when": {"hostile": "angry"}, "do": "engage"}]}, "not in")
        self.bad({"reactions": [{"when": {"hostile": "any"}, "do": "dance"}]}, "unknown action")
        self.bad({"reactions": [{"when": {"hostile": "any"}, "do": {"pursue": {}}}]}, "needs max_km")
        self.bad({"reactions": [{"when": {"hostile": "any"}, "do": {"engage": {"speed": 1}}}]}, "unknown parameter")
        self.bad({"flavour": 1}, "expected keys")

    def test_objective_in_both_places_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("r", "red", mission={"objective": {"type": "coast"}}, objective={"type": "coast"})
            with self.assertRaises(ConfigError):
                load_config(write_scenario(d, units=[u]), REPO)

    def test_objective_may_live_inside_mission(self):
        g = self.load({"objective": {"type": "hold_position"}}).groups[0]
        self.assertEqual(g.objective["type"], "hold_position")


class Detection(unittest.TestCase):
    def test_contact_gained_only_inside_range(self):
        with tempfile.TemporaryDirectory() as d:
            # red closes on a stationary blue at 1000 km/s from 3e6 km; detection range 2e6 km -> gained after ~1000 s
            units = [unit("r", "red", vel=(1000, 0, 0)), unit("b", "blue", pos=(1e8 + 3e6, 0, 0))]
            _, _, ev = run(d, units, duration=1200, sensors={"detection_range_km": 2e6})
            gained = [e for e in of(ev, "contact_gained") if e["data"]["faction"] == "red"]
            self.assertEqual(len(gained), 1)
            self.assertAlmostEqual(gained[0]["t"], 1000, delta=40)
            self.assertLessEqual(gained[0]["data"]["range_m"], 2e9 * 1.01)
            self.assertFalse([e for e in of(ev, "sensor_observation_received") if e["t"] < gained[0]["t"] - 1
                              and e["data"]["faction"] == "red"])        # no observations before the contact existed

    def test_contact_lost_uses_hysteresis(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("r", "red"), unit("b", "blue", pos=(1e8 + 1.5e6, 0, 0), vel=(1000, 0, 0))]
            _, _, ev = run(d, units, duration=1000, sensors={"detection_range_km": 2e6})
            lost = [e for e in of(ev, "contact_lost") if e["data"]["faction"] == "red"]
            self.assertEqual(len(lost), 1)
            self.assertAlmostEqual(lost[0]["t"], 900, delta=40)           # 1.5e6 + 1000 t = 2e6 x 1.2

    def test_default_is_unlimited(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("r", "red"), unit("b", "blue", pos=(1e8 + 5e7, 0, 0))]
            _, _, ev = run(d, units, duration=60)
            self.assertTrue([e for e in of(ev, "contact_gained") if e["t"] <= 20])


class Engage(unittest.TestCase):
    def test_closes_to_standoff_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("r", "red", mission=ENGAGE_ANY), unit("b", "blue", pos=(1e8 + 5e5, 0, 0))]
            sim, out, ev = run(d, units, duration=1500)        # braking-curve guidance settles asymptotically
            ch = of(ev, "mission_changed")
            self.assertEqual((ch[0]["data"]["behavior"], ch[0]["data"]["target_id"]), ("engage", "b-001"))
            last = states(out)[-1]
            ships = {o["id"]: o for o in last["objects"] if o["kind"] == "ship"}
            rng = np.linalg.norm(np.array(ships["r-001"]["position_m"]) - ships["b-001"]["position_m"])
            rel_v = np.linalg.norm(np.array(ships["r-001"]["velocity_m_s"]) - ships["b-001"]["velocity_m_s"])
            self.assertAlmostEqual(rng / 1e8, 1.0, delta=0.05)           # standoff 100,000 km
            self.assertLess(rel_v, 100.0)

    def test_no_reactions_means_objective_only(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("r", "red", mission={"objective": {"type": "coast"}}),
                     unit("b", "blue", pos=(1e8 + 5e5, 0, 0))]
            _, out, ev = run(d, units, duration=100)
            self.assertFalse(of(ev, "mission_changed"))

    def test_ignore_rule_blocks_the_profile_below_it(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"profile": "aggressive_transit", "objective": {"type": "coast"},
                 "reactions": [{"when": {"hostile": "any"}, "do": "ignore"}]}
            units = [unit("r", "red", mission=m), unit("b", "blue", pos=(1e8 + 5e5, 0, 0))]
            _, _, ev = run(d, units, duration=100)
            self.assertFalse(of(ev, "mission_changed"))

    def test_resumes_objective_when_the_target_is_destroyed(self):
        with tempfile.TemporaryDirectory() as d:
            fire = {"mode": "fire_at_will", "missile": "standard_nuclear"}
            units = [unit("r", "red", mission={**ENGAGE_ANY, "objective": {"type": "coast"}}, engagement=fire),
                     unit("b", "blue", pos=(1e8 + 3e5, 0, 0))]
            sim, _, ev = run(d, units, duration=900)
            ch = of(ev, "mission_changed")
            self.assertEqual([c["data"]["behavior"] for c in ch], ["engage", "objective"])
            self.assertLessEqual(float(sim.integrity[1]), 0.05)
            self.assertLess(ch[0]["t"], ch[1]["t"])
            self.assertTrue(of(ev, "missile_launch"))


class Postures(unittest.TestCase):
    def test_posture_follows_range_rate(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("r", "red", mission={"objective": {"type": "coast"}, "reactions": [
                         {"when": {"hostile": "any"}, "do": "ignore"}]}),
                     unit("closer", "blue", pos=(1e8 + 5e6, 0, 0), vel=(-50, 0, 0)),
                     unit("away", "blue", pos=(1e8 - 5e6, 0, 0), vel=(-50, 0, 0)),
                     unit("still", "blue", pos=(1e8, 5e6, 0))]
            _, _, ev = run(d, units, duration=30)
            got = {e["data"]["target_id"]: e["data"]["posture"] for e in of(ev, "posture_changed")}
            self.assertEqual(got, {"closer-001": "closing", "away-001": "fleeing", "still-001": "holding"})


if __name__ == "__main__":
    unittest.main()
