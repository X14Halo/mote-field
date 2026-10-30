"""
MoteField for Nuke (prototype): camera-aware dust motes for comp.

Plain Python + numpy only (no plugins, no BlinkScript, no NukeX), so it runs in regular Nuke 15, on the farm, or outside Nuke.

Setup (once per Nuke session)
    In the Script Editor (Windows > Script Editor), run:
        import sys; sys.path.append('/folder/with/this/file'); import motefield; motefield.create()
    or put the file in ~/.nuke and just run:  import motefield; motefield.create()

Trying it
    1. Press "Create stand-in shot" for a placeholder interior with a window opening and a moving camera, or point
       "Utility pass" at a shot's utility EXRs (world position + the camera in the EXR header; #### for the frame).
    2. Set the dust box (scene units) and its edge fade, and the key light's direction (a directional light).
    3. "Render this frame" to look, "Render frame range" for the shot. Frames go to the cache folder and the node's
       output reads them back. Merge it over the plate with plus. The noholdout layer has the dust without holdouts.

The dust is simulated from a fixed seed, so a frame always comes out the same, whatever order frames are rendered in.

Shadows from
    stand-in geometry   exact, for the stand-in shot
    utility pass        a light depth map built from the utility pass. It only knows what the camera saw, so with
                        "Interior" on, light only comes in where the camera saw sky (a window).
    none                everything in the box is lit
"""

import math, os, struct, time, zlib, json, hashlib
import numpy as np

__version__ = '0.1'

# =====================================================================================================
#  EXR input / output (scanline; single or multi-part; NONE, RLE, ZIPS, ZIP)
# =====================================================================================================
_COMP_NAMES = {0: 'NONE', 1: 'RLE', 2: 'ZIPS', 3: 'ZIP', 4: 'PIZ', 5: 'PXR24', 6: 'B44', 7: 'B44A', 8: 'DWAA', 9: 'DWAB'}
_COMP_LINES = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256}
_PIX_DTYPE = {0: '<u4', 1: '<f2', 2: '<f4'}
_PIX_BYTES = {0: 4, 1: 2, 2: 4}


def _cstr(b, o):
    e = b.find(b'\0', o)
    return b[o:e].decode('latin-1'), e + 1


def _read_attr(b, o, typ, size):
    if typ == 'chlist':
        chans, p = [], o
        while b[p] != 0:
            n, q = _cstr(b, p)
            ptype = struct.unpack_from('<i', b, q)[0]
            chans.append((n, ptype))
            p = q + 16
        return chans
    if typ in ('compression', 'lineOrder', 'envmap', 'deepImageState'):
        return b[o]
    fmt = {'box2i': '<4i', 'int': '<i', 'float': '<f', 'double': '<d', 'm44f': '<16f', 'm44d': '<16d', 'm33f': '<9f',
           'v2f': '<2f', 'v3f': '<3f', 'v2i': '<2i', 'v3i': '<3i', 'box2f': '<4f'}.get(typ)
    if typ == 'string':
        return b[o:o + size].decode('utf-8', 'replace')
    if fmt:
        v = struct.unpack_from(fmt, b, o)
        return v[0] if len(v) == 1 else list(v)
    return None


def _read_header(b, o):
    h = {}
    while True:
        name, a = _cstr(b, o)
        if name == '':
            return h, a
        typ, c = _cstr(b, a)
        size = struct.unpack_from('<i', b, c)[0]
        h[name] = _read_attr(b, c + 4, typ, size)
        o = c + 4 + size


def _un_rle(src, n):
    out, i, o = bytearray(n), 0, 0
    while i < len(src) and o < n:
        c = src[i] - 256 if src[i] > 127 else src[i]
        i += 1
        if c < 0:
            c = -c
            out[o:o + c] = src[i:i + c]
            o += c
            i += c
        else:
            out[o:o + c + 1] = bytes([src[i]]) * (c + 1)
            o += c + 1
            i += 1
    return bytes(out)


def _un_predict(raw):
    t = np.frombuffer(raw, np.uint8).astype(np.int64)
    if t.size == 0:
        return raw
    d = t.copy()
    d[1:] -= 128
    t = (np.cumsum(d) & 255).astype(np.uint8)
    half = (t.size + 1) // 2
    out = np.empty_like(t)
    out[0::2] = t[:half]
    out[1::2] = t[half:]
    return out.tobytes()


def _predict(raw):
    t = np.frombuffer(raw, np.uint8)
    half = (t.size + 1) // 2
    il = np.empty_like(t)
    il[:half] = t[0::2]
    il[half:] = t[1::2]
    d = il.astype(np.int16)
    d[1:] = (d[1:] - d[:-1] + 128) & 255
    return d.astype(np.uint8).tobytes()


