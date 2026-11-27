"""Where things live.

The checkout holds source only: code, data (classes/, rules/, systems/), reference art (images/) and scenarios, each
scenario a folder scenarios/<id>/ with its scenario.yaml and its shot lists in shots/.

Everything that is built goes to a working directory outside the checkout, and all of it can be rebuilt:

    <work>/runs/<scenario id>/                        a simulation run (state, missiles, events, metadata, report)
    <work>/renders/<scenario id>/<film>[.<profile>]/  shot data, the Blender scene, frames, a farm bake
    <work>/films/<scenario id>_<film>[.<profile>].mp4 finished films

<work> is --work on the command line, else $STARSHIP_WORK, else ~/starshiptactics-work.
"""
import hashlib
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCENARIO_FILE = "scenario.yaml"
_work = None


def set_work(path):
    """Use this working directory from now on (the --work option). None: back to the environment or the default."""
    global _work
    _work = Path(path).expanduser().resolve() if path else None


def work_dir():
    if _work is not None:
        return _work
    env = os.environ.get("STARSHIP_WORK")
    return Path(env).expanduser().resolve() if env else Path.home() / "starshiptactics-work"


def repo_root(start):
    """The folder above `start` that holds classes/ (the data a scenario is read against); this checkout if none does."""
    for d in Path(start).resolve().parents:
        if (d / "classes").is_dir():
            return d
    return REPO


def scenario_ids():
    return sorted(p.parent.name for p in (REPO / "scenarios").glob(f"*/{SCENARIO_FILE}"))


def scenario_file(ref):
    """A scenario file from its id (scenarios/<id>/scenario.yaml), its folder, or a path to any scenario file."""
    p = Path(ref)
    if p.is_file():
        return p
    if (p / SCENARIO_FILE).is_file():
        return p / SCENARIO_FILE
    named = REPO / "scenarios" / str(ref) / SCENARIO_FILE
    if named.is_file():
        return named
    raise FileNotFoundError(f"no scenario '{ref}': not a file, and not one of {', '.join(scenario_ids()) or '(none)'}")


def shot_lists(scenario_id):
    return sorted(p.stem for p in (REPO / "scenarios" / scenario_id / "shots").glob("*.yaml"))


def shots_file(scenario_id, name):
    p = REPO / "scenarios" / scenario_id / "shots" / f"{name}.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"no shot list '{name}' for {scenario_id}: it has {', '.join(shot_lists(scenario_id)) or 'none'} "
                                f"(scenarios/{scenario_id}/shots/)")
    return p


def run_dir(scenario_id):
    return work_dir() / "runs" / scenario_id


def _tag(film, profile):
    return f"{film}.{profile}" if profile else film


def render_dir(scenario_id, film, profile=None):
    return work_dir() / "renders" / scenario_id / _tag(film, profile)


def film_path(scenario_id, film, profile=None):
    return work_dir() / "films" / f"{scenario_id}_{_tag(film, profile)}.mp4"


def find_run(ref):
    """(scenario id, run directory) from a scenario id or from a path to a run directory."""
    p = Path(ref)
    if (p / "metadata.json").is_file():
        return json.loads((p / "metadata.json").read_text()).get("scenario_id", p.name), p
    return str(ref), run_dir(str(ref))


# The code a run's numbers depend on. Rendering, reporting and the command line do not change a run.
_NOT_SIMULATION = ("render", "report.py", "cli.py", "paths.py", "__main__.py")


def code_hash():
    """One hash over the simulator's own sources, to tell whether a stored run was made by this code."""
    h = hashlib.sha256()
    base = Path(__file__).resolve().parent
    for p in sorted(base.rglob("*.py")):
        rel = p.relative_to(base)
        if rel.parts[0] in _NOT_SIMULATION:
            continue
        h.update(str(rel).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def run_is_current(cfg, run):
    """(True, "") if the run in `run` was made from this scenario, this data and this code; else (False, why)."""
    meta = Path(run) / "metadata.json"
    if not meta.is_file() or not (Path(run) / "state.jsonl").is_file():
        return False, "no run yet"
    try:
        m = json.loads(meta.read_text())
    except ValueError:
        return False, "its metadata cannot be read"
    if m.get("source_file_sha256") != cfg.file_hashes:
        return False, "the scenario or its data files have changed"
    if m.get("code_sha256") != code_hash():
        return False, "the simulator's code has changed"
    return True, ""
