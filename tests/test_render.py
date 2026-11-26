import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.render import hulls, shot, shotlist
from simulator.render.interp import Run, hermite, slerp

from .helpers import REPO, unit, write_scenario


class Interpolation(unittest.TestCase):
    def test_hermite_reproduces_constant_acceleration(self):
        a, h = np.array([3.0, -2.0, 0.5]), 10.0
        p0, v0 = np.array([1.0, 2.0, 3.0]), np.array([10.0, 0.0, -4.0])
        p1, v1 = p0 + v0 * h + 0.5 * a * h * h, v0 + a * h
        self.assertTrue(np.allclose(hermite(0.0, h, p0, v0, p1, v1), p0))
        self.assertTrue(np.allclose(hermite(1.0, h, p0, v0, p1, v1), p1))
        t = 4.0                                                    # a cubic is exact for constant acceleration
        self.assertTrue(np.allclose(hermite(t / h, h, p0, v0, p1, v1), p0 + v0 * t + 0.5 * a * t * t))

    def test_slerp_is_unit_and_takes_the_short_way(self):
        q0 = np.array([0.0, 0.0, 0.0, 1.0])
        q1 = np.array([0.0, 0.0, np.sin(np.pi / 4), np.cos(np.pi / 4)])      # 90 degrees about z (xyzw)
        mid = slerp(q0, q1, 0.5)
        self.assertAlmostEqual(float(np.linalg.norm(mid)), 1.0)
        self.assertTrue(np.allclose(mid, [0.0, 0.0, np.sin(np.pi / 8), np.cos(np.pi / 8)]))
        self.assertTrue(np.allclose(slerp(q0, -q1, 0.5), mid))               # -q is the same rotation


