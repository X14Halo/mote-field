"""Reading MoteField 0.8 settings files.

0.8 wrote one flat JSON dict with every knob in it. This turns it into the new layers: by default one shot file holding
every value that differs from the new defaults; with split=True, also a look file and a show file, each value going to
the layer it normally lives in (schema.layer).

What changes on the way:
    fade_xn .. fade_zp            -> fade_neg (x, y, z) and fade_pos (x, y, z)
    standin_view                  -> standin, when the utility pass is 0.8's stand-in files (standin_utility.####.exr);
                                     otherwise dropped (in 0.8 it only chose what Create stand-in shot made)
    shadows 'stand-in geometry'   -> 'auto' (which picks the stand-in geometry for stand-in shots)
    hold_frame                    -> kept, but it is now a viewer-only review aid: renders never hold the camera
    fg_fade, standin_json         -> dropped (0.8 internals)
    movers_baked, sim_checkpoints -> dropped: baked data, not settings. Bake again for the farm.
"""

import os

from . import schema
from .schema import BY_KEY, SettingError
from .settings import Layer

V08_MARKERS = ('box_center', 'box_size', 'density', 'light_az')
DROPPED = ('fg_fade', 'standin_json', 'movers_baked', 'sim_checkpoints')


def looks_like_v08(d):
    return isinstance(d, dict) and 'motefield' not in d and all(k in d for k in V08_MARKERS)


def from_v08(d, split=False, name=''):
    """Returns (shot, look, show) Layers; look and show are None unless split. Also returns a list of notes on what was
    changed or dropped, for the artist: (shot, look, show, notes)."""
    if not looks_like_v08(d):
        raise SettingError('not a MoteField 0.8 settings file')
    d = dict(d)
    notes = []
    vals = {}
    fades = {k: d.pop(k) for k in ('fade_xn', 'fade_yn', 'fade_zn', 'fade_xp', 'fade_yp', 'fade_zp') if k in d}
    if fades:
        vals['fade_neg'] = (fades.get('fade_xn', 1.0), fades.get('fade_yn', 1.0), fades.get('fade_zn', 1.0))
        vals['fade_pos'] = (fades.get('fade_xp', 1.0), fades.get('fade_yp', 1.0), fades.get('fade_zp', 1.0))
    view = d.pop('standin_view', None)
    util = str(d.get('utility') or '')
    if view and os.path.basename(util).startswith('standin_utility.'):
        vals['standin'] = view
        d['utility'] = ''
        notes.append('The 0.8 stand-in files are replaced by the built-in %s stand-in shot.' % view)
    if d.get('shadows') == 'stand-in geometry':
        d['shadows'] = 'auto'
    for k in DROPPED:
        if k in d:
            d.pop(k)
            if k in ('movers_baked', 'sim_checkpoints'):
                notes.append('%s was baked data, not a setting: bake again for the farm.' % k)
    if d.get('hold_frame'):
        notes.append('Hold camera on frame is now a viewer-only review aid; renders never hold the camera.')
    for k, v in d.items():
        if k not in BY_KEY:
            notes.append('Dropped %s: no such setting any more.' % k)
            continue
        try:
            vals[k] = schema.coerce(k, v)
        except SettingError as e:
            notes.append('Dropped %s: %s' % (k, e))
    vals = {k: v for k, v in vals.items() if v != schema.DEFAULTS[k]}
    if not split:
        return Layer('shot', vals, name=name), None, None, notes
    by = {'show': {}, 'look': {}, 'shot': {}}
    for k, v in vals.items():
        by[BY_KEY[k].layer][k] = v
    return (Layer('shot', by['shot'], name=name), Layer('look', by['look'], name=name + ' look' if name else ''),
            Layer('show', by['show'], name=name + ' show' if name else ''), notes)
