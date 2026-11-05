"""A small synthetic utility pass for tests, written the way production passes often are.

A floor, a back wall that stops short of the top of frame (so there is sky) and a sphere, seen by a camera, ray traced
with 4 x 4 samples a pixel:

    pos, depth, normal   filtered: the mean over the samples, with misses counting as 0 (so premultiplied by coverage)
    depthNoFilter        unfiltered: one sample (the first), a real surface at every pixel
    A                    coverage
    sceneObjectID        the object under the first sample (1 floor, 2 wall, 3 sphere)
    backward             motion vectors (zeros: nothing moves)

The camera goes in the header as a camToWorld string and a field of view, with lens details only inside a JSON
'renderCam' attribute, after a JSON list of other cameras that must not be used.
"""

import json
import math

import numpy as np

CAM_POS = np.array([30.0, 150.0, 600.0])
LOOK = np.array([0.0, 90.0, 0.0])
HFOV = 50.0
SPHERE_C, SPHERE_R = np.array([40.0, 90.0, 0.0]), 80.0
WALL_Z, WALL_TOP = -400.0, 260.0
BLADES, CATEYE = 7, 0.25


def camera_axes():
    back = CAM_POS - LOOK
    back /= np.linalg.norm(back)
    right = np.cross([0.0, 1.0, 0.0], back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    return right, up, back


def cam_to_world():
    r, u, b = camera_axes()
    return [r[0], r[1], r[2], 0, u[0], u[1], u[2], 0, b[0], b[1], b[2], 0, CAM_POS[0], CAM_POS[1], CAM_POS[2], 1]


def _hits(O, D):
    """Nearest hit along rays D from O: (t, object id, normal); t = inf for a miss."""
    n = len(D)
    t = np.full(n, np.inf)
    oid = np.zeros(n, np.int32)
    N = np.zeros((n, 3))
    # floor y = 0, |x| < 600, -400 < z < 500
    with np.errstate(divide='ignore', invalid='ignore'):
        tf = -O[1] / D[:, 1]
    P = O + D * tf[:, None]
    ok = (tf > 0) & (np.abs(P[:, 0]) < 600) & (P[:, 2] > WALL_Z) & (P[:, 2] < 500)
    upd = ok & (tf < t)
    t[upd], oid[upd], N[upd] = tf[upd], 1, [0, 1, 0]
    # back wall z = WALL_Z, |x| < 600, 0 < y < WALL_TOP
    with np.errstate(divide='ignore', invalid='ignore'):
        tw = (WALL_Z - O[2]) / D[:, 2]
    P = O + D * tw[:, None]
    ok = (tw > 0) & (np.abs(P[:, 0]) < 600) & (P[:, 1] > 0) & (P[:, 1] < WALL_TOP)
    upd = ok & (tw < t)
    t[upd], oid[upd], N[upd] = tw[upd], 2, [0, 0, 1]
    # sphere
    oc = O - SPHERE_C
    b = D @ oc
    c = oc @ oc - SPHERE_R ** 2
    disc = b * b - c
    ts = -b - np.sqrt(np.maximum(disc, 0))
    ok = (disc > 0) & (ts > 0)
    upd = ok & (ts < t)
    Ps = O + D * ts[:, None]
    t[upd], oid[upd] = ts[upd], 3
    N[upd] = (Ps[upd] - SPHERE_C) / SPHERE_R
    return t, oid, N


def render(W=96, H=54, ss=4, zdepth=False):
    """{channel: (H, W) float32} and the header attributes."""
    r, u, b = camera_axes()
    th = math.tan(math.radians(HFOV) / 2)
    tv = th * H / W
    offs = [((i + 0.5) / ss, (j + 0.5) / ss) for j in range(ss) for i in range(ss)]
    acc = dict(P=np.zeros((H, W, 3)), Z=np.zeros((H, W)), N=np.zeros((H, W, 3)), A=np.zeros((H, W)))
    Zraw = np.zeros((H, W))
    ID = np.zeros((H, W))
    ys, xs = np.mgrid[0:H, 0:W]
    for si, (ox, oy) in enumerate(offs):
        sx = ((xs + ox) / W * 2 - 1) * th
        sy = (1 - (ys + oy) / H * 2) * tv
        D = sx[..., None] * r + sy[..., None] * u - b
        Dn = D / np.linalg.norm(D, axis=-1, keepdims=True)
        t, oid, N = _hits(CAM_POS, Dn.reshape(-1, 3))
        hit = np.isfinite(t)
        P = CAM_POS + Dn.reshape(-1, 3) * np.where(hit, t, 0)[:, None]
        dist = np.where(hit, t, 0.0)
        if zdepth:
            dist = dist * np.abs(Dn.reshape(-1, 3) @ b)
        acc['P'] += np.where(hit[:, None], P, 0).reshape(H, W, 3)
        acc['Z'] += dist.reshape(H, W)
        acc['N'] += np.where(hit[:, None], N, 0).reshape(H, W, 3)
        acc['A'] += hit.reshape(H, W)
        if si == 0:
            Zraw = dist.reshape(H, W)
            ID = oid.reshape(H, W)
    k = 1.0 / len(offs)
    ch = {'A': acc['A'] * k, 'depth.Z': acc['Z'] * k, 'depthNoFilter.R': Zraw, 'sceneObjectID.R': ID,
          'backward.u': np.zeros((H, W)), 'backward.v': np.zeros((H, W))}
    for i, c in enumerate('RGB'):
        ch['pos.' + c] = acc['P'][..., i] * k
        ch['normal.' + c] = acc['N'][..., i] * k
    ch = {n: v.astype(np.float32) for n, v in ch.items()}
    others = {'persp': dict(fStop=1000, bladeCount=0, focusDistance=100, cateyeBokeh=0)}
    render_cam = dict(fStop=4.0, focusDistance=620.0, bladeCount=BLADES, cateyeBokeh=CATEYE, fieldCurvature=0.1,
                      fStopFullAperture=1.4, focalLength=3.2, shutterOpen=-0.5, shutterClose=0.0,
                      camToWorld=str(cam_to_world()))
    attrs = {'studio/cam/camToWorld': str(['%.9f' % x for x in cam_to_world()]).replace("'", ''),
             'studio/cam/horizontalFOV_deg': '%r' % HFOV, 'studio/cam/fStop': '4.0',
             'studio/cam/focusDistance': '620.0', 'studio/cam/focalLength': '32.0',
             'studio/render/cameras': json.dumps(others), 'studio/render/renderCam': json.dumps(render_cam)}
    return ch, attrs
