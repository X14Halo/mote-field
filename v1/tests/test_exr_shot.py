"""EXR reading and writing, the header camera, and what each pixel sees, on a synthetic utility pass (tests/synth.py)
and, when MOTEFIELD_TEST_PASSES points at a folder of real passes, on those too (never committed)."""

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import synth  # noqa: E402
from motefield import camera, check, exr  # noqa: E402
from motefield.shot import Shot, build_surface, frame_path, frames_on_disk, survey  # noqa: E402

try:
    import OpenEXR
    HAVE_OPENEXR = hasattr(OpenEXR, 'File')
except Exception:
    HAVE_OPENEXR = False


class Exr(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_round_trip_half_and_float(self):
        rng = np.random.default_rng(1)
        a = (rng.random((37, 53)) * 100).astype(np.float32)
        b = (rng.random((37, 53)) - 0.5).astype(np.float32)
        p = exr.write(os.path.join(self.dir, 'x.exr'), {'R': a, 'layer.G': b}, half={'R'},
                      attrs={'motefield/version': '1.0', 'motefield/model': 3, 'm': ('float', 2.5)})
        with exr.ExrFile(p) as f:
            self.assertEqual(len(f.parts), 1)
            part = f.parts[0]
            self.assertEqual((part.width, part.height), (53, 37))
            self.assertEqual({c.name: c.type for c in part.channels}, {'R': exr.HALF, 'layer.G': exr.FLOAT})
            d = f.read(0, ['R', 'layer.G'])
            self.assertEqual(part.header['motefield/version'], '1.0')
            self.assertEqual(part.header['motefield/model'], 3)
            self.assertEqual(part.header['m'], 2.5)
        np.testing.assert_array_equal(d['R'], a.astype(np.float16).astype(np.float32))
        np.testing.assert_array_equal(d['layer.G'], b)

    def test_errors(self):
        p = os.path.join(self.dir, 'not.exr')
        with open(p, 'wb') as fh:
            fh.write(b'hello there, not an exr')
        with self.assertRaises(exr.ExrError):
            exr.ExrFile(p)
        good = exr.write(os.path.join(self.dir, 'g.exr'), {'R': np.zeros((4, 4))})
        with exr.ExrFile(good) as f:
            with self.assertRaises(exr.ExrError):
                f.read(0, ['nope'])

    @unittest.skipUnless(HAVE_OPENEXR, 'the OpenEXR module is not installed')
    def test_reads_what_the_openexr_library_writes(self):
        rng = np.random.default_rng(2)
        planes = {'pos.R': rng.random((41, 30)).astype(np.float32) * 1e4,
                  'pos.G': rng.random((41, 30)).astype(np.float32), 'A': rng.random((41, 30)).astype(np.float16)}
        for comp, name in ((OpenEXR.ZIPS_COMPRESSION, 'zips'), (OpenEXR.ZIP_COMPRESSION, 'zip'),
                           (OpenEXR.RLE_COMPRESSION, 'rle'), (OpenEXR.NO_COMPRESSION, 'none'),
                           (OpenEXR.PIZ_COMPRESSION, 'piz')):
            p = os.path.join(self.dir, name + '.exr')
            hdr = {'compression': comp, 'type': OpenEXR.scanlineimage, 'name': 'geo'}
            parts = [OpenEXR.Part(hdr, {k: v for k, v in planes.items() if k != 'A'}),
                     OpenEXR.Part(dict(hdr, name='alpha'), {'A': planes['A']})]
            OpenEXR.File(parts).write(p)
            with exr.ExrFile(p) as f:
                self.assertTrue(f.multipart)
                self.assertEqual([q.name for q in f.parts], ['geo', 'alpha'])
                d = f.read(0, ['pos.R', 'pos.G'])
                np.testing.assert_array_equal(d['pos.R'], planes['pos.R'], name)
                np.testing.assert_array_equal(d['pos.G'], planes['pos.G'], name)
                np.testing.assert_array_equal(f.read(1, ['A'])['A'], planes['A'].astype(np.float32), name)

    def test_frame_patterns(self):
        for pat, want in (('a.####.exr', 'a.0073.exr'), ('a_%04d.exr', 'a_0073.exr'), ('a.$F4.exr', 'a.0073.exr'),
                          ('a.@@@.exr', 'a.073.exr'), ('a.%d.exr', 'a.73.exr'), ('plain.exr', 'plain.exr')):
            self.assertEqual(frame_path(pat, 73), want)
        for f in (1001, 1002, 1004):
            open(os.path.join(self.dir, 'u.%04d.exr' % f), 'wb').close()
        open(os.path.join(self.dir, 'u.1003.exr.tmp'), 'wb').close()
        self.assertEqual(frames_on_disk(os.path.join(self.dir, 'u.####.exr')), [1001, 1002, 1004])


class HeaderCamera(unittest.TestCase):
    def test_reads_the_render_camera_not_the_decoy(self):
        _, attrs = synth.render(8, 6, 1)
        cam = camera.from_headers([attrs])
        np.testing.assert_allclose(cam.pos, synth.CAM_POS, atol=1e-6)
        self.assertAlmostEqual(math.degrees(cam.hfov), synth.HFOV, places=9)
        self.assertEqual(cam.focal, 32.0)                      # the plain attribute, not the JSON (in cm)
        self.assertEqual(cam.lens['blades'], synth.BLADES)
        self.assertAlmostEqual(cam.lens['cateye'], synth.CATEYE)
        self.assertAlmostEqual(cam.lens['fstop_full'], 1.4)
        self.assertEqual(cam.shutter, dict(angle=180.0, mode='backward'))

    def test_matrix_conventions(self):
        _, attrs = synth.render(8, 6, 1)
        M = np.array(synth.cam_to_world()).reshape(4, 4)
        for key, val in (('x/camToWorld', M.T.reshape(16)),                       # column-vector convention
                         ('x/worldToCamera', np.linalg.inv(M).reshape(16))):
            cam = camera.from_headers([{key: list(val), 'x/horizontalFOV_deg': 50.0}])
            np.testing.assert_allclose(cam.pos, synth.CAM_POS, atol=1e-6)
        cam = camera.from_headers([{'x/camToWorld': list(M.reshape(16)), 'x/focalLength': 35.0,
                                    'x/horizontalAperture': 36.0 / 25.4}])        # aperture in inches
        self.assertAlmostEqual(cam.film, 36.0)
        self.assertAlmostEqual(cam.hfov, 2 * math.atan(36.0 / 70.0))
        self.assertIsNone(camera.from_headers([{'x/focalLength': 35.0}]))

    def test_projection_round_trip(self):
        _, attrs = synth.render(8, 6, 1)
        cam = camera.from_headers([attrs])
        W, H = 96, 54
        rays = cam.rays(W, H)
        P = cam.pos + rays * 500.0
        px, py, z = cam.project(P.reshape(-1, 3), W, H)
        ys, xs = np.mgrid[0:H, 0:W]
        np.testing.assert_allclose(px, (xs + 0.5).ravel(), atol=1e-6)
        np.testing.assert_allclose(py, (ys + 0.5).ravel(), atol=1e-6)


class Surfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.ch, cls.attrs = synth.render()
        exr.write(os.path.join(cls.dir, 'util.0101.exr'), cls.ch, attrs=cls.attrs, half=False)
        chz, _ = synth.render(zdepth=True)
        exr.write(os.path.join(cls.dir, 'zdepth.0101.exr'), {k: v for k, v in chz.items() if
                                                            not k.startswith('pos.')}, attrs=cls.attrs, half=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_survey(self):
        with exr.ExrFile(os.path.join(self.dir, 'util.0101.exr')) as f:
            L = survey([f])
        self.assertEqual(L['P'].channels, ['pos.R', 'pos.G', 'pos.B'])
        self.assertEqual(L['Z'].channels, ['depth.Z'])
        self.assertEqual(L['Zraw'].channels, ['depthNoFilter.R'])
        self.assertEqual(L['A'].channels, ['A'])
        self.assertEqual(L['ID'].channels, ['sceneObjectID.R'])
        self.assertEqual(L['MV'].channels, ['backward.u', 'backward.v'])

    def test_premultiplied_positions_are_recovered(self):
        shot = Shot(os.path.join(self.dir, 'util.####.exr'))
        self.assertEqual(shot.frames, [101])
        s = shot.surface(101)
        self.assertTrue(s.info['position_premultiplied'])
        self.assertTrue(s.info['depth_premultiplied'])
        self.assertEqual(s.info['holdouts_from'], 'unfiltered depth')
        er = s.info['edge_reprojection_px']
        self.assertGreater(er['as_stored'], 5 * er['divided_by_coverage'])
        # every non-empty pixel's position now lies on a real surface along its own pixel
        some = s.cov > 0
        from motefield.shot import _reproj_error
        self.assertLess(_reproj_error(s.cam, s.P, s.W, s.H, some), 0.6)

    def test_clean_pixels_are_single_surfaces(self):
        s = Shot(os.path.join(self.dir, 'util.####.exr')).surface(101)
        self.assertTrue((s.cov[s.clean] >= 0.98).all())
        raw = self.ch['depthNoFilter.R']
        d = np.linalg.norm(s.P - s.cam.pos, axis=-1)
        self.assertTrue((np.abs(raw[s.clean] - d[s.clean]) <= 0.02 * d[s.clean] + 1e-3).all())
        self.assertGreater(s.info['clean_fraction'], 0.7)

    def test_view_axis_depth_is_converted(self):
        shot = Shot(os.path.join(self.dir, 'zdepth.####.exr'))
        s = shot.surface(101)
        self.assertEqual(s.info['position_from'], 'depth and the camera')
        # without positions there is nothing to compare with, so depth is taken as distance along the ray: say so
        self.assertIn('assumed', s.info['unfiltered_depth_kind'])
        P = np.stack([self.ch['pos.' + c] for c in 'RGB'], -1)
        s2 = build_surface(s.cam, s.W, s.H, P=P, Z=None, Zraw=synth.render(zdepth=True)[0]['depthNoFilter.R'],
                           A=self.ch['A'])
        self.assertIn('view axis', s2.info['unfiltered_depth_kind'])
        ray = np.linalg.norm(s2.P - s2.cam.pos, axis=-1)
        # the unfiltered sample sits off the pixel centre, so on a sloping floor it differs a little; unconverted it
        # would be off by up to the cosine of the corner angle (about 25% here)
        np.testing.assert_allclose(s2.dist[s2.clean], ray[s2.clean], rtol=0.021)

    def test_shot_check(self):
        rep = check.check(os.path.join(self.dir, 'util.####.exr'), units='centimetres')
        levels = {level for level, _, _ in rep['items']}
        self.assertNotIn(check.PROBLEM, levels)
        msgs = ' '.join(m for _, _, m in rep['items'])
        self.assertIn('premultiplied', msgs)
        self.assertIn('7 blades', msgs)
        self.assertIn('unfiltered depth', msgs)
        self.assertEqual(rep['camera']['lens']['blades'], 7)
        bad = check.check(os.path.join(self.dir, 'util.####.exr'), units='metres')   # a 1 km room: suspicious
        self.assertTrue(any(level == check.WARN and topic == 'Units' for level, topic, _ in bad['items']))
        none = check.check(os.path.join(self.dir, 'missing.####.exr'))
        self.assertEqual(none['items'][0][0], check.PROBLEM)


PASSES = os.environ.get('MOTEFIELD_TEST_PASSES')


@unittest.skipUnless(PASSES and os.path.isdir(PASSES), 'set MOTEFIELD_TEST_PASSES to a folder of real utility passes')
class RealPasses(unittest.TestCase):
    """A real production pass: <folder>/util_geometry.####.exr and optionally util_mattes.####.exr."""

    def test_check(self):
        util = os.path.join(PASSES, 'util_geometry.####.exr')
        mattes = os.path.join(PASSES, 'util_mattes.####.exr')
        rep = check.check(util, extra=mattes if frames_on_disk(mattes) else '')
        self.assertNotIn(check.PROBLEM, {level for level, _, _ in rep['items']})
        s = Shot(util).surface(rep['frame'])
        from motefield.shot import _reproj_error
        self.assertLess(_reproj_error(s.cam, s.P, s.W, s.H, s.clean), 0.3)
        self.assertLess(_reproj_error(s.cam, s.P, s.W, s.H, (s.cov > 0.02) & (s.cov < 0.98)), 1.0)
        print('\n' + check.text(rep))
        print(json.dumps(rep['camera'], default=str))


if __name__ == '__main__':
    unittest.main()
