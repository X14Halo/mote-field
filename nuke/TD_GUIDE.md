# MoteField: TD guide and port plan

**Live tool (browser):** https://x14halo.github.io/mote-field/nuke/  
**Code:** this folder. `motefield.py` is the Nuke tool. Everything else is the fast viewer and its glue.

---

## 1. What it is

MoteField adds floating dust motes to a shot in comp, with no 3D particle render. It reads the shot's **utility pass**: world position, normals, depth and surface colour, with the camera in the EXR header. It simulates dust in a box you place in scene space and lights it with a **directional key light** (shadows from the set, plus bounce light). It renders the dust through the **shot's own lens**:

- depth of field with your bokeh controls;
- motion blur with the studio shutter (180°, backward, backwardsShape);
- depth holdouts.

**Output:** an EXR per frame with the dust in **RGBA** and the same dust with no holdouts in a **noholdout** layer. The node has a plate input and can add the dust over it itself (*Output: merged over plate*), or give the dust alone to merge with *plus*.

The simulation is deterministic: the same settings always give the same frame, in any order and on any machine. An artist's setup and a farm render therefore match.

## 2. Quick start (for whoever builds the first Nuke version)

1. **Install.** Copy this folder somewhere Nuke can see it, then in the Script Editor:
   ```python
   import sys; sys.path.append('/path/to/motefield'); import motefield; motefield.create()
   ```
2. **Try the stand-in.** Press **Create stand-in shot**. Pick *wide* or *close-up* in **Stand-in camera** first. It writes a placeholder interior (utility EXRs + a clay plate) and points the node at it.
3. **Live viewer (fast).** Press **Live viewer (GPU)**. It opens the real-time viewer, the same one as the browser version, in a Nuke panel, or in your browser if this Nuke has no QtWebEngine. It reads and writes the node's knobs, so whatever you set in the viewer is what the node renders.
4. **Your shot:**
   - **Connect it:** the plate to input 0 (*plate*) and the utility pass Read to input 1 (*utility*). With the **Utility pass** knob blank, the node uses that Read's file (through any nodes in between); or type the path with `####`. The camera comes from the EXR header automatically.
   - **Output** (MoteField tab): *dust only* (default, to merge yourself), *merged over plate* (added over input 0, like an inline filter), or *holdout check* (what the holdouts take away, in red, over the plate).
   - **Scene units:** check them. Then press **Fit box to shot** and adjust the box in the viewer's 3D view by dragging its handles.
   - **Key light:**
     - **From a Nuke light:** select the light node your Houdini light export made and press **Key light from selected Nuke light**.
     - **From a file:** use **Import lights (.json / USD)**. `mflights.py` exports the .json from Houdini; a .usd file works directly if Nuke has `pxr`.
   - **Lens:** focal length, f-stop and focus come from the header. The Lens tab can override or offset everything.
5. **See it in the comp quickly.** In the live viewer press **Render range on GPU → cache**. It writes `dust.####.exr` into the node's cache folder in seconds, and the node's Read reloads.
6. **Final frames on the farm.** Press **Export settings (farm)**. It **bakes** what every farm task would otherwise redo: the movers (if *Moving things push the dust* is on) go into the settings, and the dust and ember state every 10 frames or so into `motefield_settings.sim.npz` beside them (a few MB). Then on the farm run:
   ```bash
   python motefield.py render --settings motefield_settings.json --frames 1001-1048
   ```
   This is the reference renderer (Python + numpy, CPU only). It takes a few seconds a frame and needs no GPU or Nuke licence. Split the range any way you like: a task starts from the nearest checkpoint, and the frames are bit-for-bit the ones a single sequential render makes. For settings written another way, `python motefield.py preflight --settings <json>` bakes them in one quick job before the frames fan out.

## 3. How fast, and why

| What | Where it runs | Speed (1280×536, ~40-80k motes) |
|---|---|---|
| Live viewer: play, scrub, change any knob, fly a free camera | GPU (WebGL2) | real time |
| Render range on GPU → cache (frames for the Nuke graph) | GPU, written by Python | well under a second a frame on a workstation GPU |
| Reference / farm render (`render_frames`, CLI) | CPU (numpy) | 3-4 s a frame here, plus ~10-20 s once for pre-roll and light setup |

The viewer stays fast because the expensive parts happen on the graphics card:

- **On the GPU:** the simulation, the per-mote maths and the splatting of every mote's bokeh.
- **Once per shot in Python:** the dust's starting state, the cameras, the light map and the bounce lights. These take seconds.
- **Cached per frame:** the stand-in's lit set.

Things that keep it from bogging down:
- **Max motes** caps the count. Density × box volume is clamped.
- **Largest bokeh radius** caps the cost of huge out-of-focus motes.
- The simulation keeps a state every 5 frames, so scrubbing back costs at most 5 steps.
- Python work runs in a background thread with a Nuke progress bar, so the UI never blocks.
- Utility frames are read once and cached (the last few).

