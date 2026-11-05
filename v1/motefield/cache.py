"""The node's frame cache: rendered frames on disk, named so they can never be out of date.

Nuke's Python can't hand pixels straight to the node graph, so the node renders frames into a folder and reads them
back. The folder is named after everything that changes the pixels:

    <cache root>/<cache key>_<inputs>/dust.####.exr

  * the cache key: every setting that changes a pixel, and the model version (Settings.cache_key);
  * the inputs: the utility pass files themselves (their sizes and modification times), so a re-delivered pass
    re-renders even at the same path.

Change anything and the node simply reads from a different folder; frames already rendered for a setup are reused if
you come back to it. Old folders are tidied away (tidy).

Each frame: rgba (alpha none or a matte), plus the noholdout, dust_key and dust_fill layers when they're switched on,
as half float. The header says which MoteField made it and how.
"""

import hashlib
import os
import shutil
import time

import numpy as np

from . import MODEL_VERSION, __version__, exr, pipeline
from .shot import frame_path, split_patterns

NAME = 'dust'


def input_fingerprint(e, shot=None):
    """A short hash of the inputs a render reads: the stand-in, or the utility pass files over the shot's frames."""
    h = hashlib.sha256()
    if e['standin'] != 'none' and not e['utility']:
        size = shot.resolution() if shot is not None else ('default',)
        h.update(('standin %s %s' % (e['standin'], size)).encode())
    else:
        patterns = [e['utility']] + split_patterns(e.get('extra_passes', ''))
        for f in range(int(e['first']) - 1, int(e['last']) + 2):
            for p in patterns:
                path = frame_path(p, f)
                try:
                    st = os.stat(path)
                    h.update(('%s %d %d;' % (path, st.st_size, st.st_mtime_ns)).encode())
                except OSError:
                    h.update(('%s missing;' % path).encode())
    return h.hexdigest()[:10]


class FrameCache(object):
    def __init__(self, settings, shot=None, root=None):
        e = settings.engine(settings['first'])
        self.key = settings.cache_key()
        self.inputs = input_fingerprint(e, shot)
        self.root = root or settings['cache'] or pipeline.cache_root()
        self.folder = os.path.join(self.root, '%s_%s' % (self.key[:16], self.inputs))

    def path(self, f):
        return os.path.join(self.folder, '%s.%04d.exr' % (NAME, int(f)))

    def pattern(self):
        """The frame pattern for a Nuke Read (forward slashes, #### for the frame)."""
        return os.path.join(self.folder, NAME + '.####.exr').replace('\\', '/')

    def has(self, f):
        return os.path.exists(self.path(f))

    def missing(self, frames):
        return [f for f in frames if not self.has(f)]

    def write(self, f, out, seconds=None):
        """Writes one rendered frame (render.render_frame's dict)."""
        os.makedirs(self.folder, exist_ok=True)
        ch = {}
        rgb = out['rgb']
        for i, c in enumerate('RGB'):
            ch[c] = rgb[..., i]
        ch['A'] = out['alpha']
        for layer in ('noholdout', 'dust_key', 'dust_fill'):
            if layer in out:
                for i, c in enumerate('RGB'):
                    ch['%s.%s' % (layer, c)] = out[layer][..., i]
        attrs = {'motefield/version': __version__, 'motefield/model': MODEL_VERSION, 'motefield/cache_key': self.key,
                 'motefield/inputs': self.inputs, 'motefield/frame': int(f), 'motefield/made_by': 'cpu',
                 'motefield/numpy': np.__version__}
        if seconds is not None:
            attrs['motefield/seconds'] = float(seconds)
        return exr.write(self.path(f), ch, attrs=attrs, half=True)

    def render(self, f, renderer, force=False):
        """The frame's path, rendering it first if it isn't there yet."""
        if force or not self.has(f):
            t = time.time()
            out = renderer.frame(f)
            self.write(f, out, time.time() - t)
        return self.path(f)


def tidy(root, keep=24, protect=()):
    """Removes all but the `keep` most recently used setup folders under root (never those in protect)."""
    try:
        names = [n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)) and '_' in n]
    except OSError:
        return []
    folders = sorted(names, key=lambda n: os.path.getmtime(os.path.join(root, n)), reverse=True)
    gone = []
    for n in folders[keep:]:
        full = os.path.join(root, n)
        if full in protect:
            continue
        shutil.rmtree(full, ignore_errors=True)
        gone.append(full)
    return gone
