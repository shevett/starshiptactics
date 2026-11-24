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
