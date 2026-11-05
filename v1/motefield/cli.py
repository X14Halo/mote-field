"""python -m motefield <command> ...

    schema    write the generated settings files: viewer/js/schema.json and docs/SETTINGS.md
    show      print a shot's resolved settings and where each comes from
    keys      print a shot's cache keys (prep, sim, cache)
    diff      compare two settings files, resolved
    snapshot  freeze a shot and its look and show into one file for the farm
    migrate   convert a MoteField 0.8 settings file
    doctor    check this Python for what MoteField needs and can use

    check     the shot check: what's in a utility pass and what MoteField will do with it

    render    render frames into the node's cache (or another folder), e.g. to fill it ahead on a farm
"""

import argparse
import json
import os
import sys

from . import MODEL_VERSION, __version__, schema, settings
from .curves import is_curve

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fmt(v):
    if is_curve(v):
        return 'animated (%d keys)' % len(v['keys'])
    if isinstance(v, tuple):
        return ' '.join('%g' % x for x in v)
    if isinstance(v, float):
        return '%g' % v
    if isinstance(v, list):
        return '%d item%s' % (len(v), '' if len(v) == 1 else 's')
    return str(v)


def cmd_schema(a):
    targets = []
    if a.json or not a.md:
        targets.append((a.json_out or os.path.join(ROOT, 'viewer', 'js', 'schema.json'), schema.to_json() + '\n'))
    if a.md or not a.json:
        targets.append((a.md_out or os.path.join(ROOT, 'docs', 'SETTINGS.md'), schema.to_markdown()))
    for path, text in targets:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        print('wrote %s' % path)


def cmd_show(a):
    s = settings.load(a.settings)
    rows = []
    for p in schema.PARAMS:
        src = s.source(p.key)
        if a.all or src != 'default':
            v = s.value(p.key, a.frame) if (a.frame is not None and s.is_animated(p.key)) else s[p.key]
            rows.append((p.key, _fmt(v), src, p.affects))
    w = max([len(r[0]) for r in rows] + [3])
    for k, v, src, aff in rows:
        print('%-*s  %-28s  %-7s  %s' % (w, k, v, src, aff))
    ov = s.overrides()
    if ov:
        print('\nThe shot overrides:')
        for k, (kind, was, now) in ov.items():
            print('  %s: %s in the %s, %s in the shot' % (k, _fmt(was), kind, _fmt(now)))


def cmd_keys(a):
    s = settings.load(a.settings)
    print('prep   %s\nsim    %s\ncache  %s\nmodel  %d' % (s.prep_key(), s.sim_key(), s.cache_key(), MODEL_VERSION))


def cmd_diff(a):
    x, y = settings.load(a.a), settings.load(a.b)
    n = 0
    for p in schema.PARAMS:
        if x[p.key] != y[p.key]:
            n += 1
            print('%-18s %-24s -> %-24s (%s)' % (p.key, _fmt(x[p.key]), _fmt(y[p.key]), p.affects))
    if not n:
        print('No differences.')
    for kind in ('prep', 'sim', 'cache'):
        kx, ky = getattr(x, kind + '_key')(), getattr(y, kind + '_key')()
        print('%-5s key %s' % (kind, 'same' if kx == ky else 'differs'))
    return 1 if n else 0


def cmd_snapshot(a):
    s = settings.load(a.settings)
    out = a.out or os.path.splitext(a.settings)[0] + '.farm.json'
    settings.save_layer(s.snapshot(), out)
    print('wrote %s (cache key %s)' % (out, s.cache_key()))


def cmd_migrate(a):
    from . import migrate
    d = settings.read_json(a.old)
    shot, look, show, notes = migrate.from_v08(d, split=a.split, name=os.path.splitext(os.path.basename(a.old))[0])
    base = os.path.splitext(a.out or a.old.replace('.json', '') + '_v1.json')[0]
    if a.split:
        lp, sp = base + '.look.json', base + '.show.json'
        settings.save_layer(look, lp)
        settings.save_layer(show, sp)
        shot.look, shot.show = os.path.abspath(lp), os.path.abspath(sp)
        print('wrote %s\nwrote %s' % (lp, sp))
    settings.save_layer(shot, base + '.json')
    print('wrote %s' % (base + '.json'))
    for n in notes:
        print('  note: %s' % n)


def cmd_check(a):
    from . import check
    if a.target.endswith('.json'):
        s = settings.load(a.target)
        pattern, extra, units = s['utility'], a.extra or s['extra_passes'], a.units or s['units']
        if not pattern:
            print('motefield: %s has no utility pass set' % a.target, file=sys.stderr)
            return 1
    else:
        s, pattern, extra, units = None, a.target, a.extra or '', a.units or 'centimetres'
    rep = check.check(pattern, extra=extra, units=units, frame=a.frame, settings=s)
    if a.json:
        print(json.dumps(rep, indent=1, default=str))
    else:
        print(check.text(rep))
    return 1 if any(level == check.PROBLEM for level, _, _ in rep['items']) else 0


