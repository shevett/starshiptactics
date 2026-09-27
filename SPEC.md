# Starship Tactics Simulation Specification

## 1. Purpose

Build a deterministic 3D space-combat simulation whose authoritative output can drive two visualizations:

1. a tactical plot showing contacts, vectors, ranges, sensor delays, weapon envelopes, and events; and
2. a cinematic Blender renderer showing the same already-computed simulation from arbitrary camera viewpoints.

The simulator owns physics, navigation, sensors, weapons, doctrine, and state. Blender and tactical viewers are read-only consumers of simulation output.

The initial design is inspired by large-scale military space opera, especially combat involving high-acceleration capital ships, missile salvos, direct-fire energy weapons, defensive gravity geometry, and meaningful light-speed sensor delay.

## 2. Architectural rule: classes vs instances

Reusable capabilities MUST be defined separately from scenarios.

A scenario may say "place five battlecruisers here". It MUST NOT repeat the battlecruiser's propulsion, sensors, weapon loadout, dimensions, magazine size, or defensive geometry.

Reusable definitions live in `classes/`. Individual objects created for a battle live in `scenarios/` and runtime state.

## 3. Configuration layers

### 3.1 `systems/*.yaml`

Static astronomical environment definitions:

- stars
- planets
- moons
- selected dwarf planets / asteroids
- mass
- radius
- standard gravitational parameter
- orbital elements
- parent relationships
- optional fictional system boundaries such as a hyper limit

The system file is epoch-independent. Orbital phase is supplied by the scenario or generated deterministically from a scenario seed.

### 3.2 `classes/ships.yaml`

Reusable ship classes. A ship class may define:

- dimensions
- optional mass
- maximum acceleration
- allowed primary thrust axis
- rotation capability
- sensors and communications
- defensive geometry
- missile batteries / tubes
- missile compatibility
- launcher cycle time
- magazine capacity
- direct-fire weapons

Mass is not required to compute ship motion when capability is specified directly as acceleration. It is retained for future collision, damage, thrust, or momentum models.

### 3.3 `classes/missiles.yaml`

Reusable missile and direct-fire weapon classes.

Missile class fields include:

- acceleration
- powered-flight duration
- seeker properties
- post-burn ballistic behavior
- warhead type

Two initial missile warhead families are supported conceptually:

- nuclear / blast-radius warheads
- X-ray-laser warheads that generate multiple directed beams at detonation

Direct-fire energy weapons such as grasers are also reusable classes. In vacuum their practical effectiveness is primarily limited by beam divergence / spot size rather than atmospheric absorption.

### 3.4 `classes/stations.yaml`

Reusable station/platform definitions. This may remain empty in milestone 1, but the loader MUST tolerate an empty class file.

### 3.5 `rules/navigation.yaml`

Global operational safety constraints, separate from both ship capabilities and tactical doctrine.

Initial rules:

- minimum ship-to-ship separation: 100 km
- configurable station standoff
- configurable celestial-body surface clearance
- trajectories may not intersect celestial bodies
- projected-path collision checking
- main propulsion is constrained to the ship's longitudinal axis unless a ship class says otherwise
- changing the main thrust vector requires changing ship orientation first
- a ship may not exceed its class acceleration limit
- safety avoidance has precedence over ordinary navigation orders

### 3.6 `rules/doctrine.yaml`

Deterministic combat decision rules. Doctrine expresses what a ship or formation WANTS to do; navigation determines whether and how it can do it safely.

Initial doctrine is intentionally thin. It will expand after the physics/navigation core works.

### 3.7 `scenarios/*.yaml`

A scenario defines a particular engagement:

- system
- deterministic random seed
- start time
- fixed simulation time step
- maximum duration
- factions
- groups / instances
- initial positions
- initial velocities
- initial orientations
- class references
- tactical objectives
- rules of engagement
- termination conditions

A group with `count: N` expands into N uniquely identified runtime objects.

## 4. Coordinate system and units

Internal simulation calculations MUST use SI units:

- position: meters
- velocity: meters/second
- acceleration: meters/second^2
- time: seconds
- mass: kilograms
- angles: radians

