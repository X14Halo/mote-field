"""The drop-in node (dist/MoteField.nk, built by node/build_node.py), checked without Nuke: the script reads back
as Nuke would read it, every kernel's source survives the quoting, the internal wiring is right, and the Python
inside the node links the kernels, finds the layers, reads the camera from the header and fits the box."""

import json
import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'blink'))

import nkload                                     # noqa: E402
from node import blinklib, build_node            # noqa: E402
from motefield import standin                     # noqa: E402

TEXT = build_node.build()


def header_for(cam):
    """EXR-header-style metadata for a camera, the way Nuke shows it (exr/ prefix): world-to-camera as a list,
    lens values, and lens extras inside a JSON attribute next to a decoy camera."""
    M = np.eye(4)
    M[0, :3], M[1, :3], M[2, :3], M[3, :3] = cam.right, cam.up, cam.back, cam.pos
    w2c = np.linalg.inv(M).reshape(16).tolist()
    info = {'default': {'fStop': 5.6, 'bladeCount': 0}, 'main': {'fStop': cam.fstop, 'bladeCount': 7,
                                                                    'catEye': 0.3}}
    return {'exr/worldToCamera': w2c, 'exr/cameraFocalLength': cam.focal, 'exr/cameraFilmBackWidth': cam.film,
            'exr/cameraFStop': cam.fstop, 'exr/cameraFocusDistance': cam.focus,
            'exr/renderInfo': json.dumps(info), 'exr/shutterAngle': 180.0}


class Loaded(object):
    """The node loaded into stand-in nodes, connected to a stand-in utility Read."""

    def __init__(self, view='wide', frames=(1001, 1048)):
        self.shot = standin.StandinShot(view, 320, 180)
        util = nkload.Node('Read', None)
        util._knobs['name'].setValue('Read1')
        util._channels = ['rgba.red', 'rgba.green', 'rgba.blue', 'rgba.alpha', 'pos.x', 'pos.y', 'pos.z',
                          'normal.x', 'normal.y', 'normal.z', 'depth.Z', 'depthNoFilter.Z', 'surfaceColor.red',
                          'surfaceColor.green', 'surfaceColor.blue']
        util._first, util._last = frames
        self.util = util
        self.sc = nkload.load(TEXT, outside_inputs={0: util})
        util.script = self.sc
        self.g = self.sc.root.children['MoteField1']
        shot = self.shot
        self.sc.metadata = lambda node, f: header_for(shot.camera(f if f is not None else frames[0]))
        self.surf_cache = {}
        self.sc.size = lambda node: (shot.W, shot.H)
        self.sc.sample = self.sample
        sys.modules['nuke'] = nkload.fake_nuke(self.sc)

    def sample(self, node, chan, x, y):
        """Pixels of the node's surface image, from the kernels run in the C++ stand-in for Blink."""
        if node.name() != 'mf_surface':
            return 0.0
        if 'img' not in self.surf_cache:
            import run
            import pipeline
            f = 1024
            pos, depth, nrm, alb = pipeline.pass_images(self.shot, f)
            cam = self.shot.camera(f)
            p = pipeline.camera_params(cam, 'cam')
            facts = run.run('facts', p, {'space': np.zeros((1, 3, 4), np.float32), 'pos': pos, 'depth': depth})
            pp = dict(p, p_premult=-1, z_kind=-1, z_premult=-1)
            self.surf_cache['img'] = run.run('surface', pp, {'pos': pos, 'depth': depth, 'facts': facts})
        img = self.surf_cache['img']
        c = 'rgba'.index(chan.split('.')[1][0]) if chan.split('.')[1] in ('red', 'green', 'blue', 'alpha') else 0
        return float(img[int(y), int(x), c])

    def run(self, node_name, knob_name, knob=None):
        n = self.g if node_name is None else self.g.node(node_name)
        nkload.run_python(n, n[knob_name].value(), knob if knob is not None else n[knob_name],
                          group=self.g)


