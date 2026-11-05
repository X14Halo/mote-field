"""The Nuke node, against tests/fake_nuke.py (a stand-in for Nuke's Python module). Real Nuke is the final word:
see docs/NUKE.md for what to try there."""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_nuke  # noqa: E402

nuke = fake_nuke.install(gui=False)

from motefield import exr, nodelink, nuke_node, schema, settings  # noqa: E402
from motefield.curves import is_curve  # noqa: E402


class NodeTest(unittest.TestCase):
    def setUp(self):
        fake_nuke.install(gui=False)
        nuke_node._REGISTERED[:] = []
        nuke_node._BATCH_RENDERERS.clear()
        self.dir = tempfile.mkdtemp()
        self._env = os.environ.get('MOTEFIELD_CACHE')
        os.environ['MOTEFIELD_CACHE'] = os.path.join(self.dir, 'cache')
        self.g = nuke_node.create()

    def tearDown(self):
        if self._env is None:
            os.environ.pop('MOTEFIELD_CACHE', None)
        else:
            os.environ['MOTEFIELD_CACHE'] = self._env
        shutil.rmtree(self.dir)

    def read_file(self):
        return self.g.node('mf_frames')['file'].value()


class Create(NodeTest):
    def test_every_setting_is_a_knob(self):
        for k in nodelink.NODE_KEYS:
            self.assertIsNotNone(self.g.knob(k), k)
        self.assertIsNone(self.g.knob('dof_view'))                   # viewer-only settings stay in the viewer
        self.assertIn(fake_nuke.NO_ANIMATION, self.g['density'].flags)
        self.assertNotIn(fake_nuke.NO_ANIMATION, self.g['master_brightness'].flags)
        self.assertEqual(self.g['output_mode'].value(), 'dust only')
        self.assertEqual(self.g['box_size'].value(), list(schema.DEFAULTS['box_size']))

    def test_inside(self):
        rd = self.g.node('mf_frames')
        self.assertTrue(rd['raw'].value())
        self.assertEqual(rd['on_error'].value(), 'black')
        self.assertEqual(self.g.node('mf_output')['which'].expression, 'parent.output_mode')
        self.assertIn('Connect the utility pass', self.g['mf_status'].value())
        self.assertEqual(len(nuke._callbacks['beforeFrameRender']), 1)

    def test_a_new_node_is_all_defaults(self):
        s = nuke_node.node_settings(self.g)
        self.assertEqual(s.changed(), {})
        self.assertEqual(s.cache_key(), settings.defaults().cache_key())


class Edits(NodeTest):
    def test_the_read_follows_the_settings(self):
        fake_nuke.change(self.g, 'standin', 'wide')
        a = self.read_file()
        self.assertTrue(a.startswith(os.environ['MOTEFIELD_CACHE'].replace('\\', '/')))
        self.assertTrue(a.endswith('/dust.####.exr'))
        fake_nuke.change(self.g, 'density', 300.0)
        b = self.read_file()
        self.assertNotEqual(a, b)
        fake_nuke.change(self.g, 'output_mode', 'merged over plate')      # the node's output: same frames
        self.assertEqual(self.read_file(), b)
        fake_nuke.change(self.g, 'alpha', 'matte')
        self.assertEqual(self.g.node('mf_over_plate')['operation'].value(), 'over')
        self.assertEqual(json.loads(self.g['mf_edited'].value()), ['standin', 'density', 'output_mode', 'alpha'])

    def test_animated_look_knobs(self):
        k = self.g['master_brightness']
        k.setValueAt(0.0, 1001)
        k.setValueAt(1.0, 1011)
        s = nuke_node.node_settings(self.g)
        self.assertTrue(is_curve(s['master_brightness']))
        self.assertAlmostEqual(s.engine(1006)['brightness'], 0.5)
        self.assertAlmostEqual(s.engine(1020)['brightness'], 1.0)
        with self.assertRaises(RuntimeError):
            self.g['density'].setAnimated()

    def test_utility_from_the_input(self):
        rd = nuke.nodes.Read(name='util')
        rd['file'].setValue('/shows/x/util.%04d.exr')
        self.g.setInput(1, rd)
        self.assertEqual(nuke_node.node_settings(self.g)['utility'], '/shows/x/util.%04d.exr')
        fake_nuke.change(self.g, 'utility', '/elsewhere/u.####.exr')     # the knob wins when it's set
        self.assertEqual(nuke_node.node_settings(self.g)['utility'], '/elsewhere/u.####.exr')


