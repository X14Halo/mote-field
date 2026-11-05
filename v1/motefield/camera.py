"""The shot camera, read from the utility pass's EXR header.

Renderers write the camera under their own names, so keys are matched loosely by what they end with (camToWorld,
focalLength, fStop, focusDistance, horizontalFOV...). Some renderers also put the whole render camera in one JSON
string attribute; lens details found only there (blade count, cat-eye, field curvature, full aperture, shutter
open and close) are read from it too. Plain attributes win over JSON ones, and lengths are never taken from JSON (its
units aren't stated).

A pipeline that knows its exact header names can pin them in pipeline.py (CAMERA_KEYS).
"""

import json
import math
import re

import numpy as np


class Camera(object):
    """A pinhole camera in scene units. right, up and back are unit vectors; it looks down -back."""

    def __init__(self, pos, right, up, back, hfov, focal=None, film=None, fstop=None, focus=None, lens=None,
                 shutter=None, source=None):
        self.pos = np.asarray(pos, np.float64)
        self.right = np.asarray(right, np.float64)
        self.up = np.asarray(up, np.float64)
        self.back = np.asarray(back, np.float64)
        self.hfov = float(hfov)
        self.focal, self.film, self.fstop, self.focus = focal, film, fstop, focus
        self.lens = dict(lens or {})          # blades, cateye, anamorphic, field_curv, fstop_full
        self.shutter = dict(shutter or {})    # angle (degrees), mode (backward / centred / forward), shape
        self.source = dict(source or {})      # which header key each value came from (for the shot check)

    def project(self, P, W, H):
        """World points (N, 3) -> pixel x, y (row 0 at the top, pixel centres at .5) and distance along the view."""
        d = np.asarray(P, np.float64) - self.pos
        x, y, z = d @ self.right, d @ self.up, -(d @ self.back)
        th = math.tan(self.hfov / 2)
        tv = th * H / float(W)
        zs = np.where(z > 1e-9, z, 1e-9)
        return (x / zs / th * 0.5 + 0.5) * W, (0.5 - y / zs / tv * 0.5) * H, z

    def rays(self, W, H):
        """(H, W, 3) unit world directions through each pixel centre."""
        th = math.tan(self.hfov / 2)
        tv = th * H / float(W)
        xs = ((np.arange(W) + 0.5) / W * 2 - 1) * th
        ys = (1 - (np.arange(H) + 0.5) / H * 2) * tv
        X, Y = np.meshgrid(xs, ys)
        d = X[..., None] * self.right + Y[..., None] * self.up - self.back
        return d / np.linalg.norm(d, axis=-1, keepdims=True)

    def lerp(self, other, t):
        """This camera blended toward other (t in 0..1), for sub-frame shutter samples."""
        if t <= 0:
            return self
        if t >= 1:
            return other
        r, u, b = _orthonormal(self.right * (1 - t) + other.right * t, self.up * (1 - t) + other.up * t,
                               self.back * (1 - t) + other.back * t)
        return Camera(self.pos * (1 - t) + other.pos * t, r, u, b, self.hfov * (1 - t) + other.hfov * t, self.focal,
                      self.film, self.fstop, self.focus, self.lens, self.shutter, self.source)

    def to_world_m44(self):
        """Row-vector camera-to-world matrix (translation in the last row), as EXR headers usually store it."""
        r, u, b, p = self.right, self.up, self.back, self.pos
        return [r[0], r[1], r[2], 0, u[0], u[1], u[2], 0, b[0], b[1], b[2], 0, p[0], p[1], p[2], 1]

    def summary(self):
        return dict(position=[round(float(x), 3) for x in self.pos], hfov_deg=round(math.degrees(self.hfov), 4),
                    focal_mm=self.focal, film_mm=self.film, fstop=self.fstop, focus=self.focus, lens=self.lens,
                    shutter=self.shutter)