class ExrFile(object):
    """A scanline EXR (single or multi-part). read(part, [channel names]) -> {name: float32 array (H, W)}."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, 'rb')
        try:
            import mmap
            b = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:            # no mmap (e.g. the browser version): read the whole file
            self._f.seek(0)
            b = self._f.read()
        self._b = b
        if struct.unpack_from('<I', b, 0)[0] != 20000630:
            raise ValueError('%s is not an OpenEXR file' % path)
        ver = struct.unpack_from('<I', b, 4)[0]
        tiled, deep, self.multi = bool(ver & 0x200), bool(ver & 0x800), bool(ver & 0x1000)
        if deep:
            raise ValueError('Deep EXRs are not supported; use a flat utility pass')
        o, self.parts = 8, []
        if self.multi:
            while b[o] != 0:
                h, o = _read_header(b, o)
                self.parts.append({'header': h})
            o += 1
        else:
            h, o = _read_header(b, o)
            self.parts.append({'header': h})
        for p in self.parts:
            h = p['header']
            dw = h['dataWindow']
            p['name'] = h.get('name', '')
            p['x0'], p['y0'] = dw[0], dw[1]
            p['width'], p['height'] = dw[2] - dw[0] + 1, dw[3] - dw[1] + 1
            p['channels'] = h.get('channels', [])
            p['comp'] = h.get('compression', 0)
            p['lines'] = _COMP_LINES.get(p['comp'], 1)
            p['tiled'] = tiled or ('tiled' in str(h.get('type', '')))
            n = h.get('chunkCount') or int(math.ceil(p['height'] / float(p['lines'])))
            p['offsets'] = struct.unpack_from('<%dQ' % n, b, o)
            o += 8 * n

    def close(self):
        for h in (self._b, self._f):
            try:
                h.close()
            except Exception:
                pass

    def headers(self):
        return [p['header'] for p in self.parts]

    def read(self, pi, names):
        p, b = self.parts[pi], self._b
        if p['tiled']:
            raise ValueError('Tiled EXRs are not supported; write scanline images')
        if p['comp'] not in (0, 1, 2, 3):
            raise ValueError('%s uses %s compression. Write utility passes with ZIP, ZIPS, RLE or no compression.'
                             % (os.path.basename(self.path), _COMP_NAMES.get(p['comp'], p['comp'])))
        W, H, chs = p['width'], p['height'], p['channels']
        offs, o = [], 0
        for n, t in chs:
            offs.append(o)
            o += _PIX_BYTES[t] * W
        line_bytes = o
        out = {n: np.zeros((H, W), np.float32) for n in names}
        want = [(i, n, t) for i, (n, t) in enumerate(chs) if n in out]
        for q in p['offsets']:
            if self.multi:
                q += 4
            y, size = struct.unpack_from('<ii', b, q)
            q += 8
            ly = y - p['y0']
            nl = min(p['lines'], H - ly)
            expect = line_bytes * nl
            data = b[q:q + size]
            if size < expect:
                if p['comp'] == 1:
                    data = _un_predict(_un_rle(data, expect))
                else:
                    data = _un_predict(zlib.decompress(data))
            a = np.frombuffer(data, np.uint8, count=expect).reshape(nl, line_bytes)
            for i, n, t in want:
                seg = a[:, offs[i]:offs[i] + _PIX_BYTES[t] * W].copy().view(_PIX_DTYPE[t]).reshape(nl, W)
                out[n][ly:ly + nl] = seg.astype(np.float32)
        return out


def _attr_bytes(name, typ, value):
    if typ == 'chlist':
        body = b''.join(n.encode() + b'\0' + struct.pack('<iB3xii', t, 0, 1, 1) for n, t in value) + b'\0'
    elif typ in ('compression', 'lineOrder'):
        body = struct.pack('<B', value)
    elif typ == 'string':
        body = value.encode('utf-8')
    else:
        fmt = {'box2i': '<4i', 'int': '<i', 'float': '<f', 'double': '<d', 'm44f': '<16f', 'v2f': '<2f', 'v3f': '<3f'}[typ]
        body = struct.pack(fmt, *(value if isinstance(value, (list, tuple)) else [value]))
    return name.encode() + b'\0' + typ.encode() + b'\0' + struct.pack('<i', len(body)) + body


def write_exr(path, channels, attrs=None, half=False):
    """channels: {name: (H, W) array}. attrs: {name: (type, value)} extra header attributes. ZIP compressed."""
    names = sorted(channels)
    H, W = channels[names[0]].shape
    ptype = 1 if half else 2
    dt = '<f2' if half else '<f4'
    lines = 16
    hdr = [_attr_bytes('channels', 'chlist', [(n, ptype) for n in names]),
           _attr_bytes('compression', 'compression', 3),
           _attr_bytes('dataWindow', 'box2i', [0, 0, W - 1, H - 1]),
           _attr_bytes('displayWindow', 'box2i', [0, 0, W - 1, H - 1]),
           _attr_bytes('lineOrder', 'lineOrder', 0),
           _attr_bytes('pixelAspectRatio', 'float', 1.0),
           _attr_bytes('screenWindowCenter', 'v2f', [0.0, 0.0]),
           _attr_bytes('screenWindowWidth', 'float', 1.0)]
    for k, (t, v) in (attrs or {}).items():
        hdr.append(_attr_bytes(k, t, v))
    head = struct.pack('<II', 20000630, 2) + b''.join(hdr) + b'\0'
    planes = [np.ascontiguousarray(channels[n], dtype=dt) for n in names]
    chunks = []
    for y in range(0, H, lines):
        nl = min(lines, H - y)
        raw = b''.join(b''.join(pl[yy].tobytes() for pl in planes) for yy in range(y, y + nl))
        comp = zlib.compress(_predict(raw), 4)
        data = comp if len(comp) < len(raw) else raw
        chunks.append(struct.pack('<ii', y, len(data)) + data)
    pos = len(head) + 8 * len(chunks)
    table = []
    for c in chunks:
        table.append(pos)
        pos += len(c)
    tmp = path + '.tmp%d' % os.getpid()
    with open(tmp, 'wb') as f:
        f.write(head)
        f.write(struct.pack('<%dQ' % len(table), *table))
        for c in chunks:
            f.write(c)
    os.replace(tmp, path)


# ---- which layers are which ------------------------------------------------------------------------
_ROLE = {
    'P': ['pos', 'p', 'pworld', 'position', 'worldposition', 'worldpos', 'pw', 'wp', 'ppass', 'world', 'positionworld', 'worldp'],
    'N': ['normal', 'n', 'nworld', 'normals', 'worldnormal', 'nw', 'normalworld', 'nn', 'worldnormals'],
    'Z': ['depth', 'z', 'zdepth', 'depthnofilter', 'distance'],
    'C': ['surfacecolor', 'albedo', 'diffusealbedo', 'basecolor', 'diffusecolor', 'surfacealbedo'],
}


def _comp_index(c):
    c = c.lower()
    return 0 if c in ('r', 'x', 'u') else 1 if c in ('g', 'y', 'v') else 2 if c in ('b', 'z', 'w') else -1


def survey_layers(exr):
    """Find world position / normals / depth / colour layers. Returns {role: (part, [channel names])}."""
    allc = []
    for pi, p in enumerate(exr.parts):
        for n, t in p['channels']:
            k = n.rfind('.')
            if k >= 0:
                layer, comp = n[:k], n[k + 1:]
            elif len(n) == 1 and n in 'ZRGBA' and p['name']:
                layer, comp = ('depth' if n == 'Z' else p['name']), n
            else:
                layer, comp = n, ''
            allc.append((pi, n, ''.join(ch for ch in layer.lower() if ch.isalnum()), comp))
    found = {}
    for role in ('P', 'N', 'C'):
        for key in _ROLE[role]:
            g = [a for a in allc if a[2] == key]
            names = [None, None, None]
            for a in g:
                i = _comp_index(a[3])
                if i >= 0 and names[i] is None:
                    names[i] = a
            if all(names) and len(set(a[0] for a in names)) == 1:
                found[role] = (names[0][0], [a[1] for a in names])
                break
    for key in _ROLE['Z']:
        a = [a for a in allc if a[2] == key and (a[3] == '' or a[3].lower() in ('z', 'r', 'y'))]
        if not a and key == 'z':
            a = [x for x in allc if x[1] == 'Z']
        if a:
            found['Z'] = (a[0][0], [a[0][1]])
            break
    return found


def _nums(v):
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, str):
        import re
        return [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', v)]
    return []


class Camera(object):
    """A pinhole camera in scene units. right/up/back are unit vectors (camera looks down -back)."""

    def __init__(self, pos, right, up, back, hfov, focal=None, film=None, fstop=None, focus=None, extra=None):
        self.pos = np.asarray(pos, np.float64)
        self.extra = dict(extra or {})      # lens metadata: blades, cateye, anamorphic, fieldcurv, fstop_full
        self.right = np.asarray(right, np.float64)
        self.up = np.asarray(up, np.float64)
        self.back = np.asarray(back, np.float64)
        self.hfov = float(hfov)
        self.focal, self.film, self.fstop, self.focus = focal, film, fstop, focus

    def to_world_m44(self):
        """Row-vector matrix (translation in the last row), as EXR camToWorld attributes are usually written."""
        r, u, b, p = self.right, self.up, self.back, self.pos
        return [r[0], r[1], r[2], 0, u[0], u[1], u[2], 0, b[0], b[1], b[2], 0, p[0], p[1], p[2], 1]

    def project(self, P, W, H):
        """World points (N,3) -> pixel x, y (row 0 at the top), distance along the view axis."""
        d = P - self.pos
        x = d @ self.right
        y = d @ self.up
        z = -(d @ self.back)
        th = math.tan(self.hfov / 2)
        tv = th * H / float(W)
        zs = np.where(z > 1e-6, z, 1e-6)
        px = (x / zs / th * 0.5 + 0.5) * W
        py = (0.5 - y / zs / tv * 0.5) * H
        return px, py, z


def _orthonormal(right, up, back):
    b = back / (np.linalg.norm(back) + 1e-12)
    r = right - b * (right @ b)
    r /= np.linalg.norm(r) + 1e-12
    u = np.cross(b, r)
    return r, u, b


def camera_from_headers(headers):
    cam, extra = {}, {}
    for h in headers:
        for k, v in h.items():
            kl = k.lower()
            kn = ''.join(ch for ch in kl if ch.isalnum())
            lens_key = ('fstop_full' if any(t in kn for t in ('fullaperture', 'fstopfull', 'maxaperture', 'wideopen')) else
                        'cateye' if 'cateye' in kn else
                        'fieldcurv' if 'fieldcurvature' in kn else
                        'anamorphic' if ('anamorphic' in kn or kn.endswith('squeeze')) else
                        'blades' if (kn.endswith('bladecount') or kn.endswith('blades') or kn.endswith('numblades')) else None)
            if lens_key:
                n = _nums(v)
                if n and lens_key not in extra:
                    extra[lens_key] = float(n[0])
                continue
            if 'toworld' not in cam and (kl.endswith('camtoworld') or kl.endswith('cameratoworld')):
                n = _nums(v)
                if len(n) >= 16:
                    cam['toworld'] = n[:16]
            elif 'toworld' not in cam and 'w2c' not in cam and kl.endswith('worldtocamera'):
                n = _nums(v)
                if len(n) >= 16:
                    cam['w2c'] = n[:16]
            elif 'hfov' not in cam and 'horizontalfov' in kl:
                cam['hfov'] = _nums(v)[0]
            elif 'focal' not in cam and kl.endswith('focallength'):
                cam['focal'] = _nums(v)[0]
            elif 'fstop' not in cam and (kl.endswith('fstop') or kl.endswith('fnumber')):
                cam['fstop'] = _nums(v)[0]
            elif 'focus' not in cam and kl.endswith('focusdistance'):
                cam['focus'] = _nums(v)[0]
            elif 'aperture' not in cam and any(kl.endswith(s) for s in ('horizontalaperture', 'filmbackwidth', 'aperturewidth', 'hfilmaperture', 'aperture')):
                cam['aperture'] = _nums(v)[0]
    if 'toworld' not in cam and 'w2c' in cam:
        cam['toworld'] = list(np.linalg.inv(np.array(cam['w2c']).reshape(4, 4)).reshape(16))
    if 'toworld' not in cam:
        return None
    M = np.array(cam['toworld'], np.float64).reshape(4, 4)
    if np.abs(M[:3, 3]).sum() > np.abs(M[3, :3]).sum():   # column-vector convention: transpose to row-vector
        M = M.T
    r, u, b = _orthonormal(M[0, :3], M[1, :3], M[2, :3])
    focal, ap = cam.get('focal'), cam.get('aperture')
    if ap is not None and ap < 5:     # inches
        ap *= 25.4
    if cam.get('hfov'):
        hfov = math.radians(cam['hfov'])
    elif focal and ap:
        hfov = 2 * math.atan(ap / (2 * focal))
    else:
        return None
    film = ap if ap else (2 * focal * math.tan(hfov / 2) if focal else None)
    return Camera(M[3, :3], r, u, b, hfov, focal=focal, film=film, fstop=cam.get('fstop'), focus=cam.get('focus'), extra=extra)


def _blend_camera(a, b, t):
    """Blend (or, outside 0..1, extrapolate) two cameras."""
    r, u, bk = _orthonormal(a.right * (1 - t) + b.right * t, a.up * (1 - t) + b.up * t, a.back * (1 - t) + b.back * t)
    return Camera(a.pos * (1 - t) + b.pos * t, r, u, bk, a.hfov * (1 - t) + b.hfov * t, a.focal, a.film, a.fstop, a.focus, a.extra)


def lerp_camera(a, b, t):
    """Blend two cameras (for sub-frame shutter samples)."""
    if t <= 0:
        return a
    if t >= 1:
        return b
    r, u, bk = _orthonormal(a.right * (1 - t) + b.right * t, a.up * (1 - t) + b.up * t, a.back * (1 - t) + b.back * t)
    return Camera(a.pos * (1 - t) + b.pos * t, r, u, bk, a.hfov * (1 - t) + b.hfov * t, a.focal, a.film, a.fstop, a.focus, a.extra)


# =====================================================================================================
#  Small helpers
# =====================================================================================================
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


def light_dir(azimuth, elevation):
    """Unit vector pointing toward a directional light (Y up). Azimuth 0 = light comes from -X, 90 = from -Z."""
    az, el = math.radians(azimuth), math.radians(elevation)
    return np.array([-math.cos(az) * math.cos(el), math.sin(el), -math.sin(az) * math.cos(el)])


def _smooth01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def boxes_blocked(P, L, boxes, eps=1e-3):
    """True where the ray from each point P toward direction L hits any box. boxes: (min, max[, colour, name])."""
    N = len(P)
    hit = np.zeros(N, bool)
    if N == 0:
        return hit
    Ls = np.where(np.abs(L) < 1e-9, 1e-9, L)
    Pc = (P[:, 0].copy(), P[:, 1].copy(), P[:, 2].copy())
    inv = tuple(np.full(N, 1.0 / Ls[k]) for k in range(3))
    groups = {}
    for bx in boxes:
        groups.setdefault(bx[3] if len(bx) > 3 else '', []).append(bx)
    for mem in groups.values():
        gmn = np.min([m[0] for m in mem], 0)
        gmx = np.max([m[1] for m in mem], 0)
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


# =====================================================================================================
#  Stand-in shot: an interior with a window opening (no window geometry) and a moving camera
# =====================================================================================================
STANDIN = dict(first=1001, last=1048, width=1280, height=536, focal=24.0, film=24.89, fstop=4.0, focus=430.0,
               sun_az=25.0, sun_el=33.0)


def standin_boxes():
    """The stand-in room in centimetres: list of (min, max, albedo, name)."""
    B = []

    def box(mn, mx, c, name):
        B.append((tuple(float(v) for v in mn), tuple(float(v) for v in mx), tuple(c), name))
    plaster, wood, dark, trim = (0.36, 0.32, 0.27), (0.24, 0.15, 0.08), (0.12, 0.08, 0.05), (0.42, 0.40, 0.36)
    box((-420, -20, -620), (420, 0, 370), (0.22, 0.14, 0.08), 'floor')
    box((-420, 350, -620), (420, 370, 370), plaster, 'ceiling')
    box((-420, 0, -620), (420, 350, -600), plaster, 'wall_back')
    box((-420, 0, 350), (420, 350, 370), plaster, 'wall_front')
    box((400, 0, -620), (420, 350, 370), plaster, 'wall_right')
    # left wall with a window opening: z -400..-140, y 90..290 (no glass, no frame)
    box((-420, 0, -620), (-400, 350, -400), plaster, 'wall_left')
    box((-420, 0, -140), (-400, 350, 370), plaster, 'wall_left')
    box((-420, 0, -400), (-400, 90, -140), plaster, 'wall_left')
    box((-420, 290, -400), (-400, 350, -140), plaster, 'wall_left')
    box((-420, 84, -406), (-385, 90, -134), trim, 'sill')
    for z in (-470, -270, -70, 130):
        box((-400, 326, z - 11), (400, 350, z + 11), dark, 'beam')
    # table with a few things on it
    box((-150, 72, -340), (50, 78, -230), wood, 'table')
    for x, z in ((-145, -335), (40, -335), (-145, -240), (40, -240)):
        box((x, 0, z), (x + 6, 72, z + 6), wood, 'table')
    box((-90, 78, -300), (-60, 104, -275), (0.45, 0.42, 0.36), 'vase')
    box((-20, 78, -310), (20, 84, -270), (0.30, 0.10, 0.08), 'books')
    # bookcase on the back wall
    box((-330, 0, -600), (-140, 215, -596), wood, 'bookcase')
    box((-330, 0, -600), (-326, 215, -562), wood, 'bookcase')
    box((-144, 0, -600), (-140, 215, -562), wood, 'bookcase')
    for y in (0, 52, 104, 156, 208):
        box((-330, y, -600), (-140, y + 4, -562), wood, 'bookcase')
    seed = 7
    for row, y in enumerate((4, 56, 108, 160)):
        x = -326.0
        while x < -146:
            seed = (seed * 16807) % 2147483647
            w = 2.5 + (seed % 100) / 100.0 * 4.0
            h = 22 + (seed % 37)
            c = (0.10 + (seed % 23) / 60.0, 0.06 + (seed % 17) / 80.0, 0.05 + (seed % 13) / 70.0)
            box((x, y, -596), (min(x + w, -144), y + min(h, 46), -570), c, 'books')
            x += w + 0.4
    # crates, an armchair, a rug, a stool
    box((200, 0, -530), (275, 62, -455), (0.30, 0.21, 0.11), 'crate')
    box((212, 62, -515), (262, 106, -465), (0.26, 0.18, 0.09), 'crate')
    box((150, 0, -210), (232, 44, -128), (0.30, 0.06, 0.05), 'armchair')
    box((212, 44, -210), (232, 104, -128), (0.30, 0.06, 0.05), 'armchair')
    box((150, 44, -214), (232, 66, -200), (0.30, 0.06, 0.05), 'armchair')
    box((150, 44, -138), (232, 66, -124), (0.30, 0.06, 0.05), 'armchair')
    box((-220, 0, -420), (110, 1.2, -150), (0.28, 0.08, 0.06), 'rug')
    box((70, 0, -150), (110, 46, -110), wood, 'stool')
    return B


def standin_camera(frame):
    S = STANDIN
    u = (frame - S['first']) / float(max(S['last'] - S['first'], 1))
    u = u * u * (3 - 2 * u)
    E = np.array([310.0, 165.0, 320.0]) * (1 - u) + np.array([190.0, 150.0, 290.0]) * u
    T = np.array([-160.0, 105.0, -290.0]) * (1 - u) + np.array([-235.0, 118.0, -300.0]) * u
    back = E - T
    back /= np.linalg.norm(back)
    right = np.cross([0.0, 1.0, 0.0], back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    hfov = 2 * math.atan(S['film'] / (2 * S['focal']))
    return Camera(E, right, up, back, hfov, focal=S['focal'], film=S['film'], fstop=S['fstop'], focus=S['focus'])


def _group_boxes(boxes):
    """Two-level grouping (by name) so rays only test the members of groups whose bounds they hit."""
    groups = {}
    for b in boxes:
        groups.setdefault(b[3] if len(b) > 3 else '', []).append(b)
    out = []
    for name, mem in groups.items():
        mn = np.min([m[0] for m in mem], 0)
        mx = np.max([m[1] for m in mem], 0)
        out.append((mn, mx, mem))
    return out


def _slab(O, inv, mn, mx):
    """Ray/box slab test on component columns. O, inv: tuples of 3 arrays (origin, 1/direction)."""
    tx1, tx2 = (mn[0] - O[0]) * inv[0], (mx[0] - O[0]) * inv[0]
    ty1, ty2 = (mn[1] - O[1]) * inv[1], (mx[1] - O[1]) * inv[1]
    tz1, tz2 = (mn[2] - O[2]) * inv[2], (mx[2] - O[2]) * inv[2]
    nx, ny, nz = np.minimum(tx1, tx2), np.minimum(ty1, ty2), np.minimum(tz1, tz2)
    tmin = np.maximum(np.maximum(nx, ny), nz)
    tmax = np.minimum(np.minimum(np.maximum(tx1, tx2), np.maximum(ty1, ty2)), np.maximum(tz1, tz2))
    return (nx, ny, nz), tmin, tmax


def _cols(A, N):
    A = np.asarray(A, np.float64)
    if A.ndim == 1 or len(A) == 1:
        A = A.reshape(-1)
        return tuple(np.full(N, A[k]) for k in range(3))
    return A[:, 0].copy(), A[:, 1].copy(), A[:, 2].copy()


def _trace_boxes(O, D, boxes):
    """Nearest hit of rays from O (shared (1,3) or per ray) along D (N,3) against boxes: t, normal, albedo."""
    N = len(D)
    Ds = np.where(np.abs(D) < 1e-9, 1e-9, D)
    Oc = _cols(O, N)
    inv = (1.0 / Ds[:, 0], 1.0 / Ds[:, 1], 1.0 / Ds[:, 2])
    best = np.full(N, np.inf)
    nrm = np.zeros((N, 3))
    alb = np.zeros((N, 3))
    for gmn, gmx, mem in _group_boxes(boxes):
        _, a, b = _slab(Oc, inv, gmn, gmx)
        idx = np.where((b >= np.maximum(a, 1e-4)) & (a < best))[0]
        if not len(idx):
            continue
        Oi = tuple(c[idx] for c in Oc)
        Ii = tuple(c[idx] for c in inv)
        for mn, mx, c, _ in mem:
            (nx, ny, nz), tmin, tmax = _slab(Oi, Ii, mn, mx)
            ok = (tmax >= np.maximum(tmin, 1e-4)) & (tmin > 1e-4) & (tmin < best[idx])
            if not ok.any():
                continue
            j = idx[ok]
            ax = np.where((nx[ok] >= ny[ok]) & (nx[ok] >= nz[ok]), 0, np.where(ny[ok] >= nz[ok], 1, 2))
            best[j] = tmin[ok]
            n = np.zeros((len(j), 3))
            n[np.arange(len(j)), ax] = -np.sign(Ds[j, ax])
            nrm[j] = n
            alb[j] = c
    return best, nrm, alb


def render_standin_frame(frame, width=None, height=None, intensity=6.0, kelvin=5000.0):
    """Utility pass + clay plate for one stand-in frame (scene units: centimetres)."""
    S = STANDIN
    W, H = width or S['width'], height or S['height']
    cam = standin_camera(frame)
    th = math.tan(cam.hfov / 2)
    tv = th * H / float(W)
    xs = ((np.arange(W) + 0.5) / W * 2 - 1) * th
    ys = (1 - (np.arange(H) + 0.5) / H * 2) * tv
    sx, sy = np.meshgrid(xs, ys)
    D = -cam.back[None, :] + sx.reshape(-1, 1) * cam.right[None, :] + sy.reshape(-1, 1) * cam.up[None, :]
    D /= np.linalg.norm(D, axis=1, keepdims=True)
    boxes = standin_boxes()
    t, n, alb = _trace_boxes(cam.pos[None, :], D, boxes)
    hit = np.isfinite(t)
    P = np.zeros((W * H, 3))
    P[hit] = cam.pos + D[hit] * t[hit, None]
    L = light_dir(S['sun_az'], S['sun_el'])
    sun = kelvin_rgb(kelvin) * intensity
    lit = np.zeros(W * H)
    nl = np.clip(n @ L, 0, None)
    cand = hit & (nl > 0)
    if cand.any():
        blocked = boxes_blocked(P[cand] + n[cand] * 0.3, L, boxes)
        lit[np.where(cand)[0][~blocked]] = 1.0
    sky = np.array([0.66, 0.76, 0.95]) * intensity * 0.45
    fill = intensity * (0.035 + 0.02 * np.clip(n[:, 1], 0, 1))
    rgb = np.where(hit[:, None], alb * (sun[None, :] * (nl * lit)[:, None] + fill[:, None] * np.array([0.95, 0.97, 1.0])), sky[None, :])
    depth = np.where(hit, t, 0.0)
    f32 = lambda a: a.reshape(H, W).astype(np.float32)
    util = {'pos.R': f32(P[:, 0]), 'pos.G': f32(P[:, 1]), 'pos.B': f32(P[:, 2]),
            'normal.R': f32(n[:, 0]), 'normal.G': f32(n[:, 1]), 'normal.B': f32(n[:, 2]),
            'depth.Z': f32(depth), 'surfaceColor.R': f32(alb[:, 0]), 'surfaceColor.G': f32(alb[:, 1]), 'surfaceColor.B': f32(alb[:, 2])}
    plate = {'R': f32(rgb[:, 0]), 'G': f32(rgb[:, 1]), 'B': f32(rgb[:, 2]), 'A': f32(np.ones(W * H))}
    attrs = {'camToWorld': ('m44f', cam.to_world_m44()), 'focalLength': ('float', cam.focal),
             'horizontalAperture': ('float', cam.film), 'fStop': ('float', cam.fstop),
             'focusDistance': ('float', cam.focus), 'motefield/standin': ('string', 'MoteField stand-in shot, scene units cm')}
    return util, plate, attrs


def make_standin_shot(folder, progress=None):
    """Writes standin_utility.####.exr and standin_plate.####.exr (+ standin.json) into folder."""
    S = STANDIN
    os.makedirs(folder, exist_ok=True)
    frames = list(range(S['first'], S['last'] + 1))
    for i, f in enumerate(frames):
        if progress and progress(i / float(len(frames)), 'Stand-in shot: frame %d' % f) is False:
            return None
        util, plate, attrs = render_standin_frame(f)
        write_exr(os.path.join(folder, 'standin_utility.%04d.exr' % f), util, attrs)
        write_exr(os.path.join(folder, 'standin_plate.%04d.exr' % f), plate, attrs, half=True)
    meta = dict(S, boxes=[[b[0], b[1], b[3]] for b in standin_boxes()], units='centimetres')
    with open(os.path.join(folder, 'standin.json'), 'w') as fh:
        json.dump(meta, fh)
    return dict(utility=os.path.join(folder, 'standin_utility.####.exr'), plate=os.path.join(folder, 'standin_plate.####.exr'),
                first=S['first'], last=S['last'])


