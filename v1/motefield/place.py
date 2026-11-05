"""Placing things in the shot from the utility pass.

    fit_box(e, shot)        a dust box filling the air the camera looks through, from the camera to the set
    point_at(shot, f, x, y) the world position and normal under a pixel (clicking on the plate)
"""

import numpy as np

from . import schema


def fit_box(e, shot, frame=None, far_pct=85.0):
    """(centre, size) in scene units: an upright box over the floor the camera sees, from just in front of the camera
    out to most of the set (far_pct of clean points by distance), up to a little above the camera."""
    frames = getattr(shot, 'frames', None) or [e['first']]
    f = frame if frame is not None else frames[len(frames) // 2]
    s = shot.surface(f)
    su = schema.UNITS[e['units']]
    pts = s.points(step=2).astype(np.float64)
    if len(pts) < 100:
        raise ValueError('Not enough of the set is visible on frame %d to fit a box.' % f)
    cam = s.cam
    dist = np.linalg.norm(pts - cam.pos, axis=1)
    near = pts[dist <= np.percentile(dist, far_pct)]
    lo = np.percentile(near, 1.0, axis=0)
    hi = np.percentile(near, 99.0, axis=0)
    # take in the camera too, so the dust reaches the lens (with half a metre around it)
    pad = 0.5 / su
    lo = np.minimum(lo, cam.pos - pad)
    hi = np.maximum(hi, cam.pos + pad)
    # from the floor (the lowest of what is seen) to a metre above the camera at most
    hi[1] = min(hi[1], cam.pos[1] + 1.0 / su)
    centre, size = (lo + hi) / 2, np.maximum(hi - lo, 0.2 / su)
    return tuple(float(x) for x in centre), tuple(float(x) for x in size)


def point_at(shot, f, x, y):
    """World position (scene units) and normal under pixel x, y (from the top left) on frame f, or None if empty."""
    s = shot.surface(f)
    xi, yi = int(np.clip(x, 0, s.W - 1)), int(np.clip(y, 0, s.H - 1))
    if s.cov[yi, xi] <= 0:
        return None
    N = shot.read(f, 'N')
    n = N[yi, xi] / max(np.linalg.norm(N[yi, xi]), 1e-6) if N is not None else None
    return s.P[yi, xi].astype(np.float64), n
