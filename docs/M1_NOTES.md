# Milestone 1 implementation notes

Choices made where the spec was silent (tunables live in `rules/*.yaml`, not code):

- Orbits: node / periapsis default to 0 (warned); missing eccentricity/inclination default to 0 (warned);
  Triton's negative period is read as |period| (its 156.9 deg inclination already makes it retrograde).
  Inclinations are relative to the ecliptic, not the parent's orbit plane.
- Gravity acts on ships from all bodies of the types listed in the system file (spec 7.2, not full N-body).
- Guidance: `transit_to_point` flies a braking-curve profile and stops at the destination. A destination inside
  a body's exclusion zone is moved to 1.05x the zone boundary (event `destination_adjusted`). Group ships keep
  their formation offset so they do not converge onto one point. Local gravity is feed-forward compensated.
- Safety: each step, the "coast while turning, then brake to rest" path is checked against every body's
  exclusion sphere (moving bodies); a violation triggers brake + lateral avoidance. Ship-ship: straight-line
  closest approach within the prediction window; avoidance pushes apart. Avoidance overrides objectives.
- Ships reason about own-faction ships with truth state and about other factions only through light-delayed
  tracks (`sensors/lightdelay.py`). Own-faction comms delay is not modelled yet.
- Rotation uses the 2 deg/s simulation default; thrust is applied only when the axis is within the alignment
  tolerance, using the component of the command along the actual axis.

Known limitations: no path planning around bodies (a destination behind a planet can cause stop/start at the
zone edge); station standoff is configured but no stations exist to enforce it; hyper-limit transitions, damage,
missiles and grasers are not implemented (weapon objectives raise a ConfigError listing unresolved values).

## Orbits (added after the first milestone-1 pass)

Scenario syntax (`scenarios/earth_orbit.yaml` is a worked example; `body` is a key from `systems/*.yaml`):

```yaml
- group: blue_picket            # start already in orbit (replaces position_km / velocity_km_s)
  orbit:
    body: earth
    altitude_km: 20000          # or radius_km (from the centre)
    inclination_deg: 0          # relative to the ecliptic; also longitude_of_ascending_node_deg
    direction: prograde         # or retrograde
    phase_deg: 90               # optional; omitted = seeded random, derived from seed + group id
    spacing_km: 500             # optional along-track spacing (default: rules/simulation.yaml)

- group: red_bc_group           # fly there and enter orbit
  objective: {type: transit_to_orbit, body: earth, altitude_km: 30000, direction: prograde}
```

- Orbits are circular and use only the body's own gravitational parameter; the Sun and other bodies still act
  on the ships, so an orbit slowly drifts (blue, 20,000 km altitude: about 6 km in 6 h).
- An orbit inside the body's exclusion zone (x `exclusion_destination_margin`) is a ConfigError.
- `transit_to_orbit`: brake to rest relative to the body at an anchor point on the orbit sphere (fixed when the
  group first navigates, each ship keeping its formation offset), wait until the whole group is ready, then burn
  together to circular speed. Complete when the actual orbit's semi-major axis and eccentricity are within
  `orbit_radius_tolerance_frac` / `orbit_eccentricity_tolerance` (0.5% each), then the ships coast.
- The insertion approach point is the direction the group first sees the body from, not an optimised
  intercept; there is no choice of plane other than the `inclination_deg` / node given.
- Integration is now velocity Verlet with gravity re-evaluated at the new positions.
- Ship-ship prediction now extrapolates each ship with its current thrust (so braking ships are predicted to
  stop), acts only at the last responsible moment, and ignores groupmates unless they are within 1.5x the
  minimum separation. Unknown thrust of other factions' ships is taken as zero.

## Missiles and damage (first pass)

Scenario syntax (per group; default is `hold_fire`):

```yaml
engagement:
  mode: fire_at_will          # or hold_fire
  missile: standard_nuclear   # must be carried by the ship class; only nuclear warheads are implemented
  salvo_size: 10              # optional; default from rules/doctrine.yaml
```

- **Flight:** launched from the ship's position and velocity; burns at the class acceleration for `powered_duration_s`
  steering at the predicted intercept of the target *track* (light-delayed faction information, refreshed every
  step while missiles fly, extrapolated at constant velocity; no separate seeker or datalink delay). After burnout
  it coasts. Gravity on missiles is ignored. A missile that has not detonated is removed
  `ballistic_expiry_s` after burnout.
- **Detonation and damage:** at the point of closest approach, if within `damage_radius_km` (1 km) of a hostile ship:
  `damage = max_damage x (1 - range / damage_radius)`, `integrity *= (1 - damage)` (`max_damage` 0.5). Integrity is
  1.0 undamaged, approaching 0 at complete structural failure. It is recorded in `state.jsonl`, `detonation`
  events and `metadata.json`, but does NOT yet change any ship capability (mapping damage to capability is open).
  Closest approach is solved exactly within each step because closing speeds are millions of m/s.
- **Firing (`rules/doctrine.yaml`, all PLACEHOLDERS):** each ready battery fires `salvo_size` (capped by tubes,
  magazine and room) at the nearest hostile track inside `fire_range_fraction` (0.8) of the missile's powered reach,
  at most every `salvo_interval_s` (30 s, and >= the battery cycle time). A target is skipped while
  `max_missiles_in_flight_per_target` (20) are already aimed at it, or once its integrity is <=
  `assumed_dead_integrity` (0.05, using true integrity, i.e. perfect battle damage assessment).
- **Output:** `missiles.jsonl` (in-flight missiles every `missile_output_interval_s`, default 2 s) and events
  `missile_launch`, `missile_burnout`, `detonation`, `missile_expired`. The viewer draws missiles, detonation
  flashes and fades damaged ships.
- **Limits:** no evasion, point defence, defensive-field effect, or friendly fire; a target that thrusts hard
  after the missile's last track update will be missed (hits of the coasting/orbiting ships in the examples land
  within ~100 m). Only the missile's own warhead radius is used, so nothing else about the warhead is required.
