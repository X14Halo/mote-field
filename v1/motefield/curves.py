"""Keyed values: smooth (Catmull-Rom) through the keys, held before the first and after the last.

The same interpolation is used for animated settings and for shapes, in Python and in the viewer (viewer/js), so a key
set in the viewer gives the same value on the farm. Pure Python: no numpy, so it is cheap to call per frame.
"""

INTERPS = ('smooth', 'linear', 'step')


def _lerp(a, b, t):
    return a + (b - a) * t


def hermite(frames, values, f):
    """(value, slope per frame) at frame f. frames ascending; values floats. Catmull-Rom tangents from the neighbours."""
    n = len(frames)
    if n == 1 or f <= frames[0]:
        return values[0], 0.0
    if f >= frames[-1]:
        return values[-1], 0.0
    i = 0
    while frames[i + 1] < f:
        i += 1

    def slope(j):
        a, b = max(j - 1, 0), min(j + 1, n - 1)
        return (values[b] - values[a]) / max(frames[b] - frames[a], 1e-6)
    dt = max(frames[i + 1] - frames[i], 1e-6)
    t = (f - frames[i]) / dt
    m0, m1 = slope(i) * dt, slope(i + 1) * dt
    t2, t3 = t * t, t * t * t
    v = (2 * t3 - 3 * t2 + 1) * values[i] + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * values[i + 1] + (t3 - t2) * m1
    dv = ((6 * t2 - 6 * t) * values[i] + (3 * t2 - 4 * t + 1) * m0 + (-6 * t2 + 6 * t) * values[i + 1]
          + (3 * t2 - 2 * t) * m1) / dt
    return v, dv


def evaluate(frames, values, f, interp='smooth'):
    """Value at frame f of a scalar keyed with the given interpolation."""
    n = len(frames)
    if n == 1 or f <= frames[0]:
        return values[0]
    if f >= frames[-1]:
        return values[-1]
    if interp == 'smooth':
        return hermite(frames, values, f)[0]
    i = 0
    while frames[i + 1] < f:
        i += 1
    if interp == 'step':
        return values[i]
    return _lerp(values[i], values[i + 1], (f - frames[i]) / max(frames[i + 1] - frames[i], 1e-6))


def is_curve(v):
    return isinstance(v, dict) and 'keys' in v


def make_curve(keys, interp='smooth'):
    """A curve value as stored in a settings file: {"keys": [[frame, value], ...], "interp": ...}."""
    return {'keys': sorted([[float(f), v] for f, v in keys], key=lambda k: k[0]), 'interp': interp}


def curve_at(curve, f):
    """Value of a stored curve at frame f. Values may be numbers or 3-vectors (each component interpolated alone)."""
    keys = curve['keys']
    interp = curve.get('interp', 'smooth')
    frames = [k[0] for k in keys]
    first = keys[0][1]
    if isinstance(first, (list, tuple)):
        return tuple(evaluate(frames, [k[1][c] for k in keys], f, interp) for c in range(len(first)))
    return evaluate(frames, [k[1] for k in keys], f, interp)
