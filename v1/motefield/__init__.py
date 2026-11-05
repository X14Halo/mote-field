"""MoteField: floating dust motes and fire embers for a shot, added in comp from the shot's utility pass.

The package is plain Python 3.10+ and numpy (1.23 or later), so it runs inside Nuke 15, on a farm blade and in the
browser (Pyodide) unchanged.

    schema    every setting, in one table (drives the Nuke knobs, the viewer panel, the docs and the cache keys)
    settings  show -> look -> shot settings files, master dials, animated values, hashes, v0.8.3 import
    rng       integer-hash randomness and the dust's starting state, identical in Python and JavaScript
"""

__version__ = '1.0.0a1'

# Bumped whenever a change alters rendered pixels for the same settings. It goes into every cache key and EXR header,
# so a docs-only release never marks caches as stale, and a maths change always does.
MODEL_VERSION = 1