def frames_arg(text, first, last):
    """'1001-1048', '1001,1010-1012' or '' (the shot's frames) -> a list of frames."""
    import re
    if not text:
        return list(range(first, last + 1))
    out = []
    for part in str(text).replace(' ', '').split(','):
        if not part:
            continue
        m = re.match(r'^(-?\d+)(?:-(-?\d+))?$', part)
        if not m:
            raise settings.SettingError('frames: %r is not a frame or a range like 1001-1048' % part)
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.extend(range(a, b + 1))
    return out


def cmd_render(a):
    import time
    from . import cache, render
    s = settings.load(a.settings)
    frames = frames_arg(a.frames, s['first'], s['last'])
    r = render.Renderer(s)
    fc = cache.FrameCache(s, r.shot, root=a.cache)
    print('rendering %d frames into %s' % (len(frames), fc.pattern()))
    t0 = time.time()
    for f in frames:
        t = time.time()
        made = not fc.has(f) or a.force
        fc.render(f, r, force=a.force)
        print('  %d  %s' % (f, '%.2f s' % (time.time() - t) if made else 'already there'))
    print('done in %.1f s' % (time.time() - t0))


def cmd_doctor(a):
    ok = True
    print('MoteField %s (model %d)' % (__version__, MODEL_VERSION))
    print('Python     %s' % sys.version.split()[0])
    try:
        import numpy
        major, minor = (int(x) for x in numpy.__version__.split('.')[:2])
        good = (major, minor) >= (1, 23)
        ok &= good
        print('numpy      %s%s' % (numpy.__version__, '' if good else '  (needs 1.23 or later)'))
    except ImportError:
        ok = False
        print('numpy      missing (required)')
    for mod, why in (('OpenImageIO', 'tiled, PIZ and DWA utility passes'), ('OpenEXR', 'tiled, PIZ and DWA utility passes'),
                     ('nuke', 'the Nuke node'), ('PySide2', 'the live viewer panel in Nuke 15')):
        try:
            __import__(mod)
            print('%-10s found' % mod)
        except Exception:
            print('%-10s not found (optional: %s)' % (mod, why))
    try:
        from PySide2 import QtWebEngineWidgets  # noqa: F401
        print('QtWebEngine found: the live viewer can open in a Nuke panel')
    except Exception:
        print('QtWebEngine not found: the live viewer opens in your web browser instead')
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog='motefield', description='MoteField %s' % __version__)
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('schema', help='write viewer/js/schema.json and docs/SETTINGS.md')
    p.add_argument('--json', action='store_true')
    p.add_argument('--md', action='store_true')
    p.add_argument('--json-out')
    p.add_argument('--md-out')
    p = sub.add_parser('show', help="a shot's resolved settings")
    p.add_argument('settings')
    p.add_argument('--all', action='store_true', help='include settings left at their defaults')
    p.add_argument('--frame', type=float)
    p = sub.add_parser('keys', help="a shot's cache keys")
    p.add_argument('settings')
    p = sub.add_parser('diff', help='compare two settings files')
    p.add_argument('a')
    p.add_argument('b')
    p = sub.add_parser('snapshot', help='freeze a shot for the farm')
    p.add_argument('settings')
    p.add_argument('--out')
    p = sub.add_parser('migrate', help='convert a MoteField 0.8 settings file')
    p.add_argument('old')
    p.add_argument('--out')
    p.add_argument('--split', action='store_true', help='also write a look file and a show file')
    p = sub.add_parser('check', help='the shot check on a utility pass (a frame pattern, or a shot settings file)')
    p.add_argument('target')
    p.add_argument('--extra', help='other passes for the same frames, ; separated')
    p.add_argument('--units', choices=list(schema.UNITS))
    p.add_argument('--frame', type=int)
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('render', help="render frames into the node's cache")
    p.add_argument('settings')
    p.add_argument('--frames', help='e.g. 1001-1048 or 1001,1010-1012 (default: the shot\'s frames)')
    p.add_argument('--cache', help='folder to render into (default: the settings\' cache folder)')
    p.add_argument('--force', action='store_true', help='render frames that are already there again')
    sub.add_parser('doctor', help='check this Python')
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return globals()['cmd_' + a.cmd](a) or 0
    except settings.SettingError as e:
        print('motefield: %s' % e, file=sys.stderr)
        return 1
