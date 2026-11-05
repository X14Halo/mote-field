"""Every MoteField setting, in one table.

This table is the single source of truth. The Nuke node's knobs, the viewer's panel, the knob reference in the docs and
the cache keys are all generated from it, so they can never disagree.

Each setting says:

    type      float, int, bool, enum, xyz, rgb, path, text or json
    default   the value when nothing sets it
    label     what the artist sees; help is the one-line tooltip
    ws        workspace (panel) it lives in: shot, masters, place, look, match, deliver, view
    section   heading inside that workspace
    level     basic (always shown) or advanced (behind "show all")
    unit      shown after the value: 'scene' means the scene's own units (centimetres by default)
    soft      slider range (the value can go past it)
    hard      clamp range (the value never goes past it)
    affects   what changing it invalidates, from the most to the least expensive:
                shot    the inputs themselves: everything is redone
                prep    the per-shot preparation: light map, bounce lights, moving things (and so the simulation)
                sim     the simulation: motes are re-simulated
                render  render-time only, not animatable (lens, holdouts, output layers)
                look    render-time only, may be animated over the shot
                node    the Nuke node or the cache location only; never changes a pixel of the cached frames
                view    the viewer only; never saved into a render
    layer     the settings layer it normally belongs to (show, look or shot). Any layer may still set any value.
    anim      may be keyed over the shot (only 'look' settings may)

Settings in 'shot', 'prep', 'sim', 'render' and 'look' are hashed into the cache key; 'node' and 'view' never are.
"""

import json
import math

AFFECTS = ('shot', 'prep', 'sim', 'render', 'look', 'node', 'view')
HASHED = ('shot', 'prep', 'sim', 'render', 'look')
LAYERS = ('show', 'look', 'shot')
WORKSPACES = ('shot', 'masters', 'place', 'look', 'match', 'deliver', 'view')
TYPES = ('float', 'int', 'bool', 'enum', 'xyz', 'rgb', 'path', 'text', 'json')
UNITS = {'centimetres': 0.01, 'metres': 1.0, 'millimetres': 0.001, 'inches': 0.0254, 'feet': 0.3048}


class Param(object):
    __slots__ = ('key', 'type', 'default', 'label', 'help', 'ws', 'section', 'level', 'unit', 'soft', 'hard',
                 'affects', 'layer', 'anim', 'options', 'aliases')

    def __init__(self, key, type, default, label, help, ws, section, level, unit, soft, hard, affects, layer, anim,
                 options, aliases):
        self.key, self.type, self.default, self.label, self.help = key, type, default, label, help
        self.ws, self.section, self.level, self.unit, self.soft, self.hard = ws, section, level, unit, soft, hard
        self.affects, self.layer, self.anim, self.options, self.aliases = affects, layer, anim, options, aliases

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.__slots__}
        for k in ('default', 'soft', 'hard'):
            if isinstance(d[k], tuple):
                d[k] = list(d[k])
        return d

    def __repr__(self):
        return 'Param(%r)' % self.key


PARAMS = []
_ctx = {}


class _section(object):
    """Groups the settings that follow under a workspace and heading, with shared defaults for layer and affects."""

    def __init__(self, ws, section, layer, affects):
        self.new = dict(ws=ws, section=section, layer=layer, affects=affects)

    def __enter__(self):
        self.old = dict(_ctx)
        _ctx.update(self.new)

    def __exit__(self, *exc):
        _ctx.clear()
        _ctx.update(self.old)


def _p(key, type, default, label, help, level='advanced', unit='', soft=None, hard=None, affects=None, layer=None,
       anim=False, options=None, aliases=None):
    PARAMS.append(Param(key, type, default, label, help, _ctx['ws'], _ctx['section'], level, unit, soft, hard,
                        affects or _ctx['affects'], layer or _ctx['layer'], anim, options, aliases or {}))


