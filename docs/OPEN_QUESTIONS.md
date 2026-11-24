# Open Questions

These are deliberately NOT blockers for Milestone 1.

## Ship classes

- Canon/desired dimensions by class
- Rotation rates / angular acceleration
- Sensor parameters
- Communications parameters beyond lightspeed delay

## Missiles

- Seeker range
- Guidance behavior
- Nuclear yield / blast damage curve
- X-ray-laser beam count, energy, divergence, engagement geometry

## Grasers

- Beam energy
- Aperture
- Divergence
- Fire rate
- Practical maximum range

## Defensive fields

- Numeric wedge/sidewall strengths
- Exact protected arcs
- Damage interaction rules

## Sensors

- Detection range per ship class (currently one scenario-level placeholder, `sensors.detection_range_km`)
- Detection vs. signature (drive plumes, emissions, size) and line-of-sight
- Information speed (DECIDED direction, not built). Ordinary sensing (radar, lidar, passive IR) is lightspeed-limited, and
  that is what the simulator models now (light-delayed tracks). Gravitic sensors and their comms are treated as
  faster-than-light, as in the books, so a picket or sensor platform can relay what it sees to a receiver before the light
  reaches it. No scenario has gravitic sensors or pickets yet, so this changes nothing today. When it is built: sensor
  type per class, a propagation speed for gravitic links, and relay paths between platforms.
- `sensors.detection_range_km` is a sensitivity range (how far a sensor can pick a target out of the noise), not a
  speed-of-light limit; the light delay is separate and automatic. Per-class ranges (ideally in light-seconds) are still to do.

## Doctrine

- Target selection
- Salvo sizing
- Missile-defense behavior
- Range preferences
- Defensive-roll decision thresholds
- Formation geometry

The implementation should expose these as configuration rather than inventing hidden constants.
- Reaction-rule defaults: posture thresholds, standoff range, pursue limits (`rules/doctrine.yaml`, placeholders)
- Threat score for choosing the primary target (currently nearest matching contact)
- Withdrawal / leash conditions (`withdraw`, `max_departure_km`, low integrity / magazine)