YAML may use documented convenience units such as km, km/s, g, degrees, and days. Convert to SI during load/validation.

Use a right-handed 3D coordinate system. System definitions currently use a heliocentric ecliptic J2000-style frame for astronomical data.

`1 g = 9.80665 m/s^2`.

`c = 299792458 m/s`.

## 5. Determinism

Given identical input files and the same `random_seed`, the simulator MUST produce identical object IDs, orbital phase choices, navigation decisions, events, and output.

Any randomized orbital phase, formation jitter, or future stochastic element MUST use a seeded PRNG controlled by the scenario.

## 6. Simulation clock

Milestone 1 uses a fixed-step simulation.

Default scenario parameters:

- `time_step_s: 1.0`
- `duration_s: 21600` (6 hours)
- `snapshot_interval_s: 10.0`

The values are scenario-configurable.

The engine may later add adaptive substeps for close approaches or weapons, but deterministic fixed-step behavior is the baseline.

## 7. Astronomy and gravity

### 7.1 Orbital initialization

For each orbiting body:

- if the scenario provides orbital phase / true anomaly, use it;
- otherwise derive a deterministic random phase from `random_seed`.

Missing inclination on a secondary body means `0` relative to its parent orbit plane for milestone 1, with a validation warning rather than failure.

### 7.2 Body motion

Milestone 1 SHOULD initialize bodies from their orbital elements and propagate their parent-relative positions using Keplerian two-body motion.

Spacecraft receive gravitational acceleration from configured celestial bodies.

Full mutual N-body integration of the planets themselves is NOT required for milestone 1. The `gravity_model` field in the current Solar System YAML should be interpreted as gravity sources acting on simulated craft, not a requirement to numerically integrate every planet against every other planet.

This choice keeps the initial implementation stable and deterministic while retaining meaningful local gravity.

## 8. Ship motion

Each ship runtime state contains at minimum:

- id
- class_id
- faction_id
- position vector
- velocity vector
- acceleration vector
- orientation quaternion or equivalent
- angular velocity
- current maneuver/order
- target / destination if any
- weapon state
- sensor-track state
- damage state placeholder

Primary drive acceleration is capability-based. Example: a class with `max_acceleration_g: 450` can command up to 450 g along its permitted thrust axis.

The ship's mass does not change that acceleration in milestone 1.

## 9. Orientation and thrust

The ship has a body-forward longitudinal axis.

If primary propulsion is longitudinal-only:

1. navigation selects a desired acceleration vector;
2. attitude control rotates the ship toward the required orientation;
3. primary acceleration is applied only when the actual ship axis is sufficiently aligned with the requested thrust vector.

Milestone 1 requires a numeric rotation capability. Until canon-specific values are supplied, use a clearly marked simulation default from `rules/simulation.yaml`, not a hidden constant in code.

## 10. Navigation safety

The navigation module evaluates candidate motion against rules before issuing acceleration commands.

At every step it must check:

- predicted closest approach to other ships within the configured prediction window
- celestial-body intersections
- celestial exclusion zones
- station standoff
- acceleration capability
- orientation/thrust-axis constraints

If a requested maneuver would violate a hard rule, navigation produces a deterministic avoidance maneuver. The algorithm may initially be simple and conservative.

## 11. Sensors and light delay

Truth state and perceived state MUST be separate.

An observer at position `A` cannot observe an event at position `B` before the light-travel delay:

`delay = distance(A,B) / c`

The simulator therefore maintains:

- authoritative object state
- observations generated from past authoritative state
- faction-specific contact tracks
- observation timestamp
- receive timestamp

Milestone 1 may assume perfect detection once signal/light arrives. Detection range, uncertainty, stealth, and electronic warfare are later milestones.

This light-delay architecture is required from the beginning so tactical decisions never accidentally use omniscient current state.

## 12. Weapons architecture

### 12.1 Missile launcher capability belongs to the ship

A ship definition owns:

- launcher/battery id
- tube count
- cycle time
- compatible missile classes
- magazine capacity

If a battery has 20 tubes and a 12-second cycle, it may launch at most 20 missiles per firing cycle, subject to remaining magazine inventory.

### 12.2 Missile performance belongs to the missile class

A missile class owns:

