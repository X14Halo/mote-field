"""
Browser glue for MoteField: runs the unchanged motefield.py (the Nuke tool) inside Pyodide.

The Nuke node's knobs become a web panel built from motefield._KNOBS, and the buttons call the same functions
the Nuke buttons call. Differences from Nuke:
  * files live in the browser's memory (/work), never on disk or on a network path
  * the stand-in shot is rendered one frame at a time, when a frame is first needed, at a chosen resolution
  * frames come back to the page as half floats for the viewer instead of being written to a cache folder
"""
import os, re, json, time
import numpy as np
import motefield as mf

ROOT = '/work'
STANDIN_RES = {640: 268, 960: 402, 1280: 536, 1920: 804}
_BaseShot = mf.Shot
_shots = {}
_light = {'key': None, 'fn': None}
_last = {'key': None, 'held': None, 'unheld': None}
_made = []                     # stand-in frames generated so far (oldest first), so memory stays bounded
KEEP_FRAMES = 14


def _standin_meta(folder):
    js = os.path.join(folder, 'standin.json')
    if os.path.exists(js):
        with open(js) as fh:
            return json.load(fh)
    return None


def _ensure_standin_frame(folder, meta, f):
    util = os.path.join(folder, 'standin_utility.%04d.exr' % f)
    plate = os.path.join(folder, 'standin_plate.%04d.exr' % f)
    if os.path.exists(util) and os.path.exists(plate):
        return
    u, pl, attrs = mf.render_standin_frame(f, meta['width'], meta['height'], view=meta.get('view', 'wide'))
    mf.write_exr(util, u, attrs)
    mf.write_exr(plate, pl, attrs, half=True)
    _made.append((util, plate))
    while len(_made) > KEEP_FRAMES:
        for path in _made.pop(0):
            try:
                os.remove(path)
            except OSError:
                pass


class WebShot(_BaseShot):
    """motefield.Shot, plus: stand-in frames are rendered the first time they are opened."""

    def __init__(self, pattern, units):
        _BaseShot.__init__(self, pattern, units)
        self.folder = os.path.dirname(pattern)
        self.meta = _standin_meta(self.folder) if os.path.basename(pattern).startswith('standin_utility') else None

    def _has(self, f):
        if self.meta is not None:
            return self.meta['first'] <= f <= self.meta['last']
        return _BaseShot._has(self, f)

    def _cam(self, f):
        if self.meta is not None and f not in self._cams:
            self._cams[f] = mf.standin_camera(f, self.meta.get('view', 'wide'))   # the camera that frame's header would hold
        return _BaseShot._cam(self, f)

    def _open(self, f):
        if self.meta is not None:
            _ensure_standin_frame(self.folder, self.meta, f)
        return _BaseShot._open(self, f)


mf.Shot = WebShot          # fit_box(), make_light() etc. pick this up


def schema():
    knobs = []
    for tab, key, kind, label, extra in mf._KNOBS:
        knobs.append(dict(tab=tab, key=key, kind=kind, label=label,
                          extra=list(extra) if isinstance(extra, (list, tuple)) else extra))
    d = {k: (list(v) if isinstance(v, tuple) else v) for k, v in mf.DEFAULTS.items()}
    return json.dumps(dict(version=mf.__version__, knobs=knobs, defaults=d, standin=mf.STANDIN,
                           standin_res=sorted(STANDIN_RES)))


def setup_standin(width=1280, view='wide'):
    """Like the Nuke 'Create stand-in shot' button, but frames are made on demand."""
    width = int(width)
    S = dict(mf.STANDIN, width=width, height=STANDIN_RES.get(width, int(round(width * 536 / 1280.0))))
    view = view if view in mf.STANDIN_VIEWS else 'wide'
    folder = '%s/standin_%s_%d' % (ROOT, view.replace('-', ''), width)
    os.makedirs(folder, exist_ok=True)
    meta = dict(S, boxes=[[b[0], b[1], b[3]] for b in mf.standin_boxes()], units='centimetres', view=view)
    with open(os.path.join(folder, 'standin.json'), 'w') as fh:
        json.dump(meta, fh)
    boxes = [list(b[0]) + list(b[1]) + list(b[2]) for b in mf.standin_boxes()]
    return json.dumps(dict(utility=folder + '/standin_utility.####.exr', plate=folder + '/standin_plate.####.exr', boxes=boxes,
                           plate_light=dict(az=S['sun_az'], el=S['sun_el'], intensity=6.0, kelvin=list(mf.kelvin_rgb(5000.0))),
                           room=[[-400.0, 0.0, -600.0], [400.0, 350.0, 350.0]],      # inside of the stand-in room, for the cutaway
                           first=S['first'], last=S['last'], width=S['width'], height=S['height'],
                           set=dict(units='centimetres', first=S['first'], last=S['last'], fps=24.0, shadows='stand-in geometry',
                                    standin_json=folder + '/standin.json', box_center=list(mf.DEFAULTS['box_center']),
                                    box_size=list(mf.DEFAULTS['box_size']), light_az=S['sun_az'], light_el=S['sun_el'])))


