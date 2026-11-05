"""How a node's knobs become settings, without Nuke (so it can be tested anywhere).

A node shows every setting as a knob. Some of those values are the node's own (the shot layer); the rest mirror its
look and show files. The node keeps two small records in hidden knobs:

    from_files  {key: 'look' or 'show'}  knobs currently showing a value from the look or show file
    edited      [key, ...]               knobs the artist has changed in this node: shot overrides, which reloading
                                         a look never touches

    shot_layer(values, from_files, edited)          the node's own values, as a shot Layer
    sync_from_files(values, from_files, edited, look, show)
                                                    what to set on the knobs after the look or show file changed
"""

from . import curves, schema
from .schema import BY_KEY, DEFAULTS
from .settings import Layer, Settings

# settings that are not knobs on the node: the plate is the node's input 0, and viewer settings live in the viewer
NOT_KNOBS = ('plate',)
NODE_KEYS = tuple(p.key for p in schema.PARAMS if p.ws != 'view' and p.key not in NOT_KNOBS)


def knob_kind(key):
    t = BY_KEY[key].type
    return {'float': 'double', 'int': 'int', 'bool': 'bool', 'enum': 'enum', 'xyz': 'xyz', 'rgb': 'rgb',
            'path': 'file', 'text': 'string', 'json': 'json'}[t]


def _differs(k, v):
    return v != DEFAULTS[k]


def shot_layer(values, from_files, edited, name=''):
    """The node's own values as a shot Layer: knobs not mirroring a file that differ from the default, and every knob
    the artist edited (even one set back to its default over a look's value)."""
    vals = {}
    for k in NODE_KEYS:
        if k not in values:
            continue
        v = values[k]
        if k in edited or (k not in from_files and _differs(k, v)):
            vals[k] = v
    return Layer('shot', vals, name=name)


def sync_from_files(values, from_files, edited, look=None, show=None):
    """After the look or show file changed: ({key: value to set on the knob}, new from_files). A knob the artist
    edited keeps its value; one that mirrored a file that no longer sets it goes back to its default."""
    changes, new_from = {}, {}
    for k in NODE_KEYS:
        if k in edited:
            continue
        src = None
        if look is not None and k in look.values:
            src, v = 'look', look.values[k]
        elif show is not None and k in show.values:
            src, v = 'show', show.values[k]
        if src:
            new_from[k] = src
            if values.get(k) != v:
                changes[k] = v
        elif k in from_files and values.get(k) != DEFAULTS[k]:
            changes[k] = DEFAULTS[k]
    return changes, new_from


def settings_for(values, from_files, edited, look=None, show=None, name=''):
    return Settings(show=show, look=look, shot=shot_layer(values, from_files, edited, name))


def curve_from_samples(samples):
    """A curve from (frame, value) samples of an animated knob, linear between them (sampled at every frame, so it
    gives back exactly what the knob had on each frame)."""
    return curves.make_curve(samples, interp='linear')
