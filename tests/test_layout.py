import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from simulator import cli, paths
from simulator.config import load_config
from simulator.errors import ConfigError
from simulator.render import shotlist

from .helpers import REPO, unit, write_scenario


class Layout(unittest.TestCase):
    def tearDown(self):
        paths.set_work(None)

    def test_scenarios_are_named_by_their_folder(self):
        ids = paths.scenario_ids()
        self.assertIn("incursion03", ids)
        for sid in ids:                                            # the folder is the id, everywhere
            doc = yaml.safe_load(paths.scenario_file(sid).read_text())
            self.assertEqual(doc["scenario"]["id"], sid)
        self.assertEqual(paths.scenario_file("incursion03"), REPO / "scenarios" / "incursion03" / "scenario.yaml")
        self.assertEqual(paths.scenario_file(REPO / "scenarios" / "incursion03"), paths.scenario_file("incursion03"))
        with self.assertRaises(FileNotFoundError) as e:
            paths.scenario_file("no_such_scenario")
        self.assertIn("incursion03", str(e.exception))             # the message lists what there is

    def test_a_scenario_whose_id_differs_from_its_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = yaml.safe_load(write_scenario(d, units=[unit("a")]).read_text())
            for folder, ok in (("t", True), ("other", False)):
                p = Path(d) / "scenarios" / folder / "scenario.yaml"
                p.parent.mkdir(parents=True)
                p.write_text(yaml.safe_dump(src))
                if ok:
                    self.assertEqual(load_config(p, REPO).scenario["id"], "t")
                else:
                    with self.assertRaises(ConfigError) as e:
                        load_config(p, REPO)
                    self.assertIn("must match its folder", str(e.exception))

    def test_every_shot_list_loads_and_sits_under_its_scenario(self):
        found = [(sid, name) for sid in paths.scenario_ids() for name in paths.shot_lists(sid)]
        self.assertIn(("incursion03", "arrival"), found)
        for sid, name in found:
            self.assertTrue(shotlist.load(paths.shots_file(sid, name))["shots"])
        with self.assertRaises(FileNotFoundError) as e:
            paths.shots_file("incursion03", "nothing")
        self.assertIn("arrival", str(e.exception))

    def test_built_files_go_to_the_working_directory(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"STARSHIP_WORK": d}):
                self.assertEqual(paths.work_dir(), Path(d).resolve())
                paths.set_work(Path(d) / "elsewhere")              # --work wins over the environment
                self.assertEqual(paths.work_dir(), (Path(d) / "elsewhere").resolve())
                paths.set_work(None)
                w = Path(d).resolve()
                self.assertEqual(paths.run_dir("x"), w / "runs" / "x")
                self.assertEqual(paths.render_dir("x", "arrival"), w / "renders" / "x" / "arrival")
                self.assertEqual(paths.render_dir("x", "arrival", "preview"), w / "renders" / "x" / "arrival.preview")
                self.assertEqual(paths.film_path("x", "arrival", "preview"), w / "films" / "x_arrival.preview.mp4")
            with mock.patch.dict(os.environ, clear=False) as env:
                env.pop("STARSHIP_WORK", None)
                self.assertEqual(paths.work_dir(), Path.home() / "starshiptactics-work")
                self.assertFalse(paths.work_dir().is_relative_to(REPO))      # never inside the checkout

    def test_run_command_writes_the_run_and_a_changed_scenario_makes_it_stale(self):
        with tempfile.TemporaryDirectory() as d:
            scen = write_scenario(d, scenario={"duration_s": 20}, units=[unit("a")])
            work = Path(d) / "work"
            self.assertEqual(cli.main(["--work", str(work), "run", str(scen), "--root", str(REPO)]), 0)
            run = work / "runs" / "t"
            self.assertTrue((run / "state.jsonl").is_file() and (run / "metadata.json").is_file())
            self.assertEqual(paths.find_run(run), ("t", run))
            self.assertEqual(paths.find_run("t"), ("t", work.resolve() / "runs" / "t"))
            cfg = load_config(scen, REPO)
            self.assertEqual(paths.run_is_current(cfg, run), (True, ""))
            self.assertEqual(paths.run_is_current(cfg, work / "runs" / "none")[0], False)
            meta = json.loads((run / "metadata.json").read_text())
            (run / "metadata.json").write_text(json.dumps({**meta, "code_sha256": "an older simulator"}))
            self.assertIn("code", paths.run_is_current(cfg, run)[1])
            (run / "metadata.json").write_text(json.dumps(meta))
            scen.write_text(scen.read_text() + "\n# edited\n")
            self.assertIn("scenario", paths.run_is_current(load_config(scen, REPO), run)[1])
            self.assertEqual(cli.main(["--work", str(work), "run", "no_such_scenario"]), 2)

    def test_film_profiles(self):
        spec = {"film": {"fps": 24, "resolution": [1280, 720], "watermark": "me",
                         "profiles": {"preview": {"fps": 12, "resolution": [640, 360]}}}}
        self.assertEqual(shotlist.film_settings(spec)["fps"], 24)
        self.assertNotIn("profiles", shotlist.film_settings(spec))
        small = shotlist.film_settings(spec, "preview")
        self.assertEqual((small["fps"], small["resolution"], small["watermark"]), (12, [640, 360], "me"))
        self.assertEqual(shotlist.film_settings(spec, "preview", fps=6)["fps"], 6)          # the command line still wins
        with self.assertRaises(shotlist.ShotListError):
            shotlist.film_settings(spec, "huge")
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.yaml"
            bad.write_text(yaml.safe_dump({"film": {"profiles": {"preview": {"colour": "red"}}}, "shots": [{"follow": "x"}]}))
            with self.assertRaises(shotlist.ShotListError):
                shotlist.load(bad)


if __name__ == "__main__":
    unittest.main()
