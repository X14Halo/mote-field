"""Compile and run the node's Blink kernels as C++ (tests only): run(kernel, params, inputs, size) -> image."""
import hashlib
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from node import blinklib            # noqa: E402
sys.path.insert(0, HERE)
import translate                     # noqa: E402

BUILD = os.path.join(tempfile.gettempdir(), 'mf_blink_build')


def build(name):
    src = blinklib.source(name)
    cpp = translate.translate(src)
    key = hashlib.sha1((cpp + open(os.path.join(HERE, 'blink.h')).read()).encode()).hexdigest()[:12]
    os.makedirs(BUILD, exist_ok=True)
    exe = os.path.join(BUILD, '%s_%s' % (name.replace('/', '_'), key))
    if not os.path.exists(exe):
        cp = os.path.join(BUILD, '%s_%s.cpp' % (name.replace('/', '_'), key))
        open(cp, 'w').write(cpp)
        r = subprocess.run(['g++', '-O2', '-fopenmp', '-std=c++17', '-I', HERE, '-Wno-unused', cp, '-o', exe],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError('compile failed for %s:\n%s' % (name, r.stderr[-6000:]))
    return exe


def save(path, img):
    img = np.ascontiguousarray(img, np.float32)
    if img.ndim == 2:
        img = np.stack([img] * 4, -1)
    if img.shape[2] < 4:
        img = np.concatenate([img, np.zeros(img.shape[:2] + (4 - img.shape[2],), np.float32)], 2)
    with open(path, 'wb') as f:
        np.array([img.shape[1], img.shape[0]], np.int32).tofile(f)
        img.astype(np.float32).tofile(f)


def load(path):
    with open(path, 'rb') as f:
        w, h = np.fromfile(f, np.int32, 2)
        return np.fromfile(f, np.float32).reshape(h, w, 4)


def run(name, params=None, inputs=None, size=None):
    """params: {name: value or tuple}; inputs: {name: (H, W, C) array, row 0 at the BOTTOM (Nuke's y)}."""
    exe = build(name)
    with tempfile.TemporaryDirectory() as d:
        job = []
        for k, v in (params or {}).items():
            vals = v if isinstance(v, (list, tuple, np.ndarray)) else [v]
            job.append('param %s %s' % (k, ' '.join(repr(float(x)) for x in vals)))
        for k, img in (inputs or {}).items():
            p = os.path.join(d, k + '.raw')
            save(p, img)
            job.append('input %s %s' % (k, p))
        if size:
            job.append('size %d %d' % size)
        out = os.path.join(d, 'out.raw')
        job.append('output ' + out)
        jp = os.path.join(d, 'job.txt')
        open(jp, 'w').write('\n'.join(job) + '\n')
        r = subprocess.run([exe, jp], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError('%s failed: %s' % (name, r.stderr))
        return load(out)
