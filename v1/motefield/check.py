"""The shot check: what MoteField found in a shot's utility pass, what it will do with it, and anything to fix.

    report = check(shot_settings_or_pattern, frame=None)
    print(text(report))

Each item is (level, topic, message): ok, note (worth knowing), warn (works, but results will suffer) or problem (a
feature can't work until it's fixed). The viewer shows the same report as a card; the CLI prints it
(python -m motefield check <utility pattern or shot settings>).
"""

import math
import os

import numpy as np

from . import exr, schema
from .shot import ROLE_NAMES, Shot

OK, NOTE, WARN, PROBLEM = 'ok', 'note', 'warn', 'problem'

# what's lost without each layer
NEEDED = {
    'P': (WARN, 'Positions will be rebuilt from depth and the camera.'),
    'N': (NOTE, 'Bounce light assumes surfaces face the camera.'),
    'Z': (NOTE, ''),
    'Zraw': (NOTE, 'Holdouts use the filtered depth, which blends at edges; coverage softens it.'),
    'A': (WARN, 'Edges can\'t be told from surfaces, so edge pixels may hold out dust a little wrongly.'),
    'MV': (NOTE, 'Moving things can\'t push the dust (push shapes still can).'),
    'C': (NOTE, 'Bounce light is grey instead of taking the colour of what it bounces off.'),
    'ID': (NOTE, 'Objects can\'t be picked to never hold out (ID mattes still can, if there are any).'),
}


def _m(x, su):
    return x * su


def _fmt_m(m):
    return '%.2f m' % m if m < 10 else '%.0f m' % m


