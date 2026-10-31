# MoteField: TD guide and port plan

**Live tool (browser):** https://x14halo.github.io/mote-field/nuke/  
**Code:** this folder. `motefield.py` is the Nuke tool. Everything else is the fast viewer and its glue.

---

## 1. What it is

MoteField adds floating dust motes to a shot in comp, with no 3D particle render. It reads the shot's **utility pass**: world position, normals, depth and surface colour, with the camera in the EXR header. It simulates dust in a box you place in scene space and lights it with a **directional key light** (shadows from the set, plus bounce light). It renders the dust through the **shot's own lens**:

- depth of field with your bokeh controls;
- motion blur with the studio shutter (180°, backward, backwardsShape);
- depth holdouts.

**Output:** an EXR per frame with the dust in **RGBA** and the same dust with no holdouts in a **noholdout** layer. Merge it over the plate with *plus*.

The simulation is deterministic: the same settings always give the same frame, in any order and on any machine. An artist's setup and a farm render therefore match.

## 2. Quick start (for whoever builds the first Nuke version)

1. **Install.** Copy this folder somewhere Nuke can see it, then in the Script Editor:
   ```python
   import sys; sys.path.append('/path/to/motefield'); import motefield; motefield.create()
   ```
2. **Try the stand-in.** Press **Create stand-in shot**. Pick *wide* or *close-up* in **Stand-in camera** first. It writes a placeholder interior (utility EXRs + a clay plate) and points the node at it.
3. **Live viewer (fast).** Press **Live viewer (GPU)**. It opens the real-time viewer, the same one as the browser version, in a Nuke panel, or in your browser if this Nuke has no QtWebEngine. It reads and writes the node's knobs, so whatever you set in the viewer is what the node renders.
4. **Your shot:**
   - **Utility pass:** the path with `####`. The camera comes from the EXR header automatically.
   - **Scene units:** check them. Then press **Fit box to shot** and adjust the box in the viewer's 3D view by dragging its handles.
   - **Key light:**
     - **From a Nuke light:** select the light node your Houdini light export made and press **Key light from selected Nuke light**.
     - **From a file:** use **Import lights (.json / USD)**. `mflights.py` exports the .json from Houdini; a .usd file works directly if Nuke has `pxr`.
   - **Lens:** focal length, f-stop and focus come from the header. The Lens tab can override or offset everything.
5. **See it in the comp quickly.** In the live viewer press **Render range on GPU → cache**. It writes `dust.####.exr` into the node's cache folder in seconds, and the node's Read reloads.
6. **Final frames on the farm.** Press **Export settings (farm)**, then on the farm run:
   ```bash
   python motefield.py render --settings motefield_settings.json --frames 1001-1048
   ```
   This is the reference renderer (Python + numpy, CPU only). It takes a few seconds a frame and needs no GPU or Nuke licence.

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
- **Utility pass.** Each frame is one EXR, single- or multi-part. Compression must be NONE, RLE, ZIPS or ZIP.
  - Layers are found by name (`_ROLE` in `motefield.py`): world position (`pos`, `P`, `worldPosition`…), normals, depth, surface colour.
  - Only position is required; normals and surface colour improve the lighting.
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
- **Character pushers** (optional, `push`). Things that move in the shot stir the dust. `utility_pushers` finds them from the utility pass alone:
  1. Each pixel's backward motion vector (Nuke convention: pixels, +y up, pointing to where that pixel was on the previous frame) is followed into the previous frame's position pass, giving where that surface point was: world velocity = (P now − P before) × fps.
  2. The point is only counted as moving if its motion vector disagrees with what the **camera's own move** would give for a still point (reproject P into the previous frame's camera; more than 1.5 px apart). This is what stops walls and floors reading as moving on a camera move.
  3. Pixels at depth edges, surfaces just revealed from behind something (disocclusion), and speeds below *Ignore motion slower than* are dropped.
  4. The rest are clustered into up to 4 spheres per frame (centre, radius, velocity).

  In the simulation, the air flows around each sphere as it moves (potential flow: pushed aside in front, sliding past at the sides, closing in behind), so motes end up roughly where they were, as in real air. The last 5 frames leave a wake that drags a little air along and stirs it with the micro-turbulence field. *Push strength* scales all of it, *Push reach* the sphere sizes. If your motion vectors point forward, set *Motion vector scale* to −1.
  - Tested on the stand-in **walker** shot (a box walking through the room on the wide camera move, with a `backward` layer made like the studio util pass): the walker was found on every frame (47 of 47), with no false movers, at 0.6% speed error (1280 px) and 6.7% (from a 320 px copy, which the browser uses for the stand-in).

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
- **Holdouts:** each mote is compared against the utility pass distance at its pixel. **Fade out foreground holdouts** lets a foreground character's edges stop blocking dust; you then hold it out with the character's matte in comp. The **noholdout** layer never has holdouts.
- **Splatting:** motes are splatted in blur-radius bins. Tiny ones get a Gaussian; larger ones get an FFT convolution with the aperture kernel, grouped by cat-eye direction when cat-eye is on.