class LookFiles(NodeTest):
    def test_knobs_show_the_look_until_changed_here(self):
        path = os.path.join(self.dir, 'attic.json')
        settings.save_layer(settings.Layer('look', {'density': 300, 'drift': 4.0}), path)
        fake_nuke.change(self.g, 'mf_look_file', path)
        self.assertEqual(self.g['density'].value(), 300.0)
        self.assertEqual(json.loads(self.g['mf_from_files'].value()), {'density': 'look', 'drift': 'look'})
        s = nuke_node.node_settings(self.g)
        self.assertEqual(s.source('density'), 'look')
        self.assertNotIn('density', s.layers['shot'].values)
        fake_nuke.change(self.g, 'drift', 9.0)                           # the artist's own value from now on
        settings.save_layer(settings.Layer('look', {'density': 200, 'drift': 1.0}), path)
        nuke_node.button(self.g, 'reload')
        self.assertEqual(self.g['density'].value(), 200.0)
        self.assertEqual(self.g['drift'].value(), 9.0)
        s = nuke_node.node_settings(self.g)
        self.assertEqual(s['drift'], 9.0)
        self.assertEqual(s.overrides(), {'drift': ('look', 1.0, 9.0)})
        settings.save_layer(settings.Layer('look', {}), path)            # the look no longer sets density
        nuke_node.button(self.g, 'reload')
        self.assertEqual(self.g['density'].value(), schema.DEFAULTS['density'])

    def test_overriding_back_to_the_default_sticks(self):
        path = os.path.join(self.dir, 'look.json')
        settings.save_layer(settings.Layer('look', {'density': 300}), path)
        fake_nuke.change(self.g, 'mf_look_file', path)
        fake_nuke.change(self.g, 'density', schema.DEFAULTS['density'])
        self.assertEqual(nuke_node.node_settings(self.g)['density'], schema.DEFAULTS['density'])


class Renders(NodeTest):
    def test_a_render_makes_the_frames_it_needs(self):
        fake_nuke.change(self.g, 'standin', 'wide')
        fake_nuke.change(self.g, 'proxy', 0.25)
        fake_nuke.change(self.g, 'out_keyfill', True)
        nuke.render_frame(1010)
        path = self.read_file().replace('####', '1010')
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.g.node('mf_frames')['reload'].runs, 1)
        with exr.ExrFile(path) as f:
            names = set(f.parts[0].channel_names())
            hdr = f.parts[0].header
        self.assertTrue({'R', 'G', 'B', 'A', 'noholdout.R', 'dust_key.G', 'dust_fill.B'} <= names)
        self.assertEqual(hdr['motefield/frame'], 1010)
        self.assertEqual((f.parts[0].width, f.parts[0].height), (480, 201))
        nuke.render_frame(1010)                                          # already there: not rendered again
        self.assertEqual(self.g.node('mf_frames')['reload'].runs, 1)
        nuke.render_frame(2000)                                          # outside the shot: nothing to do
        self.assertFalse(os.path.exists(self.read_file().replace('####', '2000')))

    def test_fit_box_button(self):
        fake_nuke.change(self.g, 'standin', 'wide')
        nuke.current_frame[0] = 1024
        nuke_node.button(self.g, 'fit')
        self.assertNotEqual(self.g['box_center'].value(), list(schema.DEFAULTS['box_center']))
        self.assertIn('box_size', json.loads(self.g['mf_edited'].value()))


class WorkerOrder(unittest.TestCase):
    def test_follows_the_viewer_then_fills(self):
        fake_nuke.install(gui=False)
        done = set()

        class FakeCache(object):
            folder = 'x'

            def has(self, f):
                return f in done
        s = settings.Settings(shot=settings.Layer('shot', {'first': 1001, 'last': 1100}))
        w = nuke_node.Worker(start=False)
        w.want('MoteField1', s, FakeCache())
        nuke.current_frame[0] = 1050
        order = []
        for _ in range(5):
            name, job, f = w._next()
            order.append(f)
            done.add(f)
        self.assertEqual(order, [1050, 1051, 1049, 1052, 1048])
        done.update(range(1030, 1075))                     # everything near the viewer is there
        self.assertIsNone(w._next())
        w.fill('MoteField1')
        self.assertEqual(w._next()[2], 1029)                # the nearest frame not yet there, either side
        nuke.current_frame[0] = 2000                        # past the end of the shot: from the last frame back
        done.clear()
        self.assertEqual(w._next()[2], 1100)


if __name__ == '__main__':
    unittest.main()


class Background(unittest.TestCase):
    def test_frames_fill_in_while_you_work(self):
        import time
        fake_nuke.install(gui=True)
        d = tempfile.mkdtemp()
        old = os.environ.get('MOTEFIELD_CACHE')
        os.environ['MOTEFIELD_CACHE'] = d
        try:
            g = nuke_node.create()
            fake_nuke.change(g, 'last', 1003)
            fake_nuke.change(g, 'proxy', 0.2)
            nuke.current_frame[0] = 1002
            fake_nuke.change(g, 'standin', 'wide')
            pattern = g.node('mf_frames')['file'].value()
            t0 = time.time()
            while time.time() - t0 < 120:
                if all(os.path.exists(pattern.replace('####', '%04d' % f)) for f in (1001, 1002, 1003)):
                    break
                time.sleep(0.2)
            made = sorted(os.path.getmtime(pattern.replace('####', '%04d' % f)) for f in (1001, 1002, 1003))
            self.assertEqual(len(made), 3)
            first = min((1001, 1002, 1003), key=lambda f: os.path.getmtime(pattern.replace('####', '%04d' % f)))
            self.assertEqual(first, 1002)                                  # the frame on screen came first
            time.sleep(0.5)
            self.assertIn('All 3 frames ready', g['mf_status'].value())
            self.assertGreaterEqual(g.node('mf_frames')['reload'].runs, 3)
        finally:
            nuke_node._worker().drop(g.fullName())
            if old is None:
                os.environ.pop('MOTEFIELD_CACHE', None)
            else:
                os.environ['MOTEFIELD_CACHE'] = old
            shutil.rmtree(d, ignore_errors=True)
            fake_nuke.install(gui=False)