The GPU preview and the Python render agree within about 1% of total light, with the same image structure (block correlation 0.999). The GPU integrates the motion in 32-bit floats, so individual motes drift a little apart over long shots. The motion and the look are the same.

## 4. How it works

### Inputs
- **Utility pass.** Each frame is one EXR, single- or multi-part. The built-in reader does scanline NONE, RLE, ZIPS and ZIP; tiled files and PIZ, DWAA/DWAB, B44 or PXR24 go through **OpenImageIO** or the **OpenEXR** Python module when the environment has one (`_read_with_library`), and otherwise fail with a message saying so. Lossy compression (DWAA/DWAB, B44, PXR24) on the position or depth pass prints a warning: it moves positions by tens of centimetres at edges (measured on the stand-in with DWAA), which spoils holdouts.
  - Layers are found by name (`_ROLE` in `motefield.py`): world position (`pos`, `P`, `worldPosition`…), normals, depth, surface colour.
  - Only position is required; normals and surface colour improve the lighting.
  - Optional: a **Cryptomatte** layer (or an object-id channel), for objects that never hold out.
- **Camera.** Read from each frame's header: `camToWorld` / `worldToCamera`, plus focal length and aperture (or a horizontal FOV), f-stop and focus distance.
  - Optional lens metadata: blade count, cat-eye, anamorphic aspect, field curvature, full aperture.
  - Fractional frames (for the shutter) blend the neighbouring frames' cameras.
- **Plate** (optional, viewer only). Without one, the viewer shades the utility pass with the key light as a stand-in backdrop.