- acceleration
- powered duration
- seeker parameters
- ballistic behavior after burnout
- warhead

Powered flight uses the missile's acceleration capability. After powered duration expires, the missile coasts ballistically unless another behavior is explicitly defined.

### 12.3 Combat values not yet specified

Several weapon fields intentionally remain `null`. These are NOT to be silently invented by the implementation.

The simulator must:

- validate that required values exist before enabling the corresponding combat feature;
- permit physics/navigation/sensor-light-delay simulation without complete combat values;
- emit a clear configuration error if a scenario orders a weapon whose required class values are unresolved.

The implementation may include explicitly labeled test fixtures/defaults for unit tests, but these MUST NOT masquerade as canon values.

## 13. Defensive geometry

Ship classes may define directional defensive regions:

- dorsal wedge: strongest
- ventral wedge: strongest
- port sidewall: weaker
- starboard sidewall: weaker
- bow / stern: vulnerable or unshielded arcs

The exact strength/damage numbers are not yet defined.

Geometry belongs in the ship class. Tactical use belongs in doctrine.

Example doctrine behavior:

- when a dangerous inbound missile volley is detected, prefer an orientation that places the strongest defensive geometry between the ship and the threat;
- this maneuver may conflict with offensive weapon geometry or desired acceleration, creating a tactical tradeoff.

Milestone 1 stores and visualizes defensive geometry but does not calculate damage through it.

## 14. Grasers / direct-fire energy weapons

A graser is modeled as a speed-of-light direct-fire weapon.

Potential class fields:

- beam energy per shot
- firing interval
- aperture
- beam divergence
- maximum effective range
- damage falloff model

For a simple physical model, beam spot radius grows approximately with range according to divergence, and energy density falls with illuminated area.

Canon-specific values are not yet supplied. As with missiles, unresolved values block actual damage computation but do not block the core simulator.

## 15. Doctrine

Doctrine is deterministic and configuration-driven.

It should eventually cover:

- target selection
- range preference
- intercept / retreat
- formation behavior
- missile salvo size
- broadside timing
- concentrate / divide fire
- defensive orientation
- direct-fire decisions

For milestone 1, doctrine may be limited to movement objectives and defensive-orientation hooks.

Do NOT introduce machine learning or a general LLM into the simulation loop.

## 16. Scenario schema requirements

Each scenario MUST include:

```yaml
scenario:
  id: example_engagement
  system: solar_system
  random_seed: 12345
  time_step_s: 1.0
  duration_s: 21600
  snapshot_interval_s: 10
  orbital_phase_strategy: random
```

It may define factions:

```yaml
factions:
  - id: red
  - id: blue
```

Unit groups reference reusable classes:

```yaml
units:
  - group: red_bc_group
    faction: red
    class: battlecruiser
    count: 5
    position_km: {x: 125000000, y: 2000000, z: 0}
    velocity_km_s: {x: -1200, y: 0, z: 0}
    orientation:
      forward_vector: [1, 0, 0]
    formation:
      type: line_abreast
      spacing_km: 200
    objective:
      type: transit_to_point
      destination_km: [0, 0, 0]
```

Termination conditions may initially be:

- duration reached
- all objectives complete
- explicit scripted stop

## 17. Runtime object IDs

Expansion must be deterministic. Example:

`red_bc_group` with `count: 5` becomes:

- `red_bc_group-001`
- `red_bc_group-002`
- `red_bc_group-003`
- `red_bc_group-004`
- `red_bc_group-005`

## 18. Output contract

Output must be sufficient to replay and render the battle without rerunning tactical decision logic.

Use JSON Lines (`.jsonl`) for events and snapshots so long simulations can stream to disk.

### 18.1 `metadata.json`

Contains:

- simulator version
- scenario id
- source file hashes if practical
- random seed
- units
- start/end simulation times

### 18.2 `events.jsonl`

One JSON object per discrete event.

Required common fields:

```json
{
  "t": 382.0,
  "type": "missile_launch",
  "actor_id": "red_bc_group-001",
  "event_id": "evt-00001234",
  "data": {}
}
```

Event types may include:

