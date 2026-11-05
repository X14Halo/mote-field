"""Rendering a frame of dust through the shot's lens: the image the Nuke node shows.

    r = Renderer(settings)                  # caches what doesn't change between frames
    out = r.frame(1024)                     # {'rgb': (H, W, 3), 'alpha': (H, W), 'noholdout': ..., ...} float32

What happens to each mote on a frame:
  1. where it is and how fast it moves (motion.DustMotion), and how much it fades at the box faces, near the lens and
     with the distance fades;
  2. how it looks: its kind and size, tumbling flakes and fibres, forward scattering (Henyey-Greenstein), flake glints,
     the key light with soft shadow edges, and bounce light from the sunlit set; brightness falls off with distance
     squared and grows with size, so most motes are faint and a few are bright;
  3. its streak over the shutter (its own motion and the camera's, with the studio's backwardsShape weighting), its
     circle of confusion through the lens, and whether the set hides it (coverage-weighted holdouts, averaged over
     its blur with Steady);
  4. drawn with the lens's bokeh (splat.draw).

The light is kept in separate channels where asked: the key light and the bounce light (dust_key, dust_fill), and
the dust with no holdouts (noholdout).
"""

import math

import numpy as np

from . import light, rng, splat
from .motion import DustMotion

FLAKE, FIBER, FLUFF, GRIT = rng.FLAKE, rng.FIBER, rng.FLUFF, rng.GRIT
MAX_STREAK = 24          # samples along a mote's motion-blur streak, at most


def shutter_weight(u, shape):
    """How open the shutter is at u (0 = opens, 1 = closes). backwardsShape: an S-curve, fully open at the frame."""
    u = np.asarray(u, np.float64)
    if str(shape).lower().startswith('back'):
        return 1.0 / (1.0 + np.exp(-9.0 * (u - 0.55)))
    return np.ones_like(u)


def cam_fade(dist, near, far):
    """0 nearer than near, rising to 1 at far (scene units; both 0 = off)."""
    near, far = float(near or 0.0), float(far or 0.0)
    if far > near:
        return splat.smooth01((dist - near) / (far - near))
    if near > 0:
        return (dist >= near).astype(np.float64)
    return np.ones(len(dist))


def tumble_axis(h4, h5, h6, rate, t):
    a = 2 * math.pi * (h4 + rate * t)
    b = 2 * math.pi * h5 + 4.1 * rate * t * (0.5 + h6)
    return np.stack([np.sin(a) * np.cos(b), np.cos(a), np.sin(a) * np.sin(b)], -1)


def lens_values(e, cam, fstop):
    """(blades, cat-eye amount, anamorphic aspect, field curvature): the camera's lens, with the settings added on top
    (like a studio BokehBlur). Stopping down from full aperture shrinks the cat-eye."""
    ex = cam.lens or {}
    blades = int(e['blades']) if int(e['blades']) > 0 else int(round(ex.get('blades', 0)))
    cat = ex.get('cateye', 0.0) + e['cateye']
    aspect = (ex.get('anamorphic') or 1.0) + e['anamorphic']
    fc = ex.get('field_curv', 0.0) + e['field_curv']
    full = e['fstop_full'] or ex.get('fstop_full') or fstop
    vign = min(1.0, full / max(fstop, 1e-3))
    return blades, max(cat, 0.0) * vign, max(aspect, 0.05), fc