### Dust simulation (`DustSim`)
- **Starting state.** Motes start at random positions in the box. The count is density × volume, capped by Max motes, from a fixed seed.
- **Types.** Each mote is grit, a flake, a lint fibre or fluff, with its own size distribution.
- **Air.** A divergence-free flow made of 40 large eddy modes and 20 micro modes (random Fourier modes), plus a drift wind, thermal lift where the sun hits, and settling. Motes follow the air with a type-dependent lag and wrap around inside the box.
- **Timing.** The simulation starts *pre-roll* frames before the first frame, with *Steps per frame* sub-steps.
- **Edge fade.** Density fades out toward each face of the box. There's an overall fade amount, scaled per face.
- **Character pushers** (optional, `push`). Things that move in the shot stir the dust. They come from the utility pass alone, in three steps.
  1. **Moving samples** (`_moving_samples`). Each pixel's backward motion vector (Nuke convention: pixels, +y up, pointing to where that pixel was on the previous frame) is followed into the previous frame's position pass (bilinear), giving where that surface point was: velocity = (P now − P before) × fps. A sample is kept only if:
     - its motion vector disagrees with the **camera's own move** (P reprojected into the previous camera, more than 1.5 px apart), so walls and floors don't read as moving on a camera move;
     - it isn't on a depth edge, and wasn't just uncovered: the previous frame's own motion vector where it lands must be about the same (motion is smooth; an uncovered surface lands on whatever was in front), and it may not differ from its neighbours along the line of sight only;
     - it's faster than *Ignore motion slower than* and than what a pixel of error would give at that distance.
  2. **Following movers through the shot** (`find_movers`, the **Find moving things** button; nothing runs until you press it). Each mover keeps the same parts from frame to frame:
     - its parts are predicted forward by their velocity, and each moving point goes to the mover whose predicted parts are nearest by **position** (so a limb swinging the other way still belongs to its body);
     - within the mover, points are shared out among its parts by k-means on position and velocity, **started from the prediction**, so a part stays the same part; a limb that starts moving on its own gets one more part (up to 6);
     - points nobody owns start new movers (connected blobs, split into parts, about one per 40 cm);
     - a mover that goes behind something keeps its size and is followed on its prediction for up to 8 frames;
     - at the end, each part's path is smoothed (positions over 2 frames each side, velocities over 1, weighted by how much of it was seen), gaps are bridged, and every part lasts the mover's whole life (riding along with the others where it wasn't seen), so spheres don't pop in or out.
  3. **The list.** The 6 biggest movers by size × speed × duration are listed; the others are counted, not listed, so a crowd doesn't flood the list. The ones at least 15% of the biggest are switched on. *Use movers* holds the choice (blank = the suggested ones, `all`, `none` or numbers like `1 3`). The result is cached, also as `movers_<hash>.json` in the cache folder, so farm jobs share it. Run time is mostly EXR reading: about 0.85 s a frame at 1280. In the viewer, progress shows frame by frame (with Cancel in the live viewer).
  In the simulation each part is a sphere the air flows around as it moves (potential flow: pushed aside in front, sliding past at the sides, closing in behind), so motes end up roughly where they were, as in real air. The last 5 frames leave a wake that drags a little air along and stirs it with the micro-turbulence field. At most 144 spheres act per step (this frame's first, then the wake), the same on the GPU. *Push strength* scales all of it, *Push reach* the sphere sizes. If your motion vectors point forward, set *Motion vector scale* to −1.

  **Mover stand-ins** (viewer only, *Show mover stand-ins*): each part is drawn as a circle in its mover's colour, with an arrow for where it will be in 0.2 s, in the shot camera, the free camera and 3D. They're drawn on the current frame even with the camera held. They're never held out, never lit, never rendered.
  - Tested on the stand-in **walker** shot (a box walking through the room on the wide camera move, with a swinging arm on the camera side, passing behind a coat stand, and a `backward` layer made like the studio util pass). One mover is found, with a steady 4 parts on every frame, no false movers and no parts off the walker. Through the coat stand its centre stays within 1–9 cm of the truth. Frame to frame, parts move 2 cm (median) beyond what their velocity predicts, 6 cm at the 95th percentile and 11 cm at most; before tracking these were 5, 26 and 44 cm, and the number of parts wandered between 3 and 5. The arm's fast forward swing reads right (4.5–4.7 m/s vs 4.73); on the backswing it isn't separated from the body as cleanly (the price of steady parts).

### Embers (`EmberSim`, `render_embers`)
Try them on the **campfire** stand-in scene: a fire pit, log bench, rocks and tree trunks at night (moonlight), lit by a flickering fire light with shadows, with embers on and no dust. The fire itself is a still stand-in: three glowing flame-shaped tongues (no simulation), the same in the viewer and the Python stand-in plate. They're drawn over the set only, never in the utility pass, so they hold no embers out.
A second emitter, for fire (Embers tab, off by default). Embers rise from a disc you place and size to the fire (position, radius, emission height; **Place** in the viewer puts it on the surface you click).
- **Air:** a buoyant plume that widens and slows with height, entrainment near the base, gusts (value noise), turbulence, wind, and *flutter*: a spinning flake of ash swings round a small helix, so paths squiggle and sometimes loop. Each ember follows the air with a lag that grows with its size, and heavy ones sink a little.
- **Turbulence is the MoteField mockup's:** the curl of 3D simplex noise at two scales, drifting up with the plume (`_snoise`, `_curl`; the same GLSL as the mockup). It's read along each ember's **guide path** (`EmberSim._guide`): where the ember would be in smooth air, its launch eased into a steady drift, worked out from its seed with no simulation. Read at the ember itself, the swirl feeds on its own tiny differences: 64-bit numpy and the 32-bit GPU drifted up to 3 m apart within two seconds, and rounding the farm's own state to 32 bits moved embers by up to 78 cm. Along the guide path the same rounding moves them by at most 0.01 cm, and the swirl looks the same. The simplex gradient pick is done in whole numbers so its tie-breaks (exact zeros, frequent here) come out the same in both.
- **Pops:** the crackle of burning wood. Groups of spark slots fire together on a schedule (*Pops per second*), thrown out fast; heavier, they keep their momentum and arc back down.
- **Look:** they glow by their own heat. Colour is black-body radiation at a temperature that falls as they rise (yellow-orange to dull red, gone below about 750 K), with a twinkle and a fade in and out. Brightness comes from their size and distance, like the dust. They get the same lens, shutter (streaks), holdouts and noholdout layer as the dust.
- **Deterministic, so farm frames match:** every random choice is an integer hash (PCG) of (slot, life number, what for), which gives the same 24-bit numbers in numpy and GLSL. Steady embers live in slots that respawn when their life ends; the slot count is rate × mean life. The simulation starts at least 3.5 s before the first frame, so the plume is full on frame one. A frame rendered alone equals the same frame in a sequence.
- **Output:** added to RGBA and noholdout with the dust, and on their own (held out) in the **embers** layer.
- **Defaults** are a calm fire: 20 embers a second and a pop every 10 s or so (*Pops per second* 0.1, *Pop size* 0.12).
- **Distance from camera:** *Hidden nearer than* / *Fully visible from* fade embers in with distance (scene units; both 0 = off), separately from the dust's.
- **Checked:** GPU vs Python at frame 1024: the same 28 embers alive, positions within 0.004 cm (median) and 0.16 cm at most. With turbulence 2.5 and a pop a second: 69 alive, 0.009 cm median, 0.46 cm at most. (Reading the turbulence at the ember itself, as the mockup did, these were 4.5 cm median and up to 3 m.)

### Light
- **Key light.** A directional light. `light_dir(az, el)` points toward the light; colour = temperature × RGB × intensity (`sun_rgb`).
- **Shadows:**
  - **stand-in geometry:** exact, for the stand-in shot;
  - **utility pass:** a depth map from the light, built from the utility pass of the first, middle and last frames. With *Interior* on, light only gets in where the camera saw sky, such as a window;
  - **none:** everything in the box is lit.
- **Soft shadow edges.** Visibility is averaged over 6 taps across the sun direction (Shadow edge softness, in cm).
- **Bounce light on motes** (`bounce_lights`). The sunlit patches of the set are found from the utility pass: position, normal and colour where the light lands, with each patch's size. They're clustered into 24 point lights plus an overall fill. Motes outside the beam pick up warm bounce light as they do in a real room. **Bounce light on dust** (0–20) sets how much of it the light gives; it belongs to the light, so with more lights each gets its own.
- **Scattering:**
  - each mote has a Henyey-Greenstein phase function (forward scatter);
  - flakes tumble and glint;
  - brightness scales with each mote's physical size and 1/distance², so most motes are faint and a few are bright.

### Lens and render (`render_frame`)
- **Circle of confusion:** thin lens, from focal length, f-stop, focus distance and film back. Optional field curvature, and front/back multipliers.
- **Bokeh:**
  - round and evenly bright by default, like the studio renders;
  - optional blades, rotation, rim/centre brightness, anamorphic aspect, cat-eye (stronger toward the frame edges, weaker as you stop down), or a custom bokeh image.
  - The aperture kernel is normalised, so energy is conserved: a defocused mote gets bigger and dimmer.
- **Motion blur:** each mote's own motion plus the camera's motion over the shutter. 180°, backward and *backwardsShape* by default: an S-curve that opens toward the frame. Box is available. Lint fibres also spread along their length.
- **Holdouts:** each mote is compared against the utility pass distance at its pixel. The **noholdout** layer never has holdouts.
  - *No holdout nearer than* / *Full holdout beyond* (scene units, both 0 = off) let surfaces near the camera stop blocking dust, so a foreground character's edges don't chew it; you then hold it out with the character's matte in comp. (They used to need a checkbox as well, so changing the numbers alone did nothing; the checkbox is gone.)
  - **Never hold out** (`holdout_skip`): objects that don't block the dust or embers, by name, comma separated, `*` wildcards. Names come from the utility pass's **Cryptomatte** layer (`cryptomatte_info`: rank 0 of the object layer if there are several, names from the manifest), or a plain object-id channel (`_ROLE['ID']`; its names are the numbers). Those pixels count as empty, so whatever is behind the object shows through too. **List objects** shows the names; in the viewer, **Pick in viewer** adds what you click and the object chips toggle. The stand-in utility pass now has a `CryptoObject` layer (MurmurHash3 ids, manifest in the header, as renderers write it), and the GPU stand-in flags those boxes in its holdout distance image.
- **Steady (less popping)** (Dust tab, 0–1): for dust that pops or flickers. At 1: a flake's tumbling flash goes to its average brightness, glints and the grit sparkle go away, shadow edges get 12 cm softer, the smallest blur grows from 0.25 to 0.75 px so tiny motes stop shimmering on the pixel grid, and each holdout is averaged over 5 taps across the mote's blur, so a mote slides behind an edge instead of blinking off. It applies to the embers' blur and holdouts too.
- **Distance from camera** (Dust tab): *Hidden nearer than* / *Fully visible from* fade the motes in with distance from the lens (scene units; both 0 = off).
- **Shapes** (Shapes tab, `shapes` knob, JSON): spheres (ellipsoids) and boxes, turned about the vertical, placed and keyed in the viewer.
  - *Hide inside* takes away the dust / embers in the shape, *hide outside* everything else, each with its own **soft edge** (a fade that wide, centred on the surface) and a choice of dust, embers or both. Several multiply.
  - *Push* shapes move the dust like a found mover of that size (a ball of the mean radius), on their own or with the movers, so you can key a shape sweeping through the air instead of finding motion in the shot. Their velocity is the slope of the keys.
  - Keys are smooth (Catmull-Rom through the keys, held before the first and after the last; `_hermite`, the same in the viewer); one key holds a shape still.
  - In the viewer: **+ Sphere / + Box**; **click a shape's centre** in the 2D view (shot or free camera, from any tab: shapes show faintly on the other tabs, *View > Show shapes*) to select it and get a **gizmo**, as in Maya: **W** move (an arrow moves along that world axis, the centre across the view), **E** scale (a handle stretches along the shape's own axis, the centre scales it all), **R** rotate (drag the ring to turn it about the vertical). The same tools are buttons on the Shapes tab; **Place** puts it on a surface you click; **◆ Key** a shape once and from then on moving it on another frame keys that frame (like an animated knob in Nuke); **Delete key**, **Clear keys** (still again); the keys show on the timeline and in the panel (click one to go there). Wireframes show in 2D and 3D while the Shapes tab is open.
- **Splatting:** motes are splatted in blur-radius bins. Tiny ones get a Gaussian; larger ones get an FFT convolution with the aperture kernel, grouped by cat-eye direction when cat-eye is on.

### Output
`dust.####.exr`: half float, ZIP, scene-linear. Channels `R G B A` (held out) and `noholdout.red/green/blue/alpha`; with embers on, also `embers.red/green/blue/alpha` (the embers alone, held out).
- **Alpha is 0** in every layer: the dust is light added to the plate (emission), so merging it with *plus* or *over* adds its light and leaves the plate's alpha alone. Its brightness as a matte (clamped luminance of the held-out dust), for grading through, is in **`dustmatte.alpha`**. (Up to 0.8 the alpha was that luminance, which a *plus* merge added onto the plate's alpha.)
- **Inside the node:** input 0 *plate*, input 1 *utility*, a Read of the cache, and a Switch driven by **Output**: the Read alone, a Merge (plus) of it over the plate, or an Expression that adds 3 × (noholdout − held) to red over the plate.

