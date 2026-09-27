# starshiptactics

Deterministic tactical space-combat simulation and visualization project.

Start with [`SPEC.md`](SPEC.md).

The current design intentionally makes the core physics/navigation/sensor simulator implementable before canon-specific combat numbers are settled.

Useful files:

- `SPEC.md` — authoritative architecture and milestone definition
- `systems/solar_system.yaml` — reusable Solar System environment
- `classes/ships.yaml` — reusable ship class schema/example
- `classes/missiles.yaml` — missile and graser schema/examples
- `rules/navigation.yaml` — safety/navigation rules
- `rules/simulation.yaml` — deterministic simulation defaults/constants
- `rules/doctrine.yaml` — initial doctrine hooks
- `scenarios/example.yaml`, `scenarios/earth_orbit.yaml` — example scenarios (the latter shows orbits)
- `docs/CLAUDE_HANDOFF.md` — implementation handoff
- `docs/OPEN_QUESTIONS.md` — values intentionally left unresolved

## Running

```bash
python -m simulator validate scenarios/example.yaml
python -m simulator run scenarios/example.yaml        # writes output/<scenario id>/ (~45 s for the example)
python -m simulator serve --run output/example_engagement   # tactical viewer at http://localhost:8000/viewer/
python -m unittest discover -s tests -t .
```

Requires Python 3, PyYAML and NumPy. `viewer/index.html` is a read-only tactical plot (play/pause, speed,
scrub, pan/zoom, follow, trails, vectors, light-delayed faction tracks); it can also load the three output files
by file picker or drag-and-drop.

Milestone 1 is implemented. Simulator-side assumptions and limitations are in `docs/M1_NOTES.md`.
