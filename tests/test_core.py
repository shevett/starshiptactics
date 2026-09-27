import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.errors import ConfigError
from simulator.physics.dynamics import FORWARD, rotate, slew_toward
from simulator.physics.orbits import Ephemeris
from simulator.sensors.lightdelay import History, observe
from simulator.units import C, G0, pick_vector

from .helpers import REPO, unit, write_scenario


def run(tmp, **kw):
    cfg = load_config(write_scenario(tmp, **kw), REPO)
    sim = Simulation(cfg)
    out = Path(tmp) / "out"
    sim.run(out)
    return cfg, sim, out


def jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f]


def states(out):
    return jsonl(out / "state.jsonl")


class Units(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(G0, 9.80665)
        self.assertEqual(C, 299792458.0)

    def test_pick_vector_converts_km(self):
        v = pick_vector({"position_km": {"x": 1, "y": 2, "z": 3}}, {"position_km": 1000.0, "position_m": 1.0}, "w")
        self.assertEqual(v, (1000.0, 2000.0, 3000.0))

    def test_pick_vector_rejects_both(self):
        with self.assertRaises(ValueError):
            pick_vector({"position_km": [1, 2, 3], "position_m": [1, 2, 3]}, {"position_km": 1000.0, "position_m": 1.0}, "w")

    def test_class_accel_is_si(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(write_scenario(d, units=[unit("a")]), REPO)
            self.assertAlmostEqual(cfg.ship_classes["battlecruiser"].max_accel, 450 * G0)


class Config(unittest.TestCase):
    def test_group_ids_and_formation(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("red_bc_group", count=5, pos=(1e8, 0, 0), formation={"type": "line_abreast", "spacing_km": 200},
                     orientation={"forward_vector": [1, 0, 0]})
            sim = Simulation(load_config(write_scenario(d, units=[u]), REPO))     # initial state, before any step
            self.assertEqual(sim.ids, [f"red_bc_group-{i:03d}" for i in range(1, 6)])
            ys = sorted(sim.P[:, 1] / 1000)
            self.assertAlmostEqual(ys[1] - ys[0], 200.0, places=6)
            self.assertAlmostEqual(sum(ys), 0.0, places=6)     # centred on the group position

    def test_unknown_class_reports_useful_error(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("g"); u["class"] = "nope"
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[u]), REPO)
            self.assertIn("units[g].class", str(cm.exception))

    def test_unresolved_weapon_is_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            u = unit("g", engagement={"mode": "fire_at_will", "missile": "xray_laser"})
            with self.assertRaises(ConfigError) as cm:
                load_config(write_scenario(d, units=[u]), REPO)
            self.assertIn("unresolved values", str(cm.exception))

    def test_core_runs_with_null_combat_values(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, units=[unit("g")])          # battlecruiser has null weapon values; must still run


class Orbits(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(REPO / "scenarios" / "example.yaml")
        self.eph = Ephemeris(self.cfg.bodies, 1)

    def test_earth_returns_after_one_period(self):
        i = self.eph.index["earth"]
        T = self.cfg.bodies[i].period_s
        p0, _ = self.eph.at(0)
        p1, _ = self.eph.at(T)
        self.assertLess(np.linalg.norm(p1[i] - p0[i]), 1e3)

    def test_radius_within_peri_apo(self):
        i = self.eph.index["mars"]
        b = self.cfg.bodies[i]
        for t in np.linspace(0, b.period_s, 25):
            r = np.linalg.norm(self.eph.at(t)[0][i])
            self.assertTrue(b.a_m * (1 - b.e) - 1 <= r <= b.a_m * (1 + b.e) + 1)

    def test_moon_orbits_earth(self):
        i, j = self.eph.index["moon"], self.eph.index["earth"]
        p, _ = self.eph.at(12345.0)
        d = np.linalg.norm(p[i] - p[j])
        b = self.cfg.bodies[i]
        self.assertTrue(b.a_m * (1 - b.e) - 1 <= d <= b.a_m * (1 + b.e) + 1)

    def test_velocity_matches_finite_difference(self):
        i = self.eph.index["earth"]
        (p0, _), (p1, _), (_, v) = self.eph.at(999.0), self.eph.at(1001.0), self.eph.at(1000.0)
        np.testing.assert_allclose((p1[i] - p0[i]) / 2.0, v[i], rtol=1e-4)

    def test_phases_seeded(self):
        a = Ephemeris(self.cfg.bodies, 5).phases
        self.assertEqual(a, Ephemeris(self.cfg.bodies, 5).phases)
        self.assertNotEqual(a, Ephemeris(self.cfg.bodies, 6).phases)

    def test_phase_override_true_anomaly_zero_is_periapsis(self):
        eph = Ephemeris(self.cfg.bodies, 1, overrides={"mars": 0.0})
        i = eph.index["mars"]
        b = self.cfg.bodies[i]
        self.assertAlmostEqual(np.linalg.norm(eph.at(0)[0][i]), b.a_m * (1 - b.e), delta=1.0)


class Determinism(unittest.TestCase):
    def files(self, out):
        return [(out / n).read_bytes() for n in ("state.jsonl", "events.jsonl", "metadata.json")]

    def scenario_kwargs(self, seed):
        return dict(scenario={"random_seed": seed},
                    units=[unit("r", "red", 3, formation={"type": "line_abreast", "spacing_km": 150},
                                pos=(1e8, 0, 0), vel=(-100, 0, 0),
                                objective={"type": "transit_to_point", "destination_km": [9.9e7, 0, 0]}),
                           unit("b", "blue", 2, pos=(1e8, 5e5, 0), vel=(-100, 0, 0))])

    def test_identical_runs_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            _, _, o1 = run(d1, **self.scenario_kwargs(3))
            _, _, o2 = run(d2, **self.scenario_kwargs(3))
            self.assertEqual(self.files(o1), self.files(o2))

    def test_seed_changes_orbital_phase(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            _, _, o1 = run(d1, **self.scenario_kwargs(3))
            _, _, o2 = run(d2, **self.scenario_kwargs(4))
            self.assertNotEqual(states(o1)[0]["objects"][3]["position_m"], states(o2)[0]["objects"][3]["position_m"])


class LightDelay(unittest.TestCase):
    def test_stationary_target_delay_is_distance_over_c(self):
        p = np.array([[C, 0.0, 0.0]])      # exactly one light-second away
        h = History(20, 1, 1.0, p, np.zeros((1, 3)))
        for k in range(21):
            h.record(k, p, np.zeros((1, 3)))
        t_obs, pos, _, delay = observe(h, np.zeros(3), 0, 10.0)
        self.assertAlmostEqual(delay, 1.0, places=9)
        self.assertAlmostEqual(t_obs, 9.0, places=9)

    def test_observer_sees_past_position_of_moving_target(self):
        v = np.array([[1000.0, 0, 0]])
        p0 = np.array([[C, 0.0, 0.0]])
        h = History(20, 1, 1.0, p0, v)
        for k in range(21):
            h.record(k, p0 + v * k, v)
        t_obs, pos, _, delay = observe(h, np.zeros(3), 0, 10.0)
        self.assertLess(t_obs, 10.0)
        np.testing.assert_allclose(pos, p0[0] + v[0] * t_obs, rtol=1e-12)

    def test_track_in_state_is_delayed(self):
        with tempfile.TemporaryDirectory() as d:
            c = C * 2.0 / 1000.0                       # target 2 light-seconds away, in km
            _, _, out = run(d, scenario={"duration_s": 60},
                            units=[unit("r", "red", pos=(0, 5e6, 0)), unit("b", "blue", pos=(c, 5e6, 0))])
            last = states(out)[-1]
            trk = last["tracks"]["red"][0]
            self.assertAlmostEqual(last["t"] - trk["observed_t"], 2.0, places=3)
            ev = jsonl(out / "events.jsonl")
            self.assertTrue(any(e["type"] == "sensor_observation_received" for e in ev))


class Attitude(unittest.TestCase):
    def test_slew_rate_limited(self):
        q = np.array([0, 0, 0, 1.0])
        q2, rem = slew_toward(q, np.array([-1.0, 0, 0]), math.radians(2.0))
        self.assertAlmostEqual(rem, math.pi - math.radians(2.0), places=9)
        f = rotate(q2, FORWARD)
        self.assertAlmostEqual(math.degrees(math.acos(f[0])), 2.0, places=6)

    def test_ship_does_not_thrust_until_aligned(self):
        with tempfile.TemporaryDirectory() as d:
            # facing +x, ordered to go toward -x: needs a 180 degree turn (90 s at 2 deg/s) before any thrust
            _, _, out = run(d, scenario={"duration_s": 60},
                            units=[unit("r", pos=(1e8, 4e7, 0), orientation={"forward_vector": [1, 0, 0]},
                                        objective={"type": "transit_to_point", "destination_km": [5e7, 4e7, 0]})])
            for row in states(out):
                s = [o for o in row["objects"] if o["kind"] == "ship"][0]
                self.assertLess(np.linalg.norm(s["acceleration_m_s2"]), 1.0)   # gravity only (~0.6 m/s^2 at 1 AU)


class Navigation(unittest.TestCase):
    def test_acceleration_never_exceeds_class_limit(self):
        with tempfile.TemporaryDirectory() as d:
            _, sim, out = run(d, scenario={"duration_s": 400},
                              units=[unit("r", pos=(1e8, 0, 0), vel=(-500, 0, 0),
                                          objective={"type": "transit_to_point", "destination_km": [5e7, 0, 0]})])
            for row in states(out):
                s = [o for o in row["objects"] if o["kind"] == "ship"][0]
                self.assertLessEqual(np.linalg.norm(s["acceleration_m_s2"]), 450 * G0 + 1.0)

    def test_head_on_ships_keep_minimum_separation(self):
        with tempfile.TemporaryDirectory() as d:
            _, sim, out = run(d, scenario={"duration_s": 1200},
                              units=[unit("a", "red", pos=(1e8, 0, 0), vel=(-50, 0, 0), orientation={"forward_vector": [-1, 0, 0]},
                                          objective={"type": "transit_to_point", "destination_km": [9.9e7, 0, 0]}),
                                     unit("b", "red", pos=(9.9e7, 0, 0), vel=(50, 0, 0), orientation={"forward_vector": [1, 0, 0]},
                                          objective={"type": "transit_to_point", "destination_km": [1.0e8, 0, 0]})])
            worst = min(np.linalg.norm(np.array(r["objects"][-2]["position_m"]) - np.array(r["objects"][-1]["position_m"]))
                        for r in states(out))
            self.assertGreaterEqual(worst, 100e3)

    def test_never_enters_planet_exclusion_zone(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(write_scenario(d, scenario={"duration_s": 2500, "orbital_phase_strategy": "zero"},
                                             units=[unit("r", pos=(1.6e8, 0, 0), vel=(-60, 0, 0))]), REPO)
            sim = Simulation(cfg)
            i = sim.eph.index["earth"]
            ep, _ = sim.eph.at(0)
            # aim straight at Earth as it is at t=0 (Earth barely moves in 2500 s relative to its clearance? use its position)
            sim.P[0] = ep[i] + np.array([2e7, 0, 0])
            sim.V[0] = np.array([-60e3, 0, 0]) + sim.eph.at(0)[1][i]
            out = Path(d) / "o"
            sim.run(out)
            zone = sim.excl[i]
            for row in states(out):
                bodies = {o["id"]: o for o in row["objects"] if o["kind"] == "body"}
                s = [o for o in row["objects"] if o["kind"] == "ship"][0]
                dist = np.linalg.norm(np.array(s["position_m"]) - np.array(bodies["earth"]["position_m"]))
                self.assertGreater(dist, zone * 0.999)
            ev = jsonl(out / "events.jsonl")
            self.assertTrue(any(e["type"] == "avoidance_maneuver" for e in ev))


if __name__ == "__main__":
    unittest.main()


class HyperLimit(unittest.TestCase):
    def test_weber_table(self):
        from simulator.config import hyper_limit_light_minutes as hl
        for cls, expect in (("G0", 22.00), ("G2V", 21.12), ("K0", 17.60), ("M0", 13.20), ("M9", 9.24), ("F0", 26.40)):
            self.assertAlmostEqual(hl(cls), expect, places=9, msg=cls)

    def test_bad_class_rejected(self):
        from simulator.config import hyper_limit_light_minutes as hl
        with self.assertRaises(ValueError):
            hl("X1")

    def test_solar_system_value(self):
        cfg = load_config(REPO / "scenarios" / "example.yaml")
        self.assertAlmostEqual(cfg.hyper_limit_m, 21.12 * 60 * C, delta=1.0)