# ---------------------------------------------------------------------------------------------------------------------
#  Shot: where the shot comes from
# ---------------------------------------------------------------------------------------------------------------------
with _section('shot', 'Shot', 'shot', 'shot'):
    _p('utility', 'path', '', 'Utility pass',
       'Frame pattern of the utility pass (world position, normals, depth, motion vectors, Cryptomatte; the camera in '
       'the header). Use #### for the frame number. Blank in Nuke = the Read on the utility input.', level='basic')
    _p('extra_passes', 'path', '', 'Extra passes',
       'Other utility files for the same frames, such as a mattes pass with surface colour and ID mattes. Separate '
       'several with ;. Layers are taken from the utility pass first.')
    _p('plate', 'path', '', 'Plate',
       'Frame pattern of the plate, for the viewer and the merged output. Never changes the dust.',
       level='basic', affects='node')
    _p('standin', 'enum', 'none', 'Stand-in shot',
       'A built-in stand-in shot instead of a utility pass, for trying the tool and for demos.',
       options=['none', 'wide', 'close-up', 'walker', 'campfire'])
    _p('first', 'int', 1001, 'First frame', 'First frame of the shot.', level='basic')
    _p('last', 'int', 1048, 'Last frame', 'Last frame of the shot.', level='basic')
    _p('fps', 'float', 24.0, 'Frame rate', 'Frames per second.', unit='fps', soft=(12, 60), hard=(1, 240), layer='show')
    _p('units', 'enum', 'centimetres', 'Scene units', 'The units the utility pass positions are in.',
       options=list(UNITS), layer='show')
    _p('up_axis', 'enum', 'Y', 'Up axis', 'Which world axis points up in the utility pass.', options=['Y', 'Z'],
       layer='show')

# ---------------------------------------------------------------------------------------------------------------------
#  Masters: the four dials always on screen. They multiply the detailed settings and never overwrite them.
# ---------------------------------------------------------------------------------------------------------------------
with _section('masters', 'Masters', 'shot', 'sim'):
    _p('master_amount', 'float', 1.0, 'Amount',
       'More or fewer motes and embers. Multiplies Density and Embers per second.',
       level='basic', unit='x', soft=(0, 3), hard=(0, 20))
    _p('master_brightness', 'float', 1.0, 'Brightness',
       'Brighter or dimmer dust and embers. Multiplies Dust brightness and Ember brightness. Can be keyed to fade.',
       level='basic', unit='x', soft=(0, 3), hard=(0, 100), affects='look', anim=True)
    _p('master_air', 'float', 1.0, 'Air',
       'Calmer or livelier air. Multiplies drift, eddies, micro-turbulence, rising warm air, tumbling and ember '
       'turbulence together.', level='basic', unit='x', soft=(0, 3), hard=(0, 20))

# ---------------------------------------------------------------------------------------------------------------------
#  Place: where the dust, the embers and the light are
# ---------------------------------------------------------------------------------------------------------------------
with _section('place', 'Dust volume', 'shot', 'sim'):
    _p('box_center', 'xyz', (-60.0, 175.0, -130.0), 'Box centre',
       'Centre of the box the dust lives in. Easiest to set with Fit box or the handles in the viewer.', unit='scene')
    _p('box_size', 'xyz', (760.0, 330.0, 900.0), 'Box size', 'Width, height and depth of the dust box.',
       unit='scene', hard=(0, 1e9))
    _p('box_rot', 'float', 0.0, 'Box rotate Y', 'Turns the box about the vertical.', unit='°', soft=(-180, 180))
    _p('fade', 'float', 45.0, 'Edge softness',
       'How far in from each face of the box the dust fades out, so the box never shows.',
       level='basic', unit='scene', soft=(0, 200), hard=(0, 1e9), affects='render')
    _p('fade_neg', 'xyz', (1.0, 1.0, 1.0), 'Edge softness  -X -Y -Z',
       'Multiplies the edge softness at the -X, -Y and -Z faces (0 = a hard edge, e.g. on the floor).',
       soft=(0, 3), hard=(0, 100), affects='render')
    _p('fade_pos', 'xyz', (1.0, 1.0, 1.0), 'Edge softness  +X +Y +Z',
       'Multiplies the edge softness at the +X, +Y and +Z faces.', soft=(0, 3), hard=(0, 100), affects='render')

with _section('place', 'Ember emitter', 'shot', 'sim'):
    _p('embers', 'bool', False, 'Embers',
       'A second emitter for fire: embers rise from a disc you place on the fire, cool from yellow-orange to dull red '
       'and pop in bursts.', level='basic')
    _p('ember_pos', 'xyz', (-150.0, 0.0, -250.0), 'Emitter position',
       'The base of the fire. Easiest to set with Place in the viewer: click the logs.', unit='scene')
    _p('ember_radius', 'float', 30.0, 'Emitter radius', 'Size of the fire.', level='basic', unit='scene',
       soft=(1, 200), hard=(0.01, 1e9))
    _p('ember_height', 'float', 55.0, 'Emission height', 'How high above the base embers start.', unit='scene',
       soft=(0, 150), hard=(0, 1e9))

