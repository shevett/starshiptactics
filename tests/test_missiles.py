import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError

from .helpers import REPO, unit, write_scenario
from .test_core import jsonl, states


def duel(d, gap_km=3e5, duration=200, red_engage=None, blue_engage=None, blue_faction="blue"):
    units = [unit("r", "red", pos=(1e8, 0, 0), **({"engagement": red_engage} if red_engage else {})),
             unit("b", blue_faction, pos=(1e8 + gap_km, 0, 0), **({"engagement": blue_engage} if blue_engage else {}))]
    cfg = load_config(write_scenario(d, scenario={"duration_s": duration}, units=units), REPO)
    sim = Simulation(cfg)
    out = Path(d) / "o"
    sim.run(out)
    return sim, out, jsonl(out / "events.jsonl")


FIRE = {"mode": "fire_at_will", "missile": "standard_nuclear"}


class Firing(unittest.TestCase):
    def test_hold_fire_is_default(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = duel(d)
            self.assertFalse([e for e in ev if e["type"] == "missile_launch"])

    def test_out_of_range_does_not_fire(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, ev = duel(d, gap_km=1e7, red_engage=FIRE)        # 1e10 m >> 1.9e9 m powered reach
            self.assertFalse([e for e in ev if e["type"] == "missile_launch"])

    def test_in_range_fires_salvos_with_cycle_and_magazine_accounting(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _, ev = duel(d, red_engage=FIRE)
            launches = [e for e in ev if e["type"] == "missile_launch"]
            self.assertTrue(launches)
            times = sorted({e["t"] for e in launches})
            self.assertEqual(times[0], 0.0)
            self.assertTrue(all(b - a >= 30.0 for a, b in zip(times, times[1:])))        # doctrine salvo interval
            per_salvo = [sum(1 for e in launches if e["t"] == t) for t in times]
            self.assertTrue(all(n == 10 for n in per_salvo))                            # doctrine salvo_size
            self.assertEqual(sim.batteries[0][0]["magazine"], 1200 - len(launches))
            self.assertTrue(all(e["actor_id"] == "r-001" and e["data"]["target_id"] == "b-001" for e in launches))

    def test_same_faction_is_never_targeted_or_hit(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _, ev = duel(d, red_engage=FIRE, blue_faction="red")
            self.assertFalse([e for e in ev if e["type"] in ("missile_launch", "detonation")])
            self.assertEqual(list(sim.integrity), [1.0, 1.0])


class Damage(unittest.TestCase):
    def test_hits_follow_linear_damage_rule(self):
        with tempfile.TemporaryDirectory() as d:
            sim, out, ev = duel(d, red_engage=FIRE)
            dets = [e for e in ev if e["type"] == "detonation"]
            self.assertTrue(dets, "expected at least one detonation")
            integrity = 1.0
            for e in dets:
                r, dmg = e["data"]["range_m"], e["data"]["damage"]
                self.assertLess(r, 1000.0)
                self.assertAlmostEqual(dmg, 0.5 * (1 - r / 1000.0), places=5)
                self.assertAlmostEqual(e["data"]["integrity_before"], integrity, places=5)
                integrity *= 1 - dmg
                self.assertAlmostEqual(e["data"]["integrity_after"], integrity, places=5)
                self.assertEqual(e["data"]["target_id"], "b-001")
            self.assertAlmostEqual(sim.integrity[1], integrity, places=4)
            self.assertEqual(sim.integrity[0], 1.0)
            self.assertLess(sim.integrity[1], 1.0)
            self.assertGreater(sim.integrity[1], 0.0)          # damage never quite reaches zero in one step
            last = states(out)[-1]
            ship = [o for o in last["objects"] if o["id"] == "b-001"][0]
            self.assertAlmostEqual(ship["integrity"], sim.integrity[1], places=5)

    def test_missiles_are_accounted_for(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _, ev = duel(d, red_engage=FIRE)
            n = lambda t: sum(1 for e in ev if e["type"] == t)
            self.assertEqual(n("missile_launch"), len(sim.swarm) + n("detonation") + n("missile_expired"))

    def test_missile_flight_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, _ = duel(d, red_engage=FIRE)
            rows = jsonl(out / "missiles.jsonl")
            self.assertTrue(rows)
            self.assertEqual(rows[0]["t"], 0.0)
            self.assertEqual(len(rows[0]["missiles"]), 10)


class Determinism(unittest.TestCase):
    def test_identical_bytes_with_missiles(self):
        outs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                _, out, _ = duel(d, red_engage=FIRE, blue_engage=FIRE)
                outs.append([(out / n).read_bytes() for n in ("state.jsonl", "events.jsonl", "missiles.jsonl", "metadata.json")])
        self.assertEqual(outs[0], outs[1])


class Config(unittest.TestCase):
    def test_unsupported_and_unknown_missiles_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for name, msg in (("xray_laser", "only nuclear warheads"), ("nope", "not carried")):
                u = unit("g", engagement={"mode": "fire_at_will", "missile": name})
                with self.assertRaises(ConfigError) as cm:
                    load_config(write_scenario(d, units=[u]), REPO)
                self.assertIn(msg, str(cm.exception))

    def test_bad_mode(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConfigError):
                load_config(write_scenario(d, units=[unit("g", engagement={"mode": "yolo"})]), REPO)


if __name__ == "__main__":
    unittest.main()