## 5. Files

| File | What it is |
|---|---|
| `motefield.py` | **The tool.** EXR I/O, cameras, stand-in shot, settings, simulation, renderer, Nuke node, settings files, CLI. Plain Python + numpy. |
| `mflive.py` | Live viewer server (127.0.0.1 only) and Nuke panel. Serves the viewer and a binary API that calls `mfweb.py` with native Python. Writes GPU-rendered frames to the node's cache. |
| `mflights.py` | Lights from Houdini (OBJ lights or a Solaris/USD stage) to a small .json, and .json/.usd to the node's key-light knobs. |
| `mfweb.py` | Glue between the viewer and `motefield.py`: stand-in frames on demand, preview data (starting state, light map, bounce lights), shaded plates. Used by both the browser (Pyodide) and the live viewer (native). |
| `index.html` | The viewer UI. Its panel is built from `motefield._KNOBS`, so it always matches the node. |
`, so it always matches the node. |
| `preview.js` | The GPU dust: the same simulation and per-mote maths as `motefield.py`, in WebGL2 shaders. |
| `look.js` | Viewer-only look: the stand-in lit like a render (bounce light, haze shaft, lens blur), glow and the AgX tone curve. Never in the output. |
| `worker.js`, `pyodide/` | Browser only: Python + numpy in WebAssembly. |

## 6. The browser version and the Nuke tool

Shots are picked at the top of the viewer from a **Sequence / Shot** pulldown, as a production tool would list the show's shots; here they're stand-ins made on the fly (sq010 reading room: wide, close-up, walker; sq020 campfire). Switching shots looks through the new shot's camera with nothing held. In Nuke the same choice is the *Stand-in scene* knob used by Create stand-in shot.

They are one tool: same knobs, same Python (`motefield.py`), same GPU viewer.

**Browser only** (it has no file system or Nuke):
- utility and plate EXRs are picked from your computer and kept in browser memory;
- **Save this frame** downloads the EXR instead of writing to a cache folder.

**Nuke only:**
- **3D preview:** a point cloud in Nuke's 3D viewer;
- **Key light from selected Nuke light**;
- writing to the cache folder;
- the Read inside the node.

**Viewer extras** (both versions): free camera, hold camera, the stand-in look, glow and the tone curve. They're review aids and change nothing in the output, apart from Hold camera, which is the node's `hold_frame` knob.

## 7. Knob reference

Generated from `motefield._KNOBS`, so it always matches the node.

| Tab | Key | Knob | Default |
|---|---|---|---|
| MoteField | `utility` | Utility pass |  |
| MoteField | `units` | Scene units | centimetres |
| MoteField | `first` | First frame | 1001 |
| MoteField | `last` | Last frame | 1048 |
| MoteField | `fps` | Frame rate | 24.0 |
| MoteField | `standin_view` | Stand-in scene | wide |
| MoteField | `box_center` | Box centre | (-60.0, 175.0, -130.0) |
| MoteField | `box_size` | Box size | (760.0, 330.0, 900.0) |
| MoteField | `box_rot` | Box rotate Y | 0.0 |
| MoteField | `fade` | Edge fade | 45.0 |
| MoteField | `fade_neg` | Fade  -X -Y -Z |  |
| MoteField | `fade_pos` | Fade  +X +Y +Z |  |
| MoteField | `cache` | Cache folder |  |
| MoteField | `output_mode` | Output | dust only |
| MoteField | `proxy` | Resolution | 1.0 |
| MoteField | `hold_frame` | Hold camera on frame (0 = off) | 0 |
| Dust | `density` | Density (per m³) | 180.0 |
| Dust | `seed` | Seed | 1 |
| Dust | `size_min` | Finest grit (µm) | 4.0 |
| Dust | `size_max` | Coarsest grit (µm) | 60.0 |
| Dust | `size_bias` | Fine-grit bias | 2.4 |
| Dust | `flakes` | Flakes % | 18.0 |
| Dust | `fibers` | Lint fibres % | 1.2 |
| Dust | `fluff` | Fluff % | 0.4 |
| Dust | `flake_scale` | Flake size | 1.0 |
| Dust | `fiber_scale` | Fibre length | 1.0 |
| Dust | `fluff_scale` | Fluff size | 0.6 |
| Dust | `brightness` | Brightness | 1.0 |
| Dust | `forward` | Forward scatter | 0.65 |
| Dust | `shadow_fill` | Shadow-side fill | 0.35 |
| Dust | `glint` | Flake glints | 0.4 |
| Dust | `tumble` | Tumble rate | 1.0 |
| Dust | `tint` | Tint | (1.0, 1.0, 1.0) |
| Dust | `max_motes` | Max motes | 150000 |
| Dust | `steady` | Steady (less popping) | 0.0 |
| Dust | `cam_fade_near` | Hidden nearer than | 0.0 |
| Dust | `cam_fade_far` | Fully visible from | 0.0 |
| Motion | `speed` | Speed | 1.0 |
| Motion | `drift` | Drift (cm/s) | 1.2 |
| Motion | `drift_dir` | Drift heading | 200.0 |
| Motion | `lift` | Vertical drift (cm/s) | 0.0 |
| Motion | `eddies` | Eddies (cm/s) | 3.0 |
| Motion | `eddy_size` | Eddy size (m) | 1.4 |
| Motion | `micro` | Micro-turbulence (cm/s) | 0.8 |
| Motion | `thermal` | Thermal lift in light (cm/s) | 1.5 |
| Motion | `settle` | Settling (cm/s) | 0.5 |
| Motion | `preroll` | Pre-roll frames | 48 |
| Motion | `substeps` | Steps per frame | 2 |
| Motion | `push` | Moving things push the dust | False |
| Motion | `movers` | Use movers |  |
| Motion | `show_movers` | Show mover stand-ins (viewer) | True |
| Motion | `push_amount` | Push strength | 1.0 |
| Motion | `push_radius` | Push reach | 1.0 |
| Motion | `push_min` | Ignore motion slower than (m/s) | 0.25 |
| Motion | `mv_scale` | Motion vector scale | 1.0 |
| Light | `light_az` | Azimuth | 25.0 |
| Light | `light_el` | Elevation | 33.0 |
| Light | `light_int` | Intensity | 6.0 |
| Light | `light_k` | Colour temperature (K) | 5000.0 |
| Light | `light_rgb` | Light colour | (1.0, 1.0, 1.0) |
| Light | `bounce` | Bounce light on dust | 1.0 |
| Light | `shadows` | Shadows from | stand-in geometry |
| Light | `shadow_soft` | Shadow edge softness (cm) | 3.0 |
| Light | `interior` | Interior: light only through openings | True |
| Camera | `dof` | Defocus | True |
| Camera | `dof_view` | Defocus in the viewer | True |
| Camera | `mb` | Motion blur | True |
| Camera | `mb_view` | Motion blur in the viewer | True |
| Camera | `focal` | Focal length (mm, 0 = header) | 0.0 |
| Camera | `film` | Film back width (mm, 0 = header) | 0.0 |
| Camera | `fstop` | f-stop (0 = header) | 0.0 |
| Camera | `focus` | Focus distance (scene units, 0 = header) | 0.0 |
| Camera | `blades` | Blade count (0 = header, < 3 round) | 0 |
| Camera | `cateye` | Cat-eye bokeh (+ header) | 0.0 |
| Camera | `anamorphic` | Anamorphic aspect (+ header) | 0.0 |
| Camera | `field_curv` | Field curvature (+ header) | 0.0 |
| Camera | `fstop_full` | f-stop full aperture (0 = header) | 0.0 |
| Camera | `front_mult` | Front multiplier | 1.0 |
| Camera | `back_mult` | Back multiplier | 1.0 |
| Camera | `max_bokeh` | Largest bokeh radius (px) | 110.0 |
| Camera | `bokeh_image` | Custom bokeh shape |  |
| Camera | `bokeh_rot` | Bokeh rotation | 0.0 |
| Camera | `bokeh_falloff` | Bokeh centre / rim brightness | 0.0 |
| Camera | `shutter` | Shutter angle | 180.0 |
| Camera | `shutter_mode` | Shutter | backward |
| Camera | `shutter_shape` | Shutter shape | backwardsShape |
| Embers | `embers` | Embers | False |
| Embers | `ember_pos` | Emitter position | (-150.0, 0.0, -250.0) |
| Embers | `ember_radius` | Emitter radius | 30.0 |
| Embers | `ember_height` | Emission height | 55.0 |
| Embers | `ember_rate` | Embers per second | 20.0 |
| Embers | `ember_life` | Lifetime | 1.0 |
| Embers | `ember_temp` | Start temperature (K) | 1300.0 |
| Embers | `ember_size` | Ember size (mm) | 0.6 |
| Embers | `ember_gain` | Brightness | 1.0 |
| Embers | `ember_twinkle` | Twinkle | 0.6 |
| Embers | `ember_fade_near` | Hidden nearer than | 0.0 |
| Embers | `ember_fade_far` | Fully visible from | 0.0 |
| Embers | `ember_rise` | Plume speed (m/s) | 2.2 |
| Embers | `ember_spread` | Launch spread | 1.0 |
| Embers | `ember_flutter` | Flutter | 1.0 |
| Embers | `ember_turb` | Turbulence | 0.9 |
| Embers | `ember_wind` | Wind (m/s) | 0.3 |
| Embers | `ember_wind_dir` | Wind heading | 200.0 |
| Embers | `ember_pops` | Pops per second | 0.1 |
| Embers | `ember_pop_size` | Pop size | 0.12 |
| Holdouts | `holdout` | Depth holdouts | True |
| Holdouts | `fg_near` | No holdout nearer than | 0.0 |
| Holdouts | `fg_far` | Full holdout beyond | 0.0 |
| Holdouts | `holdout_skip` | Never hold out |  |
| Shapes | `shapes` | Shapes (JSON) | [] |

## 8. Porting to the pipeline: checklist

Every place to change is marked `# PIPELINE:` in `motefield.py`.