with _section('place', 'Key light', 'shot', 'prep'):
    _p('light_az', 'float', 25.0, 'Light heading',
       'Direction the light travels, around the vertical (0 = toward +X, 90 = toward +Z). Easiest to set with '
       'Aim light in the viewer.', level='basic', unit='°', soft=(-180, 180))
    _p('light_el', 'float', 33.0, 'Light elevation', 'Height of the light above the horizon.', level='basic',
       unit='°', soft=(0, 90), hard=(-90, 90))
    _p('light_int', 'float', 6.0, 'Light intensity', 'Brightness of the key light.', level='basic',
       soft=(0, 20), hard=(0, 1e6), affects='look', anim=True)
    _p('light_k', 'float', 5000.0, 'Colour temperature', 'Warmth of the key light.', level='basic', unit='K',
       soft=(2500, 10000), hard=(1000, 40000), affects='look', anim=True)
    _p('light_rgb', 'rgb', (1.0, 1.0, 1.0), 'Light colour', 'Multiplies the colour from the temperature.',
       hard=(0, 1e6), affects='look', anim=True)
    _p('bounce', 'float', 1.0, 'Bounce light',
       'Light bounced off the sunlit parts of the set onto motes outside the beam.', unit='x', soft=(0, 5),
       hard=(0, 100), affects='look', anim=True)
    _p('shadows', 'enum', 'auto', 'Shadows from',
       'Where shadows come from: the utility pass (what the camera saw), the stand-in geometry, or none. Auto picks '
       'the stand-in geometry for stand-in shots and the utility pass otherwise.',
       options=['auto', 'utility pass', 'stand-in geometry', 'none'])
    _p('interior', 'bool', True, 'Light only through openings',
       'Interiors: light gets in only where the camera saw sky, such as a window.')
    _p('shadow_soft', 'float', 3.0, 'Shadow edge softness', 'Width of the soft edge of shadows on the dust.',
       unit='cm', soft=(0, 20), hard=(0, 1e4), affects='look')

with _section('place', 'Shapes', 'shot', 'look'):
    _p('shapes', 'json', [], 'Shapes',
       'Spheres and boxes that hide dust or embers inside or outside them, or push the dust as they move. Added and '
       'keyed in the viewer.', level='basic')

# ---------------------------------------------------------------------------------------------------------------------
#  Look: what the dust and embers look like and how the air moves (the shareable look file)
# ---------------------------------------------------------------------------------------------------------------------
with _section('look', 'Dust', 'look', 'sim'):
    _p('density', 'float', 180.0, 'Density', 'Motes per cubic metre (before Amount).', unit='/m³',
       soft=(1, 1000), hard=(0, 1e6))
    _p('seed', 'int', 1, 'Seed', 'A different arrangement of the same dust.', layer='shot', hard=(0, 2 ** 31 - 1))
    _p('flakes', 'float', 18.0, 'Flakes', 'Share of motes that are skin and paper flakes, which tumble and glint.',
       level='basic', unit='%', soft=(0, 60), hard=(0, 100))
    _p('fibers', 'float', 1.2, 'Lint fibres', 'Share of motes that are lint fibres, long and slow.', level='basic',
       unit='%', soft=(0, 12), hard=(0, 100))
    _p('fluff', 'float', 0.4, 'Fluff', 'Share of motes that are fluff balls, big, soft and floaty.', level='basic',
       unit='%', soft=(0, 6), hard=(0, 100))
    _p('size_min', 'float', 4.0, 'Finest grit', 'Diameter of the smallest grit.', unit='µm', soft=(1, 30),
       hard=(0.1, 1e4))
    _p('size_max', 'float', 60.0, 'Coarsest grit', 'Diameter of the largest grit.', unit='µm', soft=(20, 300),
       hard=(0.1, 1e4))
    _p('size_bias', 'float', 2.4, 'Fine-grit bias', 'Higher = more of the grit is fine.', soft=(0.5, 4),
       hard=(0.05, 20))
    _p('flake_scale', 'float', 1.0, 'Flake size', 'Scales the size of flakes.', unit='x', soft=(0.3, 3),
       hard=(0.01, 100), affects='look')
    _p('fiber_scale', 'float', 1.0, 'Fibre length', 'Scales the length of lint fibres.', unit='x', soft=(0.3, 3),
       hard=(0.01, 100), affects='look')
    _p('fluff_scale', 'float', 0.6, 'Fluff size', 'Scales the size of fluff.', unit='x', soft=(0.15, 2),
       hard=(0.01, 100), affects='look')
    _p('max_motes', 'int', 150000, 'Max motes', 'A ceiling on the number of motes, whatever the density.',
       soft=(1000, 500000), hard=(0, 5000000), layer='show')

