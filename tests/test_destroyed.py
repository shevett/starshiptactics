import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.config import load_config
from simulator.engine import Simulation

from .helpers import REPO, unit, write_scenario
from .test_core import states
from .test_hyper import ship


class Hulk(unittest.TestCase):
    def test_destroyed_ship_drifts_on_its_momentum(self):
        far = (5.0e10, 0, 0)
        with tempfile.TemporaryDirectory() as d:
            units = [unit("dead", "red", pos=far, vel=(2000, 0, 0), objective={"type": "transit_to_point", "destination_km": [1e7, 1e7, 0]}),
                     unit("live", "blue", pos=(0, 5.0e10, 0), vel=(2000, 0, 0), objective={"type": "transit_to_point", "destination_km": [1e7, 1e7, 0]})]
            cfg = load_config(write_scenario(d, scenario={"duration_s": 300}, units=units), REPO)
            sim = Simulation(cfg)
            sim.destroyed[0] = True
            out = Path(d) / "o"
            sim.run(out)
            rows = states(out)
            dead0, dead1 = ship(rows[0], sim.ids[0]), ship(rows[-1], sim.ids[0])
            live1 = ship(rows[-1], sim.ids[1])
            self.assertTrue(np.allclose(dead0["velocity_m_s"], dead1["velocity_m_s"], rtol=1e-3))   # no thrust: only gravity acts
            self.assertTrue(dead1.get("destroyed"))
            self.assertGreater(np.linalg.norm(np.array(live1["velocity_m_s"]) - [2.0e6, 0, 0]), 1.0e5)   # live ship manoeuvres
