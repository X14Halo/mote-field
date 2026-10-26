# MoteField

A live WebGL mockup of **MoteField**, a proposed Nuke node that adds physically based dust motes in comp instead of rendering them as a 3D particle system.

**Live page:** https://x14halo.github.io/mote-field/

The node would read the shot camera, a P / depth pass and the key light's depth map. In this mockup a stand-in room, sun and camera supply that data:

- Four kinds of mote (grit, skin/paper flakes, lint fibres, fluff), each with its own size range, shape, scattering and drag
- Lit only where the sun reaches them (light-space depth map), with a forward-scattering phase function
- Per-mote bokeh from focal length, T-stop, focus distance and film back, convolved with each mote's shape
- Shutter-accurate motion blur from mote velocity and camera open/close matrices
- Depth holdouts, including soft holdouts behind out-of-focus foreground
- Curl-noise air, drift, thermal lift in the shaft, size-dependent settling, puffs of air

Controls: click to focus, shift-click to puff, drag to orbit, scroll to dolly, `L` for a pixel loupe, `1`–`7` for viewer channels.

Single self-contained `index.html`; needs a browser with WebGL 2.