with _section('look', 'Dust look', 'look', 'look'):
    _p('brightness', 'float', 1.0, 'Dust brightness', 'Brightness of the dust (before the Brightness dial).',
       unit='x', soft=(0, 4), hard=(0, 1e4), anim=True)
    _p('forward', 'float', 0.65, 'Forward scatter',
       'How much brighter motes look when you look toward the light, as real dust does.', soft=(0, 0.9),
       hard=(0, 0.98))
    _p('shadow_fill', 'float', 0.35, 'Shadow-side fill', 'Light on motes from the room, outside the beam.',
       soft=(0, 2), hard=(0, 100), anim=True)
    _p('glint', 'float', 0.4, 'Flake glints', 'Sparkle of flakes catching the light as they tumble.',
       soft=(0, 1), hard=(0, 100), anim=True)
    _p('tumble', 'float', 1.0, 'Tumble rate', 'How fast flakes and fibres turn over.', unit='x', soft=(0, 3),
       hard=(0, 100))
    _p('tint', 'rgb', (1.0, 1.0, 1.0), 'Tint', 'Colours the dust.', hard=(0, 1e4), anim=True)
    _p('steady', 'float', 0.0, 'Steady',
       'Calms motes that pop or flicker: steadier flakes, no glints, softer shadow edges, a touch of blur on tiny '
       'motes, and holdouts averaged over each mote so it slides behind an edge instead of blinking off.',
       level='basic', soft=(0, 1), hard=(0, 1))
    _p('cam_fade_near', 'float', 0.0, 'Hidden nearer than',
       'Motes nearer the camera than this are hidden (with Fully visible from; both 0 = off).', unit='scene',
       soft=(0, 2000), hard=(0, 1e9), layer='shot', anim=True)
    _p('cam_fade_far', 'float', 0.0, 'Fully visible from', 'Motes are at full strength from this distance on.',
       unit='scene', soft=(0, 2000), hard=(0, 1e9), layer='shot', anim=True)

with _section('look', 'Air', 'look', 'sim'):
    _p('drift', 'float', 1.2, 'Drift', 'A steady breeze through the room.', level='basic', unit='cm/s',
       soft=(0, 20), hard=(0, 1e4))
    _p('drift_dir', 'float', 200.0, 'Drift heading', 'Which way the breeze blows (0 = toward +X, 90 = toward +Z).',
       level='basic', unit='°', soft=(0, 360))
    _p('eddies', 'float', 3.0, 'Eddies', 'Large slow swirls in the air.', level='basic', unit='cm/s',
       soft=(0, 15), hard=(0, 1e4))
    _p('eddy_size', 'float', 1.4, 'Eddy size', 'Size of the large swirls.', unit='m', soft=(0.2, 4),
       hard=(0.05, 1000))
    _p('micro', 'float', 0.8, 'Micro-turbulence', 'Small fast jitter in the air.', unit='cm/s', soft=(0, 5),
       hard=(0, 1e4))
    _p('thermal', 'float', 1.5, 'Rising warm air', 'Air rising where the sun warms it.', unit='cm/s',
       soft=(0, 6), hard=(-1e4, 1e4))
    _p('lift', 'float', 0.0, 'Vertical drift', 'A steady upward (or, negative, downward) drift.', unit='cm/s',
       soft=(-20, 20), hard=(-1e4, 1e4))
    _p('settle', 'float', 0.5, 'Settling', 'How fast heavier motes sink.', unit='cm/s', soft=(0, 4),
       hard=(0, 1e4))
    _p('speed', 'float', 1.0, 'Speed', 'Plays the whole simulation faster or slower.', unit='x', soft=(0, 3),
       hard=(0, 100))
    _p('preroll', 'int', 48, 'Pre-roll', 'Frames simulated before the first frame so the air is already moving.',
       unit='frames', soft=(0, 200), hard=(0, 100000))
    _p('substeps', 'int', 2, 'Steps per frame', 'Simulation steps per frame.', soft=(1, 8), hard=(1, 64),
       layer='show')