- maneuver_command
- avoidance_maneuver
- sensor_observation_generated
- sensor_observation_received
- missile_launch
- missile_burnout
- weapon_fire
- detonation
- objective_complete

### 18.3 `state.jsonl`

Periodic authoritative snapshots.

Each row:

```json
{
  "t": 120.0,
  "objects": [
    {
      "id": "red_bc_group-001",
      "kind": "ship",
      "position_m": [0,0,0],
      "velocity_m_s": [0,0,0],
      "orientation_xyzw": [0,0,0,1]
    }
  ]
}
```

Snapshots are for replay/rendering and debugging, not tactical decision-making.

## 19. Tactical visualization

The tactical display should resemble a clean military tactical plot rather than a cinematic starfield.

Required visual concepts:

- color-coded factions
- distinct symbols/blips for ships, missiles, stations, and celestial bodies
- object labels / IDs
- velocity vectors
- optional acceleration vectors
- projected paths
- range rings / weapon envelopes when selected
- sensor observation age / delayed-track indication
- formation/group relationships
- event markers for launches/detonations
- scalable 2D ecliptic view first
- 3D view later

The tactical view MUST be driven entirely from simulator output.

## 20. Blender visualization

Blender is a cinematic renderer, not a simulator.

A Blender import script should eventually:

- read metadata/events/state output
- instantiate visual assets by class
- keyframe position and orientation
- render missile motion/trails
- render weapon effects
- render explosions
- support arbitrary camera placement
- optionally render overlays

The same simulation should be renderable repeatedly from different viewpoints without recalculating the battle.

## 21. Hyper-limit support

The system schema should support an optional fictional FTL boundary around a star, expressed as either:

- an explicit radius; or
- a derived rule tied to stellar properties.

For the Solar System template, keep this field optional and separate from real astronomical physics. A scenario may place ships on or outside that boundary and transition them into normal-space simulation at a specified time.

The core simulator does not need to simulate FTL travel; it only needs to support an arrival/transition event at the boundary.

## 22. Milestones

### Milestone 1 — Core deterministic simulator

Implement now:

1. YAML parsing and validation
2. unit normalization to SI
3. deterministic scenario/group expansion
4. seeded orbital phase generation
5. Keplerian celestial positions
6. spacecraft gravity acceleration
7. 3D ship kinematics
8. orientation and longitudinal thrust constraint
9. basic safe navigation and collision/exclusion checking
10. true state vs light-delayed observation events
11. metadata/events/state JSONL output
12. minimal command-line runner
13. tests for determinism and units

Milestone 1 DOES NOT require damage, missile combat, graser damage, EW, or sophisticated combat doctrine.

### Milestone 2 — Missile flight

- launcher cycle and magazine accounting
- missile objects
- powered phase
- ballistic phase
- simple intercept guidance
- launch / burnout / intercept events

No damage model required yet.

### Milestone 3 — Combat resolution

- nuclear blast model
- X-ray-laser warhead model
- graser model
- defensive geometry
- damage effects

Requires explicit numerical values or intentionally fictional defaults approved in config.

### Milestone 4 — Doctrine

- targeting
- salvos
- range control
- formation tactics
- defensive roll/orientation

### Milestone 5 — Visualization

- tactical viewer
- Blender importer
- cinematic assets/effects

## 23. Implementation constraints

- Python is preferred for the simulator.
- Do not hard-code reusable ship or weapon characteristics in Python.
- Keep physics, navigation, sensors, weapons, doctrine, and visualization modular.
- Favor transparent, inspectable algorithms over cleverness.
- Preserve determinism.
- Validate configuration aggressively and fail with useful messages.
- Missing future-combat values should not prevent running Milestone 1.

## 24. Open numerical questions

The following remain intentionally unresolved and should be configuration questions, not assumptions hidden in code:

- ship rotation rates / angular acceleration
- sensor detection ranges and uncertainty
- missile seeker range
- nuclear yield / damage radii
- X-ray-laser beam count/energy/divergence
- graser energy, aperture, divergence, fire rate, practical range
- numeric wedge/sidewall protection
- detailed damage model
- detailed Honorverse ship dimensions and weapon counts

See `docs/OPEN_QUESTIONS.md`.
