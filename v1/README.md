# MoteField v1

Floating dust motes and fire embers for a shot, added in comp from the shot's utility pass. A rebuild of MoteField 0.8
(`../nuke/`), which stays live alongside for comparison.

**Status: phase 1 of 7, foundations.** Settings, the settings files and the randomness are done and tested. There is
nothing to render yet; phase 2 (the Python core) starts writing EXRs.

## Layout

```
motefield/            the Python package: plain Python 3.10+ and numpy 1.23+ (Nuke 15, the farm, the browser)
  schema.py           every setting, in one table
  settings.py         show -> look -> shot files, master dials, animated values, cache keys, farm snapshots
  migrate.py          reading MoteField 0.8 settings files
  rng.py              integer-hash randomness and the dust's starting state
  curves.py           keyed values (Catmull-Rom), shared by animated settings and shapes
  cli.py              python -m motefield ...
viewer/js/
  mfseed.js           the JavaScript twin of rng.py (bit-identical)
  schema.json         generated from schema.py for the viewer's panel
docs/SETTINGS.md      generated settings reference
tests/                python -m pytest tests   (or: python -m unittest discover -s tests)
```

## The rules the code keeps

**One table.** `schema.py` lists every setting with its type, default, label, help, panel, basic/advanced level, units,
ranges, the settings layer it normally lives in, and what it *affects*:

| affects | a change redoes | examples |
|---|---|---|
| shot | everything | utility pass, frame range, units |
| prep | light map, bounce lights, moving things, and the simulation | light direction, shadows |
| sim | the simulation | density, air, embers, Amount, Air |
| render | the render (not animatable) | lens, holdouts, edge softness, output layers |
| look | the render (may be keyed) | brightness, tint, light intensity and colour, Brightness |
| node | nothing in the cached frames | cache folder, node output, plate |
| view | nothing outside the viewer | viewer speed switches, hold camera |

The Nuke knobs, the viewer panel, the docs and the cache keys are all generated from it.

**Three layers.** A value comes from the defaults, then the *show* file (studio conventions: units, the studio lens and
shutter, output layers), then the *look* file (the sequence's shareable look), then the *shot* file. A shot file names
its look and show by relative path. `python -m motefield snapshot` freezes the whole stack into one file for the farm,
so a look edited mid-render can't change frames mid-shot.

**Master dials multiply, never overwrite.** Amount, Brightness and Air multiply what they scale (`schema.MASTERS`), so
a look's recipe survives any amount of dialling in a shot. Steady is the fourth dial.

**Cache keys.** `prep_key`, `sim_key` and `cache_key` hash exactly the settings each depends on, plus `MODEL_VERSION`,
which is bumped only when the maths changes. A docs-only release never makes caches stale. A hide shape never
re-simulates, but a push shape does.

**Randomness is a hash, not a stream.** Every random number is a PCG hash of (seed, what it's for, which one, which
number), so:
- mote *i* is always mote *i*: raising the density adds motes and moves none;
- the viewer builds the starting dust itself in about 60 ms, with no Python;
- Python under numpy 1.23 and numpy 2, and the JavaScript, all give the same bytes (tested).

Values that need exp, log or pow use versions built from `+ - * /` and `sqrt`, which IEEE 754 makes exact everywhere,
and are then rounded to float32.

## Commands

```
python -m motefield schema      regenerate viewer/js/schema.json and docs/SETTINGS.md (run after editing schema.py)
python -m motefield show S      a shot's settings and where each value comes from
python -m motefield keys S      its cache keys
python -m motefield diff A B    what differs, and which keys that changes
python -m motefield snapshot S  freeze for the farm
python -m motefield migrate OLD [--split]   convert a 0.8 settings file
python -m motefield doctor      what this Python has
```

## Next

2. Python core: EXR reading (coverage-aware P), shot check, prep, dust simulation, CPU renderer (stamping + tiled
   FFT), output layers, `render`.
3. GPU renderer and the Python/GPU parity tests.
4. Viewer UX.
5. Embers, shapes, moving things.
6. The Nuke node, live link, stale-cache badge, pipeline hooks, farm bake.
7. The browser demo page, artist quick start, TD guide.
