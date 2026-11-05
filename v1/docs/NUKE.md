# MoteField in Nuke 15

The node's output is the dust (and later the embers) as an ordinary image. Look at it in the viewer, then **plus** it
onto the comp, or **over** it with *Alpha* set to *matte*. There are no files to manage and no render buttons to
press: frames appear as you work, and any render of the comp makes the frames it needs.

## Install

MoteField v1 is the `v1` folder of the repository. It needs only Python and numpy, which Nuke 15 ships with.

Add the `v1` folder to `NUKE_PATH`. That's all: its `init.py` installs the render callback (in the GUI and in batch
renders on the farm), and its `menu.py` adds **Other > MoteField** to the Nodes menu. If your pipeline sets up
plug-ins another way, these are the two lines that matter:

```python
import motefield.nuke_node; motefield.nuke_node.register()                                # init.py
nuke.menu('Nodes').addCommand('Other/MoteField', 'import motefield.nuke_node as m; m.create()')   # menu.py
```

Don't put MoteField 0.8.3 (`nuke/motefield.py`) on `NUKE_PATH` at the same time: both are called `motefield`, and
whichever comes first would be loaded.

Check the Python Nuke will use with `python -m motefield doctor`, run with Nuke's Python, or with this in the Script
Editor:

```python
import motefield.cli; motefield.cli.main(['doctor'])
```

## Try it

1. **Create the node:** Other > MoteField, or in the Script Editor `import motefield.nuke_node as m; m.create()`.
   With a Read selected, it becomes the node's utility input.
2. **The stand-in first:** set *Stand-in shot* to *wide* and view the node. The frame you're on appears in a second or
   two, then the frames around it. The status line says what it's doing. Try *close-up* for big bokeh.
3. **Your shot:**
   - Connect the utility pass Read to the node's **utility** input (input 1), or type its path in *Utility pass*.
   - Put the mattes pass in *Extra passes*, for surface colour (bounce light) and ID mattes.
   - Set *First frame* and *Last frame*.
   - Press **Shot check**: it lists what it found in the pass and anything to fix.
   - Press **Fit box to shot** to place the dust.
   - Set the key light in the Place tab (heading and elevation).
   - Tune with the four dials, then the Look tab.
4. **Comp it:** Merge (plus) the node over your comp. For an over, set *Alpha* to *matte* and merge with over. *Node
   output* can also show the dust merged over input 0 (the plate), or a holdout check.

## Renders and the farm

Before each frame of any Write render, locally or with `nuke -x` on the farm, the node renders that frame if it isn't
already there. A farm machine needs `motefield` on its `NUKE_PATH` too; the node's `onCreate` registers the callback
when the script loads.

Frames live in folders named after every setting that changes a pixel, so a frame is never out of date. Where they go:

- the node's *Cache folder* (Deliver tab), if set;
- otherwise `$MOTEFIELD_CACHE`;
- otherwise Nuke's temp folder.

Set *Cache folder* to a shared location and the farm reuses the frames you already rendered while working. A frame
comes out the same on any machine: tested bit-for-bit between Nuke 15's numpy and numpy 2.

## Settings files

*Look file* points at a sequence's shared look. Knobs show its values until you change them in the node; from then on
your value stays, even through *Reload files*. *Save look...* writes this node's look settings to a file for others.
*Show file* holds the show's conventions (units, lens, shutter, channels).

## Not yet tried in Nuke itself

The node was built and tested against a stand-in for Nuke's Python module, not in Nuke. These Nuke calls are the ones
it relies on; if any misbehaves, the Script Editor will show a traceback:

- `nuke.nodes.Group`, `Read`, `Merge2`, `Expression`, `Switch`, `Input`, `Output`, and `begin()` / `end()`
- knob types: `Double_Knob`, `Int_Knob`, `Boolean_Knob`, `Enumeration_Knob`, `XYZ_Knob`, `Color_Knob`,
  `File_Knob`, `String_Knob`, `Multiline_Eval_String_Knob`, `PyScript_Knob`, `Text_Knob`, `Tab_Knob`
- knob methods: `setFlag(nuke.NO_ANIMATION)`, `setValueAt`, `getValueAt`, `isAnimated`, `setExpression`
- the Read's `raw`, `on_error`, `reload`; the Group's `knobChanged` and `onCreate`
- `nuke.addBeforeFrameRender`, `nuke.executeInMainThread`, `nuke.executeInMainThreadWithResult`, `nuke.frame`

What would help most to hear back:

1. Does the node create without errors, and do frames appear in the viewer as you scrub (stand-in, then your shot)?
2. Does a Write render of a comp with the node work, both in the GUI and with `nuke -x`?
3. Does the look file behave as described above?
4. Any traceback from the Script Editor.