with _section('look', 'Embers', 'look', 'sim'):
    _p('ember_rate', 'float', 20.0, 'Embers per second', 'How many embers the fire gives off (before Amount).',
       level='basic', unit='/s', soft=(0, 1500), hard=(0, 1e6))
    _p('ember_rise', 'float', 2.2, 'Plume speed', 'How fast the hot air rises off the fire.', level='basic',
       unit='m/s', soft=(0, 6), hard=(0, 100))
    _p('ember_turb', 'float', 0.9, 'Turbulence', 'Swirl in the plume.', level='basic', soft=(0, 3), hard=(0, 100))
    _p('ember_pops', 'float', 0.1, 'Pops per second', 'How often the wood crackles and throws out a burst.',
       level='basic', unit='/s', soft=(0, 3), hard=(0, 1000))
    _p('ember_pop_size', 'float', 0.12, 'Pop size', 'How many sparks a pop throws.', soft=(0, 1), hard=(0, 100))
    _p('ember_life', 'float', 1.0, 'Lifetime', 'How long embers glow.', unit='s', soft=(0.2, 4), hard=(0.01, 1000))
    _p('ember_spread', 'float', 1.0, 'Launch spread', 'How widely embers fly out as they leave the fire.',
       soft=(0, 2), hard=(0, 100))
    _p('ember_flutter', 'float', 1.0, 'Flutter', 'Ash flakes spiralling as they rise.', soft=(0, 2), hard=(0, 100))
    _p('ember_wind', 'float', 0.3, 'Wind', 'A breeze bending the plume.', unit='m/s', soft=(0, 5), hard=(0, 1000))
    _p('ember_wind_dir', 'float', 200.0, 'Wind heading', 'Which way the wind blows (0 = toward +X, 90 = toward +Z).',
       unit='°', soft=(0, 360))

with _section('look', 'Ember look', 'look', 'look'):
    _p('ember_gain', 'float', 1.0, 'Ember brightness', 'Brightness of the embers (before the Brightness dial).',
       level='basic', unit='x', soft=(0, 20), hard=(0, 1e4), anim=True)
    _p('ember_temp', 'float', 1300.0, 'Start temperature', 'How hot (and how yellow) embers are as they leave.',
       unit='K', soft=(900, 1700), hard=(500, 5000))
    _p('ember_size', 'float', 0.6, 'Ember size', 'Size of the glowing embers.', unit='mm', soft=(0.1, 3),
       hard=(0.01, 100))
    _p('ember_twinkle', 'float', 0.6, 'Twinkle', 'Flicker of embers as they burn.', soft=(0, 1), hard=(0, 1))
    _p('ember_fade_near', 'float', 0.0, 'Embers hidden nearer than',
       'Embers nearer the camera than this are hidden (with Embers fully visible from; both 0 = off).',
       unit='scene', soft=(0, 2000), hard=(0, 1e9), layer='shot', anim=True)
    _p('ember_fade_far', 'float', 0.0, 'Embers fully visible from', 'Embers are at full strength from here on.',
       unit='scene', soft=(0, 2000), hard=(0, 1e9), layer='shot', anim=True)

# ---------------------------------------------------------------------------------------------------------------------
#  Match: make the dust sit in the plate (holdouts, moving things, the lens)
# ---------------------------------------------------------------------------------------------------------------------
with _section('match', 'Holdouts', 'shot', 'render'):
    _p('holdout', 'bool', True, 'Depth holdouts', 'Dust behind the set is hidden by it.', level='basic')
    _p('holdout_skip', 'text', '', 'Never hold out',
       'Objects that never hide dust, by Cryptomatte name (comma separated, * wildcards). Easiest with Pick in the '
       'viewer.', level='basic')
    _p('fg_near', 'float', 0.0, 'No holdout nearer than',
       'Surfaces nearer than this never hide dust (with Full holdout beyond; both 0 = off), so a foreground '
       'character can be held out in comp with its own matte.', unit='scene', soft=(0, 2000), hard=(0, 1e9))
    _p('fg_far', 'float', 0.0, 'Full holdout beyond', 'Surfaces beyond this hide dust fully.', unit='scene',
       soft=(0, 2000), hard=(0, 1e9))

