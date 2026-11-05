import hashlib
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motefield import rng
from motefield.settings import Layer, Settings


def engine(**shot):
    return Settings(shot=Layer('shot', shot)).engine(1001)


def digest(*arrays):
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()[:16]


class Hash(unittest.TestCase):
    def test_numpy_matches_plain_python(self):
        vals = np.array([0, 1, 2, 12345, 2 ** 31, 2 ** 32 - 1, 0xDEADBEEF, 7919], np.uint64)
        self.assertEqual([int(x) for x in rng.pcg_np(vals)], [rng.pcg(int(v)) for v in vals])
        idx = np.arange(1000, dtype=np.uint32)
        got = rng.hash_np(np.uint32(42), rng.DUST, idx, 5)
        self.assertEqual([int(x) for x in got[:50]], [rng.hash_words(42, rng.DUST, i, 5) for i in range(50)])

    def test_uniforms_are_24_bit_and_in_range(self):
        h = rng.hash_np(np.uint32(1), rng.DUST, np.arange(200000, dtype=np.uint32))
        u = rng.uniform_np(h)
        self.assertTrue((u >= 0).all() and (u < 1).all())
        self.assertTrue((u.astype(np.float32).astype(np.float64) == u).all())   # exact in float32
        self.assertAlmostEqual(u.mean(), 0.5, delta=0.003)
        self.assertAlmostEqual(u.var(), 1 / 12.0, delta=0.002)
        counts = np.bincount((u * 16).astype(int), minlength=16)
        self.assertLess(counts.max() / counts.min(), 1.05)

    def test_neighbouring_seeds_and_indices_are_unrelated(self):
        a = rng.uniform_np(rng.hash_np(np.uint32(1), rng.DUST, np.arange(5000, dtype=np.uint32)))
        b = rng.uniform_np(rng.hash_np(np.uint32(2), rng.DUST, np.arange(5000, dtype=np.uint32)))
        self.assertLess(abs(np.corrcoef(a, b)[0, 1]), 0.05)
        self.assertLess(abs(np.corrcoef(a[:-1], a[1:])[0, 1]), 0.05)


class ExactMath(unittest.TestCase):
    xs = [1e-6, 0.001, 0.37, 0.5, 0.7071, 0.9999, 1.0, 1.5, 2.0, 7.3, 60.0, 1234.5, 1e8]

    def test_accuracy(self):
        for x in self.xs:
            self.assertAlmostEqual(rng.xlog(x) / math.log(x) if x != 1.0 else 1.0, 1.0, delta=4e-16 * 8)
            for y in (-20.0, -3.3, -0.2, 0.0, 0.2, 1.0, 5.7, 30.0):
                self.assertLess(abs(rng.xexp(y) / math.exp(y) - 1), 4e-15)
            for b in (0.0, 0.5, 2.4, 3.1):
                self.assertLess(abs(rng.xpow(x, b) / x ** b - 1), 1e-13)
        self.assertEqual(rng.xpow(0.0, 2.4), 0.0)
        self.assertEqual(rng.xexp(0.0), 1.0)
        self.assertEqual(rng.xlog(1.0), 0.0)

    def test_numpy_versions_match_scalar_bit_for_bit(self):
        x = np.array(self.xs)
        self.assertEqual(rng.xlog_np(x).tobytes(), np.array([rng.xlog(v) for v in self.xs]).tobytes())
        y = np.linspace(-25, 25, 501)
        self.assertEqual(rng.xexp_np(y).tobytes(), np.array([rng.xexp(v) for v in y]).tobytes())
        u = np.array([0.0, 1e-7, 0.25, 0.5, 0.999])
        self.assertEqual(rng.xpow_np(u, 2.4).tobytes(), np.array([rng.xpow(v, 2.4) for v in u]).tobytes())


