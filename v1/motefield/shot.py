"""The shot: utility pass frames on disk, what each layer is, the camera per frame, and what each pixel sees.

A frame can be spread over several EXR files (a geometry pass, a mattes pass...). Layers are found by name in all of
them; the first file that has a layer wins.

What each pixel sees comes back as a Surface, made safe to use:

  * coverage comes from the main alpha (or an implicit-alpha / coverage layer); without one, from where there is
    position or depth;
  * filtered passes are usually premultiplied by coverage, with empty pixels at 0, so an edge pixel's position sits
    partway toward the world origin. That is detected (by reprojecting edge pixels into the camera) and undone by
    dividing by coverage;
  * an unfiltered depth layer, when there is one, is a real surface at every pixel, so holdouts use it;
  * pixels that mix two surfaces (an edge, hair, leaves, motion blur) are marked, so the light map, Fit box and
    bounce lights only use clean single-surface pixels as 3D points.
"""

import glob
import os
import re

import numpy as np

from . import camera as _camera
from . import exr

# PIPELINE: layer names each role is found by (lower case, letters and digits only). Add the studio's here or in
#   pipeline.py (LAYER_NAMES), which is merged in.
ROLES = {
    'P': ('pos', 'p', 'pworld', 'position', 'worldposition', 'worldpos', 'pw', 'wp', 'ppass', 'world', 'positionworld',
          'worldp'),
    'N': ('normal', 'n', 'nworld', 'normals', 'worldnormal', 'nw', 'normalworld', 'nn', 'worldnormals'),
    'Z': ('depth', 'z', 'zdepth', 'distance', 'depthfiltered'),
    'Zraw': ('depthnofilter', 'depthunfiltered', 'nofilterdepth', 'depthraw', 'zraw', 'depthclosest', 'closestdepth',
             'zunfiltered', 'znofilter', 'depthnearest'),
    'A': ('alpha', 'implicitalpha', 'coverage', 'alphacoverage'),
    'MV': ('backward', 'backwardmotion', 'motionback', 'motionbackward', 'mvback', 'backmotion', 'motionvectors',
           'motionvector', 'motion', 'mv'),
    'MVF': ('forward', 'forwardmotion', 'motionforward', 'mvforward', 'forwardmotionvector'),
    'C': ('surfacecolor', 'surfacecolour', 'albedo', 'diffusealbedo', 'basecolor', 'diffusecolor', 'surfacealbedo'),
    'ID': ('sceneobjectid', 'objectid', 'objid', 'id', 'idr', 'objectidr', 'matteid'),
}
VECTOR = {'P': 3, 'N': 3, 'C': 3, 'MV': 2, 'MVF': 2}
SCALAR = ('Z', 'Zraw', 'A', 'ID')
ROLE_NAMES = {'P': 'world position', 'N': 'normals', 'Z': 'depth', 'Zraw': 'unfiltered depth', 'A': 'coverage',
              'MV': 'backward motion vectors', 'MVF': 'forward motion vectors', 'C': 'surface colour',
              'ID': 'object IDs'}


def _norm(s):
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def _comp_index(c):
    c = c.lower()
    return 0 if c in ('r', 'x', 'u', 'red') else 1 if c in ('g', 'y', 'v', 'green') else \
        2 if c in ('b', 'z', 'w', 'blue') else -1


def split_channel(part, name):
    """(layer, component) of a channel. Bare channels of the first part are the main image (layer '')."""
    k = name.rfind('.')
    if k >= 0:
        return name[:k], name[k + 1:]
    if len(name) == 1 and name in 'RGBAZY':
        return ('' if part.index == 0 else part.name), name
    return name, ''


class Layer(object):
    """A role found in a frame's files: which file, which part, which channels (in component order)."""

    def __init__(self, role, file_index, part, name, channels):
        self.role, self.file_index, self.part_index, self.name, self.channels = role, file_index, part.index, name, \
            channels
        types = {c.name: c.type for c in part.channels}
        self.types = [exr.PIXEL_NAMES.get(types[c], '?') for c in channels]
        self.compression = part.compression
        self.lossy = part.compression in exr.LOSSY

    def describe(self):
        return '%s (%s, %s)' % (self.name or 'main image', '/'.join(sorted(set(self.types))),
                                exr.COMPRESSION.get(self.compression, self.compression))


