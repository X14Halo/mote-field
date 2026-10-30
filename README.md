# MoteField

A live WebGL mockup of **MoteField**, a proposed Nuke node that adds physically based dust motes in comp instead of rendering them as a 3D particle system.

**Live page:** https://x14halo.github.io/mote-field/

The node would read the shot camera, the render's utility pass and the key light's depth map. The page opens on the hand-built dining room (full 3D). Switch to **Scene loader** at the top and pick a **sequence and shot**; the real node would look up that shot's utility pass in the pipeline and rebuild a rough 3D scene from it in about a second, so artists can see exactly where to place the dust volume and influencers.

The shots in the mockup are **placeholders**: each one's utility pass (world position, normals, surface colour) and the key light's depth pass are rendered in the browser from a stand-in set, a storybook attic (three shots) and a loft, and the tool only reads the pass.

- **Scene from passes:** world position becomes an indexed mesh that breaks wherever depth jumps, with skirts so the shot camera sees no cracks; normals and surface colour shade it; a voxel copy feeds a two-bounce path tracer
- Camera from the pass's header; dust fills the shot camera's view out to a chosen depth
- Shadows come from the key light's own depth pass; move the light and they fall back to the rebuilt geometry
- A 360° coverage pass (P only, six 90° faces from the shot camera's position) closes the scene around the camera and records which directions are open sky, so light only enters through real openings
- Orbit away to inspect the rebuilt scene, with the shot camera drawn as a frustum
- Objects come from the shot's object ID pass (cryptomatte in production): anything named glass, window or skylight is left out of the rebuild automatically so openings stay open, and any other object can be clicked in the viewer to hide it; only hidden objects are listed (a production ID pass can hold thousands), each with a Show button, kept per sequence
- With the coverage pass, unseen space is carved: everything starts solid and only what the camera saw through is emptied, so bounce light and the rebuilt-scene shadows only get in through real openings

The dust itself:

- Four kinds of mote (grit, skin/paper flakes, lint fibres, fluff), each with its own size range, shape, scattering and drag
- Lit only where the sun reaches them (light-space depth map), with a forward-scattering phase function
- Per-mote bokeh from focal length, T-stop, focus distance and film back, convolved with each mote's shape
- Shutter-accurate motion blur from mote velocity and camera open/close matrices
- Depth holdouts, including soft holdouts behind out-of-focus foreground
- Curl-noise air, drift, thermal lift in the shaft, size-dependent settling
- Influencers: a placeable gust of wind (door or window burst) and a stand-in moving object on a path that pushes dust aside and leaves a wake

Campfire scene: an ember emitter you can place and size, with embers that ride a buoyant plume, cool along real black-body colour, pop in bursts, and get the same motion blur, bokeh and holdouts as the dust. The fire light is path traced with soft shadows.

Controls: click or scroll to rack focus, drag to orbit, middle-drag to move the camera, right-drag (or Shift + scroll) to dolly, `G` gust, `R` run the moving object, `L` pixel loupe, `1`–`7` viewer channels.

Single self-contained `index.html`; needs a browser with WebGL 2.

## Nuke tool, and its browser version

**Browser version:** https://x14halo.github.io/mote-field/nuke/

`nuke/motefield.py` is the MoteField prototype for Nuke 15 (plain Python + numpy, no plugins). The browser version runs that same file, unchanged, with Python and numpy compiled to WebAssembly ([Pyodide](https://pyodide.org) 0.26.4, self-hosted in `nuke/pyodide/`). The properties panel is built from the Nuke node's own knob list, and the buttons call the same functions the Nuke buttons do, so the two stay in step and render the same pixels.

- Starts on the stand-in shot (an interior with a window opening and a moving camera), rendered a frame at a time when first needed
- **Choose EXRs** loads a utility pass sequence (and optionally a plate) from your computer; the files stay in the browser tab
- **Live update** re-renders the current frame when a knob changes (the Nuke node only renders on a button press)
- **Preview / Final** (viewer): *Preview* runs the simulation and the mote renderer on the GPU (WebGL2, `nuke/preview.js`) for real-time playback, scrubbing and knob changes, including the camera's depth of field (aperture blades) and motion blur. It starts from the same motes as Python (the starting state comes from `motefield.DustSim`) and does the same per-mote maths; it integrates in 32-bit floats, so individual motes drift slightly from the Python result over time. *Final* is the Python render, exactly what the Nuke node writes; the Render buttons switch to it.
- **Viewer look** (`nuke/look.js`, viewer only): the stand-in set is lit like a render on the GPU (sun and shadows, one ray-traced bounce, a light shaft through the window, the same lens defocus as the dust) and the picture goes through glow and the AgX filmic tone curve, like the mockup. *View* menu: set brightness, black sky, glow, stand-in haze, tone curve. Final shows the Python dust through the same viewer.
- **One live viewer**: the GPU preview matches the Python render (within about 1%), so the browser has no separate Final view; **Save this frame** still renders the exact EXR in Python, and final frames come from the Nuke node on the farm. The viewer renders at screen resolution.
- **Free camera** (C): fly around the set with the full look and the shot's lens; the wheel racks focus, double-click focuses on a point, F returns to the shot camera; a cutaway hides the near walls from outside the room
- **Stand-in camera**: a wide move or a close-up at f/2.8 by the window (with the mockup's close-up dust)
- **Mote lighting** (both tools): bounce light from the sunlit patches of the set, soft shadow edges (Light > Shadow edge softness), motes fade out right at the lens
- **No plate?** A picked utility pass is shaded from its own surface colour and the key light, with the lens defocus, so there is a set to judge the dust against
- **Hold camera**: the camera, plate and holdouts stay on one frame while the dust keeps moving
- **3D view**: the set as solid surfaces from the utility pass; drag the coloured dots on the dust box to resize it from that side and the gold dot to move it (Shift: up / down)
- **Lens** like the studio BokehBlur: round, evenly bright bokeh by default; blade count, cat-eye, anamorphic aspect, field curvature and full aperture from the camera metadata plus offsets; front / back multipliers; a custom bokeh shape image, its rotation and centre / rim brightness
- **Motion blur** like the studio cameras: 180 degree shutter, backward, with the backwardsShape shutter curve (or an even box shutter)
- **Play**: real time in Preview; in Final it renders any frames it doesn't have yet on the first pass, then plays at speed (like Nuke's viewer cache)
- **2D / 3D viewer** (Tab): the 3D view shows the utility pass as a point cloud, the dust box and its fade, the shot camera's path and frustum, the key light, and the rendered motes (lit ones bright). Maya navigation: Alt+LMB tumble, Alt+MMB track, Alt+RMB or wheel dolly, F to frame; the same buttons work without Alt. **Shot camera** looks through the shot camera and follows the frame. The 2D view pans and zooms the same way.
- **Save this frame** downloads the dust EXR exactly as the Nuke node writes it (RGBA + `noholdout`, half float)
- Slower than Nuke (single-threaded WebAssembly): about 2-3 s a frame at 960 x 402 once the pre-roll is simulated
- Nuke only: the 3D preview and writing to a cache folder

Files: `nuke/index.html` (panel and viewer), `nuke/worker.js` (runs Python off the main thread), `nuke/mfweb.py` (browser glue: in-memory files, on-demand stand-in frames), `nuke/motefield.py` (the Nuke tool).
