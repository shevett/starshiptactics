# Missions: detect -> engage -> objective

A group's behaviour is a small stack. From the bottom:

1. **Objective** - what the scenario wants (`transit_to_orbit`, `transit_to_point`, `hold_position`, `coast`).
2. **Engage** - pushed on top of the objective by a *reaction rule* when a hostile contact matches.
3. **Detect** - sensors produce the contacts (and `contact_gained` / `contact_lost` events) the rules look at.

When no rule matches (contact lost, destroyed, or out of the rule's range) the engage behaviour is dropped and the
group flies its objective again. Nothing else is needed to "resume the mission".

## Leaving the field: `transit_to_hyper_limit`

An objective (also the destination of the planned `withdraw` action) that flies the group out to the hyper limit and
removes each ship as it crosses (`hyper_departure`). `heading: [x, y, z]` fixes the direction; omitted, ships fly
straight out from the star. See SPEC section 21. Departed ships count as survivors ("escaped") in the report.

## Scenario syntax

```yaml
scenario:
  sensors:
    detection_range_km: 10000000       # optional; omitted = every ship is detectable at any range

units:
  - group: red_bc_group
    ...
    mission:
      profile: aggressive_transit      # optional named rule set from rules/doctrine.yaml
      objective:                       # the baseline (may also be given at group level, not both)
        type: transit_to_orbit
        body: earth
        altitude_km: 30000
      reactions:                       # optional; evaluated BEFORE the profile's rules
        - when: {hostile: fleeing, max_range_km: 3000000}
          do: {pursue: {max_km: 3000000}}
        - when: {hostile: closing}
          do: {engage: {standoff_km: 500000}}
```

`when.hostile` is `closing`, `fleeing`, `holding` or `any`; `max_range_km` (optional) limits which contacts match.

| `do` | effect |
|---|---|
| `engage` (`standoff_km`, `max_range_km`) | steer to the target's predicted position, come to rest relative to it at the standoff range |
| `pursue` (`max_km` required, `standoff_km`) | `engage` that is abandoned once the target is farther than `max_km` |
| `ignore` / `resume_objective` | stop evaluating rules; fly the objective (use to shield lower rules) |

Rules are evaluated top to bottom; the first rule with a matching contact wins. Among matching contacts the nearest is
chosen, and the group sticks with its current target while it still matches (no flip-flopping). Fire control prefers the
group's engage target over the nearest contact.

Unknown profiles, actions, parameters, or an objective given in two places are `ConfigError`s with the path of the
offending rule.

## Posture

A hostile track's posture is its range-rate relative to the group's centre of mass (from light-delayed tracks only):
`closing` (range shrinking faster than `posture_min_rate_m_s`), `fleeing` (growing faster), otherwise `holding`.
The first sighting takes the posture at face value; later changes must persist `posture_min_duration_s` first.
Tuning values are in `rules/doctrine.yaml` (`doctrine.contact`, `doctrine.engage`, `doctrine.profiles`) and are
placeholders.

## Output events

`contact_gained`, `contact_lost` (actor `faction:<id>`), `posture_changed` and `mission_changed` (actor = group id).
`maneuver_command.objective` reads `engage` while the engage behaviour is active. The after-action report
(`python3 -m simulator report <run>`) lists the first contact and every switch to and from engage.

## Limits of this first slice

- Detection is a single range (scenario `sensors.detection_range_km`); per-class sensors and line-of-sight come later.
- Posture is relative-motion based, so a stationary target closed on by the group is `closing` from the group's view.
- No `withdraw` action, integrity/magazine conditions, or leash (`max_departure_km`) for defenders yet.
- Target choice is "nearest matching contact"; a threat-score formula is a later doctrine item.
- Engage guidance assumes the target and group fall together (no gravity compensation) and extrapolates the target at
  constant velocity, so a target in a tight orbit is tracked approximately.
- Destroyed-ness is judged by true integrity (perfect battle damage assessment), as in fire control.
- Damage still does not reduce ship capability, so a "destroyed" group still flies its objective.

## Toward a doctrine builder

Everything above is plain data (`rules/doctrine.yaml` profiles, scenario `mission:` blocks), validated and
normalised in one place (`simulator/config.py::_parse_mission`), so a future editor can read and write the same
structures, re-run, and compare reports.
