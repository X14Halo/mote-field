# MoteField v1

Floating dust motes and fire embers for a shot, added in comp from the shot's utility pass. The node shows them in the
Nuke viewer as an ordinary image, with depth of field and motion blur, to plus or over onto the comp. A rebuild of
MoteField 0.8 (`../nuke/`), which stays live alongside for comparison.

**Status: phase 3 of 9, the Nuke node.** The Python core (settings, shot loader and check, motion, light, renderer,
frame cache) is done and tested; the Nuke node is built and tested against a stand-in for Nuke's Python, and next
needs trying in Nuke 15 ([docs/NUKE.md](docs/NUKE.md)). Progress page: https://x14halo.github.io/mote-field/v1/

**In Nuke:** add this folder to `NUKE_PATH`. Its `init.py` and `menu.py` add *Other > MoteField* and the callback that
renders frames during renders.

## Layout

```
motefield/            the Python package: plain Python 3.10+ and numpy 1.23+ (Nuke 15, the farm, the browser)
  schema.py           every setting, in one table
  settings.py         show -> look -> shot files, master dials, animated values, cache keys, farm snapshots
  migrate.py          reading MoteField 0.8 settings files
  rng.py              integer-hash randomness and the dust's starting state
  curves.py           keyed values (Catmull-Rom), shared by animated settings and shapes
  exr.py              EXR reading (multi-part scanline; a library for the rest) and writing
  camera.py           the shot camera and lens, from the EXR header
  shot.py             the utility pass: layers by role, coverage, premultiplied edges, unfiltered depth
  check.py            the shot check
  standin.py          the built-in stand-in room, made in memory
  place.py            Fit box, and what is under a pixel
  light.py            the key light: shadows, light map, bounce lights
  motion.py           where every mote is at any time, with no simulation state
  splat.py            drawing blurred points with the lens's bokeh
  render.py           a frame of dust through the shot's lens
  cache.py            the node's frame cache: folders named after the settings and inputs
  nodelink.py         knobs, look files and the artist's edits -> settings (no Nuke needed)
  nuke_node.py        the Nuke node: knobs, internal graph, background renderer, render callback
  pipeline.py         studio hooks (cache root, header and layer names), overridable from motefield_pipeline
  cli.py              python -m motefield ...
init.py, menu.py      what Nuke runs when this folder is on NUKE_PATH
viewer/js/
  mfseed.js           the JavaScript twin of rng.py (bit-identical)
  schema.json         generated from schema.py for the viewer's panel
docs/SETTINGS.md      generated settings reference
docs/NUKE.md          installing and trying the node in Nuke 15
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
python -m motefield check P     the shot check on a utility pass (P: a frame pattern or a shot settings file)
python -m motefield render S    render frames into the node's cache (e.g. to fill it ahead on the farm)
python -m motefield doctor      what this Python has
python tools/build_page.py      run the tests, then rebuild index.html (the progress page)
```

## Next

3. The Nuke node: try it in Nuke 15 and fix what real Nuke disagrees with.
4. The GPU live viewer, matched to the render.
5. Ease of use: first run, clicking on the plate, diagnostic views, presets, undo.
6. Matched edges (holdouts blurred like the plate's own edges) and optional haze / light shafts.
7. Embers and shapes.
8. Moving things pushing the dust.
9. The browser demo page, artist quick start, TD guide.
