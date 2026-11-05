"""Builds v1/index.html (the site page, also published as the progress artifact) from web/index.src.html.

    python tools/build_page.py

It runs the test suite first and refuses to build if anything fails, so the page never claims tests that don't pass.
Then it embeds the settings table and a shot check of the synthetic stand-in pass, so the page is one file.
"""

import datetime
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))


def _embed(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).replace('</', '<\\/')


def run_tests():
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, 'tests'))
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    if res.failures or res.errors:
        for t, tb in res.failures + res.errors:
            print(t, tb, file=sys.stderr)
        raise SystemExit('tests fail: not building the page')
    return res.testsRun - len(res.skipped)


def stand_in_check():
    import synth
    from motefield import check, exr
    d = tempfile.mkdtemp()
    ch, attrs = synth.render(192, 108, 4)
    exr.write(os.path.join(d, 'standin_utility.0101.exr'), ch, attrs=attrs, half=False)
    rep = check.check(os.path.join(d, 'standin_utility.####.exr'))
    rep['pattern'] = 'standin_utility.####.exr'
    return dict(pattern=rep['pattern'], frame=rep.get('frame'), items=rep['items'])


def first_frames():
    """Two stand-in frames, dust plus'd over a clay plate, as JPEG data URIs, and how long each took."""
    import base64
    import time
    import numpy as np
    from PIL import Image
    from motefield import render, standin
    from motefield.settings import Layer, Settings
    out = {}
    for view in ('wide', 'close-up'):
        shot = standin.StandinShot(view, 1920, 804)
        r = render.Renderer(Settings(shot=Layer('shot', standin.settings_for(view))), shot)
        shot.surface(1024)                               # trace the stand-in first: it is not part of a render
        t = time.time()
        img = r.frame(1024)['rgb']
        took = time.time() - t
        comp = shot.plate(1024) * 0.35 + img
        x = np.maximum(comp * 1.6, 0)
        x = x / (1 + x)
        rgb = (np.clip(x, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
        im = Image.fromarray(rgb).resize((1280, 536), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=90)
        out[view] = ('data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode(), took)
    return out


def main():
    from motefield import schema
    n = run_tests()
    with open(os.path.join(ROOT, 'web', 'index.src.html'), encoding='utf-8') as fh:
        page = fh.read()
    page = page.replace('<!--SCHEMA-->', _embed(json.loads(schema.to_json())))
    page = page.replace('<!--CHECK-->', _embed(stand_in_check()))
    page = page.replace('<!--UPDATED-->', datetime.date.today().strftime('%-d %B %Y'))
    shots = first_frames()
    page = page.replace('<!--IMG_WIDE-->', shots['wide'][0]).replace('<!--IMG_CLOSE-->', shots['close-up'][0])
    page = page.replace('<!--T_WIDE-->', '%.1f s to render' % shots['wide'][1])
    page = page.replace('<!--T_CLOSE-->', '%.1f s to render' % shots['close-up'][1])
    page = page.replace('<b id="nTests">77</b>', '<b id="nTests">%d</b>' % n)
    out = os.path.join(ROOT, 'index.html')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print('wrote %s (%d tests passing)' % (out, n))


if __name__ == '__main__':
    main()