class Modes(unittest.TestCase):
    def test_tables(self):
        for seed in (1, 2, 99):
            for (which, count), t in zip(rng.MODE_SETS, rng.mode_tables(seed)):
                self.assertEqual(t.shape, (count, 8))
                self.assertEqual(t.dtype, np.float32)
                K, A = t[:, :3].astype(np.float64), t[:, 3:6].astype(np.float64)
                kmag = np.linalg.norm(K, axis=1)
                khat = K / kmag[:, None]
                np.testing.assert_allclose((A * khat).sum(1), 0, atol=1e-6)          # divergence-free
                np.testing.assert_allclose(np.linalg.norm(A, axis=1), math.sqrt(6.0 / count), rtol=1e-6)
                self.assertTrue(((t[:, 6] >= 0) & (t[:, 6] < 2 * math.pi + 1e-6)).all())
                np.testing.assert_allclose(t[:, 7] / kmag, np.clip(t[:, 7] / kmag, 0.4, 1.0), rtol=1e-6)
                # wave numbers spread around 2 pi / 1 m like the 0.8 log-normal
                self.assertTrue(0.6 < np.exp(np.log(kmag / (2 * math.pi)).std()) < 1.8)

    def test_tables_depend_only_on_the_seed(self):
        a, b, c = rng.mode_tables(1), rng.mode_tables(1), rng.mode_tables(2)
        self.assertEqual(a[0].tobytes(), b[0].tobytes())
        self.assertNotEqual(a[0].tobytes(), c[0].tobytes())


class DustStart(unittest.TestCase):
    def test_count_and_bounds(self):
        e = engine(box_size=(400, 300, 500), density=100)
        s = rng.dust_start(e)
        self.assertEqual(s['n'], 6000)                                 # 4 x 3 x 5 m x 100 per m3
        for c, half in enumerate((2.0, 1.5, 2.5)):
            self.assertTrue((np.abs(s['local'][:, c]) <= half).all())
        self.assertEqual(rng.mote_count(engine(box_size=(400, 300, 500), density=100, master_amount=2.0))[0], 12000)
        self.assertEqual(rng.mote_count(engine(box_size=(400, 300, 500), density=1e5, max_motes=5000))[0], 5000)

    def test_more_density_adds_motes_and_moves_none(self):
        small = rng.dust_start(engine(density=50, box_size=(500, 300, 500)))
        big = rng.dust_start(engine(density=150, box_size=(500, 300, 500)))
        n = small['n']
        for k in ('local', 'h', 'kind', 'settle', 'tau'):
            self.assertEqual(big[k][:n].tobytes(), small[k].tobytes(), k)

    def test_recipe_changes_types_not_places(self):
        a = rng.dust_start(engine(flakes=10))
        b = rng.dust_start(engine(flakes=40))
        self.assertEqual(a['local'].tobytes(), b['local'].tobytes())
        self.assertGreater((b['kind'] == rng.FLAKE).sum(), (a['kind'] == rng.FLAKE).sum())

    def test_mix(self):
        s = rng.dust_start(engine(flakes=20, fibers=5, fluff=2, density=200, box_size=(1000, 500, 1000)))
        frac = np.bincount(s['kind'], minlength=4) / float(s['n'])
        np.testing.assert_allclose(frac[[rng.FLAKE, rng.FIBER, rng.FLUFF]], [0.20, 0.05, 0.02], atol=0.005)
        g = s['kind'] == rng.GRIT
        self.assertTrue((s['grit_d'][g] >= 4.0 - 1e-4).all() and (s['grit_d'][g] <= 60.0 + 1e-3).all())
        self.assertTrue((s['grit_d'][~g] == 0).all())
        self.assertTrue(set(np.unique(s['tau'])) <= {np.float32(0.22), np.float32(0.35)})

    def test_golden(self):
        # Fixed so an accidental change to the starting state is caught. If you change it on purpose, bump
        # MODEL_VERSION and update these. Must be the same under numpy 1.23 (Nuke 15) and numpy 2.
        s = rng.dust_start(engine(seed=7, density=120, box_size=(300, 200, 300), flakes=25, fibers=3, fluff=1))
        self.assertEqual(s['n'], 2160)
        got = digest(s['local'], s['h'], s['kind'], s['settle'], s['tau'], s['grit_d'])
        tables = digest(*rng.mode_tables(7))
        self.assertEqual((got, tables), GOLDEN)


GOLDEN = ('9ff46b53047128c4', 'b5da1ba9d93d1d72')

if __name__ == '__main__':
    unittest.main()