def add_files(kind, names, blobs):
    """Writes picked EXR files into /work/<kind> and works out the frame pattern and range."""
    folder = '%s/user_%s' % (ROOT, kind)
    if os.path.isdir(folder):
        for n in os.listdir(folder):
            os.remove(os.path.join(folder, n))
    os.makedirs(folder, exist_ok=True)
    groups = {}
    for n, b in zip(names, blobs):
        n = os.path.basename(str(n))
        with open(os.path.join(folder, n), 'wb') as fh:
            fh.write(bytes(b))
        m = re.match(r'^(.*?)(\d+)(\.exr)$', n, re.I)
        if m:
            groups.setdefault((m.group(1), len(m.group(2)), m.group(3)), []).append(int(m.group(2)))
    for k in list(_shots):
        if k.startswith(folder):
            _shots.pop(k)
    if not groups:
        if len(names) == 1:
            return json.dumps(dict(pattern=folder + '/' + os.path.basename(str(names[0])), first=1, last=1))
        raise ValueError('Name the files with a frame number, like shot_utility.1001.exr')
    (pre, pad, ext), frames = max(groups.items(), key=lambda kv: len(kv[1]))
    tok = '####' if pad == 4 else '%%0%dd' % pad
    pattern = folder + '/' + pre + tok + ext
    out = dict(pattern=pattern, first=min(frames), last=max(frames), count=len(frames))
    if kind == 'utility':
        e = mf.ExrFile(mf.frame_path(pattern, min(frames)))
        cam = mf.camera_from_headers(e.headers())
        found = mf.survey_layers(e)
        out['size'] = [e.parts[0]['width'], e.parts[0]['height']]
        e.close()
        out['camera'] = cam is not None
        out['layers'] = sorted(found)
        if cam is None:
            raise ValueError('No camera in the EXR header (looked for camToWorld / worldToCamera and a field of view or focal length + aperture).')
        if 'P' not in found and 'Z' not in found:
            raise ValueError('No world position or depth layer found in %s.' % os.path.basename(mf.frame_path(pattern, min(frames))))
    return json.dumps(out)


def _params(pjson):
    raw = json.loads(pjson)
    p = mf.settings(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()})
    p['cache'] = ROOT + '/cache'
    return p


def _shot(p):
    key = p['utility'] + '|' + p['units']
    if key not in _shots:
        if len(_shots) > 3:
            _shots.pop(next(iter(_shots)))
        _shots[key] = WebShot(p['utility'], p['units'])
    return _shots[key]


def _light_for(p, shot):
    key = json.dumps([p[k] for k in ('utility', 'units', 'shadows', 'light_az', 'light_el', 'interior', 'standin_json', 'first', 'last')])
    if _light['key'] != key:
        _light['fn'] = mf.make_light(p, shot)
        _light['key'] = key
    return _light['fn']