def survey(files, extra_names=None):
    """{role: Layer} found across the open ExrFiles of one frame, plus 'mattes': [(file, part, channel), ...] (ID
    mattes such as charMatte3.G) and 'crypto': {...} when a Cryptomatte layer is present."""
    names = {r: tuple(v) + tuple((extra_names or {}).get(r, ())) for r, v in ROLES.items()}
    found, mattes, crypto = {}, [], None
    for fi, f in enumerate(files):
        groups = {}
        for part in f.parts:
            for c in part.channels:
                layer, comp = split_channel(part, c.name)
                groups.setdefault((part.index, layer), []).append((comp, c.name))
            for k, v in part.header.items():
                if crypto is None and k.startswith('cryptomatte/') and k.endswith('/name'):
                    crypto = dict(file=fi, part=part.index, layer=str(v), key=k[:-len('name')])
        for (pi, layer), comps in groups.items():
            part = f.parts[pi]
            ln = _norm(layer)
            if layer == '' and any(comp == 'A' for comp, _ in comps) and 'A' not in found:
                found['A'] = Layer('A', fi, part, 'alpha', [n for comp, n in comps if comp == 'A'])
            if 'matte' in ln and not ln.startswith('crypto'):
                mattes += [(fi, pi, n) for comp, n in comps if _comp_index(comp) >= 0 or comp == 'A']
            for role, keys in names.items():
                if role in found or ln not in keys:
                    continue
                if role in VECTOR:
                    want = [None] * VECTOR[role]
                    for comp, n in comps:
                        i = _comp_index(comp)
                        if 0 <= i < len(want) and want[i] is None:
                            want[i] = n
                    if all(want):
                        found[role] = Layer(role, fi, part, layer, want)
                else:
                    pick = sorted(comps, key=lambda cn: (cn[0].lower() not in ('', 'z', 'r', 'y', 'a'), cn[0]))
                    found[role] = Layer(role, fi, part, layer, [pick[0][1]])
    found['mattes'] = mattes
    found['crypto'] = crypto
    return found


# ---------------------------------------------------------------------------------------------------------------------
#  Frame patterns
# ---------------------------------------------------------------------------------------------------------------------
_HASHES = re.compile(r'#+|@+|%0?(\d*)d|\$F(\d?)')


def frame_path(pattern, f):
    """The file for frame f: #### (padded to the number of #), %04d, $F4 and @@@@ all work."""
    m = _HASHES.search(pattern)
    if not m:
        return pattern
    tok = m.group(0)
    if tok[0] in '#@':
        pad = len(tok)
    elif tok.startswith('%'):
        pad = int(m.group(1) or 0)
    else:
        pad = int(m.group(2) or 0)
    return pattern[:m.start()] + ('%0*d' % (pad, int(f))) + pattern[m.end():]


def frames_on_disk(pattern):
    """Sorted frame numbers that exist for a pattern."""
    m = _HASHES.search(pattern)
    if not m:
        return []
    head, tail = pattern[:m.start()], pattern[m.end():]
    rx = re.compile(re.escape(os.path.basename(head)) + r'(-?\d+)' + re.escape(tail) + '$')
    out = []
    for p in glob.glob(glob.escape(head) + '*' + glob.escape(tail)):
        mm = rx.match(os.path.basename(p))
        if mm:
            out.append(int(mm.group(1)))
    return sorted(set(out))


def split_patterns(text):
    return [s.strip() for s in re.split(r'[;\n]', text or '') if s.strip()]


# ---------------------------------------------------------------------------------------------------------------------
#  What a pixel sees
# ---------------------------------------------------------------------------------------------------------------------
class Surface(object):
    """What each pixel of one frame sees, made safe to use (see the module notes). All arrays are (H, W[, 3]).

        cov      coverage 0..1
        P        world position (scene units), divided by coverage where the pass was premultiplied; 0 where empty
        dist     distance from the camera to the surface, for holdouts (0 = nothing there)
        clean    a single surface fills the pixel: safe to use as a 3D point
        info     how it was worked out (for the shot check)
    """

    def __init__(self, W, H, cam, cov, P, dist, clean, info):
        self.W, self.H, self.cam = W, H, cam
        self.cov, self.P, self.dist, self.clean, self.info = cov, P, dist, clean, info

    def points(self, step=1):
        """(N, 3) clean world points, every step-th pixel."""
        m = self.clean[::step, ::step]
        return self.P[::step, ::step][m]