with _section('match', 'Moving things', 'shot', 'sim'):
    _p('push', 'bool', False, 'Moving things push the dust',
       'Things moving in the shot push the dust aside and leave a swirling wake. They are found from the backward '
       'motion vectors with Find moving things.', level='basic')
    _p('movers', 'text', '', 'Use movers',
       'Which moving things push the dust: blank = the suggested ones, "all", "none", or their numbers, e.g. "1 3".')
    _p('push_amount', 'float', 1.0, 'Push strength', 'How hard moving things push the dust.', unit='x',
       soft=(0, 4), hard=(0, 100))
    _p('push_radius', 'float', 1.0, 'Push reach', 'Scales the size of the spheres that push.', unit='x',
       soft=(0.3, 3), hard=(0.01, 100))
    _p('push_min', 'float', 0.25, 'Ignore motion slower than', 'Slower motion is treated as noise.', unit='m/s',
       soft=(0, 2), hard=(0, 1000), affects='prep')
    _p('mv_scale', 'float', 1.0, 'Motion vector scale',
       'Use -1 if the motion vectors point forward rather than backward.', soft=(-2, 2), affects='prep',
       layer='show')

with _section('match', 'Lens', 'shot', 'render'):
    _p('dof', 'bool', True, 'Defocus', 'Depth of field, matched to the shot camera.', level='basic')
    _p('mb', 'bool', True, 'Motion blur', 'Motion blur with the shot shutter.', level='basic')
    _p('focal', 'float', 0.0, 'Focal length', '0 = from the camera in the utility pass.', unit='mm',
       soft=(0, 200), hard=(0, 1e5))
    _p('film', 'float', 0.0, 'Film back width', '0 = from the camera in the utility pass.', unit='mm',
       soft=(0, 60), hard=(0, 1e4))
    _p('fstop', 'float', 0.0, 'f-stop', '0 = from the camera in the utility pass.', soft=(0, 22), hard=(0, 1e4))
    _p('focus', 'float', 0.0, 'Focus distance', '0 = from the camera in the utility pass.', unit='scene',
       soft=(0, 5000), hard=(0, 1e9))
    _p('front_mult', 'float', 1.0, 'Front defocus', 'Scales the blur in front of the focus.', unit='x',
       soft=(0, 5), hard=(0, 100))
    _p('back_mult', 'float', 1.0, 'Back defocus', 'Scales the blur behind the focus.', unit='x', soft=(0, 5),
       hard=(0, 100))

with _section('match', 'Bokeh', 'show', 'render'):
    _p('max_bokeh', 'float', 110.0, 'Largest bokeh', 'Caps the blur of motes right in front of the lens.',
       unit='px', soft=(10, 300), hard=(1, 2000))
    _p('blades', 'int', 0, 'Blade count', '0 = from the camera; below 3 = round.', soft=(0, 12), hard=(0, 64))
    _p('cateye', 'float', 0.0, 'Cat-eye', 'Lemon-shaped bokeh toward the frame edges (added to the camera\'s).',
       soft=(0, 1), hard=(0, 10))
    _p('anamorphic', 'float', 0.0, 'Anamorphic aspect', 'Oval bokeh (added to the camera\'s).',
       soft=(-0.5, 1.5), hard=(-0.95, 10))
    _p('field_curv', 'float', 0.0, 'Field curvature', 'Focus that curves toward the frame edges (added to the '
       'camera\'s).', soft=(-0.5, 0.5), hard=(-10, 10))
    _p('fstop_full', 'float', 0.0, 'f-stop full aperture', 'The lens wide open, for cat-eye (0 = from the camera).',
       soft=(0, 8), hard=(0, 1e4))
    _p('bokeh_image', 'path', '', 'Custom bokeh shape',
       'An image of the aperture (EXR or PNG, white = open). Replaces the blades.')
    _p('bokeh_rot', 'float', 0.0, 'Bokeh rotation', 'Turns the bokeh shape.', unit='°', soft=(-180, 180))
    _p('bokeh_falloff', 'float', 0.0, 'Bokeh centre / rim', 'Positive = brighter centre, negative = brighter rim.',
       soft=(-1, 1), hard=(-1, 1))

with _section('match', 'Shutter', 'show', 'render'):
    _p('shutter', 'float', 180.0, 'Shutter angle', 'How long the shutter is open, in degrees of a frame.', unit='°',
       soft=(0, 360), hard=(0, 3600))
    _p('shutter_mode', 'enum', 'backward', 'Shutter timing', 'When the shutter is open relative to the frame.',
       options=['backward', 'centred', 'forward'])
    _p('shutter_shape', 'enum', 'backwardsShape', 'Shutter shape',
       'backwardsShape: opens gradually toward the frame, like the studio cameras. box: fully open throughout.',
       options=['backwardsShape', 'box'])

