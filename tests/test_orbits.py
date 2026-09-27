import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError

from .helpers import REPO, unit, write_scenario
from .test_core import jsonl, states

MU_EARTH = 3.98600435436e14


def orbit_unit(**orbit):
    spec = {"body": "earth", "altitude_km": 20000}
    spec.update(orbit)
    return {"group": "o", "faction": "blue", "class": "battlecruiser", "count": 3, "orbit": spec}


def earth_rel(row, sid):
    ob = {o["id"]: o for o in row["objects"]}
    return (np.array(ob[sid]["position_m"]) - np.array(ob["earth"]["position_m"]),
            np.array(ob[sid]["velocity_m_s"]) - np.array(ob["earth"]["velocity_m_s"]))


def elements(p, v):
    r = np.linalg.norm(p)
    a = 1 / (2 / r - v @ v / MU_EARTH)
    e = np.linalg.norm(np.cross(v, np.cross(p, v)) / MU_EARTH - p / r)
    return r, a, e


class InitialOrbit(unittest.TestCase):
    def test_placed_on_circular_orbit_and_stays_there(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(write_scenario(d, scenario={"duration_s": 7200}, units=[orbit_unit(spacing_km=400)]), REPO)
            sim = Simulation(cfg)
            sim.run(Path(d) / "o")
            rows = states(Path(d) / "o")
            r_expect = 6371e3 + 20000e3
            for sid in sim.ids:
                for row in (rows[0], rows[-1]):
                    r, a, e = elements(*earth_rel(row, sid))
                    self.assertAlmostEqual(r / r_expect, 1.0, delta=2e-3)
                    self.assertLess(e, 2e-3)                       # circular
            p0 = [earth_rel(rows[0], s)[0] for s in sim.ids]
            self.assertAlmostEqual(np.linalg.norm(p0[1] - p0[0]) / 1e3, 400.0, delta=0.5)   # along-track spacing

    def test_retrograde_and_inclination(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(write_scenario(d, scenario={"duration_s": 60}, units=[
                orbit_unit(direction="retrograde", inclination_deg=90, phase_deg=10)]), REPO)
            sim = Simulation(cfg)
            sim.run(Path(d) / "o")
            p, v = earth_rel(states(Path(d) / "o")[0], sim.ids[0])
            h = np.cross(p, v)
            self.assertAlmostEqual(abs(h[2]) / np.linalg.norm(h), 0.0, delta=1e-6)     # polar orbit: plane contains z
            cfg2 = load_config(write_scenario(d, scenario={"duration_s": 60}, units=[
                orbit_unit(direction="prograde", phase_deg=10)]), REPO)
            sim2 = Simulation(cfg2)
            sim2.run(Path(d) / "o2")
            p2, v2 = earth_rel(states(Path(d) / "o2")[0], sim2.ids[0])
            self.assertGreater(np.cross(p2, v2)[2], 0)                                     # prograde: counter-clockwise
            cfg3 = load_config(write_scenario(d, scenario={"duration_s": 60}, units=[
                orbit_unit(direction="retrograde", phase_deg=10)]), REPO)
            sim3 = Simulation(cfg3)
            sim3.run(Path(d) / "o3")
            p3, v3 = earth_rel(states(Path(d) / "o3")[0], sim3.ids[0])
            self.assertLess(np.cross(p3, v3)[2], 0)

    def test_random_phase_is_seeded(self):
        with tempfile.TemporaryDirectory() as d:
            def first(seed):
                cfg = load_config(write_scenario(d, scenario={"random_seed": seed, "duration_s": 10},
                                                 units=[orbit_unit()]), REPO)
                return Simulation(cfg).P.copy()
            np.testing.assert_array_equal(first(1), first(1))
            self.assertFalse(np.allclose(first(1), first(2)))

    def test_config_errors(self):
        with tempfile.TemporaryDirectory() as d:
            cases = {
                "exclusion zone": orbit_unit(altitude_km=100),
                "unknown body": orbit_unit(body="vulcan"),
                "exactly one of": orbit_unit(altitude_km=1000, radius_km=9000),
            }
            for msg, u in cases.items():
                with self.assertRaises(ConfigError, msg=msg) as cm:
                    load_config(write_scenario(d, units=[u]), REPO)
                self.assertIn(msg, str(cm.exception))
            u = orbit_unit(); u["position_km"] = [1, 2, 3]
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[u]), REPO)
            self.assertIn("replaces position_km", str(cm.exception))


class TransitToOrbit(unittest.TestCase):
    def test_arrives_and_circularises(self):
        with tempfile.TemporaryDirectory() as d:
            obj = {"type": "transit_to_orbit", "body": "earth", "altitude_km": 30000}
            cfg = load_config(write_scenario(d, scenario={"duration_s": 2500, "orbital_phase_strategy": "zero"},
                                             units=[unit("r", count=2, objective=obj,
                                                         formation={"type": "line_abreast", "spacing_km": 200})]), REPO)
            sim = Simulation(cfg)
            ep, ev_ = sim.eph.at(0)
            i = sim.eph.index["earth"]
            for k in range(2):
                sim.P[k] = ep[i] + np.array([3e8, 0.0, 0.0]) + sim.offset[k]
                sim.V[k] = ev_[i]
            sim.run(Path(d) / "o")
            events = jsonl(Path(d) / "o" / "events.jsonl")
            done = [e for e in events if e["type"] == "objective_complete"]
            self.assertEqual(len(done), 2)
            r_expect = 6371e3 + 30000e3
            row = states(Path(d) / "o")[-1]
            for sid in sim.ids:
                r, a, e = elements(*earth_rel(row, sid))
                self.assertAlmostEqual(r / r_expect, 1.0, delta=0.01)
                self.assertLess(e, 0.01)
            self.assertFalse([e for e in events if e["type"] == "avoidance_maneuver"])


if __name__ == "__main__":
    unittest.main()
