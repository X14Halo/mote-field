"""The motion, the light, the splatter and the renderer, on a small stand-in."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motefield import light, motion, place, render, splat, standin  # noqa: E402
from motefield.settings import Layer, Settings  # noqa: E402

W, H = 192, 80


def setup(view='wide', **shot):
    vals = standin.settings_for(view)
    vals.update(shot)
    return Settings(shot=Layer('shot', vals))


SHOTS = {}


def small_shot(view='wide'):
    if view not in SHOTS:
        SHOTS[view] = standin.StandinShot(view, W, H)
    return SHOTS[view]


class Splat(unittest.TestCase):
    def tearDown(self):
        splat.FORCE = None

    def test_light_is_conserved(self):
        rng = np.random.default_rng(4)
        n = 400
        X = rng.uniform(80, 420, n)
        Y = rng.uniform(80, 220, n)
        R = np.concatenate([rng.uniform(0.25, 2.4, n // 2), rng.uniform(2.6, 40, n // 2)])
        E = rng.random((n, 2))
        for lens in (splat.Lens(), splat.Lens(blades=7, aspect=1.4), splat.Lens(blades=9, cateye=0.4, falloff=-0.5)):
            for force in (None, 'stamp', 'fft'):
                splat.FORCE = force
                img = np.zeros((300, 500, 2))
                splat.draw(img, X, Y, R, E, lens)
                np.testing.assert_allclose(img.sum((0, 1)), E.sum(0), rtol=1e-9, err_msg=str(force))

    def test_stamped_and_fft_bokeh_agree(self):
        X, Y = np.array([120.3, 140.8, 300.5]), np.array([90.2, 110.7, 150.1])
        R = np.array([12.0, 30.0, 55.0])
        E = np.ones((3, 1))
        lens = splat.Lens(blades=6, cateye=0.3)
        out = {}
        for force in ('stamp', 'fft'):
            splat.FORCE = force
            img = np.zeros((260, 420, 1))
            splat.draw(img, X, Y, R, E, lens)
            out[force] = img
        # the FFT path splats each sample bilinearly; stamping puts it on the nearest pixel: within a pixel's shift
        a, b = out['stamp'][..., 0], out['fft'][..., 0]
        self.assertLess(np.abs(a - b).sum() / a.sum(), 0.12)
        for img in (a, b):
            ys, xs = np.mgrid[0:img.shape[0], 0:img.shape[1]]
            cx = (img * (xs + 0.5)).sum() / img.sum()
            self.assertAlmostEqual(cx, X.mean(), delta=0.8)

    def test_small_blur_lands_where_the_sample_is(self):
        img = np.zeros((40, 40, 1))
        splat.draw(img, np.array([17.3]), np.array([22.8]), np.array([1.5]), np.ones((1, 1)), splat.Lens())
        ys, xs = np.mgrid[0:40, 0:40]
        # a Gaussian on the pixel grid, cut off at 3 sigma: its centre is within a thousandth of a pixel
        self.assertAlmostEqual((img[..., 0] * (xs + 0.5)).sum(), 17.3, delta=1e-3)
        self.assertAlmostEqual((img[..., 0] * (ys + 0.5)).sum(), 22.8, delta=1e-3)

    def test_kernels(self):
        for blades, cat in ((0, (0, 0)), (5, (0, 0)), (8, (0.3, -0.2))):
            k = splat.aperture(20.0, blades=blades, cat=cat)
            self.assertAlmostEqual(k.sum(), 1.0)
            self.assertTrue((k >= 0).all())
        self.assertEqual(splat._fast_len(257), 270)


class Motion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = setup()
        cls.e = cls.s.engine(1001)
        cls.vis = staticmethod(light.visibility(cls.e, small_shot(), key='test-wide'))
        cls.m = motion.DustMotion(cls.e, cls.vis)

    def test_any_frame_alone_is_the_same(self):
        a = self.m.at(1030).P
        fresh = motion.DustMotion(self.e, self.vis)
        for f in (1001, 1047, 1012):
            fresh.at(f)
        np.testing.assert_array_equal(fresh.at(1030).P, a)

    def test_velocity_is_the_derivative(self):
        a, b = self.m.at(1020), self.m.at(1020.02)
        ok = np.abs(b.local - a.local).max(1) < 0.5                     # not wrapping across the box
        fd = (b.P - a.P) / (b.t - a.t)
        err = np.linalg.norm(fd[ok] - (a.V[ok] + b.V[ok]) / 2, axis=1)
        self.assertLess(np.median(err), 1e-6)
        self.assertLess(np.percentile(err, 99), 1e-3)

    def test_motes_stay_in_the_box(self):
        st = self.m.at(1047)
        self.assertTrue((np.abs(st.local) <= self.m.size / 2 + 1e-9).all())

    def test_warm_air_rises_in_the_sun(self):
        st = self.m.at(1040)
        lit = self.vis(st.P) > 0
        self.assertGreater(lit.sum(), 50)
        self.assertGreater(st.V[lit, 1].mean(), st.V[~lit, 1].mean() + 0.5)        # cm/s

    def test_more_density_moves_no_existing_mote(self):
        e2 = setup(density=400).engine(1001)
        m2 = motion.DustMotion(e2, self.vis)
        a, b = self.m.at(1033), m2.at(1033)
        self.assertGreater(m2.n, self.m.n)
        np.testing.assert_allclose(b.P[:self.m.n], a.P, atol=1e-9)


class Light(unittest.TestCase):
    def test_aim_is_the_inverse_of_light_dir(self):
        for az, el in ((25, 33), (-140, 70), (90, 5)):
            L = light.light_dir(az, el)
            a, b = light.aim(np.array([0, 0, 0]) + L * 300, np.zeros(3))
            self.assertAlmostEqual(a, az)
            self.assertAlmostEqual(b, el)

    def test_kelvin_has_unit_luminance(self):
        for K in (1800, 3200, 5600, 9000):
            c = light.kelvin_rgb(K)
            self.assertAlmostEqual(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], 1.0)


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = setup()
        cls.r = render.Renderer(cls.s, small_shot())
        cls.out = cls.r.frame(1024)

    def test_image(self):
        rgb = self.out['rgb']
        self.assertEqual(rgb.shape, (H, W, 3))
        self.assertTrue(np.isfinite(rgb).all())
        self.assertGreaterEqual(rgb.min(), 0.0)
        self.assertGreater(rgb.sum(), 1.0)
        self.assertTrue((self.out['alpha'] == 0).all())
        self.assertGreaterEqual(self.out['noholdout'].sum(), rgb.sum())

    def test_same_frame_same_image(self):
        again = render.Renderer(self.s, small_shot()).frame(1024)
        np.testing.assert_array_equal(again['rgb'], self.out['rgb'])

    def test_key_plus_fill_is_rgb(self):
        s = setup(out_keyfill=True)
        o = render.Renderer(s, small_shot()).frame(1024)
        np.testing.assert_allclose(o['dust_key'] + o['dust_fill'], o['rgb'], rtol=1e-6, atol=1e-9)
        self.assertGreater(o['dust_fill'].sum(), 0)
        np.testing.assert_allclose(o['rgb'], self.out['rgb'], rtol=1e-5, atol=1e-7)

    def test_brightness_and_light_scale_without_rebuilding(self):
        s = setup(light_int=12.0)
        self.assertEqual(s.prep_key(), self.s.prep_key())
        r2 = render.Renderer(s, small_shot())
        self.assertIs(r2.bounce, self.r.bounce)                      # the bounce lights are not rebuilt
        o = r2.frame(1024)
        np.testing.assert_allclose(o['rgb'], self.out['rgb'] * 2, rtol=1e-5, atol=1e-7)

    def test_matte_alpha(self):
        o = render.Renderer(setup(alpha='matte', matte_gain=4.0), small_shot()).frame(1024)
        a = o['alpha']
        lum = o['rgb'] @ np.array([0.2126, 0.7152, 0.0722])
        np.testing.assert_allclose(a, np.clip(lum * 4.0, 0, 1), rtol=1e-5, atol=1e-7)
        self.assertGreater(a.max(), 0.0)

    def test_holdouts_take_light_away(self):
        off = render.Renderer(setup(holdout=False), small_shot()).frame(1024)
        self.assertLess(self.out['rgb'].sum(), off['rgb'].sum())
        np.testing.assert_allclose(off['rgb'], off['noholdout'], rtol=1e-5, atol=1e-7)

    def test_no_blur_concentrates_light(self):
        sharp = render.Renderer(setup(dof=False, mb=False), small_shot()).frame(1024)
        self.assertGreater(sharp['rgb'].max(), self.out['rgb'].max())

    def test_fit_box(self):
        e = self.s.engine(1001)
        c, size = place.fit_box(e, small_shot())
        cam = small_shot().camera(1024).pos
        self.assertTrue(all(abs(cam[i] - c[i]) <= size[i] / 2 for i in range(3)))
        self.assertTrue(all(100 < x < 2000 for x in size))


if __name__ == '__main__':
    unittest.main()