# ---------------------------------------------------------------------------------------------------------------------
#  Deliver: what gets written and where
# ---------------------------------------------------------------------------------------------------------------------
with _section('deliver', 'Output', 'shot', 'node'):
    _p('output_mode', 'enum', 'dust only', 'Node output',
       'What the Nuke node outputs: the dust alone (to merge with plus), the dust merged over the plate, or a '
       'holdout check (what holdouts take away, in red).', level='basic',
       options=['dust only', 'merged over plate', 'holdout check'])
    _p('cache', 'path', '', 'Cache folder', 'Where the dust frames are written. Each node needs its own folder.',
       level='basic')
    _p('proxy', 'float', 1.0, 'Resolution', 'Renders at a fraction of the shot resolution.', unit='x',
       soft=(0.1, 1), hard=(0.05, 4), affects='render')

with _section('deliver', 'Layers', 'show', 'render'):
    _p('out_noholdout', 'bool', True, 'noholdout layer', 'The dust with no holdouts, for holding out in comp.')
    _p('out_embers', 'bool', True, 'embers layer', 'The embers on their own (held out).')
    _p('out_dustmatte', 'bool', True, 'dustmatte layer', 'A matte of the dust\'s brightness, to grade through.')
    _p('out_keyfill', 'bool', False, 'dust_key and dust_fill layers',
       'The dust lit by the key light and by bounce light, separately. dust_key + dust_fill + embers = RGBA.')
    _p('out_float', 'bool', False, '32-bit float', 'Writes 32-bit float instead of half float (twice the size).')

# ---------------------------------------------------------------------------------------------------------------------
#  View: the viewer only. Never saved into a render.
# ---------------------------------------------------------------------------------------------------------------------
with _section('view', 'Viewer', 'shot', 'view'):
    _p('dof_view', 'bool', True, 'Defocus in the viewer', 'Turn off to make the viewer faster. Renders keep Defocus.')
    _p('mb_view', 'bool', True, 'Motion blur in the viewer',
       'Turn off if a camera move plays slowly. Renders keep Motion blur.')
    _p('show_movers', 'bool', True, 'Show moving things', 'Draws the spheres that stand in for moving things.')
    _p('hold_frame', 'int', 0, 'Hold camera on frame',
       'Review aid: the camera, plate and holdouts stay on this frame while the dust keeps moving (0 = off).',
       hard=(0, 10 ** 7))


# ---------------------------------------------------------------------------------------------------------------------
#  Lookups and the master dials
# ---------------------------------------------------------------------------------------------------------------------
BY_KEY = {p.key: p for p in PARAMS}
DEFAULTS = {p.key: p.default for p in PARAMS}

# What each master dial multiplies. Steady, the fourth dial, is a setting in its own right.
MASTERS = {
    'master_amount': ('density', 'ember_rate'),
    'master_brightness': ('brightness', 'ember_gain'),
    'master_air': ('drift', 'lift', 'eddies', 'micro', 'thermal', 'tumble', 'ember_turb', 'ember_flutter'),
}
DIALS = ('master_amount', 'master_brightness', 'master_air', 'steady')

_RANK = {a: i for i, a in enumerate(AFFECTS)}


def _check_table():
    """The table's own rules. Runs at import, so a bad edit fails loudly instead of quietly."""
    seen = set()
    for p in PARAMS:
        assert p.key not in seen, 'duplicate setting %s' % p.key
        seen.add(p.key)
        assert p.type in TYPES, p.key
        assert p.ws in WORKSPACES, p.key
        assert p.layer in LAYERS, p.key
        assert p.affects in AFFECTS, p.key
        assert p.level in ('basic', 'advanced'), p.key
        assert not p.anim or p.affects == 'look', '%s: only look settings can be animated' % p.key
        assert p.label and p.help, p.key
        if p.type == 'enum':
            assert p.options and p.default in p.options, p.key
        for r in (p.soft, p.hard):
            assert r is None or (len(r) == 2 and r[0] <= r[1]), p.key
    for dial, keys in MASTERS.items():
        assert dial in BY_KEY, dial
        for k in keys:
            assert k in BY_KEY, k
            # a dial must never be cheaper than what it multiplies, or a change would skip work it needs
            assert _RANK[BY_KEY[dial].affects] <= _RANK[BY_KEY[k].affects], (dial, k)
            # an animated dial makes what it multiplies vary over the shot, so that must be render-time only
            assert not BY_KEY[dial].anim or BY_KEY[k].affects == 'look', (dial, k)
    for k in DIALS:
        assert k in BY_KEY, k


_check_table()


# ---------------------------------------------------------------------------------------------------------------------
#  Values: checking and coercing what comes from files, Nuke knobs or the viewer
# ---------------------------------------------------------------------------------------------------------------------
class SettingError(ValueError):
    pass