**Needed for real shots**
1. **Utility pass path from the shot** (`Shot`). Replace the path knob with a shot picker (show / seq / shot / layer / version) that resolves the frame pattern. The `utility` key stays the resolved pattern, so nothing else changes.
2. **Camera header keys** (`camera_from_headers`). Matching is loose today: any key ending in `camToWorld`, `focalLength`, `fStop`, `focusDistance`, `horizontalAperture`, and the lens extras. Check one of the studio's utility EXRs with ViewMetadata and pin the names.
3. **Layer names** (`_ROLE`). Add the studio's names for position, normals, depth, surface colour and the backward motion vectors (for the pushers; `backward` is already in the list). For *Never hold out*, check which Cryptomatte layer the utility pass carries (`cryptomatte_info` prefers one with "object" in its name) and whether it's in the utility EXR or a separate one; only rank 0 is read, so an object counts at the pixels where it's the main one.
4. **Units and up axis** (`UNITS`). Centimetres and Y-up are assumed. A Z-up scene needs one swap where the pass is read.
5. **Key light.** Use the existing Houdini → Nuke light export with **Key light from selected Nuke light** (`light_from_nuke_node`; check the `world_matrix` knob name in Nuke 15). For a live link, set the node's light knobs by expression from the light node.
6. **Cache / publish location** (`render_frames`, `_nk_defaults`). Write to the studio cache, version it, and register the output.
7. **Farm submission.** Submit `python motefield.py render --settings <json> --frames <range>` through the studio's farm tool, from settings made by **Export settings** (or run `preflight` once first). Then split the range freely: each task starts from the nearest baked checkpoint, and never re-tracks the movers. Without checkpoints a task re-simulates from the pre-roll (about 0.2 s a frame at 40,000 motes, so frame 200 of a 200-frame shot pays ~50 s); chunks of 10–20 frames then cost little either way. The checkpoints are checked against the settings, so a stale file is ignored (and costs only time). If the farm sees the utility pass under a different path, the checkpoints don't match and are skipped: keep paths the same, or re-bake there.
8. **Packaging** (`create`). Make it a gizmo or menu entry. The knobs come from `_KNOBS`, so a gizmo wrapper only needs to call `create()`.
`.

**Check in your environment**
- `numpy` in Nuke: confirmed (1.23.5).
- **QtWebEngine in Nuke 15**, for the live viewer panel: `from PySide2 import QtWebEngineWidgets`. If it's missing, the viewer opens in the browser instead, which works the same. Check that WebGL2 runs there with GPU acceleration on Linux.
- The **Nuke-side buttons** were exercised against a mock `nuke` module, not real Nuke: Live viewer, Export settings (baking in a background thread), Key light from Nuke light, 3D preview, the node's inputs and Output switch, and the utility path from the input. Expect small fixes on first run.
- **OpenImageIO / OpenEXR in Nuke and on the farm**, if any utility pass is tiled or PIZ: `import OpenImageIO` or `import OpenEXR` in Nuke's Script Editor and on a blade. Nuke doesn't necessarily ship the Python bindings.

**Worth adding next**
- **More lights:** point, spot and area lights, practicals. Light visibility and phase per light. `mflights.py` already exports them.
- **Exact shadows from USD geometry** when there's `pxr` access: ray-cast the shot's geometry instead of the utility-pass light map.
- **Bounce from the beauty render:** use the rendered plate (or a diffuse lighting AOV) at every visible surface as the fill light. That captures all of the render's GI, not just the key light's first bounce.
- **Fire light on the dust:** the fire as a flickering point light for the motes (the mockup had it); with more lights this comes almost free.
- **Mote shapes when sharp:** tilted ellipses for flakes, curved strands for lint (the mockup had these).
- **In-graph GPU render (optional):** a BlinkScript kernel that splats a per-frame mote list from Python with tile binning, so the Nuke viewer itself is real time without the panel. The GPU render-to-cache button already covers most of that need.

## 9. What has been checked

- **Farm baking:** on the walker stand-in with pushers on, frame 1040 rendered alone from baked settings took 1.8 s against 12.9 s from plain settings (which re-tracked the movers and simulated from the pre-roll), and the two EXRs are identical, every channel, bit for bit. The same with embers on (frame 1037): identical, 0.9 s against 7.9 s.
- **EXR library fallback:** the stand-in utility pass rewritten by OpenImageIO as scanline PIZ, tiled ZIP and tiled PIZ reads back identical to the ZIP original, through OpenImageIO and through the OpenEXR module; with neither installed it fails with a clear message. As DWAA (lossy), positions moved by up to 4.6 m at depth edges, hence the warning.
- **EXR reader:** reprojects a real studio utility pass to 0.115 px. Files the writer produces read back with the OpenEXR library.
- **Determinism:** a frame rendered on its own equals the same frame from a sequential run.
- **Browser Python vs desktop Python:** identical output, bit for bit.
- **GPU preview vs Python render:** total light within 0.1-1% with every option tested, block correlation 0.97-1.0. The tests covered DOF, blades, cat-eye, anamorphic, custom bokeh, shutter shapes, utility-pass shadows, bounce light and soft shadows.
- **Character pushers:** detection on the stand-in walker shot as in section 4. Given the same movers, the GPU and Python simulations put every mote in the same place (median difference 0.001 cm after 77 frames).
- **Final output** (toolbar button): the dust and embers as the farm renders them, on the GPU: full shot resolution (× the Resolution knob), motion blur and defocus as set by *Motion blur* / *Defocus* (the viewer-only switches are ignored), every sample along each streak (48 per mote; the live viewer draws 16), through the shot camera with nothing held. It opens on the Dust view; Comp shows it over the plate. Each frame is rendered once and kept (up to 1.5 GB); pressing play renders the frames it doesn't have (with progress and Cancel), then plays from memory. Checked against the Python farm render of the same frame: total light within 0.1%, block correlation 0.9998. **Save this frame (.exr)** still writes the exact Python EXR.
- **Camera moves in the viewer:** each new view of the stand-in set has to be lit (ray-traced bounce light, haze, lens blur), which a held camera does once. The viewer lights the set at up to 1920 wide (the stand-in's full resolution), keeps a whole shot of lit frames (up to 1.6 GB), and while nothing else is happening lights the frames ahead in the background (*lighting the stand-in ahead* in the HUD), so a camera move plays from memory like a held camera. Frames reached during playback before that are lit with a quicker pass and refined when you stop. Viewport motion blur is on by default again now that camera moves play from memory (on a move every mote smears across the frame, up to 16 samples each; *Motion blur in the viewer* turns it off).
- **Viewer resolution** (*View > Resolution*): by default the dust is rendered at the shot's own resolution (more when zoomed in past 1:1); *Fit to viewer* renders at the size it's shown, which is faster on a small viewer. Either way the picture is scaled to fit the window. The stand-in shots are 1920 × 804 by default (Nuke's *Create stand-in shot* too); *Stand-in size* in the browser can pick a smaller one.
- **Viewer speed switches** (top of the Camera tab, which was called Lens): *Defocus in the viewer* and *Motion blur in the viewer* turn those off in the live viewer only; *Defocus* and *Motion blur* turn them off everywhere, the render (farm) included. The GPU render-to-cache writes what the node renders, so it ignores the viewer switches.
- **Holdouts, steady, distance fade and shapes, GPU vs Python** (the Final output path vs the farm render, same frame): the same frame through the Final output path and the farm render, total light and 16-px block correlation:
  - baseline (wide): 387.53 vs 387.34, correlation 0.9995;
  - *Steady* 0.8: both +3.1%, correlation of the change 0.998;
  - distance fade 250–550: change correlation 0.9998;
  - a hide-inside sphere and a keyed, turning hide-outside box: both −5.3%, change correlation 0.9999;
  - *Never hold out* `coatstand, walker*` on the walker shot: 297.05 → 452.68 vs 297.04 → 452.67 (the dust behind them comes back), change correlation 0.9997; with *Steady* 0.8 as well: 387.84 vs 387.80;
  - campfire: an embers-only hide sphere 810.0 → 670.4 vs 810.2 → 670.6; ember distance fade 810.0 → 507.8 vs 810.2 → 507.8 (correlation 0.99999);
  - a push shape keyed through the dust at 3 m/s: every mote within 0.0007 cm of the farm's (median), the same as with no shape; at 8 m/s the image correlation is 0.9992 (motes right in front of a fast sphere go left or right of it on tiny differences).
  - Fixed on the way: the first time the GPU made its pusher texture it knocked out the per-mote data for one step, so the first push run after loading drifted every mote about 1 cm from the farm's. Textures are now always made on a spare unit.
- **Holdouts:** the **held out (red)** layer in the viewer shows what they remove. In the wide stand-in only about 2% of the dust is behind anything, so switching holdouts off changes little there; the walker shot shows them clearly.
- **Live viewer:** tested in headless Chromium against the native server, including loading a utility pass from a disk path and the GPU render-to-cache.
- **Not tested here:** anything inside a real Nuke session (see section 8), and real GPU frame rates. The headless tests ran on a software renderer.