def _orthonormal(right, up, back):
    b = back / (np.linalg.norm(back) + 1e-12)
    r = right - b * (right @ b)
    r /= np.linalg.norm(r) + 1e-12
    return r, np.cross(b, r), b


_NUM = re.compile(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


def _nums(v):
    if isinstance(v, bool):
        return [float(v)]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            out += _nums(x)
        return out
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, str):
        return [float(x) for x in _NUM.findall(v)]
    return []


def _norm(k):
    return ''.join(ch for ch in k.lower() if ch.isalnum())


def _flatten_json(prefix, v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            _flatten_json(prefix + '/' + k, x, out)
    else:
        out[prefix] = v


def header_items(headers):
    """(key, value, group) for every attribute of every part (group None), then the fields of JSON-valued string
    attributes (group = the path of the JSON object the field sits in)."""
    plain, nested = [], []
    for h in headers:
        for k, v in h.items():
            plain.append((k, v, None))
            if isinstance(v, str) and v[:1] == '{' and len(v) < 2000000:
                try:
                    d = json.loads(v)
                except ValueError:
                    continue
                flat = {}
                _flatten_json(k, d, flat)
                nested += [(fk, fv, fk.rsplit('/', 1)[0]) for fk, fv in flat.items()]
    return plain + nested


def _rank_groups(items, cam):
    """JSON objects ordered by how well they agree with the plain header's camera: a header can list several
    cameras (a default perspective camera among them), and only the one that rendered the shot should count."""
    score = {}
    for k, v, g in items:
        if g is None:
            continue
        kn = _norm(k.rsplit('/', 1)[-1])
        vals = _nums(v)
        s = score.setdefault(g, 0)
        for field, ends, w in (('fstop', ('fstop',), 1), ('focus', ('focusdistance',), 1),
                               ('toworld', ('camtoworld', 'cameratoworld'), 2)):
            if field in cam and any(kn == e for e in ends) and vals:
                want = cam[field] if isinstance(cam[field], list) else [cam[field]]
                if len(vals) >= len(want) and all(abs(a - b) <= 1e-3 * max(1.0, abs(b)) for a, b in zip(vals, want)):
                    s += w
        score[g] = s
    return score


# what each lens or shutter value's key ends with (normalised: lower case, letters and digits only)
_LENS = (('fstop_full', ('fstopfullaperture', 'fullaperture', 'fstopfull', 'maxaperture', 'wideopenfstop')),
         ('cateye', ('cateyebokeh', 'cateye')),
         ('field_curv', ('fieldcurvature',)),
         ('anamorphic', ('anamorphicaspect', 'anamorphicsqueeze', 'squeeze', 'anamorphic')),
         ('blades', ('bladecount', 'numblades', 'blades', 'irisblades')))


def from_headers(headers, keys=None):
    """A Camera from EXR headers, or None if they don't hold one. keys: optional exact names to use first
    ({'toworld': 'myRenderer/camToWorld', 'focal': ..., ...}), from pipeline.py."""
    items = header_items(headers)
    cam, src, lens = {}, {}, {}

    def take(field, k, v, n=1):
        vals = _nums(v)
        if len(vals) >= n and field not in cam:
            cam[field] = vals[:n] if n > 1 else vals[0]
            src[field] = k

    if keys:
        byname = {k: v for k, v, _ in items}
        for field, name in keys.items():
            if name in byname:
                take(field, name, byname[name], 16 if field in ('toworld', 'w2c') else 1)
    for k, v, g in items:
        if g is None:
            _match(k, v, False, cam, src, lens, take)
    # then the JSON objects, the one that agrees best with the plain header first
    score = _rank_groups(items, cam)
    for k, v, g in sorted([it for it in items if it[2] is not None], key=lambda it: -score.get(it[2], 0)):
        _match(k, v, True, cam, src, lens, take)
    return _build(cam, src, lens)


def _match(k, v, nested, cam, src, lens, take):
    """Files one header attribute under the camera value it is, if it is one."""
    kn = _norm(k.rsplit('/', 1)[-1])
    full = _norm(k)
    lens_field = next((f for f, ends in _LENS if any(kn.endswith(e) for e in ends)), None)
    if lens_field:
        vals = _nums(v)
        if vals and lens_field not in lens:
            lens[lens_field] = vals[0]
            src['lens.' + lens_field] = k
        return
    if kn.endswith('camtoworld') or kn.endswith('cameratoworld'):
        take('toworld', k, v, 16)
    elif kn.endswith('worldtocamera') or kn.endswith('worldtocam'):
        take('w2c', k, v, 16)
    elif nested:
        if kn in ('shutteropen', 'shutterclose'):
            take(kn, k, v)
        return                                  # lengths from JSON: units unknown
    elif 'horizontalfov' in kn or kn in ('fovx', 'hfov'):
        take('hfov', k, v)
    elif kn.endswith('focallength'):
        take('focal', k, v)
    elif kn.endswith('fstop') or kn.endswith('fnumber'):
        take('fstop', k, v)
    elif kn.endswith('focusdistance'):
        take('focus', k, v)
    elif any(kn.endswith(e) for e in ('horizontalaperture', 'filmbackwidth', 'aperturewidth', 'filmwidth',
                                      'hfilmaperture')):
        take('aperture', k, v)
    elif kn.endswith('shutterangle'):
        take('shutter_angle', k, v)
    elif kn.endswith('shuttershape') and isinstance(v, str):
        cam.setdefault('shutter_shape', v)
        src.setdefault('shutter_shape', k)
    elif full.endswith('cameradirection') and isinstance(v, str):
        cam.setdefault('shutter_dir', v)
        src.setdefault('shutter_dir', k)


def _build(cam, src, lens):
    if 'toworld' not in cam and 'w2c' in cam:
        cam['toworld'] = list(np.linalg.inv(np.array(cam['w2c'], np.float64).reshape(4, 4)).reshape(16))
    if 'toworld' not in cam:
        return None
    M = np.array(cam['toworld'], np.float64).reshape(4, 4)
    if np.abs(M[:3, 3]).sum() > np.abs(M[3, :3]).sum():        # column-vector convention: transpose to row-vector
        M = M.T
    r, u, b = _orthonormal(M[0, :3], M[1, :3], M[2, :3])
    focal, ap = cam.get('focal'), cam.get('aperture')
    if ap is not None and ap < 5:              # given in inches
        ap *= 25.4
    if cam.get('hfov'):
        hfov = math.radians(cam['hfov'])
    elif focal and ap:
        hfov = 2 * math.atan(ap / (2 * focal))
    else:
        return None
    film = ap if ap else (2 * focal * math.tan(hfov / 2) if focal else None)
    if 'blades' in lens:
        lens['blades'] = int(round(lens['blades']))
    # the shutter: an angle and a direction, or open and close times in frames (-0.5 .. 0 = 180 degrees backward)
    shutter = {}
    if 'shutteropen' in cam and 'shutterclose' in cam:
        o, c = cam['shutteropen'], cam['shutterclose']
        shutter['angle'] = abs(c - o) * 360.0
        shutter['mode'] = 'backward' if c <= 0 else 'forward' if o >= 0 else 'centred'
    if 'shutter_angle' in cam:
        shutter['angle'] = cam['shutter_angle']
    d = _norm(cam.get('shutter_dir', ''))
    if d in ('backward', 'forward', 'centered', 'centred', 'center', 'centre'):
        shutter['mode'] = 'centred' if d.startswith('cent') else d
    if cam.get('shutter_shape'):
        shape = _norm(cam['shutter_shape'])
        shutter['shape'] = 'backwardsShape' if 'backwardsshape' in shape else \
            'box' if shape in ('box', 'square', 'flat') else cam['shutter_shape']
    return Camera(M[3, :3], r, u, b, hfov, focal=focal, film=film, fstop=cam.get('fstop'), focus=cam.get('focus'),
                  lens=lens, shutter=shutter, source=src)