def _clamp(x, rng):
    if rng is None:
        return x
    return min(max(x, rng[0]), rng[1])


def _num(x, key):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise SettingError('%s: expected a number, got %r' % (key, x))
    if not math.isfinite(v):
        raise SettingError('%s: expected a finite number, got %r' % (key, x))
    return v


def coerce(key, value):
    """The value as the schema's type, clamped to its hard range. Raises SettingError for anything that can't be."""
    p = BY_KEY.get(key)
    if p is None:
        raise SettingError('unknown setting %r' % key)
    t = p.type
    if t == 'float':
        return float(_clamp(_num(value, key), p.hard))
    if t == 'int':
        v = _num(value, key)
        if v != round(v):
            raise SettingError('%s: expected a whole number, got %r' % (key, value))
        return int(_clamp(int(round(v)), p.hard))
    if t == 'bool':
        if isinstance(value, str):
            s = value.strip().lower()
            if s in ('1', 'true', 'yes', 'on'):
                return True
            if s in ('0', 'false', 'no', 'off', ''):
                return False
            raise SettingError('%s: expected true or false, got %r' % (key, value))
        return bool(value)
    if t == 'enum':
        v = p.aliases.get(value, value)
        if v not in p.options:
            raise SettingError('%s: %r is not one of %s' % (key, value, ', '.join(p.options)))
        return v
    if t in ('xyz', 'rgb'):
        if isinstance(value, str):
            value = [s for s in value.replace(',', ' ').split() if s]
        try:
            vals = list(value)
        except TypeError:
            raise SettingError('%s: expected three numbers, got %r' % (key, value))
        if len(vals) != 3:
            raise SettingError('%s: expected three numbers, got %r' % (key, value))
        return tuple(float(_clamp(_num(v, key), p.hard)) for v in vals)
    if t in ('path', 'text'):
        return '' if value is None else str(value)
    if t == 'json':
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip() else []
            except ValueError as e:
                raise SettingError('%s: not valid JSON (%s)' % (key, e))
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            raise SettingError('%s: not JSON-serialisable' % key)
        return value
    raise SettingError('%s: unknown type %s' % (key, t))


def keys_affecting(*kinds):
    return tuple(p.key for p in PARAMS if p.affects in kinds)


# ---------------------------------------------------------------------------------------------------------------------
#  Generated outputs: JSON for the viewer, Markdown for the docs
# ---------------------------------------------------------------------------------------------------------------------
def to_json():
    """The whole table as JSON, for the viewer (viewer/js/schema.json is written from this)."""
    from . import MODEL_VERSION, __version__
    return json.dumps(dict(version=__version__, model=MODEL_VERSION, affects=AFFECTS, hashed=HASHED, layers=LAYERS,
                           workspaces=WORKSPACES, masters={k: list(v) for k, v in MASTERS.items()}, dials=DIALS,
                           params=[p.to_dict() for p in PARAMS]), indent=1, ensure_ascii=False)


def _fmt(v):
    if isinstance(v, tuple):
        return '(%s)' % ', '.join(_fmt(x) for x in v)
    if isinstance(v, bool):
        return 'on' if v else 'off'
    if isinstance(v, float):
        return ('%g' % v)
    if v == '' or v == []:
        return ''
    return str(v)


def to_markdown():
    """The knob reference for the docs."""
    out = ['# MoteField settings', '',
           'Generated from `motefield/schema.py`; do not edit by hand (`python -m motefield schema --md`).', '',
           '*Affects*: what a change redoes (shot > prep > sim > render > look; node and view never change cached '
           'frames). *Layer*: the settings file it normally lives in. ✎ = can be keyed over the shot.', '']
    ws = None
    for p in PARAMS:
        if p.ws != ws:
            ws = p.ws
            out += ['', '## %s' % ws.capitalize(), '', '| Section | Setting | Key | Default | Unit | Affects | Layer | Level |',
                    '|---|---|---|---|---|---|---|---|']
        out.append('| %s | %s%s | `%s` | %s | %s | %s | %s | %s |' % (
            p.section, p.label, ' ✎' if p.anim else '', p.key, _fmt(p.default), p.unit, p.affects, p.layer, p.level))
    out += ['', '## Master dials', '']
    for dial, keys in MASTERS.items():
        out.append('- **%s** multiplies %s.' % (BY_KEY[dial].label, ', '.join('`%s`' % k for k in keys)))
    return '\n'.join(out) + '\n'
