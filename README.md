# MoteField

A live WebGL mockup of **MoteField**, a proposed Nuke node that adds physically based dust motes in comp instead of rendering them as a 3D particle system.

**Live page:** https://x14halo.github.io/mote-field/

The node would read the shot camera, the render's utility pass and the key light's depth map. In the mockup you pick a **sequence and shot** at the top of the page; the real node would look up that shot's utility pass in the pipeline and rebuild a rough 3D scene from it in about a second, so artists can see exactly where to place the dust volume and influencers.

The shots in the mockup are **placeholders**: each one's utility pass (world position, normals, surface colour) and the key light's depth pass are rendered in the browser from a stand-in set, a storybook attic (three shots) and a loft, and the tool only reads the pass.

- **Scene from passes:** world position becomes an indexed mesh that breaks wherever depth jumps, with skirts so the shot camera sees no cracks; normals and surface colour shade it; a voxel copy feeds a two-bounce path tracer
- Camera from the pass's header; dust fills the shot camera's view out to a chosen depth
- Shadows come from the key light's own depth pass; move the light and they fall back to the rebuilt geometry, with sunlight only entering through openings the camera saw as sky
- Orbit away to inspect the rebuilt scene, with the shot camera drawn as a frustum

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