# =====================================================================================================
#  Settings
# =====================================================================================================
UNITS = {'centimetres': 0.01, 'metres': 1.0, 'millimetres': 0.001, 'inches': 0.0254, 'feet': 0.3048}

DEFAULTS = dict(
    utility='', units='centimetres', first=1001, last=1048, fps=24.0, proxy=1.0, seed=1,
    # the dust volume (scene units) and how it fades out toward each face
    box_center=(-60.0, 175.0, -130.0), box_size=(760.0, 330.0, 900.0), box_rot=0.0,
    fade=45.0, fade_xn=1.0, fade_xp=1.0, fade_yn=1.0, fade_yp=1.0, fade_zn=1.0, fade_zp=1.0,
    # population
    density=180.0, max_motes=150000, size_min=4.0, size_max=60.0, size_bias=2.4,
    flakes=18.0, fibers=1.2, fluff=0.4, flake_scale=1.0, fiber_scale=1.0, fluff_scale=0.6,
    # scattering
    brightness=1.0, forward=0.65, shadow_fill=0.35, glint=0.4, tumble=1.0, tint=(1.0, 1.0, 1.0),
    # air (cm/s, metres for eddy size)
    speed=1.0, drift=1.2, drift_dir=200.0, lift=0.0, eddies=3.0, eddy_size=1.4, micro=0.8, thermal=1.5, settle=0.5,
    preroll=48, substeps=2,
    # key light: a directional light
    light_az=25.0, light_el=33.0, light_int=6.0, light_k=5000.0, bounce=1.0, shadows='stand-in geometry', interior=True,
    # lens (0 = use the camera from the utility pass headers)
    focal=0.0, film=0.0, fstop=0.0, focus=0.0, dof=True, blades=0, max_bokeh=110.0,
    cateye=0.0, anamorphic=0.0, field_curv=0.0, fstop_full=0.0, front_mult=1.0, back_mult=1.0,
    bokeh_image='', bokeh_rot=0.0, bokeh_falloff=0.0,
    # shutter: as on the studio cameras (180 degrees, backward, backwardsShape)
    shutter=180.0, shutter_mode='backward', shutter_shape='backwardsShape', mb=True,
    # holdouts
    holdout=True, fg_fade=False, fg_near=0.0, fg_far=0.0,
    cache='', standin_json='',
)

# keys that change the simulation (everything else only changes how frames are rendered)
SIM_KEYS = ('units', 'first', 'fps', 'seed', 'box_center', 'box_size', 'box_rot', 'density', 'max_motes', 'size_min', 'size_max',
            'size_bias', 'flakes', 'fibers', 'fluff', 'speed', 'drift', 'drift_dir', 'lift', 'eddies', 'eddy_size', 'micro',
            'thermal', 'settle', 'preroll', 'substeps', 'light_az', 'light_el', 'shadows', 'interior', 'standin_json', 'utility')


def settings(**kw):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def _sim_key(p):
    return hashlib.md5(json.dumps([p[k] for k in SIM_KEYS], default=str).encode()).hexdigest()


# =====================================================================================================
#  The shot: utility pass frames, camera per frame, and what the camera saw
# =====================================================================================================
def frame_path(pattern, f):
    if '####' in pattern:
        return pattern.replace('####', '%04d' % f)
    if '%' in pattern:
        return pattern % f
    import re
    m = re.search(r'#+', pattern)
    if m:
        return pattern[:m.start()] + str(f).zfill(len(m.group())) + pattern[m.end():]
    return pattern


