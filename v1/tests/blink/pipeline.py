"""Runs the node's kernels in the order the node does, from a shot and a Settings, through the C++ stand-in for Blink
(tests only). Images here are row 0 at the bottom (Nuke's); render() returns them flipped to row 0 at the top, like
the Python reference renderer.

    out = render(settings, shot, frame)      {'rgb': (H, W, 3), 'alpha': (H, W), 'state': ..., ...}
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..'))
import run                                    # noqa: E402
from node import blinklib as bl               # noqa: E402
from motefield import schema                  # noqa: E402

FLAGS = ('interior', 'holdout', 'dof', 'mb')


def engine_params(e):
    """Kernel params from Settings.engine(frame) (the master dials already applied)."""
    p = {}
    for name, (t, default) in bl.PARAMS.items():
        if name in e:
            v = e[name]
            if name in FLAGS:
                v = 1 if v else 0
            elif name == 'shadows':
                v = 1 if v == 'none' else 0
            elif name in ('shutter_mode', 'shutter_shape'):
                v = schema.BY_KEY[name].options.index(v)
            elif name == 'alpha':
                continue
            p[name] = v
    p['su'] = e['scene_unit']
    p['alpha_mode'] = 1 if e.get('alpha') == 'matte' else 0
    p['matte_gain'] = e.get('matte_gain', 1.0)
    return p


def camera_params(cam, prefix):
    th = math.tan(cam.hfov / 2)
    return {prefix + '_pos': cam.pos, prefix + '_right': cam.right, prefix + '_up': cam.up,
            prefix + '_back': cam.back, prefix + '_th': th}


def header_params(cam):
    lens = cam.lens or {}
    return dict(hdr_focal=cam.focal or 0.0, hdr_film=cam.film or 0.0, hdr_fstop=cam.fstop or 0.0,
                hdr_focus=cam.focus or 0.0, hdr_blades=int(lens.get('blades', 0)), hdr_cateye=lens.get('cateye', 0.0),
                hdr_anamorphic=lens.get('anamorphic', 0.0) or 0.0, hdr_field_curv=lens.get('field_curv', 0.0),
                hdr_fstop_full=lens.get('fstop_full', 0.0) or 0.0)


def shutter_times(e):
    s = e['shutter'] / 360.0 if e['mb'] else 0.0
    mode = e['shutter_mode']
    return (-s, 0.0) if mode == 'backward' else (0.0, s) if mode == 'forward' else (-s / 2, s / 2)


def up(img):
    """Row 0 at the top -> row 0 at the bottom (and back)."""
    return np.ascontiguousarray(img[::-1])


def pass_images(shot, f):
    """The pos (P, alpha) and depth (depth, unfiltered depth) images the node's shuffles make, and normals."""
    W, H = shot.resolution(f)
    P = shot.read(f, 'P')
    A = shot.read(f, 'A')
    if A is None:
        hit = shot.read(f, 'Zraw')
        A = (hit > 0).astype(np.float32) if hit is not None else np.ones((H, W), np.float32)
    Z = shot.read(f, 'Z')
    Zr = shot.read(f, 'Zraw')
    N = shot.read(f, 'N')
    pos = np.zeros((H, W, 4), np.float32)
    if P is not None:
        pos[..., :3] = P
    pos[..., 3] = A
    depth = np.zeros((H, W, 4), np.float32)
    if Z is not None:
        depth[..., 0] = np.nan_to_num(Z)
    if Zr is not None:
        depth[..., 1] = np.nan_to_num(Zr)
    nrm = np.zeros((H, W, 4), np.float32)
    if N is not None:
        nrm[..., :3] = N
    C = shot.read(f, 'C')
    alb = np.zeros((H, W, 4), np.float32)
    if C is not None:
        alb[..., :3] = C
    return up(pos), up(depth), up(nrm), (up(alb) if C is not None else None)


def render(settings, shot, frame, extra=None, keep=False):
    e = settings.engine(frame)
    p = engine_params(e)
    cam = shot.camera(frame)
    t_open, t_close = shutter_times(e)
    p.update(camera_params(cam, 'cam'))
    p.update(camera_params(shot.camera(frame + t_open) if t_open else cam, 'o'))
    p.update(camera_params(shot.camera(frame + t_close) if t_close else cam, 'c'))
    p.update(header_params(cam))
    p['frame'] = float(frame)
    p.update(extra or {})
    pos, depth, nrm, alb = pass_images(shot, frame)
    p['use_albedo'] = 1 if alb is not None else 0
    if alb is None:
        alb = np.zeros_like(nrm)
    H, W = pos.shape[:2]

    def k(name, inputs, space=None):
        src = bl.source(name)
        names = [n for t, n in bl.params(src)]
        pp = {n: p[n] for n in names if n in p}
        ins = dict(inputs)
        if space is not None:
            ins = dict([('space', np.zeros((space[1], space[0], 4), np.float32))] + list(ins.items()))
        return run.run(name, pp, ins)

    out = {}
    modes = k('modes', {}, (2, 64))
    facts = k('facts', {'pos': pos, 'depth': depth}, (4, 1))
    surf = k('surface', {'pos': pos, 'depth': depth, 'facts': facts})
    setbox = k('bounds', {'surf': surf}, (2, 1))
    cells = k('bounce', {'surf': surf, 'setbox': setbox, 'nrm': nrm, 'alb': alb}, (3, bl.NCELLS))
    place = k('place', {'modes': modes, 'surf': surf, 'setbox': setbox}, (2 * bl.STATE_COLS, bl.STATE_H))
    motes = k('motes', {'place': place, 'surf': surf, 'cells': cells}, (bl.STATE_W, bl.STATE_H))
    bins = k('bins', {'motes': motes, 'surf': surf}, (bl.NTILES, bl.ROWS1))
    rows = k('rows', {'bins': bins}, (bl.NTILES, bl.ROWS2))
    img = k('draw', {'surf': surf, 'motes': motes, 'bins': bins, 'rows': rows})
    out['rgb'] = up(img[..., :3])
    out['alpha'] = up(img[..., 3])
    out['facts'] = facts
    if keep:
        out.update(modes=modes, surf=surf, setbox=setbox, cells=cells, place=place, motes=motes, bins=bins, rows=rows)
    return out
