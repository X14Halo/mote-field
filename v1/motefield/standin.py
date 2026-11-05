"""Built-in stand-in shots: a reading room with a window (a wide camera move and a close-up in the sunbeam), made in
memory as a utility pass would be, for trying the tool, demos and tests. Scene units are centimetres.

    shot = StandinShot('wide', width=1920, height=804)
    shot behaves like shot.Shot: frames, camera(f), surface(f), read(f, role), resolution(f), layers(f)
    shot.plate(f)            a clay render of the set lit by the key light, for the viewer and comp tests
    shot.write(folder)       the same thing as EXR files (utility and plate), for testing the Nuke node

The campfire (for embers) and the walker (for moving things) come back with those features.
"""

import math
import os

import numpy as np

from . import exr, light
from .camera import Camera
from .shot import Surface, build_surface

FIRST, LAST = 1001, 1048
WIDTH, HEIGHT = 1920, 804
SUN = dict(az=25.0, el=33.0, intensity=6.0, kelvin=5000.0)

VIEWS = {
    # a wide move across the room
    'wide': dict(E0=(310.0, 165.0, 320.0), E1=(190.0, 150.0, 290.0), T0=(-160.0, 105.0, -290.0),
                 T1=(-235.0, 118.0, -300.0), focal=24.0, film=24.89, fstop=4.0, focus=430.0,
                 lens=dict(blades=0)),
    # standing in the sunbeam, looking back up it toward the window: motes at the lens are lit (big soft bokeh)
    'close-up': dict(E0=(-170.0, 120.0, -150.0), E1=(-182.0, 118.0, -162.0), T0=(-380.0, 160.0, -430.0),
                     T1=(-386.0, 157.0, -440.0), focal=50.0, film=24.89, fstop=2.8, focus=190.0,
                     lens=dict(blades=7, cateye=0.3, fstop_full=1.4)),
}


def room_boxes():
    """The room: (min, max, albedo, name) in centimetres."""
    B = []

    def box(mn, mx, c, name):
        B.append((tuple(float(v) for v in mn), tuple(float(v) for v in mx), tuple(c), name))
    plaster, wood, dark, trim = (0.36, 0.32, 0.27), (0.24, 0.15, 0.08), (0.12, 0.08, 0.05), (0.42, 0.40, 0.36)
    box((-420, -20, -620), (420, 0, 370), (0.22, 0.14, 0.08), 'floor')
    box((-420, 350, -620), (420, 370, 370), plaster, 'ceiling')
    box((-420, 0, -620), (420, 350, -600), plaster, 'wall_back')
    box((-420, 0, 350), (420, 350, 370), plaster, 'wall_front')
    box((400, 0, -620), (420, 350, 370), plaster, 'wall_right')
    # the left wall, with a window opening: z -400..-140, y 90..290 (no glass, no frame)
    box((-420, 0, -620), (-400, 350, -400), plaster, 'wall_left')
    box((-420, 0, -140), (-400, 350, 370), plaster, 'wall_left')
    box((-420, 0, -400), (-400, 90, -140), plaster, 'wall_left')
    box((-420, 290, -400), (-400, 350, -140), plaster, 'wall_left')
    box((-420, 84, -406), (-385, 90, -134), trim, 'sill')
    for z in (-470, -270, -70, 130):
        box((-400, 326, z - 11), (400, 350, z + 11), dark, 'beam')
    box((-150, 72, -340), (50, 78, -230), wood, 'table')
    for x, z in ((-145, -335), (40, -335), (-145, -240), (40, -240)):
        box((x, 0, z), (x + 6, 72, z + 6), wood, 'table')
    box((-90, 78, -300), (-60, 104, -275), (0.45, 0.42, 0.36), 'vase')
    box((-20, 78, -310), (20, 84, -270), (0.30, 0.10, 0.08), 'books')
    box((-330, 0, -600), (-140, 215, -596), wood, 'bookcase')
    box((-330, 0, -600), (-326, 215, -562), wood, 'bookcase')
    box((-144, 0, -600), (-140, 215, -562), wood, 'bookcase')
    for y in (0, 52, 104, 156, 208):
        box((-330, y, -600), (-140, y + 4, -562), wood, 'bookcase')
    seed = 7
    for y in (4, 56, 108, 160):
        x = -326.0
        while x < -146:
            seed = (seed * 16807) % 2147483647
            w = 2.5 + (seed % 100) / 100.0 * 4.0
            h = 22 + (seed % 37)
            c = (0.10 + (seed % 23) / 60.0, 0.06 + (seed % 17) / 80.0, 0.05 + (seed % 13) / 70.0)
            box((x, y, -596), (min(x + w, -144), y + min(h, 46), -570), c, 'books')
            x += w + 0.4
    box((200, 0, -530), (275, 62, -455), (0.30, 0.21, 0.11), 'crate')
    box((212, 62, -515), (262, 106, -465), (0.26, 0.18, 0.09), 'crate')
    box((150, 0, -210), (232, 44, -128), (0.30, 0.06, 0.05), 'armchair')
    box((212, 44, -210), (232, 104, -128), (0.30, 0.06, 0.05), 'armchair')
    box((150, 44, -214), (232, 66, -200), (0.30, 0.06, 0.05), 'armchair')
    box((150, 44, -138), (232, 66, -124), (0.30, 0.06, 0.05), 'armchair')
    box((-220, 0, -420), (110, 1.2, -150), (0.28, 0.08, 0.06), 'rug')
    box((70, 0, -150), (110, 46, -110), wood, 'stool')
    return B