class Shot(object):
    def __init__(self, pattern, units):
        self.pattern = pattern
        self.su = UNITS.get(units, 0.01)
        self._cams = {}
        self._frames = {}
        self.layers = None

    def _open(self, f):
        path = frame_path(self.pattern, f)
        if not os.path.exists(path):
            raise IOError('Utility pass frame not found: %s' % path)
        return ExrFile(path)

    def camera(self, f):
        """Camera at frame f. Fractional frames blend neighbouring frames' cameras (extrapolating at the ends)."""
        f0 = int(math.floor(f))
        if abs(f - f0) < 1e-6 and self._has(f0):
            return self._cam(f0)
        if self._has(f0) and self._has(f0 + 1):
            return _blend_camera(self._cam(f0), self._cam(f0 + 1), f - f0)
        if self._has(f0 + 1) and self._has(f0 + 2):            # before the first frame
            return _blend_camera(self._cam(f0 + 1), self._cam(f0 + 2), f - (f0 + 1))
        if self._has(f0) and self._has(f0 - 1):                # after the last frame
            return _blend_camera(self._cam(f0 - 1), self._cam(f0), f - (f0 - 1))
        for g in (f0, f0 + 1, f0 - 1):
            if self._has(g):
                return self._cam(g)
        return self._cam(int(round(f)))

    def _has(self, f):
        return f in self._cams or os.path.exists(frame_path(self.pattern, f))

    def _cam(self, f):
        if f not in self._cams:
            e = self._open(f)
            c = camera_from_headers(e.headers())
            e.close()
            if c is None:
                raise ValueError('No camera found in the EXR header of frame %d (looked for camToWorld / worldToCamera and '
                                 'horizontal FOV or focal length + aperture).' % f)
            self._cams[f] = c
        return self._cams[f]

    def surface(self, f):
        """(H, W) distance from the camera to what each pixel sees (scene units, 0 = nothing), plus the world positions."""
        if f in self._frames:
            return self._frames[f]
        e = self._open(f)
        found = survey_layers(e)
        self.layers = found
        cam = self._cam(f)
        P = None
        if 'P' in found:
            pi, names = found['P']
            d = e.read(pi, names)
            P = np.stack([d[n] for n in names], -1)
            valid = np.abs(P).sum(-1) > 0
            dist = np.where(valid, np.linalg.norm(P - cam.pos.astype(np.float32), axis=-1), 0.0).astype(np.float32)
        elif 'Z' in found:
            pi, names = found['Z']
            dist = e.read(pi, names)[names[0]]
            dist = np.where(np.isfinite(dist) & (dist > 0) & (dist < 1e9), dist, 0.0).astype(np.float32)
        else:
            e.close()
            raise ValueError('Frame %d has no world position or depth layer.' % f)
        e.close()
        if len(self._frames) > 4:
            self._frames.pop(next(iter(self._frames)))
        self._frames[f] = (dist, P)
        return self._frames[f]


# =====================================================================================================
#  Where the key light reaches
# =====================================================================================================
class LightMap(object):
    """Depth map from a directional light, built from points (the utility pass seen from the camera).
    Only knows surfaces some frame of the shot saw, so off-screen geometry won't cast shadows."""

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


