"""The node's Blink kernels, compiled as C++ by the stand-in for Blink (tests/blink) and checked against the Python
reference: the same random numbers, the same swirling air, the same motes in the same places, the same light and the
same picture, and the bokeh and Gaussians keep each mote's light exactly.

Needs g++ (the tests skip without it)."""

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'blink'))

from motefield import exr, light, rng, standin                    # noqa: E402
from motefield.motion import DustMotion                          # noqa: E402
from motefield.render import Renderer                            # noqa: E402
from motefield.settings import Layer, Settings                   # noqa: E402
from motefield.shot import Shot                                  # noqa: E402
from node import blinklib as bl                                  # noqa: E402

HAVE_CXX = shutil.which('g++') is not None
if HAVE_CXX:
    import pipeline                                              # noqa: E402
    import run                                                   # noqa: E402

W, H = 320, 180
SHOTS = {}


def shot(view='wide'):
    if view not in SHOTS:
        SHOTS[view] = standin.StandinShot(view, W, H)
    return SHOTS[view]


def setup(view='wide', **kw):
    vals = standin.settings_for(view)
    vals.update(kw)
    return Settings(shot=Layer('shot', vals))


def blurred(img, r=3):
    """A box blur (numpy only), to compare pictures without caring about single pixels."""
    a = img.mean(2).astype(np.float64)
    k = 2 * r + 1
    for ax in (0, 1):
        c = np.cumsum(np.pad(a, [(r + 1, r) if i == ax else (0, 0) for i in range(2)], mode='edge'), axis=ax)
        a = (np.take(c, range(k, c.shape[ax]), axis=ax) - np.take(c, range(0, c.shape[ax] - k), axis=ax)) / k
    return a