def render(pjson, frame, progress=None):
    """Simulates and renders one frame, exactly as the Nuke node's 'Render this frame' does."""
    if not json.loads(pjson).get('utility'):
        raise ValueError('Choose a utility pass first (or press Create stand-in shot).')
    t0 = time.time()
    p = _params(pjson)
    frame = int(frame)
    shot = _shot(p)
    if progress:
        progress(0.0, 'Reading frame %d' % frame)
    shot.surface(frame)
    light = _light_for(p, shot)
    cb = None
    if progress:
        cb = lambda x, msg: progress(0.1 + 0.6 * x, msg)
    sim = mf.sim_at(p, frame, light, cb)
    if progress:
        progress(0.75, 'Rendering frame %d' % frame)
    held, unheld = mf.render_frame(p, shot, frame, sim)
    _last.update(key=(pjson, frame), held=held, unheld=unheld)
    H, W = held.shape[:2]
    wgt = sim.edge_weight()
    inside = wgt > 1e-4
    n = int(np.count_nonzero(inside))
    # the motes themselves, for the 3D view: position (scene units) and how lit / how faded each one is
    pts = sim.scene_points()[inside].astype(np.float32)
    lit = np.clip(np.asarray(sim._vis)[inside] * 0.75 + 0.25, 0, 1) * np.clip(wgt[inside], 0, 1)
    return dict(w=W, h=H, held=held.astype(np.float16).tobytes(), unheld=unheld.astype(np.float16).tobytes(),
                motes=n, secs=time.time() - t0, pts=pts.tobytes(), lit=(lit * 255).astype(np.uint8).tobytes())