### Output
`dust.####.exr`: half float, ZIP, scene-linear. Channels `R G B A` (held out) and `noholdout.red/green/blue/alpha`.

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
| MoteField | `standin_view` | Stand-in camera | wide |
| MoteField | `btn_standin` | Create stand-in shot | (button) |
| MoteField | `btn_fit` | Fit box to shot | (button) |
| MoteField | `btn_3d` | 3D preview | (button) |
| MoteField | `btn_live` | Live viewer (GPU) | (button) |
| MoteField | `box_center` | Box centre | (-60.0, 175.0, -130.0) |
| MoteField | `box_size` | Box size | (760.0, 330.0, 900.0) |
| MoteField | `box_rot` | Box rotate Y | 0.0 |
| MoteField | `fade` | Edge fade | 45.0 |
| MoteField | `fade_neg` | Fade  -X -Y -Z | (1.0, 1.0, 1.0) |
| MoteField | `fade_pos` | Fade  +X +Y +Z | (1.0, 1.0, 1.0) |
| MoteField | `cache` | Cache folder |  |
| MoteField | `proxy` | Resolution | 1.0 |
| MoteField | `btn_frame` | Render this frame | (button) |
| MoteField | `btn_range` | Render frame range | (button) |
| MoteField | `btn_export` | Export settings (farm) | (button) |
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
| Motion | `push_amount` | Push strength | 1.0 |
| Motion | `push_radius` | Push reach | 1.0 |
| Motion | `push_min` | Ignore motion slower than (m/s) | 0.25 |
| Motion | `mv_scale` | Motion vector scale | 1.0 |
| Light | `light_az` | Azimuth | 25.0 |
| Light | `light_el` | Elevation | 33.0 |
| Light | `light_int` | Intensity | 6.0 |
| Light | `light_k` | Colour temperature (K) | 5000.0 |
| Light | `light_rgb` | Light colour | (1.0, 1.0, 1.0) |
| Light | `btn_nklight` | Key light from selected Nuke light | (button) |
| Light | `btn_lights` | Import lights (.json / USD) | (button) |
| Light | `bounce` | Bounce light on dust | 1.0 |
| Light | `shadows` | Shadows from | stand-in geometry |
| Light | `shadow_soft` | Shadow edge softness (cm) | 3.0 |
| Light | `interior` | Interior: light only through openings | True |
| Lens | `focal` | Focal length (mm, 0 = header) | 0.0 |
| Lens | `film` | Film back width (mm, 0 = header) | 0.0 |
| Lens | `fstop` | f-stop (0 = header) | 0.0 |
| Lens | `focus` | Focus distance (scene units, 0 = header) | 0.0 |
| Lens | `dof` | Defocus | True |
| Lens | `blades` | Blade count (0 = header, < 3 round) | 0 |
| Lens | `cateye` | Cat-eye bokeh (+ header) | 0.0 |
| Lens | `anamorphic` | Anamorphic aspect (+ header) | 0.0 |
| Lens | `field_curv` | Field curvature (+ header) | 0.0 |
| Lens | `fstop_full` | f-stop full aperture (0 = header) | 0.0 |
| Lens | `front_mult` | Front multiplier | 1.0 |
| Lens | `back_mult` | Back multiplier | 1.0 |
| Lens | `max_bokeh` | Largest bokeh radius (px) | 110.0 |
| Lens | `bokeh_image` | Custom bokeh shape |  |
| Lens | `bokeh_rot` | Bokeh rotation | 0.0 |
| Lens | `bokeh_falloff` | Bokeh centre / rim brightness | 0.0 |
| Lens | `shutter` | Shutter angle | 180.0 |
| Lens | `shutter_mode` | Shutter | backward |
| Lens | `shutter_shape` | Shutter shape | backwardsShape |
| Lens | `mb` | Motion blur | True |
| Holdouts | `holdout` | Depth holdouts | True |
| Holdouts | `fg_fade` | Fade out foreground holdouts | False |
| Holdouts | `fg_near` | No holdout nearer than | 0.0 |
| Holdouts | `fg_far` | Full holdout beyond | 0.0 |

