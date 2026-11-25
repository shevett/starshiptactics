# Missions: detect -> (withdraw | engage) -> objective

A group's behaviour is a small stack. From the bottom:

1. **Objective** - what the scenario wants (`transit_to_orbit`, `transit_to_point`, `transit_to_hyper_limit`,
   `hold_position`, `coast`).
2. **Engage** or **withdraw** - pushed on top of the objective by a *reaction rule* when its conditions hold.
3. **Detect** - sensors produce the contacts (and `contact_gained` / `contact_lost` events) the rules look at.

When no rule matches (contact lost, destroyed, out of the rule's range...) the behaviour is dropped and the group flies
its objective again. An orbit objective is re-established if the group left its orbit in the meantime.

The vocabulary of conditions and actions is declared as data in `simulator/doctrine/registry.py` (parameters, units,
ranges, defaults, tooltip text). The scenario loader validates against it and `registry.describe()` returns it as JSON
for an editor UI.

## Scenario syntax

```yaml
scenario:
  sensors:
    detection_range_km: 10000000       # optional; omitted = every ship is detectable at any range

units:
  - group: blue_defenders
    class: reliant
    orbit: {body: earth, altitude_km: 20000}
    mission:
      profile: cautious_defender       # optional named rule set (rules/doctrine.yaml)
      profile_params: {force_ratio: 3} # optional overrides of the profile's own parameters
      objective: {type: transit_to_orbit, body: earth, altitude_km: 20000}   # may also be given at group level, not both
      reactions:                       # optional; evaluated BEFORE the profile's rules
        - when: {hostile: closing, force_ratio_over: 2.5, escape_margin_below_s: 900}
          do: {withdraw: {commit: true, if_no_route: fight}}
        - when: {any: [{own_magazine_below: 0.15}, {own_strength_lost_over: 0.6}]}
          do: withdraw
        - when: {hostile: closing}
          do: {engage: {standoff_km: 100000, leash_km: 100000}}
```

### `when`: filters and gates

Every key must hold (`any:` needs just one of its items). A rule only matches if at least one tracked hostile passes the
filters. `when` may also be written as a list of one-key mappings (meaning "all of these").

| key | kind | meaning |
|---|---|---|
| `hostile` | filter | `closing`, `fleeing`, `holding` or `any` (default): posture of the contacts the rule is about |
| `max_range_km` | filter | only contacts closer than this |
| `force_ratio_over` | gate | hostile tonnage (of the contacts the rule considers) / friendly tonnage (the group's whole faction still in the fight) |
| `own_strength_lost_over` | gate | fraction of the group's starting tonnage lost |
| `own_magazine_below` | gate | missiles remaining / starting missiles (groups with `fire_at_will` batteries only) |
| `escape_margin_below_s` | gate | the window for a safe escape to the hyper limit closes within this many seconds |

Tonnage is `mass_kg` from `classes/ships.yaml`; `force_ratio_over` fails at load if any class in the scenario has none.
Hostile classes are assumed identified as soon as a contact exists.

### `do`: actions

| action | effect |
|---|---|
| `engage` (`standoff_km`, `max_range_km`, `leash_km`) | steer to the target's predicted position, come to rest relative to it at the standoff range |
| `pursue` (`max_km` required, `standoff_km`, `leash_km`) | `engage` that is abandoned once the target is farther than `max_km` |
| `withdraw` (`to`, `commit`, `if_no_route`) | leave through the hyper limit on the safest heading (below) |
| `ignore` / `resume_objective` | stop evaluating rules; fly the objective (use it to shield rules below) |

Rules are evaluated top to bottom; the first one whose conditions hold wins. For `engage` the nearest matching contact is
the target, and the group keeps its current target while it still matches. Fire control prefers the group's engage target.

**`leash_km`** (engage/pursue, orbit objectives only) is measured from the orbited body. Outside it the rule does not
match, so the group flies back to its orbit, and it only goes out again once the orbit objective is complete.

**`withdraw`**: `to` is `hyper_limit` (the only destination so far). `commit: true` (default) means that once started the
group never re-evaluates: it keeps burning to the limit. With `commit: false` it turns back when the rule stops matching.
`release_when_clear` (default true) ends a committed withdrawal once no hostile ship or missile is left (true state: perfect
battle damage assessment); the group then flies its objective again, so a defender that fled and then won goes back to
orbit. A ship that has already crossed the hyper limit has left for good. `if_no_route` says what to do when no safe heading exists: `fight` (default) skips the rule so the ones below it decide,
`hold` stops evaluating rules, `flee_anyway` leaves straight out from the star regardless.

## Profiles and their parameters (`rules/doctrine.yaml`)

A profile is a reusable rule set. It may declare `params` (defaults) that its rules reference as `$name`; a scenario
overrides them with `profile_params` (unknown names are errors). `cautious_defender` (hold orbit, sortie against closing
hostiles inside a leash, withdraw if outweighed and the escape window is closing, or if low on missiles or badly hurt) and
`aggressive_transit` (objective first, but engage anything that shows up) are provided. All values are placeholders.

## Escape assessment

Each group re-plans every `doctrine.escape.update_interval_s`. It searches candidate headings (a ring in the ecliptic plus
two tilted rings) for a path to the hyper limit: coast while turning onto the heading, then burn at the slowest ship's
limit. A heading is **safe** if the path reaches the limit and never comes within any hostile's envelope (that hostile's
missile powered reach x `envelope_factor`, hostile extrapolated at constant velocity). The group takes the safe heading
that exits soonest; the **escape margin** is how long it could still wait before no safe heading exists (0 = no route now,
capped at `margin_cap_s`). Hostiles are assessed from all tracked contacts, not only those a rule's filters select.

## Posture

A hostile track's posture is its range-rate relative to the group's centre of mass (light-delayed tracks only): `closing`
(range shrinking faster than `posture_min_rate_m_s`), `fleeing` (growing faster), otherwise `holding`. The first sighting
takes the posture at face value; later changes must persist `posture_min_duration_s` first.

## End states

A scenario can end with the `all_objectives_complete` termination: every ship still in play has completed its objective
and no group is mid-engagement or mid-withdrawal. Destroyed ships (hulks) and departed ships do not count. So the winners
finish by getting home: defenders re-form their orbit, attackers settle into the orbit they came for. (A group that
returns to an orbit it started in re-forms it in its original order and spacing.) `incursion01` and `incursion02` use it.

## Output events

`contact_gained`, `contact_lost` (actor `faction:<id>`), `posture_changed` and `mission_changed` (actor = group id).
`mission_changed` carries the behaviour (`engage` / `withdraw` / `objective`), the reason in words (for example
"force ratio 4.60; escape margin 313 s"), and for a withdrawal the heading, margin and time to the hyper limit.
`hyper_departure` is emitted per ship (SPEC section 21). `maneuver_command.objective` reads `engage` / `withdraw` while a
behaviour is active. The report (`python3 -m simulator report <run>`) lists contact, every switch, and departures.

## Limits (placeholders and known gaps)

- Detection is one range (`sensors.detection_range_km`); per-class sensors and line of sight come later.
- Posture is relative-motion based: a stationary target that the group closes on is `closing` from its own view.
- The escape plan ignores gravity, assumes hostiles never change course or accelerate, and treats the group as one point.
  Planets and the Sun are obstacles: a heading through a body's exclusion zone (x `escape.body_margin`) is never chosen. Hostile classes without a missile envelope count as
  harmless (e.g. a class whose launchers are not yet in the database).
- `withdraw` can only go to the hyper limit. Departure is a one-way removal; there is no arrival yet.
- Hostile strength is tonnage only. Missile defence, wedge geometry and so on do not enter it, and a hostile's own
  magazine is not considered.
- Target choice is "nearest matching contact"; a threat score is a later doctrine item.
- Engage guidance assumes the target and group fall together (no gravity compensation) and extrapolates the target at
  constant velocity.
- Destroyed-ness is judged by true integrity (perfect battle damage assessment). A ship at or below
  `doctrine.engagement.destroyed_integrity` (placeholder 0.05) is a **hulk**: `ship_destroyed` is emitted and it drifts on
  its momentum. Above that, **partial damage** applies: `doctrine.damage` has piecewise-linear curves (integrity -> factor)
  for thrust (max acceleration), rotation (turn rate) and fire (tubes in action, which scales salvo size). Everything that
  reads a ship's limits sees the degraded values, including braking curves and the escape planner (slowest ship in the
  group). Not yet degraded: sensors, missile defence, wedge geometry, magazine loss. Hulks no longer count for group
  centres, escape plans or termination; missiles a hulk already launched keep flying.

## Toward a doctrine builder

Everything above is plain data (the registry, `rules/doctrine.yaml` profiles, scenario `mission:` blocks), validated in one
place (`simulator/config.py`). A future editor can read the registry for its forms and tooltips, read and write the same
YAML, re-run, and compare reports.