class Frame(object):
    """What everything drawn into one frame shares: cameras, resolution, lens, shutter, what each pixel sees."""

    def __init__(self, e, shot, frame, bokeh_image=None):
        self.frame = frame
        self.su = e['scene_unit']
        self.cam = cam = shot.camera(frame)
        self.surf = shot.surface(frame)
        self.W0, self.H0 = self.surf.W, self.surf.H
        self.sc = min(max(float(e['proxy']), 0.05), 4.0)
        self.W, self.H = max(int(round(self.W0 * self.sc)), 8), max(int(round(self.H0 * self.sc)), 8)
        self.focal = e['focal'] or cam.focal or 35.0
        self.fstop = e['fstop'] or cam.fstop or 4.0
        self.film = e['film'] or cam.film or (2 * self.focal * math.tan(cam.hfov / 2))
        self.focus_m = (e['focus'] or cam.focus or 500.0) * self.su
        self.focal_px = self.W / (2 * math.tan(cam.hfov / 2))
        s = e['shutter'] / 360.0 if e['mb'] else 0.0
        mode = e['shutter_mode']
        self.t_open, self.t_close = (-s, 0.0) if mode == 'backward' else (0.0, s) if mode == 'forward' else (-s / 2,
                                                                                                             s / 2)
        self.cam_o = shot.camera(frame + self.t_open) if s else cam
        self.cam_c = shot.camera(frame + self.t_close) if s else cam
        self.spf = e['speed'] / float(e['fps'])                    # air seconds per frame
        self.tsec = (frame - e['first']) / float(e['fps']) * e['speed']
        self.max_r = e['max_bokeh'] * self.sc
        self.margin = self.max_r + 40
        self.blades, self.cat, self.aspect, self.fcurv = lens_values(e, cam, self.fstop)
        self.bokeh = splat.Lens(self.blades, rot=0.25 + math.radians(e['bokeh_rot']), aspect=self.aspect,
                                cateye=self.cat, falloff=e['bokeh_falloff'], image=bokeh_image,
                                img_rot=math.radians(e['bokeh_rot']))

    def coc(self, e, px, py, z_m):
        """Circle of confusion (px) at depth z_m (metres) and image position px, py."""
        if not e['dof']:
            return np.zeros(len(z_m))
        W, H = self.W, self.H
        rn2 = ((px - W / 2.0) ** 2 + (py - H / 2.0) ** 2) / ((W / 2.0) ** 2 + (H / 2.0) ** 2)
        s1 = self.focus_m * 1000.0 * np.maximum(1 + self.fcurv * rn2, 0.05)      # field curvature
        s2 = np.maximum(z_m, 0.02) * 1000.0
        f, N = self.focal, self.fstop
        c = (f / N) * f * (s2 - s1) / (s2 * np.maximum(s1 - f, 1.0)) / self.film * W
        return c * np.where(c < 0, e['front_mult'], e['back_mult'])


