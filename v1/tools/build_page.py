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


def main():
    from motefield import schema
    n = run_tests()
    with open(os.path.join(ROOT, 'web', 'index.src.html'), encoding='utf-8') as fh:
        page = fh.read()
    page = page.replace('<!--SCHEMA-->', _embed(json.loads(schema.to_json())))
    page = page.replace('<!--CHECK-->', _embed(stand_in_check()))
    page = page.replace('<!--UPDATED-->', datetime.date.today().strftime('%-d %B %Y'))
    page = page.replace('<b id="nTests">77</b>', '<b id="nTests">%d</b>' % n)
    out = os.path.join(ROOT, 'index.html')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print('wrote %s (%d tests passing)' % (out, n))


if __name__ == '__main__':
    main()