class Script(unittest.TestCase):
    def setUp(self):
        self.L = Loaded()

    def test_dist_files_are_current(self):
        from node import build_check
        with open(os.path.join(ROOT, 'dist', 'MoteField.nk'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), TEXT, 'run: python3 node/build_node.py')
        with open(os.path.join(ROOT, 'dist', 'MoteField_check.nk'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), build_check.build(), 'run: python3 node/build_check.py')

    def test_links_survive_another_knob_naming(self):
        """If a Nuke version named the kernel knobs with another prefix, the links still find them."""
        L = self.L
        n = L.g.node('mf_draw')
        for name in list(n.knobs()):
            if name.startswith('MFDraw_'):
                k = n._knobs.pop(name)
                n._knobs['Draw_' + name[len('MFDraw_'):]] = k
        L.run('Output1', 'onCreate')
        mod = sys.modules['_motefield_node']
        self.assertEqual(mod.link_kernel(L.g, 'mf_draw'), [])
        self.assertEqual(n['Draw_blades'].expr[0], 'parent.blades')
        self.assertEqual(n['Draw_hdr_blades'].expr[0], 'parent.hdr_blades')

    def test_kernels_survive_the_quoting(self):
        for n in self.L.g.children.values():
            if n.cls == 'BlinkScript':
                self.assertEqual(n.source, blinklib.source(n.name()[3:]), n.name())

    def test_wiring_on_the_stack_is_the_intended_wiring(self):
        g = self.L.g
        ns = {}
        exec(build_node.python_source().split('\nimport nuke\n')[0], ns)       # the module docstring and imports
        wires = None
        for line in build_node.python_source().splitlines():
            if line.startswith('WIRES = '):
                wires = eval(line[len('WIRES = '):].split('  #')[0])
        self.assertTrue(wires)
        for name, inputs in wires.items():
            n = g.node(name)
            self.assertIsNotNone(n, name)
            for i, src in enumerate(inputs):
                if src is None:
                    continue
                self.assertIsNotNone(n.input(i), (name, i))
                self.assertEqual(n.input(i).name(), src, (name, i))

    def test_knobs(self):
        g = self.L.g
        for key in ('master_amount', 'light_az', 'box_center', 'tint', 'output_mode', 'alpha_out', 'units',
                    'cam_pos', 'cam_th', 'hdr_focal', 'mf_py', 'mf_t_open', 'layer_pos'):
            self.assertIsNotNone(g.knob(key), key)
        self.assertEqual(g['box_size'].value(), [760.0, 330.0, 900.0])
        self.assertEqual(g['layer_pos'].target, 'mf_pos.in')
        self.assertEqual(g['units'].options[g['units'].value()], 'centimetres')

    def test_created_links_every_kernel_param(self):
        L = self.L
        L.run('Output1', 'onCreate')
        mod = sys.modules['_motefield_node']
        self.assertEqual(mod.setup(L.g), {})
        for node, links in mod.LINKS.items():
            n = L.g.node(node)
            for param, expr in links.items():
                k = n['%s_%s' % (n.kernel, param)]
                if isinstance(expr, list):
                    self.assertEqual([k.expr[i] for i in range(len(expr))], expr)
                else:
                    self.assertEqual(k.expr[0], expr)
            self.assertEqual(n['useGPUIfAvailable'].expr.get(0), 'parent.use_gpu')
        m = L.g.node('mf_motes')
        self.assertEqual(m['MFMotes_density'].expr[0], 'parent.density*parent.master_amount')
        self.assertEqual(m['MFMotes_o_pos'].expr[0], 'parent.cam_pos.x(frame+parent.mf_t_open)')

    def test_connecting_finds_layers_reads_the_camera_and_fits_the_box(self):
        L = self.L
        L.run(None, 'knobChanged', knob=nkload.Knob('inputChange'))
        g = L.g
        self.assertEqual(g.node('mf_pos')['in'].value(), 'pos')
        self.assertEqual(g.node('mf_pos')['alpha'].value(), 'alpha2')
        self.assertEqual(g.node('mf_depth')['in'].value(), 'depth')
        self.assertEqual(g.node('mf_depth')['in2'].value(), 'depthNoFilter')
        self.assertEqual(g.node('mf_nrm')['in'].value(), 'normal')
        self.assertEqual(g.node('mf_alb')['in'].value(), 'surfaceColor')
        self.assertEqual(g['mf_use_albedo'].value(), 1)
        self.assertEqual((g['first'].value(), g['last'].value()), (1001, 1048))
        # the stand-in camera moves: keyed on every frame, linearly
        cam = L.shot.camera(1001)
        a = g['cam_pos'].animation(0)
        self.assertIsNotNone(a)
        self.assertEqual(len(a.keys()), 48)
        self.assertEqual(a.interp, 'linear')
        self.assertAlmostEqual(a.k[1001], cam.pos[0], places=4)
        self.assertIsNone(g['cam_th'].animation(0))                 # the lens doesn't zoom: not keyed
        self.assertAlmostEqual(g['cam_th'].value(), math.tan(cam.hfov / 2), places=6)
        self.assertAlmostEqual(g['hdr_focal'].value(), cam.focal)
        self.assertAlmostEqual(g['hdr_fstop'].value(), cam.fstop)
        self.assertEqual(g['hdr_blades'].value(), 7)              # from the JSON camera that agrees with the header
        self.assertAlmostEqual(g['hdr_cateye'].value(), 0.3)
        self.assertEqual(g['shutter'].value(), 180.0)
        # the box was fitted to the set (it had the default)
        self.assertNotEqual(g['box_size'].value(), [760.0, 330.0, 900.0])
        size = g['box_size'].value()
        self.assertTrue(all(100 < s < 3000 for s in size), size)
        self.assertIn('motes', g['mf_status'].value())

    def test_check_shot_report(self):
        L = self.L
        L.run(None, 'knobChanged', knob=nkload.Knob('inputChange'))
        L.run(None, 'check_shot')
        rep = L.g['mf_report'].value()
        self.assertIn('Layers', rep)
        self.assertIn('Camera', rep)
        self.assertNotIn('PROBLEM', rep)

    def test_opening_the_panel_rereads_a_changed_camera(self):
        L = self.L
        L.run(None, 'knobChanged', knob=nkload.Knob('inputChange'))
        before = L.g['cam_pos'].animation(0).k[1001]
        L.run(None, 'knobChanged', knob=nkload.Knob('showPanel'))         # nothing changed: nothing read
        self.assertEqual(L.g['cam_pos'].animation(0).k[1001], before)
        shot = L.shot
        L.sc.metadata = lambda node, f: header_for(shot.camera((f or 1001) + 5))     # a new version of the pass
        L.run(None, 'knobChanged', knob=nkload.Knob('showPanel'))
        self.assertNotAlmostEqual(L.g['cam_pos'].animation(0).k[1001], before, places=3)

    def test_a_still_camera_is_not_keyed(self):
        L = Loaded(frames=(1001, 1001))
        L.run(None, 'knobChanged', knob=nkload.Knob('inputChange'))
        self.assertIsNone(L.g['cam_pos'].animation(0))


if __name__ == '__main__':
    unittest.main()
