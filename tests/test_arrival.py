import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError
from simulator.render.interp import Run

from .helpers import REPO, unit, write_scenario

ARRIVE = {"type": "hyper_translation", "time_s": 45}


def run(d, units, **scenario):
    cfg = load_config(write_scenario(d, scenario={"duration_s": 120, **scenario}, units=units), REPO)
    out = Path(d) / "o"
    Simulation(cfg).run(out)
    return cfg, out, [json.loads(x) for x in (out / "events.jsonl").read_text().splitlines()]


class Arrival(unittest.TestCase):
    def test_ship_is_off_the_field_until_it_arrives(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", count=2, pos=(8.0e8, 0, 0), vel=(-1000, 0, 0), objective={"type": "coast"}, arrival=ARRIVE),
                     unit("b", "blue", pos=(8.0e8, 5.0e4, 0), objective={"type": "coast"})]
            cfg, out, ev = run(d, units)
            self.assertFalse([w for w in cfg.warnings if "hyper limit" in w])                               # 800 million km is outside the Sun's hyper limit
            arr = [e for e in ev if e["type"] == "hyper_arrival"]
            self.assertEqual([(e["t"], e["actor_id"]) for e in arr], [(45.0, "a-001"), (45.0, "a-002")])
            self.assertFalse([e for e in ev if e["type"] == "contact_gained" and e["t"] < 45.0])     # nothing to see before then
            self.assertTrue([e for e in ev if e["type"] == "contact_gained"])
            rows = [json.loads(x) for x in (out / "state.jsonl").read_text().splitlines()]
            ship = lambda r: next(o for o in r["objects"] if o["id"] == "a-001")
            before, after = ship(rows[4]), ship(rows[5])                    # t = 40 and t = 50
            self.assertTrue(before.get("pending") and before.get("departed"))
            self.assertFalse(after.get("pending") or after.get("departed"))
            self.assertAlmostEqual(before["position_m"][0], 8.0e11)         # held at the arrival point
            self.assertAlmostEqual(after["position_m"][0], 8.0e11 - 1.0e6 * 5, delta=1.0e3)   # then 5 s at 1000 km/s

    def test_render_states_around_an_arrival(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, _ = run(d, [unit("a", "red", pos=(8.0e8, 0, 0), vel=(-1000, 0, 0), objective={"type": "coast"}, arrival=ARRIVE)])
            r = Run(out)
            x = lambda t: r.state("a-001", t)["p"][0]
            self.assertTrue(r.state("a-001", 44.0)["gone"] and not r.state("a-001", 45.0)["gone"])
            self.assertAlmostEqual(x(43.0), 8.0e11 + 2.0e6, delta=1.0)      # before: where it would be, coasting in
            self.assertAlmostEqual(x(47.0), 8.0e11 - 2.0e6, delta=1.0e3)
            self.assertAlmostEqual(x(60.0), 8.0e11 - 15.0e6, delta=1.0e3)

    def test_arrival_at_the_start_and_before_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, ev = run(d, [unit("a", "red", pos=(8.0e8, 0, 0), vel=(-1000, 0, 0), objective={"type": "coast"},
                                      arrival={"type": "hyper_translation"})])
            self.assertEqual([e["t"] for e in ev if e["type"] == "hyper_arrival"], [0.0])
            r = Run(out)
            self.assertTrue(r.state("a-001", -2.0)["gone"])
            self.assertAlmostEqual(r.state("a-001", -2.0)["p"][0], 8.0e11 + 2.0e6, delta=1.0)
            self.assertTrue(np.allclose(r.state("sun", -2.0)["p"], r.state("sun", 0.0)["p"], atol=1.0e3))   # bodies coast back too

    def test_staggered_arrivals_and_slot_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", count=3, pos=(8.0e8, 0, 0), vel=(-1000, 0, 0), objective={"type": "coast"},
                          formation={"type": "line_abreast", "spacing_km": 400, "offsets_km": [[0, 0, 0], [0, 0, 250], [0, 0, 0]]},
                          arrival={"type": "hyper_translation", "time_s": 10, "stagger_s": [0, 2, 1]})]
            _, out, ev = run(d, units)
            arr = {e["actor_id"]: e for e in ev if e["type"] == "hyper_arrival"}
            self.assertEqual([arr[f"a-00{k}"]["t"] for k in (1, 2, 3)], [10.0, 12.0, 11.0])
            r = Run(out)
            p = [r.state(f"a-00{k}", 20.0)["p"] for k in (1, 2, 3)]
            self.assertAlmostEqual(p[1][2] - p[0][2], 250.0e3, delta=1.0)                # the middle ship rides high
            self.assertAlmostEqual(p[1][0], p[0][0], delta=1.0e3)                        # and a late ship is still abreast of the first
            self.assertAlmostEqual(abs(p[2][1] - p[0][1]), 800.0e3, delta=1.0)
            q = [r.state(f"a-00{k}", 12.5)["q"] for k in (1, 2, 3)]                      # and all three point the same way
            self.assertTrue(np.allclose(q[0], q[1], atol=1e-3) and np.allclose(q[0], q[2], atol=1e-3))
            cfg = load_config(write_scenario(d, units=[unit("a", count=2, arrival={"type": "hyper_translation", "stagger_s": 3})]), REPO)
            self.assertEqual(cfg.groups[0].arrival["delays"], [0.0, 3.0])
            for bad in ({"arrival": {"type": "hyper_translation", "stagger_s": [0, 1, 2]}}, {"formation": {"type": "line_abreast", "offsets_km": [[0, 0, 1]]}}):
                with self.assertRaises(ConfigError):
                    load_config(write_scenario(d, units=[unit("a", count=2, **bad)]), REPO)

    def test_ships_hold_after_arriving_then_get_under_way_one_by_one(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", count=2, pos=(8.0e8, 0, 0), vel=(0, 0, 0), orientation={"forward_vector": [-1, 0, 0]},
                          objective={"type": "transit_to_point", "destination_km": [7.0e8, 0, 0]},
                          formation={"type": "line_abreast", "spacing_km": 400},
                          arrival={"type": "hyper_translation", "time_s": 2, "stagger_s": [0, 1], "hold_s": [5, 9]})]
            cfg, out, ev = run(d, units)
            self.assertEqual(cfg.groups[0].arrival["holds"], [5.0, 9.0])
            go = {e["actor_id"]: e for e in ev if e["type"] == "under_way"}
            self.assertEqual([go[k]["t"] for k in ("a-001", "a-002")], [7.0, 12.0])        # arrival + its own hold
            r = Run(out)
            x = lambda s, t: r.state(s, t)["p"][0]
            self.assertAlmostEqual(x("a-001", 6.9), 8.0e11, delta=1.0)                     # drifting: it arrived at rest
            self.assertAlmostEqual(x("a-002", 11.9), 8.0e11, delta=1.0)
            a = (8.0e11 - x("a-001", 20.0)) / (0.5 * 13.0 ** 2)                            # pushing since T+7
            self.assertAlmostEqual(8.0e11 - x("a-001", 9.0), 0.5 * a * 2.0 ** 2, delta=0.02 * a)       # also between stored samples
            self.assertAlmostEqual(8.0e11 - x("a-002", 14.0), 0.5 * a * 2.0 ** 2, delta=0.02 * a)
            self.assertEqual([r.drive("a-001", t) for t in (3.0, 6.9)], [0.0, 0.0])
            self.assertTrue(0.0 < r.drive("a-001", 7.5) < r.drive("a-001", 9.0) <= 1.0)   # the wedge's level rises as it starts to push
            self.assertEqual(r.drive("a-002", 11.0), 0.0)
            with self.assertRaises(ConfigError):
                load_config(write_scenario(d, units=[unit("a", count=2, arrival={"type": "hyper_translation", "hold_s": [1, 2, 3]})]), REPO)

    def test_bad_arrivals_are_rejected_and_inside_the_limit_warns(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ({"type": "teleport"}, {"type": "hyper_translation", "time_s": -1}, {"type": "hyper_translation", "when": 3}):
                with self.assertRaises(ConfigError):
                    load_config(write_scenario(d, units=[unit("a", arrival=bad)]), REPO)
            cfg = load_config(write_scenario(d, units=[unit("a", pos=(1.0e8, 0, 0), arrival=ARRIVE)]), REPO)
            self.assertTrue(any("inside the hyper limit" in w for w in cfg.warnings))


if __name__ == "__main__":
    unittest.main()