def make_light(p, shot=None):
    """Returns a function: world points (scene units, (N,3)) -> 0..1 visibility to the key light."""
    L = light_dir(p['light_az'], p['light_el'])
    mode = str(p['shadows']).lower()
    js = p.get('standin_json') or ''
    if mode.startswith('stand') and not (js and os.path.exists(js) and
                                          os.path.normpath(os.path.dirname(js)) == os.path.normpath(os.path.dirname(p['utility'] or ''))):
        mode = 'utility pass'      # stand-in geometry only belongs to the stand-in shot
    if mode.startswith('stand'):
        boxes = None
        js = p.get('standin_json') or ''
        if js and os.path.exists(js):
            with open(js) as fh:
                boxes = [(tuple(b[0]), tuple(b[1]), (0, 0, 0), b[2]) for b in json.load(fh)['boxes']]
        if boxes is None:
            boxes = standin_boxes()
        return lambda P: (~boxes_blocked(P, L, boxes)).astype(np.float64)
    if mode.startswith('util') and shot is not None:
        pts = []
        fs = sorted(set([p['first'], (p['first'] + p['last']) // 2, p['last']]))
        for f in fs:
            try:
                dist, P = shot.surface(f)
            except Exception:
                continue
            if P is None:
                continue
            Q = P.reshape(-1, 3)
            Q = Q[np.abs(Q).sum(1) > 0]
            if len(Q) > 900000:
                Q = Q[::int(math.ceil(len(Q) / 900000.0))]
            pts.append(Q.astype(np.float64))
        if pts:
            allp = np.concatenate(pts)
            lm = LightMap(allp, L)
            if not p.get('interior', True):
                return lm.visible
            # interior: the pass only shows what the camera saw, so let light in only through openings the camera saw
            # as empty (sky through a window). Where the ray toward the light leaves the scene, look it up in the frames.
            lo, hi = np.percentile(allp, 0.5, axis=0), np.percentile(allp, 99.5, axis=0)
            views = []
            for f in fs:
                try:
                    dist, _ = shot.surface(f)
                    views.append((shot.camera(f), dist <= 0))
                except Exception:
                    pass
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
    return lambda P: np.ones(len(P))


# =====================================================================================================
#  The dust: a deterministic simulation in a box
# =====================================================================================================
GRIT, FLAKE, FIBER, FLUFF = 0, 1, 2, 3


def _rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


class DustSim(object):
    def __init__(self, p, light=None):
        self.p = p
        su = UNITS.get(p['units'], 0.01)
        self.su = su
        self.center = np.asarray(p['box_center'], np.float64) * su          # metres
        self.size = np.maximum(np.asarray(p['box_size'], np.float64) * su, 0.02)
        self.R = _rot_y(p['box_rot'])
        vol = float(np.prod(self.size))
        n = int(max(0, min(p['max_motes'], round(p['density'] * vol))))
        rng = np.random.default_rng(int(p['seed']))
        self.local = (rng.random((n, 3)) - 0.5) * self.size
        self.V = np.zeros((n, 3))
        self.h = rng.random((n, 10))
        f_fib, f_flf, f_flk = p['fibers'] / 100.0, p['fluff'] / 100.0, p['flakes'] / 100.0
        h0 = self.h[:, 0]
        self.type = np.where(h0 < f_fib, FIBER, np.where(h0 < f_fib + f_flf, FLUFF, np.where(h0 < f_fib + f_flf + f_flk, FLAKE, GRIT)))
        u = self.h[:, 1]
        d_grit = p['size_min'] * (p['size_max'] / max(p['size_min'], 1e-3)) ** (u ** p['size_bias'])
        sf = np.where(self.type == GRIT, 0.15 + np.minimum((d_grit / 50.0) ** 2, 4.0),
                      np.where(self.type == FLAKE, 0.5, np.where(self.type == FIBER, 0.22, 0.12)))
        self.settle_f = sf
        self.tau = np.where(self.type >= FIBER, 0.22, 0.35)
        mrng = np.random.default_rng(int(p['seed']) * 7919 + 17)
        self.modes = [self._modes(mrng, 40, max(p['eddy_size'], 0.05)), self._modes(mrng, 20, max(p['eddy_size'], 0.05) * 0.22)]
        a = math.radians(p['drift_dir'])
        self.wind = np.array([math.cos(a) * p['drift'], p['lift'], math.sin(a) * p['drift']]) * 0.01
        self.light = light
        self.t = None
        self._vis = np.ones(n)

    @staticmethod
    def _modes(rng, M, scale):
        k = rng.normal(size=(M, 3))
        k /= np.linalg.norm(k, axis=1, keepdims=True)
        kmag = 2 * math.pi / scale * np.exp(rng.normal(0, 0.35, M))
        A = rng.normal(size=(M, 3))
        A -= k * (A * k).sum(1, keepdims=True)          # perpendicular to k: the flow is divergence free
        A /= np.linalg.norm(A, axis=1, keepdims=True)
        ph = rng.random(M) * 2 * math.pi
        om = (0.4 + 0.6 * rng.random(M)) * kmag
        return k * kmag[:, None], A * math.sqrt(6.0 / M), ph, om

    def _flow(self, Pw, t, modes, amp):
        if amp <= 0:
            return 0.0
        K, A, ph, om = modes
        arg = (Pw - self.wind * t) @ K.T + ph + om * (t * 0.5 * amp)
        return amp * 0.6 * (np.sin(arg) @ A)

    def world(self):
        return self.local @ self.R.T + self.center

    def scene_points(self):
        return self.world() / self.su

    def start(self, t0):
        self.t = t0

    def refresh_light(self):
        if self.light is not None and len(self.local):
            self._vis = self.light(self.scene_points())

    def step(self, dt):
        p, n = self.p, len(self.local)
        if not n:
            self.t += dt
            return
        Pw = self.world()
        tgt = self.wind + self._flow(Pw, self.t, self.modes[0], p['eddies'] * 0.01)
        micro = p['micro'] * 0.01
        if micro > 0:
            fm = self._flow(Pw, self.t, self.modes[1], micro)
            tgt = tgt + fm * np.where(self.type >= FIBER, 1.6, 1.0)[:, None]
        up = np.zeros_like(Pw)
        up[:, 1] = p['thermal'] * 0.01 * self._vis - p['settle'] * 0.01 * self.settle_f
        tgt = tgt + up
        k = 1 - np.exp(-dt / self.tau)
        self.V += (tgt - self.V) * k[:, None]
        self.local += (self.V @ self.R) * dt
        self.local = np.mod(self.local + self.size / 2, self.size) - self.size / 2
        self.t += dt

    def state(self):
        return self.local.copy(), self.V.copy(), self.t

    def set_state(self, st):
        self.local, self.V, self.t = st[0].copy(), st[1].copy(), st[2]

    def edge_weight(self):
        """0..1 per mote: fades the density out toward each face of the box."""
        p, su = self.p, self.su
        half = self.size / 2
        w = np.ones(len(self.local))
        base = max(p['fade'], 0.0) * su
        mults = ((p['fade_xn'], p['fade_xp']), (p['fade_yn'], p['fade_yp']), (p['fade_zn'], p['fade_zp']))
        for ax in range(3):
            for side, m in enumerate(mults[ax]):
                f = base * max(m, 0.0)
                if f <= 1e-9:
                    continue
                d = (self.local[:, ax] + half[ax]) if side == 0 else (half[ax] - self.local[:, ax])
                w *= _smooth01(d / f)
        return w


_SIM_CACHE = {}


def sim_at(p, frame, light, progress=None):
    """Dust state at a frame, simulated from (first - preroll). Recent states are kept so scrubbing is quick."""
    key = _sim_key(p)
    cache = _SIM_CACHE.setdefault(key, {})
    if len(_SIM_CACHE) > 3:
        for k in list(_SIM_CACHE)[:-3]:
            _SIM_CACHE.pop(k, None)
    sim = DustSim(p, light)
    fps, sub = float(p['fps']), max(int(p['substeps']), 1)
    f0 = int(p['first']) - int(p['preroll'])
    cands = [k for k in cache if k <= frame]
    start = max(cands) if cands else None
    if start is not None:
        sim.set_state(cache[start])
        f = start
    else:
        sim.start((f0 - p['first']) / fps * p['speed'])
        f = f0
    dt = p['speed'] / fps / sub
    total = max(frame - f, 1)
    f_begin = f
    while f < frame:
        sim.refresh_light()
        for _ in range(sub):
            sim.step(dt)
        f += 1
        if f % 5 == 0 or f == frame:          # keep every 5th frame (and the one asked for) to scrub back quickly
            cache[f] = sim.state()
        if progress and (frame - f) % 5 == 0 and progress((f - f_begin) / float(total), 'Simulating frame %d' % f) is False:
            return None
    if f not in cache:
        cache[f] = sim.state()
    while len(cache) > 80:
        cache.pop(min(cache))
    sim.refresh_light()
    return sim


# =====================================================================================================
#  Rendering one frame of dust
# =====================================================================================================
def _fast_len(n):
    while True:
        m = n
        for q in (2, 3, 5):
            while m % q == 0:
                m //= q
        if m == 1:
            return n
        n += 1


_BOKEH_IMAGES = {}


def _read_png_gray(path):
    """8/16-bit, non-interlaced PNG -> (H, W) float 0..1 (luminance, times alpha if there is one)."""
    with open(path, 'rb') as fh:
        b = fh.read()
    if b[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('%s is not a PNG' % path)
    o, idat, W = 8, [], 0
    while o < len(b):
        n, typ = struct.unpack('>I4s', b[o:o + 8])
        data = b[o + 8:o + 8 + n]
        if typ == b'IHDR':
            W, H, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', data)
        elif typ == b'IDAT':
            idat.append(data)
        o += 12 + n
    if inter:
        raise ValueError('Interlaced PNGs are not supported')
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = ch * depth // 8
    raw = np.frombuffer(zlib.decompress(b''.join(idat)), np.uint8)
    stride = W * bpp
    img = np.zeros((H, stride), np.int32)
    prev = np.zeros(stride, np.int32)
    for y in range(H):
        f = raw[y * (stride + 1)]
        line = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)].astype(np.int32)
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros(stride, np.int32)
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                if f == 1:
                    pr = a
                elif f == 3:
                    pr = (a + prev[i]) >> 1
                else:
                    pa, pb, pc = abs(prev[i] - c), abs(a - c), abs(a + prev[i] - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (prev[i] if pb <= pc else c)
                cur[i] = (line[i] + pr) & 255
        img[y] = cur
        prev = cur
    if depth == 16:
        px = (img[:, 0::2] * 256 + img[:, 1::2]).astype(np.float64) / 65535.0
    else:
        px = img.astype(np.float64) / 255.0
    px = px.reshape(H, W, ch)
    lum = px[..., 0] if ch <= 2 else 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]
    if ch in (2, 4):
        lum = lum * px[..., -1]
    return lum


def load_bokeh_image(path):
    """A custom aperture shape (EXR or PNG): white = open. Returned square (padded), peak 1. Cached."""
    if not path:
        return None
    key = (path, os.path.getmtime(path) if os.path.exists(path) else 0)
    if key in _BOKEH_IMAGES:
        return _BOKEH_IMAGES[key]
    if path.lower().endswith('.png'):
        img = _read_png_gray(path)
    else:
        e = ExrFile(path)
        try:
            chs = [n for n, t in e.parts[0]['channels']]
            pick = [c for c in ('Y', 'R', 'G', 'B') if c in chs]
            d = e.read(0, pick or chs[:1])
            img = d['Y'] if 'Y' in d else (0.2126 * d['R'] + 0.7152 * d['G'] + 0.0722 * d['B'] if all(c in d for c in 'RGB') else list(d.values())[0])
        finally:
            e.close()
    img = np.clip(np.nan_to_num(np.asarray(img, np.float64)), 0, None)
    H, W = img.shape
    n = max(H, W)
    sq = np.zeros((n, n))
    sq[(n - H) // 2:(n - H) // 2 + H, (n - W) // 2:(n - W) // 2 + W] = img
    sq /= max(sq.max(), 1e-9)
    _BOKEH_IMAGES.clear()
    _BOKEH_IMAGES[key] = sq
    return sq


def _sample_bilinear(img, u, v):
    """img (n, n); u, v in 0..1 (v = 0 at the top). Outside = 0."""
    n = img.shape[0]
    x, y = u * n - 0.5, v * n - 0.5
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    tx, ty = x - x0, y - y0
    out = np.zeros(u.shape)
    for dx, dy, w in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < n) & (yi >= 0) & (yi < n)
        out[ok] += w[ok] * img[yi[ok], xi[ok]]
    return out


def _aperture(R, blades=0, rot=0.25, aspect=1.0, cat=(0.0, 0.0), falloff=0.0, image=None, img_rot=0.0):
    """Lens aperture kernel, radius R px, normalised to sum 1.
    blades: iris blades (< 3 = round). aspect: anamorphic aspect (> 1 = taller bokeh, same area).
    cat: cat-eye offset (fraction of R, pointing out from the frame centre): the bokeh is the overlap of two
    discs that far apart, so bokeh toward the frame edges turn into lemon shapes.
    falloff: 0 = evenly bright (default), > 0 brighter in the middle, < 0 brighter at the rim.
    image: a custom aperture (square array, see load_bokeh_image) spanning the bokeh's diameter."""
    sa = math.sqrt(max(aspect, 0.05))
    r = int(math.ceil(R * max(sa, 1.0 / sa) + 1.5))
    y, x = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float64)
    Rs = max(R, 1e-6)
    xa, ya = x * sa, y / sa
    rr = np.hypot(xa, ya) / Rs
    if blades >= 3:
        a = np.arctan2(ya, xa) + rot
        sg = 2 * math.pi / blades
        ang = np.mod(a, sg) - sg / 2
        poly = rr * np.cos(ang) / math.cos(sg / 2)
        m = 0.65 * poly + 0.35 * rr
    else:
        m = rr
    aa = min(max(1.5 / Rs, 0.04), 0.5)
    win = lambda q: 1 - _smooth01((q - (1 - aa)) / (2 * aa))
    lens = None
    if cat[0] or cat[1]:        # cat-eye: inside both discs, offset by +-cat/2 (one soft edge for the whole outline)
        cx, cy = cat[0] * 0.5, cat[1] * 0.5
        lens = np.maximum(np.hypot(x / Rs - cx, y / Rs - cy), np.hypot(x / Rs + cx, y / Rs + cy))
    if image is not None:
        c, s_ = math.cos(-img_rot), math.sin(-img_rot)
        xr, yr = (xa * c - ya * s_) / Rs, (xa * s_ + ya * c) / Rs
        k = _sample_bilinear(image, xr * 0.5 + 0.5, yr * 0.5 + 0.5) * (1 + falloff * (0.5 - rr * rr))
        if lens is not None:
            k = k * win(lens)
    else:
        k = win(m if lens is None else np.maximum(m, lens)) * (1 + falloff * (0.5 - m * m))
    k = np.clip(k, 0, None)
    return k / max(k.sum(), 1e-12)


def shutter_weight(u, shape):
    """Shutter shape: how open the shutter is at u (0 = opens, 1 = closes). 'backwardsShape' follows the studio
    cameras' curve: closed at first, opening along an S-curve, fully open as it reaches the frame."""
    u = np.asarray(u, np.float64)
    if str(shape).lower().startswith('back'):
        return 1.0 / (1.0 + np.exp(-9.0 * (u - 0.55)))
    return np.ones_like(u)


def bokeh_shape(p):
    """(rotation for the blades in radians, falloff, custom image or None, image rotation)."""
    rot = math.radians(p.get('bokeh_rot', 0.0))
    img = None
    if p.get('bokeh_image'):
        try:
            img = load_bokeh_image(p['bokeh_image'])
        except Exception:
            img = None
    return 0.25 + rot, p.get('bokeh_falloff', 0.0), img, rot


def lens_settings(p, cam, fstop):
    """Bokeh settings: header metadata plus the knobs (offsets, like the studio's BokehBlur)."""
    ex = getattr(cam, 'extra', {}) or {}
    blades = int(p['blades']) if int(p['blades']) > 0 else int(round(ex.get('blades', 0)))
    cat = ex.get('cateye', 0.0) + p.get('cateye', 0.0)
    aspect = (ex.get('anamorphic') or 1.0) + p.get('anamorphic', 0.0)
    fc = ex.get('fieldcurv', 0.0) + p.get('field_curv', 0.0)
    full = p.get('fstop_full', 0.0) or ex.get('fstop_full') or fstop
    vign = min(1.0, full / max(fstop, 1e-3))          # stopping down from wide open shrinks the cat-eye
    return blades, max(cat, 0.0) * vign, max(aspect, 0.05), fc


def _gauss1d(s):
    r = max(1, int(math.ceil(3 * s)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    g = np.exp(-x * x / (2 * s * s))
    return g / g.sum()


def _blur_sep(img, s):
    g = _gauss1d(s)
    r = len(g) // 2
    p = np.pad(img, ((r, r), (r, r), (0, 0)))
    out = np.zeros((img.shape[0] + 2 * r, img.shape[1], img.shape[2]))
    for i in range(2 * r + 1):
        out += g[i] * p[:, i:i + img.shape[1], :]
    res = np.zeros_like(img)
    for i in range(2 * r + 1):
        res += g[i] * out[i:i + img.shape[0], :, :]
    return res


def _splat(shape, x, y, w):
    """Bilinear splat of weights w (n, C) at pixel coordinates x, y into an (h, w, C) image."""
    h, W = shape
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(np.intp), np.floor(fy).astype(np.intp)
    tx, ty = fx - x0, fy - y0
    out = np.zeros((h * W, w.shape[1]))
    for dx, dy, ww in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < h)
        if not ok.any():
            continue
        idx = yi[ok] * W + xi[ok]
        for c in range(w.shape[1]):
            out[:, c] += np.bincount(idx, weights=w[ok, c] * ww[ok], minlength=h * W)
    return out.reshape(h, W, w.shape[1])


def _fft_conv(img, k):
    """Same-size convolution of (h, w, C) img with a 2D kernel (FFT, zero padded)."""
    h, w, C = img.shape
    kh, kw = k.shape
    H2, W2 = _fast_len(h + kh - 1), _fast_len(w + kw - 1)
    K = np.fft.rfft2(k, s=(H2, W2))
    out = np.empty_like(img)
    oy, ox = kh // 2, kw // 2
    for c in range(C):
        F = np.fft.irfft2(np.fft.rfft2(img[:, :, c], s=(H2, W2)) * K, s=(H2, W2))
        out[:, :, c] = F[oy:oy + h, ox:ox + w]
    return out


def _tumble_axis(h4, h5, h6, rate, t):
    a = 2 * math.pi * (h4 + rate * t)
    b = 2 * math.pi * h5 + 4.1 * rate * t * (0.5 + h6)
    return np.stack([np.sin(a) * np.cos(b), np.cos(a), np.sin(a) * np.sin(b)], -1)


def render_frame(p, shot, frame, sim, progress=None):
    """Renders the dust for one frame. Returns (held, unheld) float32 arrays (H, W, 3)."""
    su = sim.su
    cam = shot.camera(frame)
    dist_img, _ = shot.surface(frame)
    H0, W0 = dist_img.shape
    sc = max(min(float(p['proxy']), 1.0), 0.1)
    W, H = max(int(round(W0 * sc)), 8), max(int(round(H0 * sc)), 8)
    focal = p['focal'] or cam.focal or 35.0
    film = p['film'] or cam.film or (2 * focal * math.tan(cam.hfov / 2))
    fstop = p['fstop'] or cam.fstop or 4.0
    focus_m = (p['focus'] or cam.focus or 500.0) * su
    focal_px = W / (2 * math.tan(cam.hfov / 2))          # matches the camera's field of view exactly
    held = np.zeros((H, W, 3))
    unheld = np.zeros((H, W, 3))
    n = len(sim.local)
    if n == 0:
        return held.astype(np.float32), unheld.astype(np.float32)

    # shutter
    s = p['shutter'] / 360.0 if p['mb'] else 0.0
    mode = str(p['shutter_mode']).lower()
    t_open, t_close = (-s, 0.0) if mode.startswith('back') else (0.0, s) if mode.startswith('for') else (-s / 2, s / 2)
    cam_o, cam_c = shot.camera(frame + t_open), shot.camera(frame + t_close)
    tsec = (frame - p['first']) / float(p['fps']) * p['speed']
    sec_per_frame = p['speed'] / float(p['fps'])

    Pw_m = sim.world()
    P = Pw_m / su                                   # scene units
    V = sim.V / su                                  # scene units per second (sim.V is in world axes already)
    weight = sim.edge_weight()
    d = P - cam.pos
    zc = -(d @ cam.back)
    near = 0.05 / su
    keep = (weight > 1e-4) & (zc > near)
    px, py, _ = cam.project(P, W, H)
    margin = p['max_bokeh'] * sc + 40
    keep &= (px > -margin) & (px < W + margin) & (py > -margin) & (py < H + margin)
    idx = np.where(keep)[0]
    if not len(idx):
        return held.astype(np.float32), unheld.astype(np.float32)
    P, V, weight, zc = P[idx], V[idx], weight[idx], zc[idx]
    typ, hh, vis = sim.type[idx], sim.h[idx], sim._vis[idx]
    z_m = zc * su
    dist = np.linalg.norm(P - cam.pos, axis=1)
    dist_m = dist * su
    Vd = (cam.pos - P) / dist[:, None]

    # defocus: circle of confusion in pixels
    blades, cat_amt, aspect, fcurv = lens_settings(p, cam, fstop)
    if p['dof']:
        rn2 = ((px[idx] - W / 2.0) ** 2 + (py[idx] - H / 2.0) ** 2) / ((W / 2.0) ** 2 + (H / 2.0) ** 2)
        s1 = focus_m * 1000.0 * np.maximum(1 + fcurv * rn2, 0.05)       # field curvature: focus shifts toward the edges
        s2 = np.maximum(z_m, 0.02) * 1000.0
        coc = (focal / fstop) * focal * (s2 - s1) / (s2 * np.maximum(s1 - focal, 1.0)) / film * W
        coc = coc * np.where(coc < 0, p.get('front_mult', 1.0), p.get('back_mult', 1.0))
    else:
        coc = np.zeros(len(idx))
    R = np.clip(np.abs(coc) * 0.5, 0.25, p['max_bokeh'] * sc)
    ppm = focal_px / np.maximum(z_m, 1e-3)

    # what each mote is, how big and how bright (same model as the MoteField mockup)
    L = light_dir(p['light_az'], p['light_el'])
    u = hh[:, 1]
    rate = p.get('tumble', 1.0) * np.where(typ == FLAKE, 0.12 + 0.78 * hh[:, 3], np.where(typ == GRIT, 0.1 + 0.6 * hh[:, 3], 0.03 + 0.27 * hh[:, 3]))
    ax = _tumble_axis(hh[:, 4], hh[:, 5], hh[:, 6], rate, tsec)
    sigma = np.zeros(len(idx))
    rs = np.zeros(len(idx))
    gph = np.full(len(idx), p['forward'])
    tcol = np.ones((len(idx), 3))
    m = typ == GRIT
    dg = p['size_min'] * (p['size_max'] / max(p['size_min'], 1e-3)) ** (u[m] ** p['size_bias'])
    sigma[m] = (dg / 40.0) ** 2 * (1 + 0.12 * np.sin(2 * math.pi * (rate[m] * tsec * 1.3 + hh[m, 7])))
    rs[m] = 0.5 * dg * 1e-6 * ppm[m]
    m = typ == FLAKE
    df = 30.0 * 8.0 ** (u[m] ** 2.2) * p['flake_scale']
    nl = np.abs(ax[m] @ L)
    nv = np.abs((ax[m] * Vd[m]).sum(1))
    sigma[m] = (df * df / 1600.0) * (0.4 * nl * (0.25 + 0.75 * nv) + 0.03)
    rs[m] = 0.5 * df * 1e-6 * ppm[m]
    gph[m] = p['forward'] * 0.85
    tcol[m] = np.where((hh[m, 7] > 0.5)[:, None], [1.04, 1.0, 0.94], [0.95, 0.95, 0.97])
    m = typ == FIBER
    ln = 300.0 * 8.0 ** (u[m] ** 1.8) * p['fiber_scale']
    th = 8.0 + 12.0 * hh[m, 8]
    tl = ax[m] @ L
    sigma[m] = (ln * th / 1600.0) * np.sqrt(np.maximum(1 - tl * tl, 0.03)) * 0.5
    rs[m] = th * 1e-6 * ppm[m]
    gph[m] = p['forward'] * 0.5
    h9 = hh[m, 9]
    if m.any():
        h9c = h9[:, None]
        tcol[m] = np.select([h9c < 0.4, h9c < 0.58, h9c < 0.7, h9c < 0.84], [np.array([0.95, 0.95, 0.93]), np.array([0.5, 0.58, 0.82]), np.array([0.85, 0.45, 0.42]), np.array([0.36, 0.34, 0.32])], np.array([0.82, 0.74, 0.55]))
    m = typ == FLUFF
    dl = 200.0 * 6.0 ** (u[m] ** 2.0) * p['fluff_scale']
    sigma[m] = (dl * dl / 1600.0) * 0.07
    rs[m] = 0.5 * dl * 1e-6 * ppm[m]
    gph[m] = p['forward'] * 0.45
    tcol[m] = [0.9, 0.9, 0.93]

    sun = kelvin_rgb(p['light_k']) * p['light_int']
    c = (-L * Vd).sum(1)
    hg = (1 - gph ** 2) / np.power(np.maximum(1 + gph ** 2 - 2 * gph * c, 1e-4), 1.5)
    rad = sun[None, :] * (vis * hg)[:, None]
    m = (typ == FLAKE) & (vis > 0)
    if m.any() and p['glint'] > 0:
        Hh = L[None, :] + Vd[m]
        Hh /= np.linalg.norm(Hh, axis=1, keepdims=True)
        g = 0.0
        gw = shutter_weight(np.arange(4) / 3.0, p.get('shutter_shape', 'box'))
        gw = gw * 4.0 / gw.sum()
        for i in range(4):
            tt = tsec + (t_open + (t_close - t_open) * i / 3.0) * sec_per_frame
            g = g + gw[i] * np.abs((_tumble_axis(hh[m, 4], hh[m, 5], hh[m, 6], rate[m], tt) * Hh).sum(1)) ** 90
        rad[m] += sun[None, :] * (vis[m] * g * 0.25 * 8 * p['glint'] * (0.4 + 0.6 * hh[m, 2]))[:, None]
    amb = sun * 0.004 * p['bounce']
    rad += amb[None, :] * p['shadow_fill'] * 1.5
    tint = np.asarray(p['tint'], np.float64)
    col = rad * tint[None, :] * tcol * (p['brightness'] * 0.8 * sigma / np.maximum(dist_m, 0.05) ** 2 * weight)[:, None] * (focal_px / 1100.0) ** 2

    # shutter: where each mote is on screen when the shutter opens and closes
    po = P + V * (t_open * sec_per_frame)
    pc = P + V * (t_close * sec_per_frame)
    xo, yo, _ = cam_o.project(po, W, H)
    xc, yc, _ = cam_c.project(pc, W, H)
    streak = np.hypot(xc - xo, yc - yo)
    Reff = np.where(typ == FLUFF, np.sqrt(R * R + 0.6 * rs * rs), np.where(typ == FIBER, R, np.sqrt(R * R + 0.5 * rs * rs)))
    S = np.clip(np.ceil(streak / np.maximum(Reff * 0.6, 0.6)), 1, 24).astype(np.intp)

    # fibres also spread along their length
    K = np.ones(len(idx), np.intp)
    fm = typ == FIBER
    if fm.any():
        half_len = (300.0 * 8.0 ** (u[fm] ** 1.8) * p['fiber_scale']) * 0.5e-6 / su
        a0 = P[fm] - ax[fm] * half_len[:, None]
        a1 = P[fm] + ax[fm] * half_len[:, None]
        x0f, y0f, _ = cam.project(a0, W, H)
        x1f, y1f, _ = cam.project(a1, W, H)
        flen = np.hypot(x1f - x0f, y1f - y0f)
        K[fm] = np.clip(np.ceil(flen / np.maximum(R[fm] * 0.7, 0.7)), 1, 24)
        fdx, fdy = np.zeros(len(idx)), np.zeros(len(idx))
        fdx[fm], fdy[fm] = x1f - x0f, y1f - y0f
        bend = np.zeros(len(idx))
        bend[fm] = (hh[fm, 2] - 0.5) * 0.6
    else:
        fdx = fdy = bend = np.zeros(len(idx))

    # holdouts: the surface each mote sits in front of or behind
    hold = np.ones(len(idx))
    if p['holdout']:
        sx = np.clip((px[idx] / W * W0).astype(np.intp), 0, W0 - 1)
        sy = np.clip((py[idx] / H * H0).astype(np.intp), 0, H0 - 1)
        surf = dist_img[sy, sx].astype(np.float64)
        has = surf > 0
        occl = np.where(has, _smooth01(((dist - surf) / np.maximum(surf, 1e-6) + 0.01) / 0.04), 0.0)
        fgw = np.ones(len(idx))
        if p['fg_fade'] and p['fg_far'] > p['fg_near']:
            fgw = _smooth01((surf - p['fg_near']) / (p['fg_far'] - p['fg_near']))
        elif p['fg_fade']:
            fgw = (surf >= p['fg_near']).astype(np.float64)
        hold = 1 - occl * fgw

    # expand into samples along the shutter (and along fibres)
    NS = S * K
    rep = np.repeat(np.arange(len(idx)), NS)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(NS) - NS, NS)
    Ks = K[rep]
    si, ki = j // Ks, j % Ks
    ts = (si + 0.5) / S[rep]
    kf = np.where(Ks > 1, ki / np.maximum(Ks - 1, 1) - 0.5, 0.0)
    X = xo[rep] + (xc[rep] - xo[rep]) * ts + fdx[rep] * kf - fdy[rep] * bend[rep] * (0.25 - kf * kf)
    Y = yo[rep] + (yc[rep] - yo[rep]) * ts + fdy[rep] * kf + fdx[rep] * bend[rep] * (0.25 - kf * kf)
    sw = shutter_weight(ts, p.get('shutter_shape', 'box'))
    sw = sw / np.bincount(rep, weights=sw, minlength=len(idx))[rep]     # each mote's samples sum to 1
    E = col[rep] * sw[:, None]
    Eh = E * hold[rep][:, None]
    Rr = Reff[rep]
    inside = (X > -margin) & (X < W + margin) & (Y > -margin) & (Y < H + margin)
    X, Y, E, Eh, Rr = X[inside], Y[inside], E[inside], Eh[inside], Rr[inside]
    # cat-eye: offset of each sample's bokeh, from where it sits in the frame
    half_diag = math.hypot(W / 2.0, H / 2.0)
    CX, CY = (X - W / 2.0) / half_diag * cat_amt, (Y - H / 2.0) / half_diag * cat_amt

    # bin by blur radius: tiny ones get a small gaussian, the rest the lens aperture
    edges = [0.0, 0.6, 0.9, 1.3, 1.8]
    r = 1.8
    while r < p['max_bokeh'] * sc + 1:
        r *= 1.22
        edges.append(r)
    edges.append(1e9)
    which = np.digitize(Rr, edges) - 1
    W6 = np.concatenate([Eh, E], 1)
    nb = len(edges) - 1
    # with cat-eye on, large bokeh are also grouped by their cat-eye offset (12 directions x 4 strengths)
    if cat_amt > 0:
        cm = np.hypot(CX, CY)
        zone_m = np.clip(np.round(cm / max(cat_amt, 1e-6) * 4), 0, 4).astype(np.intp)
        zone_a = np.mod(np.round(np.arctan2(CY, CX) / (2 * math.pi / 12)), 12).astype(np.intp)
        zone = np.where(zone_m == 0, 0, 1 + (zone_m - 1) * 12 + zone_a)
    else:
        zone = np.zeros(len(X), np.intp)
    groups = []
    for b in range(nb):
        mb = which == b
        if not mb.any():
            continue
        if b < 4:
            groups.append((b, mb, (0.0, 0.0)))
            continue
        for z in np.unique(zone[mb]):
            mz = mb & (zone == z)
            if z == 0:
                cvec = (0.0, 0.0)
            else:
                zm, za = (z - 1) // 12 + 1, (z - 1) % 12
                cvec = (math.cos(za * math.pi / 6) * cat_amt * zm / 4.0, math.sin(za * math.pi / 6) * cat_amt * zm / 4.0)
            groups.append((b, mz, cvec))
    for b, mb, cvec in groups:
        lo, hi = edges[b], min(edges[b + 1], p['max_bokeh'] * sc)
        Rb = math.sqrt(max(lo, 0.25) * max(hi, 0.25)) if b >= 1 else 0.4
        if b < 4:
            pad = int(math.ceil(3 * math.sqrt(0.25 * Rb * Rb + 0.12))) + 2
        else:
            sa = math.sqrt(aspect)
            pad = int(math.ceil(Rb * max(sa, 1 / sa))) + 3
        bx0 = int(max(math.floor(X[mb].min()) - pad, 0))
        by0 = int(max(math.floor(Y[mb].min()) - pad, 0))
        bx1 = int(min(math.ceil(X[mb].max()) + pad, W))
        by1 = int(min(math.ceil(Y[mb].max()) + pad, H))
        if bx1 <= bx0 or by1 <= by0:
            continue
        img = _splat((by1 - by0, bx1 - bx0), X[mb] - bx0, Y[mb] - by0, W6[mb])
        if b < 4:
            img = _blur_sep(img, math.sqrt(0.25 * Rb * Rb + 0.12))
        else:
            brot, bfall, bimg, irot = bokeh_shape(p)
            img = _fft_conv(img, _aperture(Rb, blades, rot=brot, aspect=aspect, cat=cvec, falloff=bfall, image=bimg, img_rot=irot))
        held[by0:by1, bx0:bx1] += img[:, :, :3]
        unheld[by0:by1, bx0:bx1] += img[:, :, 3:]
    return held.astype(np.float32), unheld.astype(np.float32)


# =====================================================================================================
#  Running it: one frame, or a range, written to the cache folder
# =====================================================================================================
def dust_path(p, f, name='dust'):
    return os.path.join(p['cache'], '%s.%04d.exr' % (name, f))


def write_dust(path, held, unheld):
    lum = lambda a: np.clip(0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2], 0, 1)
    ch = {'R': held[:, :, 0], 'G': held[:, :, 1], 'B': held[:, :, 2], 'A': lum(held),
          'noholdout.red': unheld[:, :, 0], 'noholdout.green': unheld[:, :, 1], 'noholdout.blue': unheld[:, :, 2], 'noholdout.alpha': lum(unheld)}
    write_exr(path, ch, {'motefield/version': ('string', __version__)}, half=True)


