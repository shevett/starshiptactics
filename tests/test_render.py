import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation
from simulator.render import hulls, shot
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


if __name__ == "__main__":
    unittest.main()
