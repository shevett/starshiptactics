import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError

from .helpers import REPO, unit, write_scenario
from .test_core import jsonl, states

LEAVE = {"type": "transit_to_hyper_limit"}
EDGE = (3.798e8, 0, 0)                     # ~90,000 km inside the Solar System hyper limit (3.799e11 m), on the +x axis


def run(d, units, duration=200):
    cfg = load_config(write_scenario(d, scenario={"duration_s": duration}, units=units), REPO)
    sim = Simulation(cfg)
    out = Path(d) / "o"
    sim.run(out)
    return cfg, sim, out, jsonl(out / "events.jsonl")


def of(ev, *types):
    return [e for e in ev if e["type"] in types]


def ship(row, sid):
    return next(o for o in row["objects"] if o["id"] == sid)


class Departure(unittest.TestCase):
    def test_departing_ship_leaves_at_the_hyper_limit(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, sim, out, ev = run(d, [unit("a", "red", pos=EDGE, vel=(1000, 0, 0), objective=LEAVE)])
            dep = of(ev, "hyper_departure")
            self.assertEqual(len(dep), 1)
            self.assertGreaterEqual(dep[0]["data"]["distance_from_star_m"], cfg.hyper_limit_m)
            self.assertLess(dep[0]["t"], 95)                                 # 9e7 m at >= 1e6 m/s
            self.assertTrue(of(ev, "objective_complete"))
            rows = states(out)
            after = [r for r in rows if r["t"] > dep[0]["t"] + 1]
            self.assertTrue(after and all(ship(r, "a-001").get("departed") for r in after))
            self.assertFalse(any(ship(r, "a-001").get("departed") for r in rows if r["t"] < dep[0]["t"] - 1))
            p = {tuple(ship(r, "a-001")["position_m"]) for r in after}
            self.assertEqual(len(p), 1)                                      # frozen where it left

    def test_a_ship_that_merely_crosses_the_limit_does_not_depart(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, sim, out, ev = run(d, [unit("a", "red", pos=EDGE, vel=(1000, 0, 0))])    # coasts straight through
            self.assertFalse(of(ev, "hyper_departure"))
            self.assertGreater(float(np.linalg.norm(sim.P[0])), cfg.hyper_limit_m)

    def test_ship_far_from_the_limit_just_burns_outward(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, sim, out, ev = run(d, [unit("a", "red", pos=(1.5e8, 0, 0), objective=LEAVE)], duration=300)
            self.assertFalse(of(ev, "hyper_departure"))
            self.assertGreater(sim.V[0][0], 1e5)                              # accelerating away from the Sun

    def test_departed_ship_is_no_longer_a_contact_or_target(self):
        with tempfile.TemporaryDirectory() as d:
            fire = {"mode": "fire_at_will", "missile": "standard_nuclear"}
            units = [unit("r", "red", pos=(3.795e8, 0, 0), vel=(1000, 0, 0), engagement=fire),
                     unit("b", "blue", pos=EDGE, vel=(1000, 0, 0), objective=LEAVE)]
            cfg, sim, out, ev = run(d, units, duration=200)
            t_dep = of(ev, "hyper_departure")[0]["t"]
            self.assertTrue([e for e in of(ev, "missile_launch") if e["t"] < t_dep])          # it was being shot at
            self.assertFalse([e for e in of(ev, "missile_launch") if e["t"] > t_dep])         # ...and then it was not
            self.assertFalse([e for e in of(ev, "detonation") if e["data"]["target_id"] == "b-001" and e["t"] > t_dep])
            self.assertTrue([e for e in of(ev, "contact_lost") if e["data"].get("reason") == "hyper_departure"])

    def test_bad_heading_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("a", "red", objective={"type": "transit_to_hyper_limit", "heading": [0, 0, 0]})
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[u]), REPO)
            self.assertIn("non-zero", str(cm.exception))

    def test_heading_is_followed(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("a", "red", pos=(1.5e8, 0, 0), objective={"type": "transit_to_hyper_limit", "heading": [0, 1, 0]})
            _, sim, _, _ = run(d, [u], duration=300)
            self.assertGreater(sim.V[0][1], 1e5)
            self.assertLess(abs(sim.V[0][0]), 0.2 * sim.V[0][1])


if __name__ == "__main__":
    unittest.main()