def render_frames(p, frames, progress=None, name='dust'):
    """Simulate and render the given frames (in order) into the cache folder. Returns the paths written."""
    if not p['cache']:
        raise ValueError('Set a cache folder first.')
    os.makedirs(p['cache'], exist_ok=True)
    shot = Shot(p['utility'], p['units'])
    light = make_light(p, shot)
    out = []
    frames = sorted(frames)
    for i, f in enumerate(frames):
        cb = None
        if progress:
            cb = lambda x, msg, i=i: progress((i + x * 0.6) / float(len(frames)), msg)
        sim = sim_at(p, f, light, cb)
        if sim is None:
            break
        if progress and progress((i + 0.6) / float(len(frames)), 'Rendering frame %d' % f) is False:
            break
        held, unheld = render_frame(p, shot, f, sim)
        path = dust_path(p, f, name)
        write_dust(path, held, unheld)
        out.append(path)
    return out


def fit_box(p, frame=None, far_pct=95.0):
    """A box around what the camera sees (scene units): centre, size."""
    shot = Shot(p['utility'], p['units'])
    fs = [frame] if frame else sorted(set([p['first'], (p['first'] + p['last']) // 2, p['last']]))
    pts = []
    for f in fs:
        dist, P = shot.surface(f)
        if P is None:
            continue
        Q = P.reshape(-1, 3)
        pts.append(Q[np.abs(Q).sum(1) > 0][::7])
    if not pts:
        raise ValueError('The utility pass needs a world position layer to fit the box.')
    Q = np.concatenate(pts).astype(np.float64)
    lo = np.percentile(Q, 100 - far_pct, axis=0)
    hi = np.percentile(Q, far_pct, axis=0)
    for f in fs:
        hi = np.maximum(hi, shot.camera(f).pos)
        lo = np.minimum(lo, shot.camera(f).pos)
    return ((lo + hi) / 2).tolist(), (hi - lo).tolist()


# =====================================================================================================
#  Nuke
# =====================================================================================================
_KNOBS = [
    # (tab, key, kind, label, extra)
    ('MoteField', None, 'text', '<b>MoteField</b> %s: camera-aware dust for comp. Renders to the cache folder; merge over the plate with plus.' % __version__, None),
    ('MoteField', 'utility', 'file', 'Utility pass', 'World position (P) and the camera in the EXR header. Use #### for the frame number.'),
    ('MoteField', 'units', 'enum', 'Scene units', list(UNITS)),
    ('MoteField', 'first', 'int', 'First frame', None),
    ('MoteField', 'last', 'int', 'Last frame', None),
    ('MoteField', 'fps', 'float', 'Frame rate', (1, 120)),
    ('MoteField', 'btn_standin', 'button', 'Create stand-in shot', 'standin'),
    ('MoteField', 'btn_fit', 'button', 'Fit box to shot', 'fit'),
    ('MoteField', 'btn_3d', 'button', '3D preview', 'preview3d'),
    ('MoteField', None, 'divider', 'Dust volume (scene units)', None),
    ('MoteField', 'box_center', 'xyz', 'Box centre', None),
    ('MoteField', 'box_size', 'xyz', 'Box size', None),
    ('MoteField', 'box_rot', 'float', 'Box rotate Y', (-180, 180)),
    ('MoteField', 'fade', 'float', 'Edge fade', (0, 200)),
    ('MoteField', 'fade_neg', 'xyz', 'Fade  -X -Y -Z', 'Multiplies the edge fade at the -X, -Y and -Z faces (0 = hard edge)'),
    ('MoteField', 'fade_pos', 'xyz', 'Fade  +X +Y +Z', 'Multiplies the edge fade at the +X, +Y and +Z faces'),
    ('MoteField', None, 'divider', 'Render', None),
    ('MoteField', 'cache', 'file', 'Cache folder', 'Where the dust frames are written. Each node needs its own folder.'),
    ('MoteField', 'proxy', 'float', 'Resolution', (0.1, 1)),
    ('MoteField', 'btn_frame', 'button', 'Render this frame', 'frame'),
    ('MoteField', 'btn_range', 'button', 'Render frame range', 'range'),
    ('MoteField', 'status', 'text', '', None),
    ('Dust', 'density', 'float', 'Density (per m³)', (1, 1000)),
    ('Dust', 'seed', 'int', 'Seed', None),
    ('Dust', 'size_min', 'float', 'Finest grit (µm)', (1, 30)),
    ('Dust', 'size_max', 'float', 'Coarsest grit (µm)', (20, 300)),
    ('Dust', 'size_bias', 'float', 'Fine-grit bias', (0.5, 4)),
    ('Dust', 'flakes', 'float', 'Flakes %', (0, 60)),
    ('Dust', 'fibers', 'float', 'Lint fibres %', (0, 12)),
    ('Dust', 'fluff', 'float', 'Fluff %', (0, 6)),
    ('Dust', 'flake_scale', 'float', 'Flake size', (0.3, 3)),
    ('Dust', 'fiber_scale', 'float', 'Fibre length', (0.3, 3)),
    ('Dust', 'fluff_scale', 'float', 'Fluff size', (0.15, 2)),
    ('Dust', None, 'divider', 'Look', None),
    ('Dust', 'brightness', 'float', 'Brightness', (0, 4)),
    ('Dust', 'forward', 'float', 'Forward scatter', (0, 0.9)),
    ('Dust', 'shadow_fill', 'float', 'Shadow-side fill', (0, 2)),
    ('Dust', 'glint', 'float', 'Flake glints', (0, 1)),
    ('Dust', 'tumble', 'float', 'Tumble rate', (0, 3)),
    ('Dust', 'tint', 'color', 'Tint', None),
    ('Dust', 'max_motes', 'int', 'Max motes', None),
    ('Motion', 'speed', 'float', 'Speed', (0, 3)),
    ('Motion', 'drift', 'float', 'Drift (cm/s)', (0, 20)),
    ('Motion', 'drift_dir', 'float', 'Drift heading', (0, 360)),
    ('Motion', 'lift', 'float', 'Vertical drift (cm/s)', (-20, 20)),
    ('Motion', 'eddies', 'float', 'Eddies (cm/s)', (0, 15)),
    ('Motion', 'eddy_size', 'float', 'Eddy size (m)', (0.2, 4)),
    ('Motion', 'micro', 'float', 'Micro-turbulence (cm/s)', (0, 5)),
    ('Motion', 'thermal', 'float', 'Thermal lift in light (cm/s)', (0, 6)),
    ('Motion', 'settle', 'float', 'Settling (cm/s)', (0, 4)),
    ('Motion', 'preroll', 'int', 'Pre-roll frames', None),
    ('Motion', 'substeps', 'int', 'Steps per frame', None),
    ('Light', 'light_az', 'float', 'Azimuth', (-180, 180)),
    ('Light', 'light_el', 'float', 'Elevation', (0, 90)),
    ('Light', 'light_int', 'float', 'Intensity', (0, 20)),
    ('Light', 'light_k', 'float', 'Colour temperature (K)', (2500, 10000)),
    ('Light', 'bounce', 'float', 'Bounce fill', (0, 4)),
    ('Light', 'shadows', 'enum', 'Shadows from', ['stand-in geometry', 'utility pass', 'none']),
    ('Light', 'interior', 'bool', 'Interior: light only through openings', 'Utility pass shadows only: the pass only shows what the camera saw, so light comes in only where the camera saw sky (a window).'),
    ('Light', None, 'text', 'A directional key light. Azimuth 0 = light travelling toward +X, 90 = toward +Z.', None),
    ('Lens', 'focal', 'float', 'Focal length (mm, 0 = header)', (0, 200)),
    ('Lens', 'film', 'float', 'Film back width (mm, 0 = header)', (0, 60)),
    ('Lens', 'fstop', 'float', 'f-stop (0 = header)', (0, 22)),
    ('Lens', 'focus', 'float', 'Focus distance (scene units, 0 = header)', (0, 5000)),
    ('Lens', 'dof', 'bool', 'Defocus', None),
    ('Lens', 'blades', 'int', 'Blade count (0 = header, < 3 round)', None),
    ('Lens', 'cateye', 'float', 'Cat-eye bokeh (+ header)', (0, 1)),
    ('Lens', 'anamorphic', 'float', 'Anamorphic aspect (+ header)', (-0.5, 1.5)),
    ('Lens', 'field_curv', 'float', 'Field curvature (+ header)', (-0.5, 0.5)),
    ('Lens', 'fstop_full', 'float', 'f-stop full aperture (0 = header)', (0, 8)),
    ('Lens', 'front_mult', 'float', 'Front multiplier', (0, 5)),
    ('Lens', 'back_mult', 'float', 'Back multiplier', (0, 5)),
    ('Lens', 'max_bokeh', 'float', 'Largest bokeh radius (px)', (10, 300)),
    ('Lens', 'bokeh_image', 'file', 'Custom bokeh shape', 'An image of the aperture (EXR or PNG), white = open. It spans the bokeh\'s diameter and replaces the blades.'),
    ('Lens', 'bokeh_rot', 'float', 'Bokeh rotation', (-180, 180)),
    ('Lens', 'bokeh_falloff', 'float', 'Bokeh centre / rim brightness', (-1, 1)),
    ('Lens', None, 'text', 'Like the studio BokehBlur: blades, cat-eye, anamorphic aspect, field curvature and full aperture come from '
                           'the camera metadata when it has them, and these knobs are added on top. Cat-eye turns bokeh toward the frame edges into '
                           'lemon shapes, less so as you stop down from full aperture. Front / back scale the blur in front of / behind focus. '
                           'Bokeh are round and evenly bright by default, like the studio renders.', None),
    ('Lens', None, 'divider', 'Motion blur', None),
    ('Lens', 'shutter', 'float', 'Shutter angle', (0, 360)),
    ('Lens', 'shutter_mode', 'enum', 'Shutter', ['centred', 'backward', 'forward']),
    ('Lens', 'shutter_shape', 'enum', 'Shutter shape', ['backwardsShape', 'box']),
    ('Lens', 'mb', 'bool', 'Motion blur', None),
    ('Holdouts', 'holdout', 'bool', 'Depth holdouts', 'Dust behind what the utility pass sees is held out.'),
    ('Holdouts', 'fg_fade', 'bool', 'Fade out foreground holdouts', None),
    ('Holdouts', 'fg_near', 'float', 'No holdout nearer than', (0, 2000)),
    ('Holdouts', 'fg_far', 'float', 'Full holdout beyond', (0, 2000)),
    ('Holdouts', None, 'text', 'Surfaces nearer than the first distance never block dust, rising to a full holdout at the second (scene units). '
                              'Use it when a foreground character\'s edges cause trouble, then hold the dust out with the character\'s matte in comp. '
                              'The <b>noholdout</b> layer always has the dust with no holdouts at all.', None),
]


def _nk():
    import nuke
    return nuke


def create():
    """Builds a MoteField node in the current Nuke script."""
    nuke = _nk()
    g = nuke.nodes.Group(name='MoteField1')
    g['tile_color'].setValue(0xe4ba6bff)
    tabs = {}
    for tab, key, kind, label, extra in _KNOBS:
        if tab not in tabs:
            g.addKnob(nuke.Tab_Knob('mf_' + tab.lower(), tab))
            tabs[tab] = True
        if kind == 'text':
            k = nuke.Text_Knob(key or ('t%d' % len(g.knobs())), '', label)
        elif kind == 'divider':
            k = nuke.Text_Knob('d%d' % len(g.knobs()), label)
        elif kind == 'file':
            k = nuke.File_Knob(key, label)
        elif kind == 'enum':
            k = nuke.Enumeration_Knob(key, label, extra)
        elif kind == 'int':
            k = nuke.Int_Knob(key, label)
        elif kind == 'float':
            k = nuke.Double_Knob(key, label)
            if extra:
                k.setRange(extra[0], extra[1])
        elif kind == 'xyz':
            k = nuke.XYZ_Knob(key, label)
        elif kind == 'bool':
            k = nuke.Boolean_Knob(key, label)
            k.setFlag(nuke.STARTLINE)
        elif kind == 'color':
            k = nuke.Color_Knob(key, label)
        elif kind == 'button':
            k = nuke.PyScript_Knob(key, label, 'import motefield; motefield._nk_button(nuke.thisNode(), %r)' % extra)
            if extra not in ('standin', 'frame'):
                k.clearFlag(nuke.STARTLINE)
        else:
            continue
        if kind in ('file', 'enum', 'float', 'int', 'xyz', 'bool') and isinstance(extra, str) and kind != 'enum':
            k.setTooltip(extra)
        g.addKnob(k)
    hid = nuke.String_Knob('standin_json', 'stand-in geometry')
    hid.setVisible(False)
    g.addKnob(hid)
    _nk_defaults(g)
    g.begin()
    rd = nuke.nodes.Read(name='dust_frames')
    rd['file'].setValue('[value parent.cache]/dust.####.exr')
    for kk, ex in (('first', 'parent.first'), ('last', 'parent.last'), ('origfirst', 'parent.first'), ('origlast', 'parent.last')):
        try:
            rd[kk].setExpression(ex)
        except Exception:
            pass
    try:
        rd['on_error'].setValue('black')
    except Exception:
        pass
    try:
        rd['raw'].setValue(True)
    except Exception:
        pass
    out = nuke.nodes.Output()
    out.setInput(0, rd)
    g.end()
    return g


def _nk_defaults(g):
    d = DEFAULTS
    for tab, key, kind, label, extra in _KNOBS:
        if not key or kind in ('button', 'text', 'divider') or key not in d and key not in ('fade_neg', 'fade_pos'):
            continue
        try:
            if key == 'fade_neg':
                g[key].setValue([d['fade_xn'], d['fade_yn'], d['fade_zn']])
            elif key == 'fade_pos':
                g[key].setValue([d['fade_xp'], d['fade_yp'], d['fade_zp']])
            elif kind in ('xyz', 'color'):
                g[key].setValue(list(d[key]))
            elif kind == 'enum':
                g[key].setValue(str(d[key]))
            elif kind == 'bool':
                g[key].setValue(bool(d[key]))
            elif kind == 'file' and key == 'cache':
                import tempfile
                g[key].setValue(os.path.join(tempfile.gettempdir(), 'motefield', g.name()).replace('\\', '/'))
            else:
                g[key].setValue(d[key])
        except Exception:
            pass


def _nk_settings(g):
    p = dict(DEFAULTS)
    for tab, key, kind, label, extra in _KNOBS:
        if not key or kind in ('button', 'text', 'divider'):
            continue
        k = g.knob(key)
        if k is None:
            continue
        v = k.value()
        if key == 'fade_neg':
            p['fade_xn'], p['fade_yn'], p['fade_zn'] = [float(x) for x in v]
        elif key == 'fade_pos':
            p['fade_xp'], p['fade_yp'], p['fade_zp'] = [float(x) for x in v]
        elif kind in ('xyz', 'color'):
            p[key] = tuple(float(x) for x in (v if isinstance(v, (list, tuple)) else [v, v, v]))
        elif kind == 'int':
            p[key] = int(v)
        elif kind == 'float':
            p[key] = float(v)
        elif kind == 'bool':
            p[key] = bool(v)
        else:
            p[key] = str(v)
    p['standin_json'] = g['standin_json'].value()
    for k in ('utility', 'cache'):
        try:
            p[k] = g[k].evaluate() if '[' in p[k] else p[k]
        except Exception:
            pass
    return p


def _nk_status(g, msg):
    nuke = _nk()

    def f():
        try:
            g['status'].setValue(msg)
        except Exception:
            pass
    try:
        nuke.executeInMainThread(f)
    except Exception:
        f()


def _nk_reload(g):
    nuke = _nk()

    def f():
        try:
            rd = g.node('dust_frames')
            rd['reload'].execute()
        except Exception:
            pass
    nuke.executeInMainThread(f)


_RUNNING = {}


def _nk_thread(g, title, work):
    """Runs work(progress) in a background thread with a Nuke progress bar, so the UI stays usable."""
    import threading
    nuke = _nk()
    name = g.fullName()
    if _RUNNING.get(name):
        nuke.message('MoteField is already working on %s.' % name)
        return

    def run():
        _RUNNING[name] = True
        task = nuke.ProgressTask(title)
        t0 = time.time()

        def progress(x, msg):
            task.setProgress(int(max(0, min(1, x)) * 100))
            task.setMessage(msg)
            return not task.isCancelled()
        try:
            res = work(progress)
            _nk_status(g, '%s: done in %.1f s' % (title, time.time() - t0) if res is not False else '%s: cancelled' % title)
        except Exception as e:
            import traceback
            traceback.print_exc()
            msg = '%s failed: %s' % (title, e)
            _nk_status(g, msg)
            nuke.executeInMainThread(nuke.message, args=(msg,))
        finally:
            del task
            _RUNNING[name] = False
    threading.Thread(target=run).start()


def _nk_button(g, what):
    nuke = _nk()
    if what == 'standin':
        return _nk_standin(g)
    p = _nk_settings(g)
    if what in ('frame', 'range', 'fit', 'preview3d') and not p['utility']:
        nuke.message('Choose a utility pass first (or press Create stand-in shot).')
        return
    if what == 'fit':
        c, s = fit_box(p)
        g['box_center'].setValue(c)
        g['box_size'].setValue(s)
        return
    if what == 'preview3d':
        return _nk_preview3d(g, p)
    if what == 'frame':
        f = int(nuke.frame())
        frames = [f]
    else:
        frames = list(range(p['first'], p['last'] + 1))

    def work(progress):
        out = render_frames(p, frames, progress)
        _nk_reload(g)
        return bool(out)
    _nk_thread(g, 'MoteField: rendering %s' % ('frame %d' % frames[0] if len(frames) == 1 else '%d frames' % len(frames)), work)


def _nk_standin(g):
    nuke = _nk()
    p = _nk_settings(g)
    base = p['cache'] or os.path.join(os.path.expanduser('~'), 'motefield')
    folder = os.path.join(os.path.dirname(base.rstrip('/')) or base, 'standin_shot').replace('\\', '/')

    def work(progress):
        res = make_standin_shot(folder, progress)
        if not res:
            return False

        def apply():
            g['utility'].setValue(res['utility'])
            g['units'].setValue('centimetres')
            g['first'].setValue(res['first'])
            g['last'].setValue(res['last'])
            g['shadows'].setValue('stand-in geometry')
            g['standin_json'].setValue(folder + '/standin.json')
            g['box_center'].setValue(list(DEFAULTS['box_center']))
            g['box_size'].setValue(list(DEFAULTS['box_size']))
            g['light_az'].setValue(STANDIN['sun_az'])
            g['light_el'].setValue(STANDIN['sun_el'])
            try:
                r = nuke.root()
                r['first_frame'].setValue(res['first'])
                r['last_frame'].setValue(res['last'])
                r['fps'].setValue(24)
            except Exception:
                pass
            plate = nuke.nodes.Read(file=res['plate'], first=res['first'], last=res['last'], name='standin_plate')
            try:
                plate['raw'].setValue(True)
            except Exception:
                pass
            plate.setXYpos(g.xpos() - 150, g.ypos() - 80)
            mg = nuke.nodes.Merge2(operation='plus', name='dust_over_plate')
            mg.setInput(0, plate)
            mg.setInput(1, g)
            mg.setXYpos(g.xpos() - 150, g.ypos() + 80)
            nuke.message('Stand-in shot ready: an interior with a window opening and a moving camera (frames %d-%d).\n\n'
                         'Press Render this frame to see the dust, or Render frame range for the whole shot.' % (res['first'], res['last']))
        nuke.executeInMainThread(apply)
        return True
    _nk_thread(g, 'MoteField: creating the stand-in shot', work)


def _nk_preview3d(g, p):
    """Point cloud of the utility pass and the dust box in Nuke's 3D viewer (classic 3D nodes)."""
    nuke = _nk()
    made, missing = [], []
    gn = g.fullName()
    rd = nuke.nodes.Read(file=p['utility'], first=p['first'], last=p['last'], name='mf_utility')
    rd.setXYpos(g.xpos() + 150, g.ypos() - 120)
    made.append(rd)
    pts = None
    for cls in ('PositionToPoints2', 'PositionToPoints'):
        try:
            pts = getattr(nuke.nodes, cls)()
            break
        except Exception:
            continue
    if pts is not None:
        pts.setInput(0, rd)
        try:
            shot = Shot(p['utility'], p['units'])
            e = shot._open(p['first'])
            found = survey_layers(e)
            e.close()
            if 'P' in found:
                pts['P_channel'].setValue(found['P'][1][0].split('.')[0])
        except Exception:
            pass
        pts.setXYpos(g.xpos() + 150, g.ypos() - 40)
        made.append(pts)
    else:
        missing.append('PositionToPoints')
    cube = None
    try:
        cube = nuke.nodes.Cube(name='mf_dust_box')
        for i, ax in enumerate('xyz'):
            cube['translate'].setExpression('%s.box_center.%s' % (gn, ax), i)
        cube['rotate'].setExpression('%s.box_rot' % gn, 1)
        ex = ['-%s.box_size.x/2', '-%s.box_size.y/2', '-%s.box_size.z/2', '%s.box_size.x/2', '%s.box_size.y/2', '%s.box_size.z/2']
        for i, e_ in enumerate(ex):
            cube['cube'].setExpression(e_ % gn, i)
        try:
            cube['display'].setValue('wireframe')
        except Exception:
            pass
        cube.setXYpos(g.xpos() + 280, g.ypos() - 40)
        made.append(cube)
    except Exception:
        missing.append('Cube')
    cam = _nk_camera(p)
    if cam is not None:
        cam.setXYpos(g.xpos() + 410, g.ypos() - 40)
        made.append(cam)
    else:
        missing.append('Camera')
    sc = None
    try:
        sc = nuke.nodes.Scene(name='mf_scene')
        n = 0
        for node in made:
            if node.Class() not in ('Read',):
                sc.setInput(n, node)
                n += 1
        sc.setXYpos(g.xpos() + 280, g.ypos() + 40)
    except Exception:
        missing.append('Scene')
    msg = 'Made a 3D preview: the utility pass as points, the dust box (it follows the node\'s box knobs) and the shot camera. ' \
          'View the Scene node in the 3D viewer (Tab).'
    if missing:
        msg += '\n\nCould not make: %s (your Nuke may use different 3D node names).' % ', '.join(missing)
    nuke.message(msg)


def _nk_camera(p):
    """A classic Camera with the utility pass camera baked in, one key per frame."""
    nuke = _nk()
    cam = None
    for cls in ('Camera3', 'Camera2', 'Camera'):
        try:
            cam = getattr(nuke.nodes, cls)(name='mf_shot_camera')
            break
        except Exception:
            continue
    if cam is None:
        return None
    try:
        shot = Shot(p['utility'], p['units'])
        cam['useMatrix'].setValue(True)
        mk = cam['matrix']
        mk.setAnimated()
        for f in range(p['first'], p['last'] + 1):
            c = shot.camera(f)
            r, u, b, t = c.right, c.up, c.back, c.pos
            # Nuke matrices are column-vector (translation in the last column), row-major in the knob
            M = [r[0], u[0], b[0], t[0], r[1], u[1], b[1], t[1], r[2], u[2], b[2], t[2], 0, 0, 0, 1]
            for i in range(16):
                mk.setValueAt(M[i], f, i)
            film = p['film'] or c.film or 24.89
            focal = film / (2 * math.tan(c.hfov / 2))
            cam['focal'].setValueAt(focal, f)
        cam['haperture'].setValue(film)
    except Exception:
        import traceback
        traceback.print_exc()
    return cam