def scene(pjson, with_cloud=True, max_points=240000):
    """What the 3D view needs: the shot camera on every frame, and a point cloud of the utility pass
    (first, middle and last frames) coloured by its surface colour, or by its normals."""
    p = _params(pjson)
    shot = _shot(p)
    first, last = int(p['first']), int(p['last'])
    cams = []
    for f in range(first, last + 1):
        c = shot.camera(f)
        cams.extend(list(c.pos) + list(c.right) + list(c.up) + list(c.back) + [c.hfov])
    fs = sorted(set([first, (first + last) // 2, last])) if with_cloud else []
    P_all, C_all, grids = [], [], []
    for f in fs:
        e = shot._open(f)
        try:
            found = mf.survey_layers(e)
            if 'P' not in found:
                continue
            pi, names = found['P']
            d = e.read(pi, names)
            P = np.stack([d[n] for n in names], -1)
            H, W = P.shape[:2]
            if 'C' in found:
                ci, cn = found['C']
                dc = e.read(ci, cn)
                C = np.stack([dc[n] for n in cn], -1)
                C = np.clip(C, 0, 1) ** (1 / 2.2)
            else:
                C = np.full_like(P, 0.5)
            if 'N' in found:
                ni, nn = found['N']
                dn = e.read(ni, nn)
                N = np.stack([dn[n] for n in nn], -1)
            else:
                N = np.zeros_like(P)
        finally:
            e.close()
        # a coarse grid of the pass for a solid mesh in the 3D view (about 320 columns)
        st = max(1, int(round(W / 320.0)))
        cam = shot.camera(f)
        grids.append(dict(w=len(range(0, W, st)), h=len(range(0, H, st)), P=P[::st, ::st].astype(np.float32).tobytes(),
                          N=N[::st, ::st].astype(np.float32).tobytes(), C=(np.clip(C[::st, ::st], 0, 1) * 255).astype(np.uint8).tobytes(),
                          cam=list(cam.pos)))
        P, C = P.reshape(-1, 3), C.reshape(-1, 3)
        ok = (np.abs(P).sum(1) > 0) & np.isfinite(P).all(1)
        P, C = P[ok], C[ok]
        step = max(1, int(np.ceil(len(P) / float(max_points // len(fs)))))
        P_all.append(P[::step])
        C_all.append(C[::step])
    P = np.concatenate(P_all).astype(np.float32) if P_all else np.zeros((0, 3), np.float32)
    C = (np.concatenate(C_all) * 255).astype(np.uint8) if C_all else np.zeros((0, 3), np.uint8)
    c0 = shot.camera(first)
    if shot.meta is not None:
        size = [shot.meta['width'], shot.meta['height']]
    else:
        e = shot._open(first)
        size = [e.parts[0]['width'], e.parts[0]['height']]
        e.close()
    lens = dict(focal=c0.focal or 0.0, film=c0.film or 0.0, fstop=c0.fstop or 0.0, focus=c0.focus or 0.0, extra=dict(c0.extra))
    return dict(first=first, last=last, cams=np.array(cams, np.float32).tobytes(), cloud=P.tobytes(), col=C.tobytes(), grids=grids,
                light=list(mf.light_dir(p['light_az'], p['light_el'])), lens=lens, size=size)


# ---------------------------------------------------------------------------------------------------
#  GPU preview: the simulation's starting state and the light, handed to WebGL
# ---------------------------------------------------------------------------------------------------
def preview_init(pjson):
    """The dust's starting state exactly as motefield.DustSim makes it (same seed, same random numbers),
    so the GPU preview integrates the same motes the Python render does."""
    p = _params(pjson)
    sim = mf.DustSim(p, None)
    m0, m1 = sim.modes
    pack = lambda m: np.concatenate([m[0], m[1], m[2][:, None], m[3][:, None]], 1).astype(np.float32)   # K(3) A(3) ph om
    st = np.zeros((len(sim.local), 4), np.float32)
    st[:, 0] = sim.type
    st[:, 1] = sim.settle_f
    st[:, 2] = sim.tau
    return dict(n=len(sim.local), local=sim.local.astype(np.float32).tobytes(), h=sim.h.astype(np.float32).tobytes(),
                st=st.tobytes(), modes0=pack(m0).tobytes(), modes1=pack(m1).tobytes(), M0=len(m0[0]), M1=len(m1[0]),
                center=list(sim.center), size=list(sim.size), R=list(sim.R.reshape(-1)), wind=list(sim.wind), su=sim.su)


def preview_light(pjson):
    """For 'utility pass' shadows: the light depth map and the sky openings, as make_light() builds them."""
    p = _params(pjson)
    shot = _shot(p)
    L = mf.light_dir(p['light_az'], p['light_el'])
    fs = sorted(set([p['first'], (p['first'] + p['last']) // 2, p['last']]))
    pts = []
    for f in fs:
        dist, P = shot.surface(f)
        if P is None:
            continue
        Q = P.reshape(-1, 3)
        Q = Q[np.abs(Q).sum(1) > 0]
        if len(Q) > 900000:
            Q = Q[::int(np.ceil(len(Q) / 900000.0))]
        pts.append(Q.astype(np.float64))
    if not pts:
        return None
    allp = np.concatenate(pts)
    lm = mf.LightMap(allp, L)
    g = np.where(np.isfinite(lm.g), lm.g, -1e30).astype(np.float32)
    lo, hi = np.percentile(allp, 0.5, axis=0), np.percentile(allp, 99.5, axis=0)
    views = []
    for f in fs:
        dist, _ = shot.surface(f)
        c = shot.camera(f)
        views.append(dict(cam=list(c.pos) + list(c.right) + list(c.up) + list(c.back) + [c.hfov], w=dist.shape[1], h=dist.shape[0],
                          sky=(dist <= 0).astype(np.uint8).tobytes()))
    return dict(res=lm.res, g=g.tobytes(), a=list(lm.a), b=list(lm.b), L=list(lm.L), lo=list(lm.lo), cell=lm.cell, bias=lm.bias,
                blo=list(lo), bhi=list(hi), views=views)


def frame_images(pjson, frame, plate_pattern):
    """Distance image (for holdouts) and plate of one frame of a picked utility pass, for the GPU preview."""
    p = _params(pjson)
    shot = _shot(p)
    dist, _ = shot.surface(int(frame))
    out = dict(w=dist.shape[1], h=dist.shape[0], dist=dist.astype(np.float32).tobytes())
    pl = plate(pjson, plate_pattern, frame) if plate_pattern else None
    if pl:
        out.update(pw=pl['w'], ph=pl['h'], rgb=pl['rgb'])
    else:
        sp = shaded_plate(p, shot, int(frame))
        if sp is not None:
            out.update(pw=sp.shape[1], ph=sp.shape[0], rgb=sp.astype(np.float16).tobytes(), synthetic=True)
    return out


def shaded_plate(p, shot, frame):
    """No plate yet: shade the utility pass itself (surface colour, the key light with the tool's own shadows, a little fill),
    so there is a set to judge the dust against. The viewer then gives it the lens defocus a render would have."""
    e = shot._open(frame)
    try:
        found = mf.survey_layers(e)
        if 'P' not in found:
            return None
        pi, names = found['P']
        d = e.read(pi, names)
        P = np.stack([d[n] for n in names], -1).astype(np.float64)
        H, W = P.shape[:2]
        if 'N' in found:
            ni, nn = found['N']
            dn = e.read(ni, nn)
            N = np.stack([dn[n] for n in nn], -1).astype(np.float64)
        else:
            N = np.zeros_like(P); N[..., 1] = 1.0
        if 'C' in found:
            ci, cn = found['C']
            dc = e.read(ci, cn)
            C = np.clip(np.stack([dc[n] for n in cn], -1).astype(np.float64), 0, 1)
        else:
            C = np.full_like(P, 0.35)
    finally:
        e.close()
    Pf, Nf, Cf = P.reshape(-1, 3), N.reshape(-1, 3), C.reshape(-1, 3)
    hit = np.abs(Pf).sum(1) > 0
    L = mf.light_dir(p['light_az'], p['light_el'])
    sun = mf.kelvin_rgb(p['light_k']) * p['light_int']
    nl = np.clip(Nf @ L, 0, None)
    lit = np.zeros(len(Pf))
    cand = hit & (nl > 0)
    if cand.any():
        light = _light_for(p, shot)
        nrm = Nf[cand] / np.maximum(np.linalg.norm(Nf[cand], axis=1, keepdims=True), 1e-6)
        lit[cand] = mf.soft_visibility(light, Pf[cand] + nrm * (2.0 / max(shot.su * 100, 1e-6)), L, max(p.get('shadow_soft', 3.0), 1.0) * 0.01 / shot.su)
    fill = p['light_int'] * (0.035 + 0.02 * np.clip(Nf[:, 1], 0, 1)) * (0.4 + 0.6 * p['bounce'])
    rgb = Cf * (sun[None, :] * (nl * lit)[:, None] + fill[:, None] * np.array([0.95, 0.97, 1.0]))
    rgb[~hit] = 0.0
    return rgb.reshape(H, W, 3).astype(np.float32)


def plate(pjson, plate_pattern, frame):
    """The plate for the viewer: the stand-in's clay render, a picked plate sequence, or nothing."""
    p = _params(pjson)
    frame = int(frame)
    path = None
    if plate_pattern:
        if '/standin_' in plate_pattern and p['utility']:
            shot = _shot(p)
            if shot.meta is not None:
                _ensure_standin_frame(shot.folder, shot.meta, frame)
        path = mf.frame_path(plate_pattern, frame)
    if not path or not os.path.exists(path):
        return None
    e = mf.ExrFile(path)
    try:
        part, names = 0, None
        for pi, pp in enumerate(e.parts):
            chs = [n for n, t in pp['channels']]
            for cand in (['R', 'G', 'B'], ['rgba.red', 'rgba.green', 'rgba.blue'], ['rgba.R', 'rgba.G', 'rgba.B']):
                if all(c in chs for c in cand):
                    part, names = pi, cand
                    break
            if names:
                break
        if not names:
            return None
        d = e.read(part, names)
        img = np.stack([d[n] for n in names], -1)
    finally:
        e.close()
    H, W = img.shape[:2]
    return dict(w=W, h=H, rgb=np.nan_to_num(img).astype(np.float16).tobytes())


def fit(pjson):
    p = _params(pjson)
    c, s = mf.fit_box(p)
    return json.dumps(dict(box_center=[round(x, 2) for x in c], box_size=[round(x, 2) for x in s]))


def save_exr(pjson, frame):
    """The dust frame as the Nuke node writes it (RGBA + noholdout layer, half float)."""
    if _last['key'] != (pjson, int(frame)):
        render(pjson, frame)
    path = ROOT + '/dust.%04d.exr' % int(frame)
    mf.write_dust(path, _last['held'], _last['unheld'])
    with open(path, 'rb') as fh:
        data = fh.read()
    os.remove(path)
    return data


def set_bokeh_image(w, h, data):
    """A custom bokeh shape picked in the browser (already decoded to 0..1 floats), stored as an EXR the
    renderer reads exactly as the Nuke node reads its 'Custom bokeh shape' file."""
    path = ROOT + '/bokeh_shape.exr'
    if not w:
        if os.path.exists(path):
            os.remove(path)
        return ''
    img = np.frombuffer(bytes(data), np.float32).reshape(int(h), int(w))
    os.makedirs(ROOT, exist_ok=True)
    mf.write_exr(path, {'Y': img})
    return path


def preview_bounce(pjson):
    """The bounce lights that fill the motes (motefield.bounce_lights), for the GPU preview."""
    p = _params(pjson)
    shot = _shot(p)
    light = _light_for(p, shot)
    pos, nrm, flx, amb = mf.bounce_lights(p, shot, light)
    return dict(n=len(pos), pos=np.asarray(pos, np.float32).tobytes(), nrm=np.asarray(nrm, np.float32).tobytes(),
                flux=np.asarray(flx, np.float32).tobytes(), amb=[float(x) for x in amb])