class Hulls(unittest.TestCase):
    def test_lathe_matches_class_dimensions(self):
        v, f, uv = hulls.lathe([[0.0, 0.5], [0.5, 1.0], [1.0, 0.5]], 1000.0, 200.0, 100.0, segments=16)
        v = np.array(v)
        self.assertTrue(np.allclose(v.max(axis=0) - v.min(axis=0), [1000.0, 200.0, 100.0]))
        self.assertTrue(np.allclose(v.mean(axis=0), 0.0, atol=1e-6))         # origin at the hull centre
        self.assertEqual(len(uv), len(v))
        self.assertTrue(all(max(face) < len(v) for face in f))

    def test_trace_finds_the_silhouette_of_a_side_view(self):
        prof, box = hulls.trace_profile(REPO / "images" / "dreadnought.png")
        r = np.array([p[1] for p in prof])
        self.assertAlmostEqual(float(r.max()), 1.0)
        self.assertGreater(r[len(r) // 2], r[5])                             # thicker amidships than at the neck
        self.assertGreater((box[2] - box[0]) / (box[3] - box[1]), 4.0)       # long and thin


class Shot(unittest.TestCase):
    def test_shot_follows_the_target_and_keeps_true_separations(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", pos=(5.0e10, 0, 0), vel=(2000, 0, 0), objective={"type": "coast"}),
                     unit("b", "blue", pos=(5.0e10, 3.0e5, 0), vel=(2000, 0, 0), objective={"type": "coast"})]
            cfg = load_config(write_scenario(d, scenario={"duration_s": 120}, units=units), REPO)
            sim = Simulation(cfg)
            out = Path(d) / "o"
            sim.run(out)
            run = Run(out)
            a, b = run.ships
            sh = shot.build(out, Path(d) / "shot", REPO, target=a, t0=10.0, t1=70.0, speed=15.0, fps=24, unit_m=1000.0)
            self.assertEqual(sh["frames"], 96)
            sa, sb = (next(s for s in sh["ships"] if s["id"] == i) for i in (a, b))
            self.assertTrue(np.allclose(sa["loc"], 0.0))                     # the target is the scene origin in every frame
            sep = np.linalg.norm(run.state(b, 40.0)["p"] - run.state(a, 40.0)["p"]) / 1000.0
            f = sh["times"].index(min(sh["times"], key=lambda t: abs(t - 40.0)))
            self.assertAlmostEqual(float(np.linalg.norm(sb["loc"][f])), sep, delta=sep * 1e-3)
            self.assertLess(max(abs(x) for s in sh["ships"] for p in s["loc"] for x in p), 1.0e6)   # small numbers: safe in float32
            self.assertTrue(json.loads((Path(d) / "shot" / "shot.json").read_text())["frames"] == 96)


class ShotList(unittest.TestCase):
    def test_three_of_four_time_settings_give_the_fourth(self):
        self.assertEqual(shotlist.solve_time(10.0, 16.0, 2.0, None), (10.0, 16.0, 2.0, 3.0))
        self.assertEqual(shotlist.solve_time(10.0, None, 2.0, 3.0), (10.0, 16.0, 2.0, 3.0))
        self.assertEqual(shotlist.solve_time(None, 16.0, 2.0, 3.0), (10.0, 16.0, 2.0, 3.0))
        self.assertEqual(shotlist.solve_time(10.0, 16.0, None, 3.0), (10.0, 16.0, 2.0, 3.0))
        for bad in ((10.0, 16.0, None, None), (10.0, 16.0, 2.0, 3.0), (16.0, 10.0, 2.0, None)):
            with self.assertRaises(shotlist.ShotListError):
                shotlist.solve_time(*bad)

    def test_watermark_is_optional(self):
        self.assertEqual(shotlist.film_settings({"film": {"watermark": "/u/someone"}})["watermark"], "/u/someone")
        for film in ({}, {"watermark": None}, {"watermark": ""}, {"watermark": "  "}):
            self.assertIsNone(shotlist.film_settings({"film": film})["watermark"])

    def test_times_are_numbers_or_anchors_with_offsets(self):
        anchors = {"kill": {"t": 100.0, "ship": "a"}, "m": {"t": 90.0, "launch": 40.0}}
        tm = lambda x: shotlist._time(x, anchors, {}, "test")
        self.assertEqual([tm(12), tm("12.5"), tm("kill"), tm("kill - 0.5"), tm("m.launch+2")], [12.0, 12.5, 100.0, 99.5, 42.0])
        for bad in ("nope", "kill.ship", "kill * 2", "m.nothing"):
            with self.assertRaises(shotlist.ShotListError):
                tm(bad)

    def test_camera_rises_eases_and_lets_go(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", pos=(5.0e10, 0, 0), vel=(-500, 0, 0), objective={"type": "coast"})]
            cfg = load_config(write_scenario(d, scenario={"duration_s": 120}, units=units), REPO)
            out = Path(d) / "o"
            Simulation(cfg).run(out)
            run = Run(out)
            cam = {"distance": 5, "azimuth": 30, "elevation": [10, 40]}
            spec = {"shots": [{"name": "rise", "follow": run.ships[0], "start": 10, "duration": 2.0, "speed": 1.0, "camera": {**cam, "ease": "out"}},
                              {"name": "go", "follow": run.ships[0], "start": 12, "duration": 4.0, "speed": 1.0,
                               "camera": {"rig": "release", "distance": 5, "azimuth": 30, "elevation": 40, "settle": 2}}]}
            sh = shot.build_sequence(run, Path(d) / "s", REPO, shotlist.plan(run, spec, REPO)["shots"])
            loc = np.array([c["loc"] for c in sh["camera"]])
            dist = np.linalg.norm(loc, axis=1)
            elev = np.degrees(np.arcsin(loc[:, 2] / dist))
            self.assertAlmostEqual(elev[0], 10.0, places=3)
            self.assertAlmostEqual(elev[47], 40.0, places=3)
            self.assertLess(elev[47] - elev[46], (elev[1] - elev[0]) / 10)                # eased out: nearly still at the end
            self.assertTrue(np.allclose(loc[48], loc[47], atol=1e-6))                      # the second shot picks up where the first stopped
            self.assertTrue(np.all(np.diff(dist[48:]) > 0))                                # then the ship runs away from the camera
            step = np.linalg.norm(loc[-1] - loc[-2])                                       # once at rest, the ship leaves at its full 500 km/s
            self.assertAlmostEqual(step, 500.0 / 24, delta=0.01)
            with self.assertRaises(shotlist.ShotListError):
                shotlist.plan(run, {"shots": [{**spec["shots"][0], "camera": {**cam, "ease": "bounce"}}]}, REPO)
            ramp = shotlist.plan(run, {"shots": [{**spec["shots"][0], "duration": 6.0, "speed": [1, 10]}]}, REPO)["shots"][0]["times"]
            step = np.diff(ramp) * 24
            self.assertAlmostEqual(step[0], 1.0, places=2)                                 # real time at first
            self.assertAlmostEqual(step[-1], 10.0, delta=0.2)                              # ten times as fast by the end
            self.assertTrue(np.all(np.diff(step) > 0))

    def test_a_list_is_resolved_against_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            units = [unit("a", "red", pos=(5.0e10, 0, 0), vel=(2000, 0, 0), objective={"type": "coast"})]
            cfg = load_config(write_scenario(d, scenario={"duration_s": 120}, units=units), REPO)
            out = Path(d) / "o"
            Simulation(cfg).run(out)
            run = Run(out)
            spec = {"anchors": {"mid": {"time": 60}},
                    "shots": [{"name": "one", "follow": run.ships[0], "start": "mid - 10", "duration": 2.0, "speed": 5.0,
                               "camera": {"distance": 3, "azimuth": 90}}]}
            plan = shotlist.plan(run, spec, REPO, fps=24)
            sp = plan["shots"][0]
            self.assertEqual((len(sp["times"]), sp["times"][0], sp["origin"]), (48, 50.0, ("ship", run.ships[0])))
            self.assertEqual(sp["rig"]["azimuth_deg"], [90.0, 90.0])
            self.assertEqual(shot.build_sequence(run, Path(d) / "s", REPO, plan["shots"])["frames"], 48)
            for change in ({"follow": "nobody"}, {"speeed": 1}, {"camera": {"rig": "crane"}}, {"camera": {"rig": "chase"}},
                           {"start": "later"}, {"start": 500}, {"end": 70}):
                with self.assertRaises(shotlist.ShotListError, msg=str(change)):
                    shotlist.plan(run, {**spec, "shots": [{**spec["shots"][0], **change}]}, REPO)
            with self.assertRaises(shotlist.ShotListError):                              # an anchor that finds nothing stops the render
                shotlist.plan(run, {**spec, "anchors": {"kill": {"event": "ship_destroyed", "ship": run.ships[0]}}}, REPO)


@unittest.skipUnless((REPO / "output" / "incursion02" / "missiles.jsonl").exists(), "needs a finished incursion02 run in output/")
class KillSequence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_ = Run(REPO / "output" / "incursion02")
        cls.plan = shotlist.plan(cls.run_, shotlist.load(shotlist.BUILTIN / "kill.yaml"), REPO, {"target": None})
        cls.killer = cls.plan["anchors"]["killer"]

    def test_missile_leaves_from_its_launcher(self):
        run, k = self.run_, self.killer
        at_launch = run.missile_pos(k["missile"], k["launch"])
        self.assertLess(np.linalg.norm(at_launch - run.state(k["launcher"], k["launch"])["p"]), 1.0)
        self.assertIsNone(run.missile_pos(k["missile"], k["launch"] - 1.0))
        self.assertIn(k["missile"], run.missiles_alive(k["launch"] + 0.5))               # before its first stored record

    def test_chase_closes_on_the_target_at_a_steady_rate(self):
        run, k = self.run_, self.killer
        ts = self.plan["shots"][1]["times"]
        self.assertTrue(all(b >= a for a, b in zip(ts, ts[1:])))
        self.assertLessEqual(ts[-1], k["impact"])
        rng = [np.linalg.norm(run.missile_pos(k["missile"], t) - run.state(k["target"], t)["p"]) for t in ts]
        ratio = np.array(rng[1:]) / np.array(rng[:-1])
        self.assertTrue(np.allclose(ratio, ratio[0], rtol=1e-3))                        # same shrink factor every frame
        self.assertEqual([s["name"] for s in self.plan["shots"]], ["launch", "chase", "kill"])

    def test_the_scenario_lists_resolve(self):
        # these files are edited by hand and the battle changes with the scenario, so only what must hold is checked
        mine = shotlist.plan(self.run_, shotlist.load(REPO / "scenarios" / "incursion02.shots.yaml"), REPO)
        built_in = shotlist.plan(self.run_, shotlist.load(shotlist.BUILTIN / "kill.yaml"), REPO, {"target": "red_majestics-003"})
        self.assertEqual(mine["anchors"]["killer"]["missile"], built_in["anchors"]["killer"]["missile"])
        self.assertEqual(len(mine["shots"]), 3)
        arrival = shotlist.plan(self.run_, shotlist.load(REPO / "scenarios" / "incursion02_arrival.shots.yaml"), REPO)
        self.assertLess(arrival["shots"][0]["times"][0], arrival["anchors"]["first"]["t"])      # opens before the first ship appears

    def test_a_ship_that_survived_stops_the_render(self):
        with self.assertRaises(shotlist.ShotListError):
            shotlist.plan(self.run_, shotlist.load(shotlist.BUILTIN / "kill.yaml"), REPO, {"target": "blue_defenders-001"})


if __name__ == "__main__":
    unittest.main()
