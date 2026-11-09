"""MoteField node helpers: the Python that lives inside the node (in its hidden mf_py knob), so there is nothing to
install. The node runs it from memory; it never writes a file.

What it does:
  * setup        wires the internal nodes and links every kernel setting to the node's knobs (runs when the node is
                 created or opened, so a script saved by any Nuke version comes back right);
  * layers       finds the position, normals, depth, coverage and surface colour layers by name;
  * camera       reads the camera (and lens and shutter) from the pass's EXR header, frame by frame;
  * fit box      fits the dust box to the set the camera sees;
  * check        reports what the node found out about the pass.

LINKS and WIRES are filled in by node/build_node.py.
"""
import json
import math
import re

import nuke

VERSION = '@VERSION@'
LINKS = {}            # kernel node -> {param: expression or [expressions per component]}
KERNELS = {}          # kernel node -> kernel name (the prefix of its param knobs)
WIRES = {}            # internal node -> [input node names] (None = leave unconnected)
DEFAULT_BOX = ((-60.0, 175.0, -130.0), (760.0, 330.0, 900.0))
UNITS = (0.01, 1.0, 0.001, 0.0254, 0.3048)          # metres per scene unit, in the order of the Scene units menu

# layer names each kind of data is found under (lower case, letters and digits only)
ROLES = {
    'P': ('pos', 'p', 'pworld', 'position', 'worldposition', 'worldpos', 'pw', 'wp', 'ppass', 'world',
          'positionworld', 'worldp'),
    'N': ('normal', 'n', 'nworld', 'normals', 'worldnormal', 'nw', 'normalworld', 'nn', 'worldnormals'),
    'Z': ('depth', 'z', 'zdepth', 'distance', 'depthfiltered'),
    'Zraw': ('depthnofilter', 'depthunfiltered', 'nofilterdepth', 'depthraw', 'zraw', 'depthclosest',
             'closestdepth', 'zunfiltered', 'znofilter', 'depthnearest'),
    'A': ('alpha', 'implicitalpha', 'coverage', 'alphacoverage'),
    'C': ('surfacecolor', 'surfacecolour', 'albedo', 'diffusealbedo', 'basecolor', 'diffusecolor', 'surfacealbedo'),
}


try:                        # Nuke 12 and older run Python 2, where text can also be unicode
    _STR = (str, unicode)   # noqa: F821
except NameError:
    _STR = (str,)


def _norm(s):
    if not isinstance(s, _STR):
        s = str(s)
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def _inner(grp, name):
    return grp.node(name)


# ---------------------------------------------------------------------------------------------------------------------
#  Setup: wiring and links
# ---------------------------------------------------------------------------------------------------------------------
def wire(grp):
    """Connects every internal node to its inputs by name (the script's own wiring, made certain)."""
    for name, inputs in WIRES.items():
        n = _inner(grp, name)
        if n is None:
            continue
        for i, src in enumerate(inputs):
            if src is None:
                continue
            s = _inner(grp, src)
            if s is not None and n.input(i) is not s:
                n.setInput(i, s)


def _param_knob(node, kernel, param):
    """The knob a BlinkScript node made for a kernel param (KernelName_param), or None."""
    k = node.knob(kernel + '_' + param)
    if k is not None:
        return k
    want = (kernel + '_' + param).lower()
    knobs = node.knobs()
    for name, k in knobs.items():
        if name.lower() == want:
            return k
    # another naming (a different prefix): the shortest knob name ending in _param that no other param explains
    ends = sorted((n for n in knobs if n.lower().endswith('_' + param.lower())), key=len)
    return knobs[ends[0]] if ends else None


def link_kernel(grp, name):
    """Links one kernel node's params to the node's knobs by expression. Returns the params it could not find."""
    node = _inner(grp, name)
    if node is None:
        return []
    kernel = KERNELS[name]
    missing = []
    for param, expr in LINKS.get(name, {}).items():
        k = _param_knob(node, kernel, param)
        if k is None:
            missing.append(param)
            continue
        try:
            if isinstance(expr, (list, tuple)):
                for i, e in enumerate(expr):
                    k.setExpression(e, i)
            else:
                k.setExpression(expr)
        except Exception:
            missing.append(param)
    gpu = node.knob('useGPUIfAvailable')
    if gpu is not None:
        try:
            gpu.setExpression('parent.use_gpu')
        except Exception:
            pass
    return missing


