"""Builds v1/index.html (the site page, also published as the progress artifact) from web/index.src.html.

It needs g++: the first frames are rendered by the node's kernels through the C++ stand-in for Blink.

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


def _jpeg(rgb8, size):
    import base64
    from PIL import Image
    im = Image.fromarray(rgb8).resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _tone(x):
    import numpy as np
    x = np.maximum(x * 1.6, 0)
    x = x / (1 + x)
    return (np.clip(x, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def first_frames():
    """Two stand-in frames rendered by the node's kernels (through the C++ stand-in for Blink), dust plus'd over a
    clay plate; a reference-vs-kernels comparison; and the light ratio between the two renderers."""
    import numpy as np
    sys.path.insert(0, os.path.join(ROOT, 'tests', 'blink'))
    import pipeline
    from motefield import render, standin
    from motefield.settings import Layer, Settings

    def settings(view, **kw):
        v = standin.settings_for(view)
        v.update(kw)
        return Settings(shot=Layer('shot', v))
    out = {}
    for view in ('wide', 'close-up'):
        shot = standin.StandinShot(view, 1920, 804)
        img = pipeline.render(settings(view), shot, 1024)['rgb']
        out[view] = _jpeg(_tone(shot.plate(1024) * 0.35 + img), (1280, 536))
    # the comparison: the close-up, both renderers, shadows from the pass in both
    shot = standin.StandinShot('close-up', 960, 402)
    s = settings('close-up', shadows='utility pass')
    ref = render.Renderer(s, shot).frame(1024)['rgb']
    got = pipeline.render(s, shot, 1024)['rgb']
    plate = shot.plate(1024) * 0.35
    out['cmp'] = _jpeg(_tone(np.concatenate([plate + ref, plate + got], 1)), (1280, 268))
    ratios = []
    for view in ('wide', 'close-up'):
        shot = standin.StandinShot(view, 640, 268)
        s = settings(view, shadows='none')
        ratios.append(pipeline.render(s, shot, 1024)['rgb'].sum() / render.Renderer(s, shot).frame(1024)['rgb'].sum())
    out['ratio'] = sum(ratios) / len(ratios)
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
    page = page.replace('<!--IMG_WIDE-->', shots['wide']).replace('<!--IMG_CLOSE-->', shots['close-up'])
    page = page.replace('<!--IMG_CMP-->', shots['cmp'])
    page = page.replace('<!--T_WIDE-->', '').replace('<!--T_CLOSE-->', '')
    page = page.replace('<!--LIGHT_RATIO-->', '%.1f%%' % (shots['ratio'] * 100))
    page = page.replace('<b id="nTests">77</b>', '<b id="nTests">%d</b>' % n)
    out = os.path.join(ROOT, 'index.html')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print('wrote %s (%d tests passing)' % (out, n))


if __name__ == '__main__':
    main()
