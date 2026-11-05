# MoteField v1

MoteField adds floating dust motes (and later fire embers) to a shot in comp, from the shot's utility pass. It is a
rebuild of MoteField 0.8 (`../nuke/`), which stays live alongside it for comparison.

**In Nuke 15 it is one node, and nothing gets installed.** Paste or drag [`dist/MoteField.nk`](dist/MoteField.nk) into
a script, connect the utility pass Read, and view it. The node shows the dust in the viewer with depth of field and
motion blur, held out by the set, to plus or over onto the comp. It writes no files and works in plain Nuke as well as
NukeX. See [docs/NUKE.md](docs/NUKE.md).

**Status: phase 4 of 9, the drop-in node.** All the rendering is inside the node as BlinkScript kernels: plain Nuke
runs a kernel saved in a script but can't edit one. The kernels are tested against the Python reference renderer, and
the node's script and embedded Python are tested against a stand-in for Nuke. Next: trying it on real shots in Nuke 15.

Progress page: https://x14halo.github.io/mote-field/v1/

## Layout

```
dist/
  MoteField.nk          the node: paste or drag into Nuke 15 (built by node/build_node.py; don't edit by hand)
  MoteField_check.nk    a two-node check that BlinkScript runs and does exact integer maths on a machine
node/
  kernels/*.blink       the node's BlinkScript kernels (shared code in lib_*.blink and body_*.blink)
  blinklib.py           puts the kernels together: includes, the shared param table, the internal image sizes
  nodepy.py             the Python inside the node: wiring, links, layers, the camera from the header, Fit box, check
  build_node.py         builds dist/MoteField.nk: knobs from the settings schema, the internal graph, the kernels
  build_check.py        builds dist/MoteField_check.nk
  nk.py                 quoting for .nk text
motefield/              the Python reference: the same dust in numpy, used to test the kernels and by the command line
  schema.py             every setting, in one table (labels, help, defaults, ranges for the node's knobs too)
  settings.py, migrate.py, curves.py   settings layers, reading MoteField 0.8 settings, keyed values
  rng.py                integer-hash randomness and the dust's starting state
  exr.py, camera.py, shot.py, check.py   reading utility passes, the camera from the header, the shot check
  standin.py            the built-in stand-in room (for tests and the page)
  light.py, motion.py, splat.py, render.py, place.py   the reference renderer
  cli.py                python -m motefield ...
tests/
  blink/                a stand-in for Blink: compiles the kernels as C++ and runs them (needs g++)
  nkload.py             reads .nk text the way Nuke does, into stand-in nodes, to test the node without Nuke
  test_blink.py         kernels against the reference: random numbers, motion, light, the picture, bokeh
  test_node.py          the node: quoting, wiring, links, layers, camera, Fit box, check
docs/NUKE.md            using the node in Nuke 15
docs/SETTINGS.md        every setting (generated)
```

## The rules the code keeps

**One table.** `schema.py` lists every setting with its type, default, label, help, level, units and ranges. The
node's knobs, the docs and the reference renderer all come from it.

**Randomness is a hash, not a stream.** Every random number is a PCG hash of (seed, what it's for, which one, which
number). The kernels do the hash in 16-bit halves, so no integer ever overflows and every CPU and GPU gives the same
bits as the Python reference (tested). This means:

- mote *i* is always mote *i*, and raising the density adds motes without moving any;
- the dust is the same on everyone's machine.

**No simulation state.** Each mote's position comes straight from the time, as exact integrals of the air's swirls
along its path. Any frame renders alone, in any order, on any machine, on the GPU or the CPU.

**Master dials multiply, never overwrite.** Amount, Brightness and Air multiply what they scale (`schema.MASTERS`).
In the node, that's done by the expressions linking the kernels to the knobs. Steady is the fourth dial.

## Commands

```
python3 node/build_node.py       rebuild dist/MoteField.nk (after changing kernels, nodepy.py or the schema)
python3 node/build_check.py dist/MoteField_check.nk
python -m motefield schema       regenerate docs/schema.json and docs/SETTINGS.md (after editing schema.py)
python -m motefield check P      the shot check on a utility pass (P: a frame pattern)
python -m motefield migrate OLD  convert a 0.8 settings file
python -m pytest tests           all tests (the kernel tests need g++)
python tools/build_page.py       run the tests, then rebuild index.html (the progress page)
```

## Next

5. Ease of use: aiming the light by clicking on the plate, diagnostic views, presets.
6. Matched edges (holdouts blurred like the plate's own edges) and optional haze / light shafts.
7. Embers and shapes.
8. Moving things pushing the dust.
9. Release: artist quick start and TD guide.