# ---------------------------------------------------------------------------------------------------------------------
#  Ray tracing boxes
# ---------------------------------------------------------------------------------------------------------------------
def _slab(O, inv, mn, mx):
    tx1, tx2 = (mn[0] - O[0]) * inv[0], (mx[0] - O[0]) * inv[0]
    ty1, ty2 = (mn[1] - O[1]) * inv[1], (mx[1] - O[1]) * inv[1]
    tz1, tz2 = (mn[2] - O[2]) * inv[2], (mx[2] - O[2]) * inv[2]
    nx, ny, nz = np.minimum(tx1, tx2), np.minimum(ty1, ty2), np.minimum(tz1, tz2)
    tmin = np.maximum(np.maximum(nx, ny), nz)
    tmax = np.minimum(np.minimum(np.maximum(tx1, tx2), np.maximum(ty1, ty2)), np.maximum(tz1, tz2))
    return (nx, ny, nz), tmin, tmax


def _groups(boxes):
    groups = {}
    for b in boxes:
        groups.setdefault(b[3], []).append(b)
    return [(np.min([m[0] for m in mem], 0), np.max([m[1] for m in mem], 0), mem) for mem in groups.values()]


def trace(O, D, boxes):
    """Nearest hit of rays from O (3,) along D (N, 3): distance (inf = miss), normal, albedo, box index (-1 = none)."""
    N = len(D)
    Ds = np.where(np.abs(D) < 1e-12, 1e-12, D)
    Oc = tuple(np.full(N, O[k]) for k in range(3))
    inv = (1.0 / Ds[:, 0], 1.0 / Ds[:, 1], 1.0 / Ds[:, 2])
    best = np.full(N, np.inf)
    nrm = np.zeros((N, 3))
    alb = np.zeros((N, 3))
    which = np.full(N, -1, np.int32)
    index = {id(b): i for i, b in enumerate(boxes)}
    for gmn, gmx, mem in _groups(boxes):
        _, a, b = _slab(Oc, inv, gmn, gmx)
        idx = np.where((b >= np.maximum(a, 1e-4)) & (a < best))[0]
        if not len(idx):
            continue
        Oi = tuple(c[idx] for c in Oc)
        Ii = tuple(c[idx] for c in inv)
        for m in mem:
            (nx, ny, nz), tmin, tmax = _slab(Oi, Ii, m[0], m[1])
            ok = (tmax >= np.maximum(tmin, 1e-4)) & (tmin > 1e-4) & (tmin < best[idx])
            if not ok.any():
                continue
            j = idx[ok]
            ax = np.where((nx[ok] >= ny[ok]) & (nx[ok] >= nz[ok]), 0, np.where(ny[ok] >= nz[ok], 1, 2))
            best[j] = tmin[ok]
            n = np.zeros((len(j), 3))
            n[np.arange(len(j)), ax] = -np.sign(Ds[j, ax])
            nrm[j] = n
            alb[j] = m[2]
            which[j] = index[id(m)]
    return best, nrm, alb, which


