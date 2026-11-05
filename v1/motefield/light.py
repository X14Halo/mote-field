"""The key light: its direction and colour, where it reaches (shadows), and the light it bounces off the set.

    vis = visibility(e, shot)          a function: world points (N, 3), scene units -> 0..1 lit
    bnc = bounce_lights(e, shot, vis)  sunlit patches of the set as point lights, at unit brightness
    sun = sun_rgb(e)                   the light's colour x intensity on this frame (may be animated)

Shadows come from the stand-in geometry (exact) or from the utility pass: a depth map seen from the light, built from
clean single-surface pixels of the first, middle and last frames. With 'Light only through openings' on, light gets
in only where the camera saw empty sky. Both are built once per shot and light direction (the prep) and cached.

Bounce lights are worked out for a white light of intensity 1 and multiplied by the light's colour and intensity when
rendering, so keying the intensity or colour never rebuilds them.
"""

import math

import numpy as np

from . import schema

_CACHE = {}


def _cached(key, make):
    if key not in _CACHE:
        if len(_CACHE) > 8:
            _CACHE.clear()
        _CACHE[key] = make()
    return _CACHE[key]


def kelvin_rgb(K):
    """Approximate black-body colour, linear, normalised to luminance 1."""
    t = K / 100.0
    if t <= 66:
        r, g = 255.0, 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r, g = 329.698727446 * (t - 60) ** -0.1332047592, 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    c = np.array([min(max(v, 0.0), 255.0) / 255.0 for v in (r, g, b)]) ** 2.2
    return c / (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def sun_rgb(e):
    """The key light's colour x intensity (e = Settings.engine(frame), so keyed values are already evaluated)."""
    return kelvin_rgb(e['light_k']) * np.asarray(e['light_rgb'], np.float64) * e['light_int']


def light_dir(azimuth, elevation):
    """Unit vector pointing toward a directional light (Y up). Heading 0 = the light travels toward +X."""
    az, el = math.radians(azimuth), math.radians(elevation)
    return np.array([-math.cos(az) * math.cos(el), math.sin(el), -math.sin(az) * math.cos(el)])


def light_from_direction(L):
    """(heading, elevation) in degrees for a unit vector pointing toward the light: the inverse of light_dir."""
    L = np.asarray(L, np.float64) / max(np.linalg.norm(L), 1e-12)
    return math.degrees(math.atan2(-L[2], -L[0])), math.degrees(math.asin(max(-1.0, min(1.0, L[1]))))


def aim(p_light, p_lit):
    """Heading and elevation of a light that shines from p_light onto p_lit (two clicks on the plate: a corner of the
    window frame, then the same corner of its patch of light on the floor)."""
    return light_from_direction(np.asarray(p_light, np.float64) - np.asarray(p_lit, np.float64))


def smooth01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


class LightMap(object):
    """A depth map seen from a directional light, from points (scene units). Only knows surfaces the camera saw on
    some frame, so off-screen geometry casts no shadow."""

    def __init__(self, points, L, res=1536):
        self.L = L / np.linalg.norm(L)
        a = np.cross(self.L, [0.0, 1.0, 0.0] if abs(self.L[1]) < 0.95 else [1.0, 0.0, 0.0])
        self.a = a / np.linalg.norm(a)
        self.b = np.cross(self.L, self.a)
        u, v, d = points @ self.a, points @ self.b, points @ self.L
        self.lo = np.array([u.min(), v.min()])
        span = max(u.max() - u.min(), v.max() - v.min(), 1e-6)
        self.cell = span / (res - 1)
        self.res = res
        iu = ((u - self.lo[0]) / self.cell).astype(np.intp)
        iv = ((v - self.lo[1]) / self.cell).astype(np.intp)
        ok = (iu >= 0) & (iu < res) & (iv >= 0) & (iv < res)
        g = np.full(res * res, -np.inf)
        np.maximum.at(g, iv[ok] * res + iu[ok], d[ok])
        g = g.reshape(res, res)
        for _ in range(2):        # close the gaps between points
            p = np.pad(g, 1, constant_values=-np.inf)
            g = np.maximum.reduce([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        self.g = g
        self.bias = self.cell * 3

    def visible(self, P):
        u, v, d = P @ self.a, P @ self.b, P @ self.L
        iu = ((u - self.lo[0]) / self.cell).astype(np.intp)
        iv = ((v - self.lo[1]) / self.cell).astype(np.intp)
        ok = (iu >= 0) & (iu < self.res) & (iv >= 0) & (iv < self.res)
        vis = np.ones(len(P))
        occ = self.g[np.clip(iv, 0, self.res - 1), np.clip(iu, 0, self.res - 1)]
        vis[ok & (occ > d + self.bias)] = 0.0
        return vis


def prep_frames(e, shot):
    """The frames the prep looks at: first, middle and last of the shot that exist."""
    want = sorted(set([e['first'], (e['first'] + e['last']) // 2, e['last']]))
    have = getattr(shot, 'frames', None) or want
    out = [f for f in want if f in have]
    if not out:
        out = [min(have, key=lambda f: abs(f - e['first']))]
    return out


def visibility(e, shot, key=None):
    """A function: world points (N, 3) in scene units -> 0..1, lit by the key light. Cached per key (the prep key)."""
    return _cached(('vis', key) if key else ('vis', id(shot), e['light_az'], e['light_el'], e['shadows'],
                                                   e['interior']), lambda: _visibility(e, shot))


def _visibility(e, shot):
    L = light_dir(e['light_az'], e['light_el'])
    mode = e['shadows']
    if mode == 'stand-in geometry' and getattr(shot, 'blocked', None) is None:
        mode = 'utility pass'
    if mode == 'none':
        return lambda P: np.ones(len(P))
    if mode == 'stand-in geometry':
        return lambda P: (~shot.blocked(P, L)).astype(np.float64)
    pts, views = [], []
    for f in prep_frames(e, shot):
        try:
            s = shot.surface(f)
        except Exception:
            continue
        Q = s.points(step=1)
        if len(Q) > 900000:
            Q = Q[::int(math.ceil(len(Q) / 900000.0))]
        pts.append(Q.astype(np.float64))
        views.append((s.cam, s.cov <= 0))
    if not pts or not sum(len(p) for p in pts):
        return lambda P: np.ones(len(P))
    allp = np.concatenate(pts)
    lm = LightMap(allp, L)
    if not e['interior']:
        return lm.visible
    # interior: the pass only shows what the camera saw, so light gets in only through openings the camera saw as
    # empty (sky through a window). Follow the ray toward the light out of the scene and look it up in the frames.
    lo, hi = np.percentile(allp, 0.5, axis=0), np.percentile(allp, 99.5, axis=0)
    Ls = np.where(np.abs(L) < 1e-9, 1e-9, L)

    def vis(P):
        v = lm.visible(P)
        t = np.min(np.where(Ls > 0, (hi - P) / Ls, (lo - P) / Ls), axis=1)
        Q = P + np.maximum(t, 0)[:, None] * L + L * (np.linalg.norm(hi - lo) * 0.01)
        open_ = np.zeros(len(P), bool)
        for cam, sky in views:
            Hh, Ww = sky.shape
            x, y, z = cam.project(Q, Ww, Hh)
            ok = (z > 0) & (x >= 0) & (x < Ww) & (y >= 0) & (y < Hh)
            xi = np.clip(x.astype(np.intp), 0, Ww - 1)
            yi = np.clip(y.astype(np.intp), 0, Hh - 1)
            open_ |= ok & sky[yi, xi]
        return v * open_
    return vis


def soft_taps(L, radius):
    """Offsets (scene units) across the light's direction: the centre and five around a ring."""
    L = L / np.linalg.norm(L)
    a = np.cross(L, [0.0, 1.0, 0.0] if abs(L[1]) < 0.95 else [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(L, a)
    taps = [np.zeros(3)]
    for k in range(5):
        t = 2 * math.pi * k / 5.0 + 0.3
        taps.append((a * math.cos(t) + b * math.sin(t)) * radius)
    return taps


def soft_visibility(vis, P, L, radius):
    """Visibility averaged over a small disc across the light's direction, so motes fade through a shadow edge."""
    if radius <= 0:
        return vis(P)
    v = np.zeros(len(P))
    taps = soft_taps(L, radius)
    for o in taps:
        v += vis(P + o)
    return v / len(taps)


def bounce_lights(e, shot, vis, key=None, count=24):
    """The sunlit patches of the set as point lights plus an overall fill, for a white key light of intensity 1:
    (positions m (n, 3), normals (n, 3), flux (n, 3), ambient (3,)). Multiply flux and ambient by sun_rgb(e)."""
    return _cached(('bounce', key) if key else ('bounce', id(shot), e['light_az'], e['light_el'], e['shadows'],
                                                      e['interior']), lambda: _bounce(e, shot, vis, count))


def _bounce(e, shot, vis, count):
    su = schema.UNITS[e['units']]
    L = light_dir(e['light_az'], e['light_el'])
    cells, tot, nfr = {}, np.zeros(3), 0
    st = 4
    if getattr(shot, 'prep_shot', None) is not None:      # a stand-in can make a smaller copy instead of skipping pixels
        shot, st = shot.prep_shot(), 1
    for f in prep_frames(e, shot):
        try:
            s = shot.surface(f)
            N = shot.read(f, 'N')
            C = shot.read(f, 'C')
        except Exception:
            continue
        if N is None:                       # no normals: assume surfaces face the camera
            N = s.cam.pos - s.P
        m = s.clean[::st, ::st]
        P = s.P[::st, ::st][m].astype(np.float64)
        N = N[::st, ::st][m].astype(np.float64)
        C = np.clip(C[::st, ::st][m].astype(np.float64), 0, 1) if C is not None else np.full_like(P, e.get('albedo', 0.35))
        N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-6)
        nl = N @ L
        ok = nl > 0
        if not ok.any():
            nfr += 1
            continue
        P, N, C, nl = P[ok], N[ok], C[ok], nl[ok]
        v = vis(P + N * (2.0 / max(su * 100, 1e-6)))
        lit = v > 0
        nfr += 1
        if not lit.any():
            continue
        P, N, C, nl, v = P[lit], N[lit], C[lit], nl[lit], v[lit]
        dvec = P - s.cam.pos
        dist_m = np.linalg.norm(dvec, axis=1) * su
        cosv = np.abs((dvec / np.maximum(np.linalg.norm(dvec, axis=1, keepdims=True), 1e-9) * N).sum(1))
        pix = 2 * math.tan(s.cam.hfov / 2) / s.W * st
        area = (dist_m * pix) ** 2 / np.maximum(cosv, 0.2)
        flux = C * (nl * v * area)[:, None]
        Pm = P * su
        keyc = np.floor(Pm / 0.8).astype(np.intp)
        axis = np.argmax(np.abs(N), 1)
        face = axis * 2 + (N[np.arange(len(N)), axis] > 0)
        w_all = flux.sum(1)
        for kk, pm, nn, fl, w in zip(map(tuple, np.concatenate([keyc, face[:, None]], 1)), Pm, N, flux, w_all):
            c = cells.get(kk)
            if c is None:
                c = cells[kk] = [np.zeros(3), np.zeros(3), np.zeros(3), 0.0]
            c[0] += pm * w
            c[1] += nn * w
            c[2] += fl
            c[3] += w
        tot += flux.sum(0)
    nfr = max(nfr, 1)
    arr = sorted([c for c in cells.values() if c[3] > 0], key=lambda c: -c[3])[:count]
    kept = sum(c[2].sum() for c in arr)
    scale = (tot.sum() / kept) if kept > 0 else 0.0
    pos = np.array([c[0] / c[3] for c in arr]).reshape(-1, 3)
    nrm = np.array([c[1] / max(np.linalg.norm(c[1]), 1e-9) for c in arr]).reshape(-1, 3)
    flx = np.array([c[2] * scale / nfr for c in arr]).reshape(-1, 3)
    amb = tot / nfr * (0.4 / (278 * 0.6))          # an overall fill from all of it
    return pos, nrm, flx, amb
