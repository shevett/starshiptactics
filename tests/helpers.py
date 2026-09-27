import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def write_scenario(tmpdir, scenario=None, factions=("red", "blue"), units=None, termination=None):
    doc = {
        "scenario": {"id": "t", "system": "solar_system", "random_seed": 7, "time_step_s": 1.0,
                     "duration_s": 300, "snapshot_interval_s": 10, "orbital_phase_strategy": "random"},
        "factions": [{"id": f} for f in factions],
        "units": units or [],
        "termination": termination or [{"type": "duration_reached"}],
    }
    if scenario:
        doc["scenario"].update(scenario)
    p = Path(tmpdir) / "t.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def unit(group, faction="red", count=1, pos=(1e8, 0, 0), vel=(0, 0, 0), **kw):
    u = {"group": group, "faction": faction, "class": "battlecruiser", "count": count,
         "position_km": list(pos), "velocity_km_s": list(vel)}
    u.update(kw)
    return u