def trace_camera(cam, W, H, boxes):
    """trace() for a camera's pixel grid, faster: each box is only tested against the pixels its projection covers
    (boxes around the camera, such as the room's walls, against all of them)."""
    D = cam.rays(W, H).reshape(-1, 3)
    N = len(D)
    best = np.full(N, np.inf)
    nrm = np.zeros((N, 3))
    alb = np.zeros((N, 3))
    which = np.full(N, -1, np.int32)
    Ds = np.where(np.abs(D) < 1e-12, 1e-12, D)
    inv = 1.0 / Ds
    O = cam.pos
    for bi, (mn, mx, c, name) in enumerate(boxes):
        mn, mx = np.asarray(mn), np.asarray(mx)
        corners = np.array([[x, y, z] for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
        px, py, z = cam.project(corners, W, H)
        if (z > 1e-3).all():
            x0, x1 = int(max(np.floor(px.min()) - 1, 0)), int(min(np.ceil(px.max()) + 1, W))
            y0, y1 = int(max(np.floor(py.min()) - 1, 0)), int(min(np.ceil(py.max()) + 1, H))
            if x1 <= x0 or y1 <= y0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            idx = (yy * W + xx).ravel()
        elif (z <= 1e-3).all() and not ((O >= mn) & (O <= mx)).all():
            continue                                                   # entirely behind the camera
        else:
            idx = np.arange(N)
        t1 = (mn[None, :] - O) * inv[idx]
        t2 = (mx[None, :] - O) * inv[idx]
        nmin, nmax = np.minimum(t1, t2), np.maximum(t1, t2)
        tmin, tmax = nmin.max(1), nmax.min(1)
        ok = (tmax >= np.maximum(tmin, 1e-4)) & (tmin > 1e-4) & (tmin < best[idx])
        if not ok.any():
            continue
        j = idx[ok]
        ax = np.argmax(nmin[ok], 1)
        best[j] = tmin[ok]
        n = np.zeros((len(j), 3))
        n[np.arange(len(j)), ax] = -np.sign(Ds[j, ax])
        nrm[j] = n
        alb[j] = c
        which[j] = bi
    return D, best, nrm, alb, which


def blocked(P, L, boxes, eps=1e-3):
    """True where the ray from each point toward direction L hits a box (it is in shadow)."""
    N = len(P)
    hit = np.zeros(N, bool)
    if N == 0:
        return hit
    Ls = np.where(np.abs(L) < 1e-12, 1e-12, L)
    Pc = (P[:, 0].copy(), P[:, 1].copy(), P[:, 2].copy())
    inv = tuple(np.full(N, 1.0 / Ls[k]) for k in range(3))
    for gmn, gmx, mem in _groups(boxes):
        _, a, b = _slab(Pc, inv, gmn, gmx)
        cand = np.where((b >= np.maximum(a, eps)) & ~hit)[0]
        if not len(cand):
            continue
        Oi = tuple(c[cand] for c in Pc)
        Ii = tuple(c[cand] for c in inv)
        for m in mem:
            _, a, b = _slab(Oi, Ii, m[0], m[1])
            hit[cand[b >= np.maximum(a, eps)]] = True
    return hit


# ---------------------------------------------------------------------------------------------------------------------
#  The shot
# ---------------------------------------------------------------------------------------------------------------------
def camera(frame, view='wide'):
    """The stand-in camera at any (fractional) frame: an eased move from start to end."""
    V = VIEWS[view]
    u = (frame - FIRST) / float(LAST - FIRST)
    u = min(max(u, -0.5), 1.5)
    u = u * u * (3 - 2 * u) if 0 <= u <= 1 else u
    E = np.array(V['E0']) * (1 - u) + np.array(V['E1']) * u
    T = np.array(V['T0']) * (1 - u) + np.array(V['T1']) * u
    back = E - T
    back /= np.linalg.norm(back)
    right = np.cross([0.0, 1.0, 0.0], back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    hfov = 2 * math.atan(V['film'] / (2 * V['focal']))
    return Camera(E, right, up, back, hfov, focal=V['focal'], film=V['film'], fstop=V['fstop'], focus=V['focus'],
                  lens=dict(V['lens']), shutter=dict(angle=180.0, mode='backward', shape='backwardsShape'))


def settings_for(view):
    """The shot settings that go with a stand-in (as a shot layer's values)."""
    return dict(standin=view, units='centimetres', first=FIRST, last=LAST, fps=24.0, light_az=SUN['az'],
                light_el=SUN['el'], light_int=SUN['intensity'], light_k=SUN['kelvin'])


class StandinShot(object):
    def __init__(self, view='wide', width=WIDTH, height=HEIGHT):
        if view not in VIEWS:
            raise ValueError('no stand-in %r (there are: %s)' % (view, ', '.join(VIEWS)))
        self.view, self.W, self.H = view, int(width), int(height)
        self.boxes = room_boxes()
        self.frames = list(range(FIRST, LAST + 1))
        self._cache = {}

    def camera(self, f):
        return camera(f, self.view)

    def prep_shot(self):
        """A smaller copy for the prep (bounce lights sample every fourth pixel anyway): a quarter of the size, but at
        least 480 pixels wide, so the bounce light is the same whatever size the frames are rendered at."""
        if getattr(self, '_prep', None) is None:
            pw = max(self.W // 4, 480)
            self._prep = StandinShot(self.view, pw, max(int(round(pw * self.H / float(self.W))), 16))
        return self._prep

    def resolution(self, f=None):
        return self.W, self.H

    def blocked(self, P, L):
        return blocked(P, L, self.boxes)

    def _trace(self, f):
        if f not in self._cache:
            cam = self.camera(f)
            D, t, n, alb, which = trace_camera(cam, self.W, self.H, self.boxes)
            hit = np.isfinite(t)
            P = np.where(hit[:, None], cam.pos + D * np.where(hit, t, 0)[:, None], 0.0)
            if len(self._cache) > 3:
                self._cache.pop(next(iter(self._cache)))
            sh = (self.H, self.W)
            self._cache[f] = dict(cam=cam, P=P.reshape(sh + (3,)), N=n.reshape(sh + (3,)), C=alb.reshape(sh + (3,)),
                                  dist=np.where(hit, t, 0.0).reshape(sh), ID=(which + 1).reshape(sh), hit=hit.reshape(sh))
        return self._cache[f]

    def surface(self, f):
        d = self._trace(f)
        s = build_surface(d['cam'], self.W, self.H, P=d['P'].astype(np.float32), Zraw=d['dist'].astype(np.float32),
                          A=d['hit'].astype(np.float32))
        s.info['standin'] = self.view
        return s

    def read(self, f, role):
        d = self._trace(f)
        return {'N': d['N'], 'C': d['C'], 'ID': d['ID'], 'P': d['P'], 'Zraw': d['dist']}.get(role)

    def layers(self, f):
        return {}

    def plate(self, f, intensity=None, kelvin=None):
        """A clay render: the set's colours lit by the key light (with shadows) and a little fill; sky in the window."""
        d = self._trace(f)
        L = light.light_dir(SUN['az'], SUN['el'])
        sun = light.kelvin_rgb(kelvin or SUN['kelvin']) * (intensity or SUN['intensity'])
        P, N, C, hit = d['P'].reshape(-1, 3), d['N'].reshape(-1, 3), d['C'].reshape(-1, 3), d['hit'].reshape(-1)
        nl = np.clip(N @ L, 0, None)
        lit = np.zeros(len(P))
        cand = hit & (nl > 0)
        if cand.any():
            lit[np.where(cand)[0][~blocked(P[cand] + N[cand] * 0.3, L, self.boxes)]] = 1.0
        fill = (intensity or SUN['intensity']) * (0.035 + 0.02 * np.clip(N[:, 1], 0, 1))
        rgb = C * (sun[None, :] * (nl * lit)[:, None] + fill[:, None] * np.array([0.95, 0.97, 1.0]))
        sky = np.array([0.66, 0.76, 0.95]) * (intensity or SUN['intensity']) * 0.45
        rgb = np.where(hit[:, None], rgb, sky[None, :])
        return rgb.reshape(self.H, self.W, 3).astype(np.float32)

    def write(self, folder, frames=None):
        """Writes standin_utility.####.exr and standin_plate.####.exr; returns the two frame patterns."""
        os.makedirs(folder, exist_ok=True)
        for f in frames or self.frames:
            d = self._trace(f)
            cam = d['cam']
            ch = {'A': d['hit'].astype(np.float32), 'depth.Z': d['dist'].astype(np.float32)}
            for i, c in enumerate('RGB'):
                ch['pos.' + c] = d['P'][..., i].astype(np.float32)
                ch['normal.' + c] = d['N'][..., i].astype(np.float32)
                ch['surfaceColor.' + c] = d['C'][..., i].astype(np.float32)
            ch['objectID.R'] = d['ID'].astype(np.float32)
            attrs = {'camToWorld': ('m44f', cam.to_world_m44()), 'focalLength': ('float', cam.focal),
                     'horizontalAperture': ('float', cam.film), 'fStop': ('float', cam.fstop),
                     'focusDistance': ('float', cam.focus), 'motefield/standin': self.view}
            for k, v in cam.lens.items():
                attrs['lens/' + {'blades': 'bladeCount', 'cateye': 'cateye', 'fstop_full': 'fStopFullAperture'}.get(k, k)] \
                    = ('float', float(v))
            exr.write(os.path.join(folder, 'standin_utility.%04d.exr' % f), ch, attrs=attrs, half={'surfaceColor.R',
                      'surfaceColor.G', 'surfaceColor.B', 'normal.R', 'normal.G', 'normal.B'})
            p = self.plate(f)
            exr.write(os.path.join(folder, 'standin_plate.%04d.exr' % f),
                      {'R': p[..., 0], 'G': p[..., 1], 'B': p[..., 2], 'A': np.ones(p.shape[:2], np.float32)})
        return (os.path.join(folder, 'standin_utility.####.exr'), os.path.join(folder, 'standin_plate.####.exr'))


def open_shot(e):
    """The shot for engine values e: the stand-in if one is chosen and there is no utility pass, else the real one."""
    if e['standin'] != 'none' and not e['utility']:
        return StandinShot(e['standin'] if e['standin'] in VIEWS else 'wide')
    from . import pipeline
    from .shot import Shot
    return Shot(e['utility'], extra=e.get('extra_passes', ''), camera_keys=pipeline.CAMERA_KEYS,
                layer_names=pipeline.LAYER_NAMES)


__all__ = ['StandinShot', 'open_shot', 'camera', 'room_boxes', 'Surface']