def setup(grp):
    """Wires and links everything; returns {kernel node: [params not found]} for the ones that went wrong."""
    wire(grp)
    bad = {}
    for name in KERNELS:
        m = link_kernel(grp, name)
        if m:
            bad[name] = m
    k = grp.knob('mf_version')
    if k is not None and k.value() != VERSION:
        k.setValue(VERSION)
    return bad


# ---------------------------------------------------------------------------------------------------------------------
#  Layers
# ---------------------------------------------------------------------------------------------------------------------
def layers_of(node):
    """{layer: [channel names]} of a node's output, in order."""
    out = {}
    if node is None:
        return out
    try:
        chans = node.channels()
    except Exception:
        return out
    for c in chans:
        layer, _, chan = c.partition('.')
        out.setdefault(layer, []).append(chan)
    return out


def pick_layer(layers, role):
    """The layer for a role, by name (exact names first, then names that end with one)."""
    names = ROLES[role]
    by = dict((_norm(l), l) for l in layers)
    for n in names:
        if n in by:
            return by[n]
    for n in names:
        if len(n) < 3:
            continue
        for k, l in by.items():
            if k.endswith(n):
                return l
    return None


def detect_layers(grp, force=False):
    """Points the internal shuffles at the pass's layers. Returns {role: layer or None}."""
    util = grp.input(0)
    col = grp.input(2)
    lu, lc = layers_of(util), layers_of(col)
    found = {}
    for role in ('P', 'N', 'Z', 'Zraw', 'A'):
        found[role] = pick_layer(lu, role)
    if found['Z'] is not None and found['Z'] == found['Zraw']:
        found['Z'] = None
    found['C'] = pick_layer(lc, 'C') or pick_layer(lu, 'C')
    if not force and grp.knob('mf_layers_set') is not None and grp['mf_layers_set'].value():
        return found
    pos, dep, nrm, alb = (_inner(grp, n) for n in ('mf_pos', 'mf_depth', 'mf_nrm', 'mf_alb'))
    if pos is not None:
        pos['in'].setValue(found['P'] or 'none')
        if found['A'] and found['A'] != 'rgba':
            pos['in2'].setValue(found['A'])
            pos['alpha'].setValue('red2')
        else:
            pos['in2'].setValue('rgba')
            pos['alpha'].setValue('alpha2')
    if dep is not None:
        dep['in'].setValue(found['Z'] or 'none')
        dep['in2'].setValue(found['Zraw'] or 'none')
    if nrm is not None:
        nrm['in'].setValue(found['N'] or 'none')
    if alb is not None:
        src = col if (col is not None and pick_layer(lc, 'C')) else util
        if src is not None:
            alb.setInput(0, _inner(grp, 'colour' if src is col else 'utility'))
        alb['in'].setValue(found['C'] or 'none')
    grp['mf_use_albedo'].setValue(1 if found['C'] else 0)
    if util is not None and grp.knob('mf_layers_set') is not None:
        grp['mf_layers_set'].setValue(True)
    return found


