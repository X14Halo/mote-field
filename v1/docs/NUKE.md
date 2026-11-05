# MoteField in Nuke 15

MoteField is one node. You paste it into a script and use it. There's nothing to install, no `NUKE_PATH`, nothing in
`.nuke`, and it never writes a file. It works in plain Nuke 15 as well as NukeX.

Its output is the dust as an ordinary image, with the shot's depth of field and motion blur, and held out by the set.
Look at it in the viewer, then **plus** it onto the comp. To **over** it instead, set *Alpha* to *matte*.

## Get the node into a script

Pick one:

- Drag `dist/MoteField.nk` from a file browser into the Node Graph.
- Open `dist/MoteField.nk` in a text editor, copy all of it, and paste it into the Node Graph.
- Use *File > Import Script*.

If a Read is selected when you paste, it becomes the node's **utility** input. To keep the node handy, put it in a
ToolSet or a shared template script. That's still nothing to install.

### Is BlinkScript OK here? (once per machine or Nuke version)

`dist/MoteField_check.nk` is a two-node script. Paste it in and view **MoteField_check**:

- dots plus a **green** square in the corner mean it's ready;
- a **red** square means this machine's integer maths differs: tell us.

Try it with *Use GPU if available* on and off.

## Use it

1. **Connect the utility pass** Read to the **utility** input. The node then:
   - finds the layers: position, normals, depth, unfiltered depth, coverage and surface colour;
   - takes the frame range from the Read;
   - reads the camera from the EXR header on every frame. This covers the lens and shutter too, including extras
     inside JSON header attributes;
   - fits the dust box to the set.

   The line at the top of the panel says what it found.
2. **Optional inputs:**
   - **plate**, to judge the look: set *Output* to *dust over plate*.
   - **colour**, a pass with a surface colour layer, such as a mattes pass, so bounce light takes the set's colours.
     If the utility pass has the layer itself, you don't need this input.
3. **Press Check shot.** It lists the layers it used and how it read the pass (premultiplied or not, depth along the
   ray or the view axis), the camera, the scene-unit sanity, and the mote count.
4. **Aim the key light** with *Light heading* and *Light elevation*. Then tune with the four dials: *Amount*,
   *Brightness*, *Air* and *Steady*. Everything else is on the *Dust & air* and *Light & lens* tabs.
5. **Comp it:** Merge (plus) the node onto your comp.

## How it renders

Inside the node are BlinkScript kernels. Plain Nuke runs a kernel that comes saved in a script, but can't edit one.
They do the whole job each frame:

- **where every mote is.** This comes straight from the time, with no simulation and no cache, so any frame renders
  alone, in any order, on any machine;
- **how the light reaches it.** Shadows are found by marching toward the light through what the camera sees. With
  *Light only through openings* on, light only gets in where the pass shows empty sky;
- **bounce light** off the sunlit set;
- **holdouts** against the unfiltered depth, weighted by coverage;
- **drawing**, with the lens: small blur as a Gaussian integrated over each pixel, so motes don't flicker, and larger
  blur as the aperture (blades, cat-eye, anamorphic squeeze), all along each mote's motion-blur streak.

*Use GPU* is on by default. It's much faster on a GPU, and the dust is the same either way: the random numbers are
exact integer maths on every machine.

A render is just a render. Write nodes, the farm and `nuke -x` all work as they would for any other node, and Nuke's
own viewer and disk caching apply.

## The Shot tab

- **Layers:** if a layer was picked wrong, choose it here, or press *Find layers* to look again.
- **Pass handling:** *Positions premultiplied*, *Depth is distance along the view axis* and *Depth premultiplied* are
  worked out from the pass by default (*auto*). Override them if the check says something surprising.
- **Camera:** the camera knobs are what was read from the header, keyed per frame if the camera moves. *Read camera*
  (on the first tab) reads them again, for example after the Read changes to a new version of the pass. The node also
  checks this when you open its panel.
- **Repair node:** relinks the internal kernels to the knobs. You shouldn't need it.

## Limits for now

- Embers, hide shapes and things moving through the dust come in later phases.
- Up to 65,536 motes per node. The check says when density times box size asks for more. Use a second node with
  another seed for more.
- Shadows only know the surfaces the camera sees on the current frame: off-screen geometry casts none.
