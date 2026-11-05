"""The viewer's JavaScript (viewer/js/mfseed.js) must build exactly the same starting dust as the Python.

Runs tests/js/dump_seed.js with Node and compares every byte. Skipped when Node isn't installed."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from motefield import rng
from motefield.settings import Layer, Settings

NODE = shutil.which('node')

CASES = [
    dict(),
    dict(seed=7, density=120, box_size=(300, 200, 300), flakes=25, fibers=3, fluff=1),
    dict(seed=2 ** 31 - 1, density=400, box_size=(760, 330, 900), size_min=1, size_max=300, size_bias=0.5),
    dict(seed=0, density=180, flakes=0, fibers=0, fluff=0, size_bias=4.0, units='metres', box_size=(9, 3, 7)),
    dict(seed=12, density=900, box_size=(120, 80, 140), flakes=60, fibers=12, fluff=6, master_amount=2.5,
         units='millimetres'),
    dict(seed=5, density=1e5, max_motes=3000, box_size=(1000, 1000, 1000), units='inches'),
]
MODE_SEEDS = [0, 1, 7, 42, 2 ** 31 - 1]


def engine(shot):
    e = Settings(shot=Layer('shot', shot)).engine(1001)
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in e.items() if isinstance(v, (int, float, str, tuple))}


@unittest.skipIf(NODE is None, 'Node is not installed')
class JavaScriptParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.scalar = [float(x) for x in np.concatenate([np.linspace(-30, 30, 241), np.geomspace(1e-9, 1e9, 181)])]
        cls.ints = [int(x) for x in np.random.default_rng(3).integers(0, 2 ** 32, 2000, dtype=np.uint64)]
        cases = dict(dust=[dict(e=engine(c), n=None) for c in CASES] + [dict(e=engine(CASES[1]), n=50)],
                     modes=MODE_SEEDS, scalar=cls.scalar, ints=cls.ints)
        path = os.path.join(cls.dir, 'cases.json')
        with open(path, 'w') as fh:
            json.dump(cases, fh)
        subprocess.run([NODE, os.path.join(HERE, 'js', 'dump_seed.js'), path, cls.dir], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def read(self, name, dtype):
        with open(os.path.join(self.dir, name), 'rb') as fh:
            return np.frombuffer(fh.read(), dtype)

    def test_hash(self):
        self.assertEqual(list(self.read('pcg.bin', np.uint32)), [rng.pcg(v) for v in self.ints])
        self.assertEqual(list(self.read('hash.bin', np.uint32)),
                         [rng.hash_words(v, 0x44555354, v ^ 0x5bd1e995, 7) for v in self.ints])

    def test_exact_math(self):
        xs = self.scalar
        self.assertEqual(self.read('xexp.bin', np.float64).tobytes(), np.array([rng.xexp(x) for x in xs]).tobytes())
        self.assertEqual(self.read('xlog.bin', np.float64).tobytes(),
                         np.array([rng.xlog(x) if x > 0 else 0.0 for x in xs]).tobytes())
        self.assertEqual(self.read('xpow.bin', np.float64).tobytes(),
                         np.array([rng.xpow(abs(x), 0.37 + abs(x) % 3) for x in xs]).tobytes())

    def test_mode_tables(self):
        for seed in MODE_SEEDS:
            for j, t in enumerate(rng.mode_tables(seed)):
                self.assertEqual(self.read('modes%d_%d.bin' % (seed, j), np.float32).tobytes(), t.tobytes(),
                                 'seed %d set %d' % (seed, j))

    def test_dust_start(self):
        dtypes = dict(local=np.float32, h=np.float32, kind=np.uint8, settle=np.float32, tau=np.float32,
                      grit_d=np.float32)
        for i, c in enumerate(CASES + [CASES[1]]):
            n = 50 if i == len(CASES) else None
            s = rng.dust_start(Settings(shot=Layer('shot', c)).engine(1001), n)
            with open(os.path.join(self.dir, 'dust%d_meta.json' % i)) as fh:
                meta = json.load(fh)
            self.assertEqual(meta['n'], s['n'], 'case %d' % i)
            self.assertEqual(meta['size'], list(s['size']), 'case %d' % i)
            for k, dt in dtypes.items():
                self.assertEqual(self.read('dust%d_%s.bin' % (i, k), dt).tobytes(), s[k].tobytes(),
                                 'case %d: %s' % (i, k))


if __name__ == '__main__':
    unittest.main()