def _appearance(e, X, P, V, idx, motion, vis, bounce, sun):
    """Each mote's light (key and bounce parts, (n, 3) each), own size in px, how much of it adds to the blur, and
    its fibre ends (for lint)."""
    su, cam = X.su, X.cam
    st = motion.start
    kind = st['kind'][idx]
    hh = st['h'][idx].astype(np.float64)
    steady = float(e['steady'])
    L = light.light_dir(e['light_az'], e['light_el'])
    soft_r = (e['shadow_soft'] + 12.0 * steady) * 0.01 / su
    v = light.soft_visibility(vis, P, L, soft_r) if vis is not None else np.ones(len(P))
    dist = np.linalg.norm(P - cam.pos, axis=1)
    dist_m = dist * su
    Vd = (cam.pos - P) / dist[:, None]
    z_m = -((P - cam.pos) @ cam.back) * su
    ppm = X.focal_px / np.maximum(z_m, 1e-3)
    u = hh[:, 1]
    rate = e['tumble'] * np.where(kind == FLAKE, 0.12 + 0.78 * hh[:, 3],
                                  np.where(kind == GRIT, 0.1 + 0.6 * hh[:, 3], 0.03 + 0.27 * hh[:, 3]))
    ax = tumble_axis(hh[:, 4], hh[:, 5], hh[:, 6], rate, X.tsec)
    n = len(idx)
    sigma, rs = np.zeros(n), np.zeros(n)
    gph = np.full(n, e['forward'])
    tcol = np.ones((n, 3))
    m = kind == GRIT
    dg = st['grit_d'][idx][m].astype(np.float64)
    sigma[m] = (dg / 40.0) ** 2 * (1 + 0.12 * (1 - steady) * np.sin(2 * math.pi * (rate[m] * X.tsec * 1.3 + hh[m, 7])))
    rs[m] = 0.5 * dg * 1e-6 * ppm[m]
    m = kind == FLAKE
    df = 30.0 * 8.0 ** (u[m] ** 2.2) * e['flake_scale']
    nl = np.abs(ax[m] @ L)
    nv = np.abs((ax[m] * Vd[m]).sum(1))
    nl, nv = nl + (0.5 - nl) * steady, nv + (0.5 - nv) * steady
    sigma[m] = (df * df / 1600.0) * (0.4 * nl * (0.25 + 0.75 * nv) + 0.03)
    rs[m] = 0.5 * df * 1e-6 * ppm[m]
    gph[m] = e['forward'] * 0.85
    tcol[m] = np.where((hh[m, 7] > 0.5)[:, None], [1.04, 1.0, 0.94], [0.95, 0.95, 0.97])
    m = kind == FIBER
    ln = 300.0 * 8.0 ** (u[m] ** 1.8) * e['fiber_scale']
    th = 8.0 + 12.0 * hh[m, 8]
    tl = ax[m] @ L
    sigma[m] = (ln * th / 1600.0) * np.sqrt(np.maximum(1 - tl * tl, 0.03)) * 0.5
    rs[m] = th * 1e-6 * ppm[m]
    gph[m] = e['forward'] * 0.5
    if m.any():
        h9 = hh[m, 9][:, None]
        tcol[m] = np.select([h9 < 0.4, h9 < 0.58, h9 < 0.7, h9 < 0.84],
                            [np.array([0.95, 0.95, 0.93]), np.array([0.5, 0.58, 0.82]), np.array([0.85, 0.45, 0.42]),
                             np.array([0.36, 0.34, 0.32])], np.array([0.82, 0.74, 0.55]))
    m = kind == FLUFF
    dl = 200.0 * 6.0 ** (u[m] ** 2.0) * e['fluff_scale']
    sigma[m] = (dl * dl / 1600.0) * 0.07
    rs[m] = 0.5 * dl * 1e-6 * ppm[m]
    gph[m] = e['forward'] * 0.45
    tcol[m] = [0.9, 0.9, 0.93]
    # the key light: forward scattering and, for flakes, glints
    c = (-L * Vd).sum(1)
    hg = (1 - gph ** 2) / np.power(np.maximum(1 + gph ** 2 - 2 * gph * c, 1e-4), 1.5)
    key = sun[None, :] * (v * hg)[:, None]
    m = (kind == FLAKE) & (v > 0)
    if m.any() and e['glint'] * (1 - steady) > 0:
        Hh = L[None, :] + Vd[m]
        Hh /= np.linalg.norm(Hh, axis=1, keepdims=True)
        gw = shutter_weight(np.arange(4) / 3.0, e['shutter_shape'])
        gw = gw * 4.0 / gw.sum()
        g = 0.0
        for i in range(4):
            tt = X.tsec + (X.t_open + (X.t_close - X.t_open) * i / 3.0) * X.spf
            g = g + gw[i] * np.abs((tumble_axis(hh[m, 4], hh[m, 5], hh[m, 6], rate[m], tt) * Hh).sum(1)) ** 90
        key[m] += sun[None, :] * (v[m] * g * 0.25 * 8 * e['glint'] * (1 - steady) * (0.4 + 0.6 * hh[m, 2]))[:, None]
    # bounce light off the sunlit set, plus an overall fill (worked out for a white light: times the light's colour)
    bpos, bnrm, bflx, bamb = bounce
    Ei = np.tile(bamb * sun, (n, 1))
    Pm = P * su
    for i in range(len(bpos)):
        dv = bpos[i] - Pm
        r2 = (dv * dv).sum(1) + 0.12
        lv = dv / np.sqrt(r2)[:, None]
        Ei += (bflx[i] * sun)[None, :] * (np.maximum(-(lv @ bnrm[i]), 0) / (math.pi * r2))[:, None]
    fill = Ei * (e['bounce'] * e['shadow_fill'] * 1.5)
    scale = (e['brightness'] * 0.8 * sigma / np.maximum(dist_m, 0.05) ** 2)[:, None] * \
        np.asarray(e['tint'], np.float64)[None, :] * tcol * (X.focal_px / 1100.0) ** 2
    fiber = None
    fm = kind == FIBER
    if fm.any():
        half_len = (300.0 * 8.0 ** (u[fm] ** 1.8) * e['fiber_scale']) * 0.5e-6 / su
        x0f, y0f, _ = cam.project(P[fm] - ax[fm] * half_len[:, None], X.W, X.H)
        x1f, y1f, _ = cam.project(P[fm] + ax[fm] * half_len[:, None], X.W, X.H)
        fiber = (fm, x0f, y0f, x1f, y1f, (hh[fm, 2] - 0.5) * 0.6)
    rsk = np.where(kind == FLUFF, 0.6, np.where(kind == FIBER, 0.0, 0.5))
    return key * scale, fill * scale, rs, rsk, fiber, dist


