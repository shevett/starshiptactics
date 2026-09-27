"""Unit conversion to SI. Everything past config loading is metres, seconds, radians."""
import math

G0 = 9.80665                 # m/s^2 per g
C = 299792458.0              # m/s
KM = 1000.0
DAY_S = 86400.0


def deg(x):
    return math.radians(x)


def as_vec3(value, where):
    """Accept [x,y,z] or {x:,y:,z:}; return a tuple of floats."""
    try:
        if isinstance(value, dict):
            return (float(value["x"]), float(value["y"]), float(value["z"]))
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(float(v) for v in value)
    except (KeyError, TypeError, ValueError):
        pass
    raise ValueError(f"{where}: expected [x, y, z] or {{x:, y:, z:}}, got {value!r}")


def pick_vector(mapping, suffixes, where, required=True):
    """Find exactly one key like 'position_km' / 'position_m' and return SI vector.

    suffixes: {'position_km': 1000.0, 'position_m': 1.0}
    """
    found = [k for k in suffixes if k in mapping]
    if len(found) > 1:
        raise ValueError(f"{where}: give only one of {', '.join(found)}")
    if not found:
        if required:
            raise ValueError(f"{where}: missing one of {', '.join(suffixes)}")
        return None
    k = found[0]
    v = as_vec3(mapping[k], f"{where}.{k}")
    return tuple(c * suffixes[k] for c in v)
