# MoteField

A live WebGL mockup of **MoteField**, a proposed Nuke node that adds physically based dust motes in comp instead of rendering them as a 3D particle system.

**Live page:** https://x14halo.github.io/mote-field/

The node would read the shot camera, the render's utility pass and the key light's depth map. The mockup rebuilds a rough 3D scene from a utility pass in about a second, so artists can see exactly where to place the dust volume and influencers:

- **Scene from passes:** world position (P) becomes an indexed mesh that breaks wherever depth jumps, with skirts so the shot camera sees no cracks; normals and surface colour shade it; a voxel copy feeds a two-bounce path tracer
- Camera from the EXR header, or solved from the position pass (RANSAC DLT) when the header has none; depth alone works when the header has a camera
- Units (cm/mm/m) and Z-up scenes detected; antialiased edge pixels that P blends toward the background are dropped
- Load your own EXRs (scanline, multi-part or single-part, NONE/RLE/ZIPS/ZIP) with **Load passes** or by dropping them on the viewer; files are read in the browser and never uploaded
- The built-in demo shot is a utility pass rendered in the browser from a stand-in loft, plus the key light's own depth pass; the tool only reads the pass

The dust itself:

- Four kinds of mote (grit, skin/paper flakes, lint fibres, fluff), each with its own size range, shape, scattering and drag
- Lit only where the sun reaches them (light-space depth map), with a forward-scattering phase function
- Per-mote bokeh from focal length, T-stop, focus distance and film back, convolved with each mote's shape
- Shutter-accurate motion blur from mote velocity and camera open/close matrices
- Depth holdouts, including soft holdouts behind out-of-focus foreground
- Curl-noise air, drift, thermal lift in the shaft, size-dependent settling
- Influencers: a placeable gust of wind (door or window burst) and a stand-in moving object on a path that pushes dust aside and leaves a wake

Campfire scene: an ember emitter you can place and size, with embers that ride a buoyant plume, cool along real black-body colour, pop in bursts, and get the same motion blur, bokeh and holdouts as the dust. The fire light is path traced with soft shadows.

Controls: click to focus, drag to orbit, scroll to dolly, `G` gust, `R` run the moving object, `L` pixel loupe, `1`–`7` viewer channels.

Single self-contained `index.html`; needs a browser with WebGL 2.