def check(pattern, extra='', units='centimetres', frame=None, settings=None):
    """A report on a utility pass. pattern: frame pattern of the utility pass; extra: other passes (';' separated)."""
    items = []
    add = lambda level, topic, msg: items.append((level, topic, msg))
    rep = dict(pattern=pattern, items=items)
    shot = Shot(pattern, extra=extra)
    frames = shot.frames
    if not frames:
        if os.path.exists(pattern):
            frames = [None]
        else:
            add(PROBLEM, 'Files', 'No frames found for %s. Use #### where the frame number goes.' % pattern)
            return rep
    if frames[0] is not None:
        gaps = sorted(set(range(frames[0], frames[-1] + 1)) - set(frames))
        add(OK if not gaps else WARN, 'Files', '%d frame%s on disk, %d-%d%s.' % (
            len(frames), '' if len(frames) == 1 else 's', frames[0], frames[-1],
            '' if not gaps else '; missing %s' % _ranges(gaps)))
        if settings is not None and (settings['first'] < frames[0] or settings['last'] > frames[-1]):
            add(WARN, 'Files', 'The shot is set to %d-%d but the pass covers %d-%d.' % (
                settings['first'], settings['last'], frames[0], frames[-1]))
    f = frame if frame is not None else (frames[len(frames) // 2] if frames[0] is not None else None)
    rep['frame'] = f
    if f is None:                                  # a single file without a frame number
        shot.paths = lambda _f: [pattern]
        f = 0
    for p in shot.paths(f)[1:]:
        if not os.path.exists(p):
            add(WARN, 'Files', 'Extra pass %s is missing on frame %d.' % (os.path.basename(p), f))
    L = shot.layers(f)
    rep['layers'] = {r: L[r].describe() for r in ROLE_NAMES if L.get(r)}
    found = [ROLE_NAMES[r] for r in ROLE_NAMES if L.get(r)]
    add(OK, 'Layers', 'Found ' + ', '.join(found) + '.')
    for r, (level, why) in NEEDED.items():
        if not L.get(r) and why:
            if r == 'Z' and (L.get('Zraw') or L.get('P')):
                continue
            add(level, 'Layers', 'No %s. %s' % (ROLE_NAMES[r], why))
    if not (L.get('P') or L.get('Z') or L.get('Zraw')):
        add(PROBLEM, 'Layers', 'No world position or depth: MoteField needs one of them.')
        return rep
    for r in ('P', 'Z', 'Zraw'):
        if L.get(r) and L[r].lossy:
            add(WARN, 'Compression', '%s uses lossy %s compression, which moves positions at edges (holdouts suffer). '
                'Ask for ZIP or PIZ.' % (ROLE_NAMES[r].capitalize(), exr.COMPRESSION[L[r].compression]))
    for r in ('P', 'Z', 'Zraw'):
        if L.get(r) and 'half' in L[r].types and r == 'P':
            add(WARN, 'Precision', 'World position is stored as half float: far from the world origin it is only '
                'accurate to several centimetres.')
    # the camera
    try:
        cam = shot.camera(f)
    except ValueError as e:
        add(PROBLEM, 'Camera', str(e))
        return rep
    rep['camera'] = cam.summary()
    W, H = shot.resolution(f)
    rep['resolution'] = [W, H]
    lens = _lens_text(cam.lens)
    add(OK, 'Camera', 'Found: %s mm lens, %.1f° wide, f/%s, focus %s; %d x %d.' % (
        '%.1f' % cam.focal if cam.focal else '?', math.degrees(cam.hfov), '%g' % cam.fstop if cam.fstop else '?',
        '%g' % cam.focus if cam.focus else '?', W, H))
    if lens:
        add(OK, 'Camera', 'Lens details from the header (the dust\'s bokeh uses them): %s.' % lens)
    sh = cam.shutter
    if sh:
        add(OK, 'Camera', 'Shutter from the header: %s.' % ', '.join(
            str(x) for x in ('%g°' % sh['angle'] if 'angle' in sh else None, sh.get('mode'), sh.get('shape')) if x))
        if settings is not None:
            diffs = [(k, sh[a], settings[k]) for a, k in (('angle', 'shutter'), ('mode', 'shutter_mode'),
                                                          ('shape', 'shutter_shape')) if a in sh and sh[a] != settings[k]]
            for k, a, b in diffs:
                add(WARN, 'Camera', 'The header says %s %s but %s is set to %s.' % (
                    schema.BY_KEY[k].label.lower(), a, schema.BY_KEY[k].label.lower(), b))
    # what each pixel sees
    s = shot.surface(f)
    info = s.info
    rep['surface'] = info
    from .shot import _reproj_error
    full = s.clean
    e = _reproj_error(cam, s.P, W, H, full)
    if e is not None:
        add(OK if e < 0.5 else WARN, 'Camera', 'Positions reproject through the header camera to %.2f px (median)%s.'
            % (e, '' if e < 0.5 else ': the camera and the pass disagree; check the header camera'))
    cv = info.get('coverage')
    add(OK if cv == 'alpha' else WARN, 'Edges', 'Coverage: %s; %.1f%% of pixels are empty, %d are edge pixels.' % (
        'from the alpha' if cv == 'alpha' else cv, 100 * info['empty_fraction'], info['edge_pixels']))
    er = info.get('edge_reprojection_px')
    if info.get('position_premultiplied'):
        add(OK, 'Edges', 'World position is premultiplied by coverage (edge pixels reproject %.0f px off); divided by '
            'coverage they reproject to %.2f px, so that is what MoteField uses.' % (er['as_stored'],
                                                                                  er['divided_by_coverage']))
    elif er:
        add(OK, 'Edges', 'World position is not premultiplied (edge pixels reproject to %.2f px).' % er['as_stored'])
    for name, label in (('depth', 'Depth'), ('unfiltered_depth', 'Unfiltered depth')):
        if name + '_kind' in info:
            pm = info.get(name + '_premultiplied')
            add(OK, 'Edges', '%s is %s%s.' % (label, info[name + '_kind'],
                                               ', premultiplied (divided by coverage)' if pm else ''))
    add(OK, 'Holdouts', 'Holdouts use the %s, weighted by coverage.' % info['holdouts_from'])
    if 'mixed_by_unfiltered_depth' in info:
        add(NOTE, 'Edges', '%.0f%% of fully covered pixels mix two surfaces (hair, leaves, motion blur...): they are '
            'left out of the light map, Fit box and bounce light.' % (100 * info['mixed_by_unfiltered_depth']))
    # how big the scene is
    su = schema.UNITS.get(units, 0.01)
    pts = s.points(step=2)
    if len(pts) > 100:
        lo, hi = np.percentile(pts, 2, axis=0), np.percentile(pts, 98, axis=0)
        ext = (hi - lo) * su
        d = np.median(np.linalg.norm(pts - cam.pos, axis=1)) * su
        rep['scene'] = dict(extent_m=[float(x) for x in ext], median_distance_m=float(d), units=units)
        msg = 'In %s, what the camera sees spans about %s x %s x %s, and is mostly %s from the camera' % (
            units, _fmt_m(ext[0]), _fmt_m(ext[1]), _fmt_m(ext[2]), _fmt_m(d))
        if cam.focus:
            msg += '; focus is at %s' % _fmt_m(cam.focus * su)
        plausible = 0.2 <= d <= 300 and 0.3 <= max(ext) <= 3000
        if plausible:
            add(OK, 'Units', msg + '.')
        else:
            alt = [u for u, k in schema.UNITS.items() if u != units and
                   0.2 <= d / su * k <= 300 and 0.3 <= max(ext) / su * k <= 3000]
            add(WARN, 'Units', msg + ', which seems unlikely. Check Scene units%s.' % (
                ' (%s would make sense)' % ' or '.join(alt) if alt else ''))
    # motion vectors and objects
    if L.get('MV'):
        mv = shot.read(f, 'MV')
        mag = np.hypot(mv[..., 0], mv[..., 1])[s.cov > 0.5]
        if len(mag):
            add(OK, 'Motion', 'Backward motion vectors: %.1f px typical, %.0f px at most (99th percentile). Their '
                'direction is checked against the camera on the first Find moving things.' % (
                    np.median(mag), np.percentile(mag, 99)))
    if L.get('ID'):
        ids = shot.read(f, 'ID')
        u = np.unique(ids[s.clean])
        add(OK, 'Objects', '%d objects in the object ID layer: they can be picked in the viewer to never hold out.'
            % len(u))
    if L.get('mattes'):
        add(OK, 'Objects', '%d ID mattes in the passes: any of them can be set to never hold out.' % len(L['mattes']))
    if L.get('crypto'):
        add(OK, 'Objects', 'Cryptomatte layer %s: objects can be picked by name.' % L['crypto']['layer'])
    if L.get('C'):
        src = 'an extra pass' if L['C'].file_index > 0 else 'the utility pass'
        add(OK, 'Lighting', 'Surface colour from %s tints the bounce light.' % src)
    return rep


def _lens_text(lens):
    out = []
    if lens.get('blades', 0) >= 3:
        out.append('%d blades' % lens['blades'])
    if lens.get('cateye'):
        out.append('cat-eye %g' % round(lens['cateye'], 4))
    if lens.get('field_curv'):
        out.append('field curvature %g' % round(lens['field_curv'], 4))
    if lens.get('fstop_full'):
        out.append('full aperture f/%g' % round(lens['fstop_full'], 3))
    if lens.get('anamorphic') and abs(lens['anamorphic'] - 1) > 1e-6:
        out.append('anamorphic aspect %g' % round(lens['anamorphic'], 4))
    return ', '.join(out)


def _ranges(fs):
    out, start, prev = [], fs[0], fs[0]
    for x in fs[1:] + [None]:
        if x is None or x != prev + 1:
            out.append('%d' % start if start == prev else '%d-%d' % (start, prev))
            if x is not None:
                start = x
        prev = x if x is not None else prev
    return ', '.join(out)


MARK = {OK: '  ok  ', NOTE: ' note ', WARN: ' WARN ', PROBLEM: ' FIX  '}


def text(rep):
    lines = ['Shot check: %s%s' % (rep['pattern'], '  (frame %s)' % rep['frame'] if rep.get('frame') else '')]
    for level, topic, msg in rep['items']:
        lines.append('[%s] %-11s %s' % (MARK[level], topic, msg))
    return '\n'.join(lines)