# ---------------------------------------------------------------------------------------------------------------------
#  The camera from the EXR header (mirrors motefield/camera.py, without numpy)
# ---------------------------------------------------------------------------------------------------------------------
_NUM = re.compile(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


def _nums(v):
    if isinstance(v, bool):
        return [float(v)]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            out += _nums(x)
        return out
    if isinstance(v, (int, float)) or type(v).__name__ == 'long':
        return [float(v)]
    if isinstance(v, _STR):
        return [float(x) for x in _NUM.findall(v)]
    try:
        return [float(x) for x in v]
    except Exception:
        return []


def _flatten_json(prefix, v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            _flatten_json(prefix + '/' + k, x, out)
    else:
        out[prefix] = v


def header_items(md):
    """(key, value, group) for every metadata entry, then the fields of JSON-valued strings (group = the JSON
    object's path)."""
    plain, nested = [], []
    for k, v in md.items():
        plain.append((k, v, None))
        if isinstance(v, _STR) and v[:1] == '{' and len(v) < 2000000:
            try:
                d = json.loads(v)
            except ValueError:
                continue
            flat = {}
            _flatten_json(k, d, flat)
            nested += [(fk, fv, fk.rsplit('/', 1)[0]) for fk, fv in flat.items()]
    return plain + nested


_LENS = (('fstop_full', ('fstopfullaperture', 'fullaperture', 'fstopfull', 'maxaperture', 'wideopenfstop')),
         ('cateye', ('cateyebokeh', 'cateye')),
         ('field_curv', ('fieldcurvature',)),
         ('anamorphic', ('anamorphicaspect', 'anamorphicsqueeze', 'squeeze', 'anamorphic')),
         ('blades', ('bladecount', 'numblades', 'blades', 'irisblades')))


def _rank_groups(items, cam):
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


def _match(k, v, nested, cam, src, lens, take):
    kn = _norm(k.rsplit('/', 1)[-1])
    full = _norm(k)
    lens_field = None
    for f, ends in _LENS:
        if any(kn.endswith(e) for e in ends):
            lens_field = f
            break
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
        return
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
    elif kn.endswith('shuttershape') and isinstance(v, _STR):
        cam.setdefault('shutter_shape', v)
        src.setdefault('shutter_shape', k)
    elif full.endswith('cameradirection') and isinstance(v, _STR):
        cam.setdefault('shutter_dir', v)
        src.setdefault('shutter_dir', k)


def _inv4(m):
    """Inverse of a 4x4 matrix (list of 16, row-major) by Gauss-Jordan elimination."""
    a = [list(m[r * 4:r * 4 + 4]) + [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-15:
            raise ValueError('singular camera matrix')
        a[c], a[p] = a[p], a[c]
        d = a[c][c]
        a[c] = [x / d for x in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0.0:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[r][4 + c] for r in range(4) for c in range(4)]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(a):
    n = math.sqrt(_dot(a, a)) + 1e-12
    return [a[0] / n, a[1] / n, a[2] / n]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def camera_from_metadata(md):
    """{'pos', 'right', 'up', 'back', 'th', 'focal', 'film', 'fstop', 'focus', 'lens', 'shutter', 'source'} from one
    frame's metadata, or None when it holds no camera."""
    items = header_items(md)
    cam, src, lens = {}, {}, {}

    def take(field, k, v, n=1):
        vals = _nums(v)
        if len(vals) >= n and field not in cam:
            cam[field] = vals[:n] if n > 1 else vals[0]
            src[field] = k

    for k, v, g in items:
        if g is None:
            _match(k, v, False, cam, src, lens, take)
    score = _rank_groups(items, cam)
    for k, v, g in sorted([it for it in items if it[2] is not None], key=lambda it: -score.get(it[2], 0)):
        _match(k, v, True, cam, src, lens, take)
    if 'toworld' not in cam and 'w2c' in cam:
        try:
            cam['toworld'] = _inv4(cam['w2c'])
        except ValueError:
            return None
    if 'toworld' not in cam:
        return None
    M = [float(x) for x in cam['toworld']]
    rows = [M[0:4], M[4:8], M[8:12], M[12:16]]
    if sum(abs(rows[i][3]) for i in range(3)) > sum(abs(x) for x in rows[3][:3]):   # column-vector convention
        rows = [[rows[c][r] for c in range(4)] for r in range(4)]
    back = _unit(rows[2][:3])
    r = rows[0][:3]
    d = _dot(r, back)
    right = _unit([r[0] - back[0] * d, r[1] - back[1] * d, r[2] - back[2] * d])
    up = _cross(back, right)
    focal, ap = cam.get('focal'), cam.get('aperture')
    if ap is not None and ap < 5:
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
    shutter = {}
    if 'shutteropen' in cam and 'shutterclose' in cam:
        o, c = cam['shutteropen'], cam['shutterclose']
        shutter['angle'] = abs(c - o) * 360.0
        shutter['mode'] = 'backward' if c <= 0 else 'forward' if o >= 0 else 'centred'
    if 'shutter_angle' in cam:
        shutter['angle'] = cam['shutter_angle']
    dn = _norm(cam.get('shutter_dir', ''))
    if dn in ('backward', 'forward', 'centered', 'centred', 'center', 'centre'):
        shutter['mode'] = 'centred' if dn.startswith('cent') else dn
    if cam.get('shutter_shape'):
        shape = _norm(cam['shutter_shape'])
        shutter['shape'] = 'backwardsShape' if 'backwardsshape' in shape else \
            'box' if shape in ('box', 'square', 'flat') else cam['shutter_shape']
    return dict(pos=rows[3][:3], right=right, up=up, back=back, th=math.tan(hfov / 2), focal=focal, film=film,
                fstop=cam.get('fstop'), focus=cam.get('focus'), lens=lens, shutter=shutter, source=src)


def _metadata(node, frame):
    try:
        return node.metadata(None, frame) or {}
    except TypeError:
        return node.metadata() or {}
    except Exception:
        return {}


def _same(a, b):
    return all(abs(x - y) <= 1e-6 * max(1.0, abs(y)) for x, y in zip(a, b))


VEC_KNOBS = (('cam_pos', 'pos'), ('cam_right', 'right'), ('cam_up', 'up'), ('cam_back', 'back'))
HDR_KNOBS = (('hdr_focal', 'focal'), ('hdr_film', 'film'), ('hdr_fstop', 'fstop'), ('hdr_focus', 'focus'))
LENS_KNOBS = (('hdr_blades', 'blades'), ('hdr_cateye', 'cateye'), ('hdr_anamorphic', 'anamorphic'),
              ('hdr_field_curv', 'field_curv'), ('hdr_fstop_full', 'fstop_full'))


def _flat(c):
    return list(c['pos']) + list(c['right']) + list(c['up']) + list(c['back']) + [c['th']] + \
        [c[k] or 0.0 for k in ('focal', 'film', 'fstop', 'focus')]


def _set_knob(k, values, frames):
    """Sets a knob to one value (static) or to linear keys per frame. values: list per frame of a number or list."""
    vals0 = values[0] if isinstance(values[0], (list, tuple)) else [values[0]]
    n = len(vals0)
    static = all(_same(v if isinstance(v, (list, tuple)) else [v], vals0) for v in values)
    for i in range(n):
        try:
            k.clearAnimated(i)
        except Exception:
            pass
    if static:
        k.setValue(vals0 if n > 1 else vals0[0])
        return
    for i in range(n):
        k.setAnimated(i)
        for f, v in zip(frames, values):
            k.setValueAt((v[i] if isinstance(v, (list, tuple)) else v), f, i)
        try:
            a = k.animation(i)
            a.changeInterpolation(a.keys(), nuke.LINEAR)
        except Exception:
            pass


def read_camera(grp, frames=None):
    """Reads the camera from the utility input's header on every frame (or the given ones) into the camera knobs.
    Returns (camera on the first frame or None, 'static' / 'moving', how many frames had one)."""
    util = grp.input(0)
    if util is None:
        return None, '', 0
    if frames is None:
        first, last = int(grp['first'].value()), int(grp['last'].value())
        if last < first:
            first, last = last, first
        probe = sorted(set([first, (first + last) // 2, last]))
        cams = [camera_from_metadata(_metadata(util, f)) for f in probe]
        cams = [c for c in cams if c]
        moving = len(cams) > 1 and not all(_same(_flat(c), _flat(cams[0])) for c in cams)
        frames = list(range(first, last + 1)) if moving else probe[:1]
    got, cams = [], []
    for f in frames:
        c = camera_from_metadata(_metadata(util, f))
        if c:
            got.append(f)
            cams.append(c)
    if not cams:
        return None, '', 0
    for kname, field in VEC_KNOBS:
        _set_knob(grp[kname], [c[field] for c in cams], got)
    _set_knob(grp['cam_th'], [c['th'] for c in cams], got)
    for kname, field in HDR_KNOBS:
        _set_knob(grp[kname], [c[field] or 0.0 for c in cams], got)
    lens = cams[0]['lens']
    for kname, field in LENS_KNOBS:
        grp[kname].setValue(lens.get(field, 0) or 0)
    sh = cams[0]['shutter']
    if sh and not grp['mf_shutter_read'].value():
        if 'angle' in sh:
            grp['shutter'].setValue(sh['angle'])
        modes = ('backward', 'centred', 'forward')
        if sh.get('mode') in modes:
            grp['shutter_mode'].setValue(modes.index(sh['mode']))
        if sh.get('shape') in ('backwardsShape', 'box'):
            grp['shutter_shape'].setValue(('backwardsShape', 'box').index(sh['shape']))
        grp['mf_shutter_read'].setValue(True)
    src = cams[0]['source']
    grp['mf_cam_src'].setValue(json.dumps(dict(
        frames=[got[0], got[-1]], n=len(got), pos=[round(x, 4) for x in cams[0]['pos']], keys=sorted(set(src.values())),
        range=[int(grp['first'].value()), int(grp['last'].value())])))
    return cams[0], ('moving' if len(cams) > 1 else 'static'), len(cams)


# ---------------------------------------------------------------------------------------------------------------------
#  Reading the node's own images (fit box, check)
# ---------------------------------------------------------------------------------------------------------------------
def _sample(node, x, y, chans='rgba'):
    names = ('red', 'green', 'blue', 'alpha')
    return [node.sample(chans + '.' + c, x + 0.5, y + 0.5) for c in names]


def _cam_now(grp):
    return dict(pos=list(grp['cam_pos'].value()), right=list(grp['cam_right'].value()), up=list(grp['cam_up'].value()),
                back=list(grp['cam_back'].value()), th=grp['cam_th'].value())


def _ray(cam, W, H, px, py):
    tv = cam['th'] * H / float(W)
    a = (px / W * 2 - 1) * cam['th']
    b = (py / H * 2 - 1) * tv
    d = [cam['right'][i] * a + cam['up'][i] * b - cam['back'][i] for i in range(3)]
    return _unit(d)


def _percentile(vals, q):
    s = sorted(vals)
    if not s:
        return 0.0
    k = (len(s) - 1) * q / 100.0
    i = int(math.floor(k))
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def surface_points(grp, nx=96, ny=54):
    """World points (scene units) of clean pixels on a grid, from the node's surface image."""
    surf = _inner(grp, 'mf_surface')
    W, H = surf.width(), surf.height()
    cam = _cam_now(grp)
    pts = []
    for j in range(ny):
        for i in range(nx):
            px = int((i + 0.5) * W / nx)
            py = int((j + 0.5) * H / ny)
            ray = surf.sample('rgba.blue', px + 0.5, py + 0.5)
            clean = surf.sample('rgba.alpha', px + 0.5, py + 0.5)
            if clean < 0.5 or ray <= 0:
                continue
            d = _ray(cam, W, H, px + 0.5, py + 0.5)
            pts.append([cam['pos'][k] + d[k] * ray for k in range(3)])
    return pts, cam


def fit_box(grp, far_pct=85.0):
    """Fits the dust box to the set (mirrors motefield/place.py): from just in front of the camera out to most of the
    set, over the floor the camera sees, up to a metre above the camera."""
    pts, cam = surface_points(grp)
    if len(pts) < 50:
        raise ValueError('Not enough of the set is visible on this frame to fit a box.')
    su = UNITS[int(grp['units'].value())]
    dist = [math.sqrt(sum((p[k] - cam['pos'][k]) ** 2 for k in range(3))) for p in pts]
    lim = _percentile(dist, far_pct)
    near = [p for p, d in zip(pts, dist) if d <= lim]
    lo = [_percentile([p[k] for p in near], 1.0) for k in range(3)]
    hi = [_percentile([p[k] for p in near], 99.0) for k in range(3)]
    pad = 0.5 / su
    lo = [min(lo[k], cam['pos'][k] - pad) for k in range(3)]
    hi = [max(hi[k], cam['pos'][k] + pad) for k in range(3)]
    hi[1] = min(hi[1], cam['pos'][1] + 1.0 / su)
    centre = [(lo[k] + hi[k]) / 2 for k in range(3)]
    size = [max(hi[k] - lo[k], 0.2 / su) for k in range(3)]
    grp['box_center'].setValue(centre)
    grp['box_size'].setValue(size)
    grp['box_rot'].setValue(0.0)
    return centre, size


def mote_count(grp):
    su = UNITS[int(grp['units'].value())]
    size = [max(s * su, 0.02) for s in grp['box_size'].value()]
    n = math.floor(grp['density'].value() * grp['master_amount'].value() * size[0] * size[1] * size[2] + 0.5)
    return int(max(0, min(n, int(grp['max_motes'].value()), MAX_MOTES))), int(n)


MAX_MOTES = 65536


def facts(grp):
    """What the facts kernel found out about the pass."""
    n = _inner(grp, 'mf_facts')
    f0, f1, f2 = (_sample(n, x, 0) for x in range(3))
    return dict(p_premult=f0[0] > 0.5, has_alpha=f0[1] > 0.5, has_p=f0[2] > 0.5, has_z=int(f0[3]) % 2 == 1,
                has_zraw=f0[3] >= 2, z_view=f1[0] > 0.5, z_premult=f1[1] > 0.5, zraw_view=f1[2] > 0.5,
                zraw_premult=f1[3] > 0.5, edge_samples=int(f2[0]), full_samples=int(f2[1]), samples=int(f2[2]))


def check(grp):
    """A report on the shot: [(level, title, detail)], level 'ok', 'note', 'warn' or 'problem'."""
    out = []
    if grp.input(0) is None:
        return [('problem', 'No utility pass', 'Connect the Read of the utility pass to the utility input.')]
    found = detect_layers(grp, force=False)
    names = dict(P='position', N='normals', Z='depth', Zraw='unfiltered depth', A='coverage', C='surface colour')
    have = ', '.join('%s (%s)' % (names[r], found[r]) for r in ('P', 'N', 'Z', 'Zraw', 'A', 'C') if found.get(r))
    out.append(('ok' if found.get('P') or found.get('Z') or found.get('Zraw') else 'problem', 'Layers',
                have or 'No position or depth layer found. Pick them on the Shot tab.'))
    try:
        src = json.loads(grp['mf_cam_src'].value() or '{}')
    except ValueError:
        src = {}
    if src.get('n'):
        out.append(('ok', 'Camera', 'From the pass header on %d frame(s) (%s).' % (src['n'], ', '.join(src.get('keys', [])[:6]))))
    else:
        out.append(('problem', 'Camera', 'No camera found in the pass header. Press Read camera, or check the pass '
                    'has the camera matrices in its header.'))
    try:
        fa = facts(grp)
        how = []
        how.append('positions %s by coverage' % ('premultiplied' if fa['p_premult'] else 'not premultiplied'))
        if fa['has_z']:
            how.append('depth along the %s%s' % ('view axis' if fa['z_view'] else 'ray',
                                                  ', premultiplied' if fa['z_premult'] else ''))
        if fa['has_zraw']:
            how.append('unfiltered depth along the %s' % ('view axis' if fa['zraw_view'] else 'ray'))
        how.append('holdouts from %s' % ('the unfiltered depth' if fa['has_zraw'] else 'the depth' if fa['has_z']
                                         else 'the positions'))
        out.append(('ok' if fa['has_alpha'] else 'note', 'The pass',
                    '; '.join(how) + ('' if fa['has_alpha'] else '; no coverage (alpha): taken as 1 wherever there is data')))
    except Exception as exc:
        out.append(('warn', 'The pass', 'Could not read it yet (%s).' % exc))
    try:
        pts, cam = surface_points(grp, 48, 27)
        if pts:
            su = UNITS[int(grp['units'].value())]
            dist = sorted(math.sqrt(sum((p[k] - cam['pos'][k]) ** 2 for k in range(3))) for p in pts)
            med = dist[len(dist) // 2] * su
            if med > 300 or med < 0.05:
                out.append(('warn', 'Scene units', 'The set is a median %.3g m from the camera in %s: check Scene '
                            'units.' % (med, grp['units'].value())))
            else:
                out.append(('ok', 'Scene units', 'The set is a median %.3g m from the camera.' % med))
    except Exception:
        pass
    n, want = mote_count(grp)
    if want > n:
        out.append(('warn', 'Motes', '%d motes (density x box wants %d; capped by Max motes or the node\'s limit '
                    'of %d).' % (n, want, MAX_MOTES)))
    else:
        out.append(('ok', 'Motes', '%d motes.' % n))
    return out


def report_html(items):
    col = dict(ok='#7c7', note='#9bd', warn='#eb6', problem='#e77')
    return '<br>'.join('<span style="color:%s">%s</span> <b>%s</b>: %s' % (col[l], l.upper(), t, d.replace('<', '&lt;'))
                       for l, t, d in items)


def status(grp):
    try:
        src = json.loads(grp['mf_cam_src'].value() or '{}')
    except ValueError:
        src = {}
    n, want = mote_count(grp)
    bits = []
    if grp.input(0) is None:
        bits.append('connect the utility pass to the <b>utility</b> input')
    else:
        bits.append('camera from the pass' if src.get('n') else '<span style="color:#e77">no camera yet: press Read camera</span>')
    bits.append('%d motes' % n + (' (capped)' if want > n else ''))
    grp['mf_status'].setValue(' &middot; '.join(bits))


# ---------------------------------------------------------------------------------------------------------------------
#  Events
# ---------------------------------------------------------------------------------------------------------------------
def connected(grp):
    """The utility input was connected: layers, frame range, camera, and a box if it still has the default."""
    util = grp.input(0)
    if util is None:
        return
    detect_layers(grp, force=True)
    try:
        f0, f1 = int(util.firstFrame()), int(util.lastFrame())
        if (int(grp['first'].value()), int(grp['last'].value())) == (1001, 1048) and f1 >= f0:
            grp['first'].setValue(f0)
            grp['last'].setValue(f1)
    except Exception:
        pass
    cam, how, n = read_camera(grp)
    if cam is not None and _same(list(grp['box_size'].value()), DEFAULT_BOX[1]) and \
            _same(list(grp['box_center'].value()), DEFAULT_BOX[0]):
        try:
            fit_box(grp)
        except Exception:
            pass
    status(grp)


def button(grp, which):
    if which == 'read_camera':
        cam, how, n = read_camera(grp)
        if cam is None:
            nuke.message('MoteField: no camera found in the header of the utility pass.')
        status(grp)
    elif which == 'fit_box':
        try:
            fit_box(grp)
        except Exception as exc:
            nuke.message('MoteField: %s' % exc)
        status(grp)
    elif which == 'check_shot':
        items = check(grp)
        grp['mf_report'].setValue(report_html(items))
    elif which == 'find_layers':
        detect_layers(grp, force=True)
        status(grp)
    elif which == 'repair':
        bad = setup(grp)
        nuke.message('MoteField: ' + ('everything is linked.' if not bad else 'could not link: %s' % bad))


def camera_changed(grp):
    """True when the pass's header no longer says what the camera knobs were read from (a new version of the pass,
    or a new frame range)."""
    util = grp.input(0)
    if util is None:
        return False
    try:
        src = json.loads(grp['mf_cam_src'].value() or '{}')
    except ValueError:
        src = {}
    if not src.get('n'):
        return False
    if src.get('range') and src['range'] != [int(grp['first'].value()), int(grp['last'].value())]:
        return True
    c = camera_from_metadata(_metadata(util, src['frames'][0]))
    return c is not None and not _same([round(x, 4) for x in c['pos']], src['pos'])


def created(grp):
    """The node was made (pasted, or its script opened): wire and link it; if it arrived already connected to a pass
    it has not read yet, read it."""
    setup(grp)
    if grp.input(0) is not None and not grp['mf_cam_src'].value():
        connected(grp)
    else:
        status(grp)


def knob_changed(grp, knob):
    name = knob.name() if knob is not None else ''
    if name == 'inputChange':
        if grp.input(0) is not None and not grp['mf_cam_src'].value():
            connected(grp)
        else:
            status(grp)
    elif name == 'showPanel':
        if grp['mf_version'].value() != VERSION:
            setup(grp)
        if camera_changed(grp):
            read_camera(grp)
        status(grp)
    elif name in ('density', 'master_amount', 'box_size', 'units', 'max_motes'):
        status(grp)