class OlderBlink(unittest.TestCase):
    """The kernels keep to what Nuke 12.2's Blink builds and runs (found on a real Nuke 12.2: an image read two
    function calls deep crashed it)."""

    def test_kernels_keep_the_rules(self):
        for k in bl.KERNELS:
            self.assertEqual(bl.rule_breaks(bl.source(k)), [], k)

    def test_the_rules_catch_what_they_should(self):
        src = bl.source('bounds').replace('  void process(int2 xy) {', """  float bad(int k) {
    return in_surf(k, in_surf.bounds.y1).x;
  }
  float worse(int k) {
    return bad(k);
  }
  void process(int2 xy) {
    float a[2];
    float4 s = in_surf(0, 0);
    int z = xy.x / 3;""", 1)
        found = bl.rule_breaks(src)
        self.assertIn('integer division in process (/ 3)', found)
        self.assertIn('a variable named s', found)
        self.assertIn('bad reads images and is called from worse', found)
        self.assertIn('bounds inside a read of in_surf', found)
        self.assertIn('an input not named in_...: surf', bl.rule_breaks(bl.source('bounds').replace('in_surf', 'surf')))
        self.assertIn('an array', found)


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class Randomness(unittest.TestCase):
    def test_hash_in_16_bit_halves_matches_the_reference(self):
        img = run.run('check', {'motes': 0}, {'src': np.zeros((130, 130, 4), np.float32)})
        self.assertEqual(img[10, 10, :3].tolist(), [np.float32(0.1), np.float32(0.9), np.float32(0.2)])   # green

    def test_swirl_tables(self):
        for seed in (1, 77, 2147483000):
            img = run.run('modes', {'seed': seed}, {'space': np.zeros((64, 2, 4), np.float32)})
            ref = np.concatenate(rng.mode_tables(seed), 0)
            got = np.concatenate([img[:60, 0, :], img[:60, 1, :]], 1)[:, [0, 1, 2, 4, 5, 6, 3, 7]]
            np.testing.assert_allclose(got, ref, rtol=2e-4, atol=1e-5)


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class Motion(unittest.TestCase):
    def _positions(self, s, f):
        out = pipeline.render(s, shot(), f, keep=True)
        n = DustMotion(s.engine(f)).n
        idx = np.arange(n)
        return out, out['place'][idx // bl.STATE_COLS, idx % bl.STATE_COLS],\
            out['place'][idx // bl.STATE_COLS, bl.STATE_COLS + idx % bl.STATE_COLS]

    def test_motes_are_where_the_reference_puts_them(self):
        for kw in (dict(shadows='none', thermal=0.0), dict(shadows='none')):
            s = setup(**kw)
            for f in (1001, 1037):
                out, pp, pv = self._positions(s, f)
                st = DustMotion(s.engine(f), (lambda P: np.ones(len(P))) if kw.get('thermal') is None else None).at(f)
                d = np.abs(pp[:, :3] - st.P).max()
                self.assertLess(d, 0.05, (kw, f, d))                      # half a millimetre (scene units are cm)
                dv = np.abs(pv[:, :3] - st.V).max()
                self.assertLess(dv, 0.02, (kw, f, dv))

    def test_light_reaches_the_motes_like_the_reference_light_map(self):
        s = setup(shadows='utility pass', thermal=0.0)
        f = 1024
        out, pp, pv = self._positions(s, f)
        shown = pp[:, 3] > 0
        e = s.engine(f)
        L = light.light_dir(e['light_az'], e['light_el'])
        exact = ~shot().blocked(pp[:, :3].astype(np.float64), L)
        lit = pv[:, 3] > 0.5
        wrongly_lit = (lit & ~exact & shown).sum() / max((~exact & shown).sum(), 1)
        missed = (~lit & exact & shown).sum() / max((exact & shown).sum(), 1)
        self.assertLess(wrongly_lit, 0.02)
        self.assertLess(missed, 0.35)          # the pass doesn't show the window's far side: as the reference


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class Picture(unittest.TestCase):
    def test_same_picture_as_the_reference(self):
        for view in ('wide', 'close-up'):
            for kw, tol in ((dict(shadows='none', thermal=0.0, bounce=0.0), 0.01), (dict(shadows='none'), 0.03),
                            (dict(shadows='utility pass'), 0.15)):
                s = setup(view, **kw)
                ref = Renderer(s, shot(view)).frame(1024)['rgb']
                got = pipeline.render(s, shot(view), 1024)['rgb']
                ratio = got.sum() / ref.sum()
                self.assertLess(abs(ratio - 1), tol, (view, kw, ratio))
                c = np.corrcoef(blurred(ref).ravel(), blurred(got).ravel())[0, 1]
                self.assertGreater(c, 0.97 if kw.get('shadows') != 'none' else 0.995, (view, kw, c))

    def test_any_frame_alone_is_the_same(self):
        s = setup('close-up')
        a = pipeline.render(s, shot('close-up'), 1030)['rgb']
        pipeline.render(s, shot('close-up'), 1010)
        b = pipeline.render(s, shot('close-up'), 1030)['rgb']
        np.testing.assert_array_equal(a, b)

    def test_matte_alpha(self):
        s = setup('close-up', alpha='matte', matte_gain=2.0)
        out = pipeline.render(s, shot('close-up'), 1024)
        lum = out['rgb'] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        np.testing.assert_allclose(out['alpha'], np.clip(lum * 2.0, 0, 1), atol=1e-5)


def unit_light(S, shape, K=1):
    """The light a mote of total light 1 hands to the drawing: divided by the sum of its samples' shutter weights."""
    import math
    sw = [1.0 / (1.0 + math.exp(-9.0 * ((s + 0.5) / S - 0.55))) if shape == 0 else 1.0 for s in range(S)]
    return 1.0 / (sum(sw) * K)


def one_mote(R, x, y, blades=0, cat=(0.0, 0.0), anamorphic=0.0, falloff=0.0, streak=(0.0, 0.0), shape=0, S=1):
    """The draw kernel on a hand-made state image holding one mote of light 1 (rgb) at (x, y)."""
    W_, H_ = 160, 120
    motes = np.zeros((bl.STATE_H, bl.STATE_W, 4), np.float32)
    for c in range(bl.STATE_COLS):
        motes[:, c] = [1e9, 1e9, -1e9, -1e9]
    # the bokeh's area as the motes kernel works it out: ask it through a tiny one-off kernel would need the whole
    # chain, so the test passes 1 / area from a fine numeric integral of the same shape instead
    ext = R * 1.6 + 3 + abs(streak[0]) + abs(streak[1])
    motes[0, 0] = [x - ext, y - ext, x + streak[0] + ext, y + streak[1] + ext]
    motes[0, bl.STATE_COLS] = [x, y, x + streak[0], y + streak[1]]
    motes[0, 2 * bl.STATE_COLS] = [R, S + 32, 0.0, 0.0]
    motes[0, 3 * bl.STATE_COLS] = [0.0, 0.0, cat[0], cat[1]]
    u = unit_light(S, shape)
    motes[0, 4 * bl.STATE_COLS] = [u, u, u, 1.0]
    surf = np.zeros((H_, W_, 4), np.float32)
    params = dict(blades=blades, hdr_blades=0, anamorphic=anamorphic, hdr_anamorphic=0.0, bokeh_rot=0.0,
                  bokeh_falloff=falloff, shutter_shape=shape, alpha_mode=0, matte_gain=1.0, held=1)
    # 1 / area: the kernel's shape integrated finely (what the motes kernel's 96-angle integral approximates)
    if R >= 2.5:
        params_area = dict(params)
        img = run.run('draw', params_area, {'surf': surf, 'motes': _with_inv(motes, 1.0), 'bins': _bins(W_, H_),
                                            'rows': _rows()})
        area = img[..., 0].sum()
        motes[0, 2 * bl.STATE_COLS, 3] = 1.0 / area
    img = run.run('draw', params, {'surf': surf, 'motes': motes, 'bins': _bins(W_, H_), 'rows': _rows()})
    return img


def _with_inv(m, inv):
    m = m.copy()
    m[0, 2 * bl.STATE_COLS, 3] = inv
    return m


def _bins(W_, H_):
    b = np.zeros((bl.ROWS1, bl.NTILES, 4), np.float32)
    b[0, :, 0] = 1.0                       # mote 0 in every tile
    return b


def _rows():
    r = np.zeros((bl.ROWS2, bl.NTILES, 4), np.float32)
    r[0, :, 0] = 1.0
    return r


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class Drawing(unittest.TestCase):
    def test_small_motes_keep_their_light_wherever_they_fall_on_a_pixel(self):
        for R in (0.25, 0.8, 2.0):
            sums = [one_mote(R, 80.0 + dx, 60.0 + dy)[..., 0].sum() for dx, dy in ((0, 0), (0.37, 0.5), (0.5, 0.91))]
            for s in sums:
                self.assertAlmostEqual(s, 1.0, delta=2e-4)

    def test_small_mote_centre(self):
        img = one_mote(0.5, 80.3, 60.7)[..., 0]
        ys, xs = np.mgrid[0:img.shape[0], 0:img.shape[1]] + 0.5
        self.assertAlmostEqual((img * xs).sum() / img.sum(), 80.3, delta=0.01)
        self.assertAlmostEqual((img * ys).sum() / img.sum(), 60.7, delta=0.01)

    def test_motion_blur_spreads_along_the_streak_and_keeps_the_light(self):
        img = one_mote(0.5, 40.0, 60.0, streak=(60.0, 0.0), shape=1, S=24)[..., 0]
        self.assertAlmostEqual(img.sum(), 1.0, delta=1e-3)
        xs = np.arange(img.shape[1]) + 0.5
        col = img.sum(0)
        self.assertAlmostEqual((col * xs).sum() / col.sum(), 70.0, delta=0.6)
        # backwardsShape: brighter toward the end (the frame) than the start
        img2 = one_mote(0.5, 40.0, 60.0, streak=(60.0, 0.0), shape=0, S=24)[..., 0]
        col2 = img2.sum(0)
        self.assertGreater(col2[85:100].sum(), col2[40:55].sum() * 2)


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class BokehArea(unittest.TestCase):
    """The motes kernel's 1 / area (an integral around the bokeh's edge) against the drawn kernel's sum."""

    def test_area_integral(self):
        src = bl.source('motes')
        body = src[src.index('  float bokeh_area('):src.index('  void empty_out(')]
        # evaluate the same integral in Python by translating the few lines (kept in step by this test)
        self.assertIn('for (int k = 0; k < 96; k++)', body)
        for blades, cat, an, fall in ((0, (0, 0), 0.0, 0.0), (6, (0, 0), 0.0, 0.0), (0, (0.2, -0.1), 0.0, 0.0),
                                      (7, (0.15, 0.1), 0.5, 0.0), (0, (0, 0), 0.0, 0.6), (9, (0, 0), -0.3, -0.5)):
            R = 14.0
            area = _bokeh_area(R, blades, cat, an, fall)
            img = one_mote(R, 80.0, 60.0, blades=blades, cat=cat, anamorphic=an, falloff=fall)
            # one_mote normalised by the drawn sum: compare that with the analytic area instead
            drawn = run.run('draw', dict(blades=blades, hdr_blades=0, anamorphic=an, hdr_anamorphic=0.0, bokeh_rot=0.0,
                                         bokeh_falloff=fall, shutter_shape=0, alpha_mode=0, matte_gain=1.0, held=1),
                            {'surf': np.zeros((120, 160, 4), np.float32), 'motes': _mote_state(R, cat, 1.0 / area),
                             'bins': _bins(160, 120), 'rows': _rows()})[..., 0].sum()
            self.assertAlmostEqual(drawn, 1.0, delta=0.02, msg=(blades, cat, an, fall, drawn))
            self.assertAlmostEqual(img[..., 0].sum(), 1.0, delta=1e-4)


def _mote_state(R, cat, inv):
    motes = np.zeros((bl.STATE_H, bl.STATE_W, 4), np.float32)
    for c in range(bl.STATE_COLS):
        motes[:, c] = [1e9, 1e9, -1e9, -1e9]
    ext = R * 1.6 + 3
    motes[0, 0] = [80 - ext, 60 - ext, 80 + ext, 60 + ext]
    motes[0, bl.STATE_COLS] = [80, 60, 80, 60]
    motes[0, 2 * bl.STATE_COLS] = [R, 1 + 32, 0.0, inv]
    motes[0, 3 * bl.STATE_COLS] = [0.0, 0.0, cat[0], cat[1]]
    u = unit_light(1, 0)
    motes[0, 4 * bl.STATE_COLS] = [u, u, u, 1.0]
    return motes


def _bokeh_area(R, blades, cat, an, fall):
    """The motes kernel's bokeh_area, in Python (the same 96-angle integral)."""
    import math
    sa = math.sqrt(max(1.0 + an, 0.05))
    rot = 0.25
    aa = min(max(1.5 / R, 0.04), 0.5)
    sg = 2 * math.pi / blades if blades >= 3 else 1.0
    cx, cy = cat
    cc = cx * cx + cy * cy
    s = 0.0
    for k in range(96):
        th = (k + 0.5) * 2 * math.pi / 96
        ux, uy = math.cos(th), math.sin(th)
        sx, sy = ux * sa, uy / sa
        slen = math.hypot(sx, sy)
        g = 1.0
        if blades >= 3:
            a = math.atan2(-sy, sx) + rot
            ang = a - sg * math.floor(a / sg) - sg / 2
            g = 0.65 * math.cos(ang) / math.cos(sg / 2) + 0.35
        slope = slen * g / R
        rho = 1 / slope
        if cc > 0:
            uc = abs(ux * cx + uy * cy)
            rho = min(rho, max(R * (-uc + math.sqrt(max(uc * uc + 1 - cc, 0))), 0))
        r2 = rho * rho
        s += r2 * (0.5 + 0.1 * aa * aa) + fall * (0.25 * r2 - slope * slope * r2 * r2 * 0.25)
    return s * 2 * math.pi / 96


@unittest.skipUnless(HAVE_CXX, 'needs g++ to compile the kernels')
class Surface(unittest.TestCase):
    """What each pixel sees, from a synthetic pass written like production ones (premultiplied position and depth,
    an unfiltered depth, coverage), against the reference's build_surface."""

    def test_surface_like_the_reference(self):
        import synth
        for zdepth in (False, True):
            d = tempfile.mkdtemp()
            ch, attrs = synth.render(160, 90, 4, zdepth=zdepth)
            exr.write(os.path.join(d, 'u.0101.exr'), ch, attrs=attrs, half=False)
            sh = Shot(os.path.join(d, 'u.####.exr'))
            s = sh.surface(101)
            pos, depth, nrm, alb = pipeline.pass_images(sh, 101)
            cam = sh.camera(101)
            p = pipeline.camera_params(cam, 'cam')
            facts = run.run('facts', p, {'space': np.zeros((1, 3, 4), np.float32), 'pos': pos, 'depth': depth})
            info = s.info
            self.assertEqual(bool(facts[0, 0, 0]), info['position_premultiplied'])
            self.assertEqual(bool(facts[0, 1, 0]), info['depth_kind'].startswith('depth along the view'))
            self.assertEqual(bool(facts[0, 1, 1]), info['depth_premultiplied'])
            surf = pipeline.up(run.run('surface', dict(p, p_premult=-1, z_kind=-1, z_premult=-1),
                                       {'pos': pos, 'depth': depth, 'facts': facts}))
            some = s.cov > 0
            rel = np.abs(surf[..., 0] - s.dist)[some] / np.maximum(s.dist[some], 1e-6)
            self.assertLess(np.median(rel), 1e-5)
            self.assertLess(np.percentile(rel, 99), 1e-3)
            np.testing.assert_allclose(surf[..., 1], s.cov, atol=1e-6)
            agree = ((surf[..., 3] > 0.5) == s.clean).mean()
            self.assertGreater(agree, 0.995)


if __name__ == '__main__':
    unittest.main()
