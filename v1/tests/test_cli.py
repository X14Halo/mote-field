import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from motefield import cli, schema, settings


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class GeneratedFilesAreCurrent(unittest.TestCase):
    """viewer/js/schema.json and docs/SETTINGS.md are generated from schema.py: regenerate them with
    python -m motefield schema whenever the schema changes."""

    def test_viewer_schema(self):
        with open(os.path.join(ROOT, 'viewer', 'js', 'schema.json'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), schema.to_json() + '\n', 'run: python -m motefield schema')

    def test_docs(self):
        with open(os.path.join(ROOT, 'docs', 'SETTINGS.md'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), schema.to_markdown(), 'run: python -m motefield schema')


class Commands(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.path.join(self.dir, 'sh020.json')
        shutil.copy(os.path.join(HERE, 'fixtures', 'v083_campfire.json'), self.old)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_migrate_show_keys_snapshot_diff(self):
        code, out = run('migrate', self.old, '--split')
        self.assertEqual(code, 0, out)
        shot = os.path.join(self.dir, 'sh020_v1.json')
        self.assertTrue(os.path.exists(shot))
        self.assertIn('bake again', out)
        code, out = run('show', shot)
        self.assertEqual(code, 0, out)
        self.assertIn('density', out)
        self.assertIn('look', out)
        code, out = run('keys', shot)
        self.assertEqual(code, 0, out)
        key = settings.load(shot).cache_key()
        self.assertIn(key, out)
        code, out = run('snapshot', shot)
        self.assertEqual(code, 0, out)
        farm = os.path.join(self.dir, 'sh020_v1.farm.json')
        self.assertEqual(settings.load(farm).cache_key(), key)
        code, out = run('diff', shot, farm)
        self.assertEqual(code, 0, out)
        self.assertIn('No differences', out)

    def test_diff_reports_what_a_change_affects(self):
        a = os.path.join(self.dir, 'a.json')
        b = os.path.join(self.dir, 'b.json')
        settings.save_layer(settings.Layer('shot', {'brightness': 1.0}), a)
        settings.save_layer(settings.Layer('shot', {'brightness': 2.0}), b)
        code, out = run('diff', a, b)
        self.assertEqual(code, 1)
        self.assertIn('brightness', out)
        self.assertIn('sim   key same', out)
        self.assertIn('cache key differs', out)

    def test_errors_are_one_line(self):
        bad = os.path.join(self.dir, 'bad.json')
        with open(bad, 'w') as fh:
            json.dump(dict(motefield='settings', format=1, layer='shot', values={'density': 'lots'}), fh)
        code, out = run('show', bad)
        self.assertEqual(code, 1)
        self.assertIn('density: expected a number', out)

    def test_doctor(self):
        code, out = run('doctor')
        self.assertEqual(code, 0, out)
        self.assertIn('numpy', out)


if __name__ == '__main__':
    unittest.main()