def _holdout(e, X, px, py, dist, R):
    """How much of each mote shows (0..1): compared with what each pixel sees, weighted by its coverage, averaged
    over the mote's blur with Steady so it slides behind an edge instead of blinking off."""
    n = len(px)
    if not e['holdout']:
        return np.ones(n)
    s = X.surf
    steady = float(e['steady'])
    taps = [(0.0, 0.0)] if steady <= 0 else [(0.0, 0.0), (1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0)]
    rr = steady * R * 0.7071
    hold = np.zeros(n)
    for ox, oy in taps:
        sx = np.clip(((px + ox * rr) / X.W * X.W0).astype(np.intp), 0, X.W0 - 1)
        sy = np.clip(((py + oy * rr) / X.H * X.H0).astype(np.intp), 0, X.H0 - 1)
        surf = s.dist[sy, sx].astype(np.float64)
        cov = s.cov[sy, sx].astype(np.float64)
        occl = np.where(surf > 0, splat.smooth01(((dist - surf) / np.maximum(surf, 1e-6) + 0.01) / 0.04), 0.0)
        fgw = np.ones(n)
        if e['fg_far'] > e['fg_near']:
            fgw = splat.smooth01((surf - e['fg_near']) / (e['fg_far'] - e['fg_near']))
        elif e['fg_near'] > 0:
            fgw = (surf >= e['fg_near']).astype(np.float64)
        hold += 1 - occl * fgw * cov
    return hold / len(taps)


def render_frame(e, shot, frame, motion, vis, bounce, stats=None):
    """One frame. e = Settings.engine(frame). Returns float32 images: rgb (H, W, 3), alpha (H, W), and, when asked
    for, noholdout, dust_key and dust_fill (H, W, 3)."""
    X = Frame(e, shot, frame)
    W, H = X.W, X.H
    split, unheld_on = bool(e['out_keyfill']), bool(e['out_noholdout'])
    nch = (6 if split else 3) + (3 if unheld_on else 0)
    img = np.zeros((H, W, nch))
    st = motion.at(frame)
    out = {}
    if motion.n:
        cam, su = X.cam, X.su
        d = st.P - cam.pos
        zc = -(d @ cam.back)
        weight = motion.edge_weight(st.local)
        weight *= splat.smooth01((zc * su - 0.06) / 0.24)                 # fade out right at the lens
        weight *= cam_fade(np.linalg.norm(d, axis=1), e['cam_fade_near'], e['cam_fade_far'])
        px, py, _ = cam.project(st.P, W, H)
        keep = (weight > 1e-4) & (zc * su > 0.05) & (px > -X.margin) & (px < W + X.margin) & \
               (py > -X.margin) & (py < H + X.margin)
        idx = np.where(keep)[0]
        if len(idx):
            P, V, w = st.P[idx], st.V[idx], weight[idx]
            key, fill, rs, rsk, fiber, dist = _appearance(e, X, P, V, idx, motion, vis, bounce, light.sun_rgb(e))
            key *= w[:, None]
            fill *= w[:, None]
            _draw(e, X, P, V, key, fill, rs, rsk, dist, fiber, img, split, unheld_on, stats)
    held = img[:, :, 0:3] + img[:, :, 3:6] if split else img[:, :, 0:3]
    out['rgb'] = held.astype(np.float32)
    if split:
        out['dust_key'] = img[:, :, 0:3].astype(np.float32)
        out['dust_fill'] = img[:, :, 3:6].astype(np.float32)
    if unheld_on:
        out['noholdout'] = img[:, :, -3:].astype(np.float32)
    if e['alpha'] == 'matte':
        lum = held @ np.array([0.2126, 0.7152, 0.0722])
        out['alpha'] = np.clip(lum * e['matte_gain'], 0.0, 1.0).astype(np.float32)
    else:
        out['alpha'] = np.zeros((H, W), np.float32)
    return out