def _reproj_error(cam, P, W, H, mask, limit=20000):
    """Median distance, in pixels, from where each masked pixel's position projects to the pixel itself."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return None
    if len(ys) > limit:
        pick = np.linspace(0, len(ys) - 1, limit).astype(np.intp)
        ys, xs = ys[pick], xs[pick]
    px, py, z = cam.project(P[ys, xs].astype(np.float64), W, H)
    err = np.hypot(px - (xs + 0.5), py - (ys + 0.5))
    err = np.where(z > 0, err, 1e9)
    return float(np.median(err))


def _depth_jumps(d, rel=0.04):
    """Pixels whose depth differs from a neighbour by more than rel: edges, where two surfaces meet."""
    j = np.zeros(d.shape, bool)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = d[max(dy, 0):d.shape[0] + min(dy, 0) or None, max(dx, 0):d.shape[1] + min(dx, 0) or None]
        b = d[max(-dy, 0):d.shape[0] + min(-dy, 0) or None, max(-dx, 0):d.shape[1] + min(-dx, 0) or None]
        big = np.abs(a - b) > rel * np.maximum(np.minimum(a, b), 1e-6)
        j[max(dy, 0):d.shape[0] + min(dy, 0) or None, max(dx, 0):d.shape[1] + min(dx, 0) or None] |= big
        j[max(-dy, 0):d.shape[0] + min(-dy, 0) or None, max(-dx, 0):d.shape[1] + min(-dx, 0) or None] |= big
    return j


def build_surface(cam, W, H, P=None, Z=None, Zraw=None, A=None):
    """A Surface from the raw layers of one frame (any may be None except one of P, Z, Zraw)."""
    info = {}
    f64 = np.float64
    if A is not None:
        cov = np.clip(A, 0.0, 1.0).astype(np.float32)
        info['coverage'] = 'alpha'
    else:
        have = np.zeros((H, W), bool)
        if P is not None:
            have |= np.abs(P).sum(-1) > 0
        for d in (Z, Zraw):
            if d is not None:
                have |= np.isfinite(d) & (d > 0)
        cov = have.astype(np.float32)
        info['coverage'] = 'none in the pass: taken as 1 wherever there is position or depth'
    some = cov > 0
    edge = (cov > 0.02) & (cov < 0.98)
    info['edge_pixels'] = int(edge.sum())
    info['empty_fraction'] = float(1.0 - some.mean())
    safe_cov = np.maximum(cov, 1e-6)[..., None]
    if P is not None:
        P = P.astype(np.float32)
        premult = False
        if edge.sum() >= 20:
            e_raw = _reproj_error(cam, P, W, H, edge)
            e_div = _reproj_error(cam, P / safe_cov, W, H, edge)
            info['edge_reprojection_px'] = dict(as_stored=e_raw, divided_by_coverage=e_div)
            premult = e_div is not None and e_raw is not None and e_div < e_raw
        info['position_premultiplied'] = premult
        if premult:
            P = np.where(some[..., None], P / safe_cov, 0.0).astype(np.float32)
        else:
            P = np.where(some[..., None], P, 0.0).astype(np.float32)
        ray = np.linalg.norm(P.astype(f64) - cam.pos, axis=-1)
    else:
        ray = None
    full = cov >= 0.98

    def as_distance(d, name):
        """A depth layer as distance along each pixel's ray: works out whether it was stored along the ray or along
        the view axis, and whether it was premultiplied, by comparing with the positions."""
        d = np.where(np.isfinite(d), d, 0.0).astype(f64)
        if ray is None:
            info[name + '_kind'] = 'assumed distance along the ray (no position layer to check against)'
            return np.where(some, np.maximum(d, 0.0), 0.0)
        m = full & (d > 0) & (ray > 0)
        kind = 'ray'
        if m.sum() >= 20:
            rays = cam.rays(W, H)
            cosang = np.abs(rays @ cam.back)                # 1 at the centre, less toward the corners
            along = np.median(np.abs(d[m] / ray[m] - 1))
            zdepth = np.median(np.abs(d[m] / (ray[m] * cosang[m]) - 1))
            if zdepth < along:
                kind = 'view axis'
                d = d / np.maximum(cosang, 1e-6)
        info[name + '_kind'] = 'distance along the ray' if kind == 'ray' else 'depth along the view axis (converted)'
        if edge.sum() >= 20:
            mm = edge & (ray > 0)
            raw_err = np.median(np.abs(d[mm] / ray[mm] - 1))
            div_err = np.median(np.abs(d[mm] / cov[mm] / ray[mm] - 1))
            if div_err < raw_err:
                info[name + '_premultiplied'] = True
                d = d / np.maximum(cov, 1e-6)
            else:
                info[name + '_premultiplied'] = False
        return np.where(some, np.maximum(d, 0.0), 0.0)

    zraw = as_distance(Zraw, 'unfiltered_depth') if Zraw is not None else None
    zf = as_distance(Z, 'depth') if Z is not None else None
    if zraw is not None:
        dist = zraw
        info['holdouts_from'] = 'unfiltered depth'
    elif zf is not None:
        dist = zf
        info['holdouts_from'] = 'depth'
    else:
        dist = np.where(some, ray, 0.0)
        info['holdouts_from'] = 'world position'
    if P is None:                                           # positions from depth and the camera
        P = (cam.pos + cam.rays(W, H) * dist[..., None]).astype(np.float32)
        P[~some] = 0
        ray = dist
        info['position_from'] = 'depth and the camera'
    # clean pixels: fully covered by one surface
    clean = full & (ray > 0)
    if zraw is not None:
        mixed = full & (np.abs(zraw - ray) > 0.02 * np.maximum(ray, 1e-6))
        info['mixed_by_unfiltered_depth'] = float(mixed.sum() / max(full.sum(), 1))
        clean &= ~mixed
    jumps = _depth_jumps(np.where(some, ray, 0.0))
    clean &= ~jumps
    info['clean_fraction'] = float(clean.sum() / max(some.sum(), 1))
    return Surface(W, H, cam, cov, P, dist.astype(np.float32), clean, info)


# ---------------------------------------------------------------------------------------------------------------------
#  The shot
# ---------------------------------------------------------------------------------------------------------------------
class Shot(object):
    """The utility pass (and any extra passes) of a shot, frame by frame.

        shot = Shot('/path/util.####.exr', extra='/path/mattes.####.exr')
        shot.frames            frames found on disk
        shot.camera(f)         the camera on frame f (fractional frames blend the neighbours)
        shot.surface(f)        a Surface
        shot.layers(f)         {role: Layer}
    """

    # PIPELINE: a shot picker can build Shot from the shot context instead of a path (see pipeline.py resolve_shot).
    def __init__(self, pattern, extra='', camera_keys=None, layer_names=None, cache_frames=3):
        self.pattern = pattern
        self.patterns = [pattern] + split_patterns(extra)
        self.camera_keys, self.layer_names = camera_keys, layer_names
        self.frames = frames_on_disk(pattern)
        self._cams, self._surfaces, self._layers = {}, {}, {}
        self.cache_frames = cache_frames

    def paths(self, f):
        return [frame_path(p, f) for p in self.patterns]

    def open(self, f):
        """The frame's files, opened (close them, or use the result as a context manager)."""
        return _Frame([exr.ExrFile(p) for p in self.paths(f) if os.path.exists(p)], self.paths(f))

    def layers(self, f):
        if f not in self._layers:
            with self.open(f) as fr:
                self._layers[f] = survey(fr.files, self.layer_names)
        return self._layers[f]

    def nearest(self, f):
        """f if it is on disk, else the nearest frame that is (the camera holds outside the frames there are)."""
        if not self.frames or f in self.frames:
            return f
        return min(self.frames, key=lambda x: (abs(x - f), x))

    def camera(self, f):
        fi = int(np.floor(f))
        if f != fi:                          # between frames (the shutter): blend the neighbours
            a, b = self.camera(fi), self.camera(fi + 1)
            return a if a is b else a.lerp(b, f - fi)
        fi = self.nearest(fi)
        if fi not in self._cams:
            with self.open(fi) as fr:
                cam = _camera.from_headers([h for fl in fr.files for h in fl.headers], self.camera_keys)
            if cam is None:
                raise ValueError('%s has no camera in its header (camToWorld or worldToCamera, and a field of view '
                                 'or focal length and film back).' % os.path.basename(self.paths(fi)[0]))
            self._cams[fi] = cam
        return self._cams[fi]

    def read(self, f, role):
        """A role's channels on frame f as float32: (H, W) for scalars, (H, W, n) for vectors; None if absent."""
        L = self.layers(f).get(role)
        if L is None:
            return None
        with self.open(f) as fr:
            d = fr.files[L.file_index].read(L.part_index, L.channels)
        if role in VECTOR:
            return np.stack([d[c] for c in L.channels], -1)
        return d[L.channels[0]]

    def resolution(self, f):
        with self.open(f) as fr:
            L = self.layers(f)
            for role in ('P', 'Z', 'Zraw', 'A'):
                if L.get(role):
                    p = fr.files[L[role].file_index].parts[L[role].part_index]
                    return p.width, p.height
            p = fr.files[0].parts[0]
            return p.width, p.height

    def surface(self, f):
        if f in self._surfaces:
            return self._surfaces[f]
        L = self.layers(f)
        if not any(L.get(r) for r in ('P', 'Z', 'Zraw')):
            raise ValueError('Frame %d has no world position or depth layer.' % f)
        W, H = self.resolution(f)
        s = build_surface(self.camera(f), W, H, P=self.read(f, 'P'), Z=self.read(f, 'Z'),
                          Zraw=self.read(f, 'Zraw'), A=self.read(f, 'A'))
        if len(self._surfaces) >= self.cache_frames:
            self._surfaces.pop(next(iter(self._surfaces)))
        self._surfaces[f] = s
        return s


class _Frame(object):
    def __init__(self, files, paths):
        self.files, self.paths = files, paths

    def __enter__(self):
        if not self.files:
            raise FileNotFoundError('No file for this frame: %s' % self.paths[0])
        return self

    def __exit__(self, *exc):
        for f in self.files:
            f.close()