## 8. Porting to the pipeline: checklist

Every place to change is marked `# PIPELINE:` in `motefield.py`.

**Needed for real shots**
1. **Utility pass path from the shot** (`Shot`). Replace the path knob with a shot picker (show / seq / shot / layer / version) that resolves the frame pattern. The `utility` key stays the resolved pattern, so nothing else changes.
2. **Camera header keys** (`camera_from_headers`). Matching is loose today: any key ending in `camToWorld`, `focalLength`, `fStop`, `focusDistance`, `horizontalAperture`, and the lens extras. Check one of the studio's utility EXRs with ViewMetadata and pin the names.
3. **Layer names** (`_ROLE`). Add the studio's names for position, normals, depth, surface colour and the backward motion vectors (for the pushers; `backward` is already in the list).
4. **Units and up axis** (`UNITS`). Centimetres and Y-up are assumed. A Z-up scene needs one swap where the pass is read.
5. **Key light.** Use the existing Houdini → Nuke light export with **Key light from selected Nuke light** (`light_from_nuke_node`; check the `world_matrix` knob name in Nuke 15). For a live link, set the node's light knobs by expression from the light node.
6. **Cache / publish location** (`render_frames`, `_nk_defaults`). Write to the studio cache, version it, and register the output.
7. **Farm submission.** Submit `python motefield.py render --settings <json> --frames <range>` through the studio's farm tool. Frames are independent, so split the range freely. Each job re-simulates from pre-roll, which takes seconds.
8. **Packaging** (`create`). Make it a gizmo or menu entry. The knobs come from `_KNOBS`, so a gizmo wrapper only needs to call `create()`.
`.

**Check in your environment**
- `numpy` in Nuke: confirmed (1.23.5).
- **QtWebEngine in Nuke 15**, for the live viewer panel: `from PySide2 import QtWebEngineWidgets`. If it's missing, the viewer opens in the browser instead, which works the same. Check that WebGL2 runs there with GPU acceleration on Linux.
- The **Nuke-side buttons** were exercised against a mock `nuke` module, not real Nuke: Live viewer, Export settings, Key light from Nuke light, 3D preview. Expect small fixes on first run.

**Worth adding next**
- **More lights:** point, spot and area lights, practicals. Light visibility and phase per light. `mflights.py` already exports them.
- **Exact shadows from USD geometry** when there's `pxr` access: ray-cast the shot's geometry instead of the utility-pass light map.
- **Bounce from the beauty render:** use the rendered plate (or a diffuse lighting AOV) at every visible surface as the fill light. That captures all of the render's GI, not just the key light's first bounce.
- **Embers:** the mockup's fire emitter (buoyant plume, black-body cooling, pops) as a second emitter type.
- **Mote shapes when sharp:** tilted ellipses for flakes, curved strands for lint (the mockup had these).
- **In-graph GPU render (optional):** a BlinkScript kernel that splats a per-frame mote list from Python with tile binning, so the Nuke viewer itself is real time without the panel. The GPU render-to-cache button already covers most of that need.

## 9. What has been checked

- **EXR reader:** reprojects a real studio utility pass to 0.115 px. Files the writer produces read back with the OpenEXR library.
- **Determinism:** a frame rendered on its own equals the same frame from a sequential run.
- **Browser Python vs desktop Python:** identical output, bit for bit.
- **GPU preview vs Python render:** total light within 0.1-1% with every option tested, block correlation 0.97-1.0. The tests covered DOF, blades, cat-eye, anamorphic, custom bokeh, shutter shapes, utility-pass shadows, bounce light and soft shadows.
- **Character pushers:** detection on the stand-in walker shot as in section 4 (every frame, no false movers). Given the same pushers, the GPU and Python simulations put every mote in the same place (median difference 0.001 cm after 77 frames).
- **Live viewer:** tested in headless Chromium against the native server, including loading a utility pass from a disk path and the GPU render-to-cache.
- **Not tested here:** anything inside a real Nuke session (see section 8), and real GPU frame rates. The headless tests ran on a software renderer.