def _draw(e, X, P, V, key, fill, rs, rsk, dist, fiber, img, split, unheld_on, stats):
    W, H, n = X.W, X.H, len(P)
    cam = X.cam
    px, py, zc = cam.project(P, W, H)
    steady = float(e['steady'])
    R = np.clip(np.abs(X.coc(e, px, py, zc * X.su)) * 0.5, 0.25 + 0.5 * steady, X.max_r)
    # the streak: where each mote is on screen as the shutter opens and closes (its motion and the camera's)
    xo, yo, _ = X.cam_o.project(P + V * (X.t_open * X.spf), W, H)
    xc, yc, _ = X.cam_c.project(P + V * (X.t_close * X.spf), W, H)
    streak = np.hypot(xc - xo, yc - yo)
    Reff = np.sqrt(R * R + rsk * rs * rs)
    S = np.clip(np.ceil(streak / np.maximum(Reff * 0.6, 0.6)), 1, MAX_STREAK).astype(np.intp)
    K = np.ones(n, np.intp)
    fdx = fdy = bend = np.zeros(n)
    if fiber is not None:                                   # lint fibres spread along their length too
        fm, x0f, y0f, x1f, y1f, fb = fiber
        flen = np.hypot(x1f - x0f, y1f - y0f)
        K[fm] = np.clip(np.ceil(flen / np.maximum(R[fm] * 0.7, 0.7)), 1, 24)
        fdx, fdy, bend = np.zeros(n), np.zeros(n), np.zeros(n)
        fdx[fm], fdy[fm], bend[fm] = x1f - x0f, y1f - y0f, fb
    hold = _holdout(e, X, px, py, dist, Reff)
    NS = S * K
    rep = np.repeat(np.arange(n), NS)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(NS) - NS, NS)
    Ks = K[rep]
    si, ki = j // Ks, j % Ks
    ts = (si + 0.5) / S[rep]
    kf = np.where(Ks > 1, ki / np.maximum(Ks - 1, 1) - 0.5, 0.0)
    Xs = xo[rep] + (xc[rep] - xo[rep]) * ts + fdx[rep] * kf - fdy[rep] * bend[rep] * (0.25 - kf * kf)
    Ys = yo[rep] + (yc[rep] - yo[rep]) * ts + fdy[rep] * kf + fdx[rep] * bend[rep] * (0.25 - kf * kf)
    sw = shutter_weight(ts, e['shutter_shape'])
    sw = sw / np.bincount(rep, weights=sw, minlength=n)[rep]          # each mote's samples add up to 1
    hr = hold[rep]
    cols = [key[rep] * (sw * hr)[:, None], fill[rep] * (sw * hr)[:, None]] if split else \
        [(key + fill)[rep] * (sw * hr)[:, None]]
    if unheld_on:
        cols.append((key + fill)[rep] * sw[:, None])
    E = np.concatenate(cols, 1)
    Rr = Reff[rep]
    inside = (Xs > -X.margin) & (Xs < W + X.margin) & (Ys > -X.margin) & (Ys < H + X.margin)
    if stats is not None:
        stats['motes'] = n
        stats['samples'] = int(inside.sum())
    splat.draw(img, Xs[inside], Ys[inside], Rr[inside], E[inside], X.bokeh, stats)


class Renderer(object):
    """Renders frames of one setup, keeping what doesn't change from frame to frame: the shot, the light's reach,
    the bounce lights and the motes' paths."""

    def __init__(self, settings, shot=None):
        from .standin import open_shot
        self.settings = settings
        e = settings.engine(settings['first'])
        self.shot = shot if shot is not None else open_shot(e)
        self.vis = light.visibility(e, self.shot, key=('vis', settings.prep_key()))
        self.bounce = light.bounce_lights(e, self.shot, self.vis, key=('bounce', settings.prep_key()))
        self.motion = DustMotion(e, self.vis)

    def frame(self, f, stats=None):
        e = self.settings.engine(f)
        return render_frame(e, self.shot, f, self.motion, self.vis, self.bounce, stats)
