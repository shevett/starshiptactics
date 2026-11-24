"""The doctrine vocabulary: every condition and action a mission reaction rule can use, declared as data.

This is the single source of truth for what a `reactions:` rule may contain. The scenario loader validates against it
(`validate_value`), and `describe()` returns the whole vocabulary as plain JSON-able data (parameters, units, ranges,
defaults, tooltip text) so an editor UI can build its forms and hints without any knowledge of the simulator internals.
Adding a condition here (and its evaluator in mission.py) makes it available to scenarios and to the editor.

A rule looks like:

    when:                          # every key must hold (the optional `any:` list needs just one of its items)
      hostile: closing             # FILTER: which hostile contacts the rule is about (default: any)
      max_range_km: 5000000        # FILTER: only contacts closer than this
      force_ratio_over: 2.5        # GATE: a group-level condition
      escape_margin_below_s: 900   # GATE
    do: {withdraw: {if_no_route: fight}}     # ACTION with parameters
"""

POSTURES = ("closing", "fleeing", "holding")

# type: number | enum | bool.  unit "km" / "s" values are stored in SI by the loader (name_km -> name_m).
FILTERS = {
    "hostile": {
        "type": "enum", "values": ["any", *POSTURES], "default": "any",
        "summary": "Posture of the hostile contacts this rule is about.",
        "tooltip": "closing: range to the contact is shrinking; fleeing: growing; holding: neither (see "
                   "doctrine.contact.posture_min_rate_m_s). 'any' matches every tracked hostile.",
    },
    "max_range_km": {
        "type": "number", "unit": "km", "min": 0, "exclusive_min": True,
        "summary": "Only consider hostile contacts closer than this.",
        "tooltip": "Measured from the centre of the group to the light-delayed track.",
    },
}

GATES = {
    "force_ratio_over": {
        "type": "number", "unit": "ratio", "min": 0, "exclusive_min": True,
        "summary": "Hostile strength divided by friendly strength exceeds this.",
        "tooltip": "Strength is total ship mass (tons). Hostile = the contacts this rule considers; friendly = every "
                   "ship of the group's faction still in the fight. Needs mass_kg on every ship class in the "
                   "scenario. Classes are assumed identified as soon as a contact is gained.",
    },
    "own_strength_lost_over": {
        "type": "number", "unit": "fraction", "min": 0, "max": 1,
        "summary": "This group has lost more than this fraction of its starting tonnage.",
        "tooltip": "A ship counts as lost once its integrity is at or below doctrine.engagement.assumed_dead_integrity.",
    },
    "own_magazine_below": {
        "type": "number", "unit": "fraction", "min": 0, "max": 1,
        "summary": "This group's missiles remaining, as a fraction of its starting magazines, is below this.",
        "tooltip": "Only groups with fire_at_will batteries have a magazine; for others the condition is never true.",
    },
    "escape_margin_below_s": {
        "type": "number", "unit": "s", "min": 0,
        "summary": "The window for a safe escape to the hyper limit closes within this many seconds.",
        "tooltip": "The group searches for a heading to the hyper limit that stays outside every hostile's missile "
                   "envelope (hostiles extrapolated at constant velocity). The margin is how long it could still "
                   "wait before no such heading exists. 0 means no safe route exists right now. Needs a "
                   "hyper_limit in the system file.",
    },
}

ACTIONS = {
    "engage": {
        "summary": "Close on the nearest matching hostile and hold a standoff range.",
        "params": {
            "standoff_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True,
                            "default_from": "doctrine.engage.default_standoff_km",
                            "summary": "Range at which the group comes to rest relative to its target."},
            "max_range_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True,
                             "summary": "Do not engage contacts farther away than this."},
            "leash_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True,
                         "summary": "Do not engage while farther than this from the objective's orbited body.",
                         "tooltip": "Only valid with a transit_to_orbit objective. Outside the leash the group flies "
                                    "back to its orbit."},
        },
    },
    "pursue": {
        "summary": "Engage, but abandon the chase beyond a maximum range.",
        "params": {
            "max_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True, "required": True,
                       "summary": "Give up when the target is farther than this."},
            "standoff_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True,
                            "default_from": "doctrine.engage.default_standoff_km",
                            "summary": "Range at which the group comes to rest relative to its target."},
            "leash_km": {"type": "number", "unit": "km", "min": 0, "exclusive_min": True,
                         "summary": "Do not pursue while farther than this from the objective's orbited body."},
        },
    },
    "withdraw": {
        "summary": "Break off and leave the field through the hyper limit on the safest heading.",
        "params": {
            "to": {"type": "enum", "values": ["hyper_limit"], "default": "hyper_limit",
                   "summary": "Where to withdraw to (only the hyper limit is implemented)."},
            "commit": {"type": "bool", "default": True,
                       "summary": "Once started, keep withdrawing even if the rule stops matching.",
                       "tooltip": "Without it the group re-evaluates every step and may turn back."},
            "if_no_route": {"type": "enum", "values": ["fight", "hold", "flee_anyway"], "default": "fight",
                            "summary": "What to do when no safe escape heading exists.",
                            "tooltip": "fight: skip this rule and let the rules below it decide (usually engage); "
                                       "hold: stop evaluating rules and fly the objective; flee_anyway: leave straight "
                                       "out from the star regardless of the hostile envelope."},
        },
    },
    "ignore": {"summary": "Stop evaluating rules; fly the objective.", "params": {}},
    "resume_objective": {"summary": "Same as ignore: carry on with the objective.", "params": {}},
}


def describe():
    """The whole vocabulary as JSON-able data (for an editor UI)."""
    return {"postures": list(POSTURES), "filters": FILTERS, "gates": GATES, "actions": ACTIONS}


def _fmt_range(spec):
    lo, hi = spec.get("min"), spec.get("max")
    if lo is None and hi is None:
        return ""
    lo_s = ("> " if spec.get("exclusive_min") else ">= ") + f"{lo:g}" if lo is not None else ""
    hi_s = f"<= {hi:g}" if hi is not None else ""
    return " and ".join(x for x in (lo_s, hi_s) if x)


def validate_value(spec, value, where):
    """Check `value` against a parameter spec and return it normalised (float / str / bool). Raises ValueError."""
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{where}: expected true or false, got {value!r}")
        return value
    if kind == "enum":
        if value not in spec["values"]:
            raise ValueError(f"{where}: {value!r} not in {spec['values']}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number{' (' + spec['unit'] + ')' if spec.get('unit') else ''}, got {value!r}")
    v = float(value)
    lo, hi = spec.get("min"), spec.get("max")
    if (lo is not None and (v < lo or (spec.get("exclusive_min") and v <= lo))) or (hi is not None and v > hi):
        raise ValueError(f"{where}: {value!r} out of range (must be {_fmt_range(spec)})")
    return v


def si_key(name, value, spec):
    """('standoff_km', 250.0) -> ('standoff_m', 250000.0); other units are already SI."""
    if spec.get("unit") == "km":
        return name[:-3] + "_m", value * 1000.0
    return name, value
