"""Settings: three layers, resolved into what a render uses.

    show   studio-wide conventions: scene units, frame rate, the studio lens and shutter, the output layers
    look   the shareable look for a sequence: the dust recipe, how the air moves, the embers, the lighting feel
    shot   this shot: its utility pass, frame range, the box and emitter, the light, lens overrides, holdouts...

A value comes from the defaults, then the show, then the look, then the shot: the last layer that sets it wins. Any
layer may set any value; the schema says where each normally lives, and the viewer marks a shot value that overrides
its look so a lead can see what a shot changed.

A shot file names its look and show files; resolving it reads them. For the farm, `Settings.snapshot()` freezes the
whole stack into one file, so a look edited while frames are rendering can't change frames mid-shot.

Settings marked animatable in the schema may hold a curve instead of a value: {"keys": [[frame, value], ...]}.
"""

import hashlib
import json
import os

from . import MODEL_VERSION, __version__, curves, schema
from .schema import BY_KEY, SettingError

FORMAT_TAG = 'motefield-settings'
FORMAT = 1


# ---------------------------------------------------------------------------------------------------------------------
#  One layer: a sparse set of values, as stored in one file
# ---------------------------------------------------------------------------------------------------------------------
def coerce_value(key, value):
    """A setting's value, or (for animatable settings) a curve, checked against the schema."""
    if curves.is_curve(value):
        p = BY_KEY.get(key)
        if p is None:
            raise SettingError('unknown setting %r' % key)
        if not p.anim:
            raise SettingError('%s can\'t be animated (it changes the simulation or the lens)' % key)
        keys = value.get('keys') or []
        if not keys:
            raise SettingError('%s: a curve needs at least one key' % key)
        interp = value.get('interp', 'smooth')
        if interp not in curves.INTERPS:
            raise SettingError('%s: unknown interpolation %r' % (key, interp))
        out = []
        for k in keys:
            try:
                f, v = k
            except (TypeError, ValueError):
                raise SettingError('%s: each key is [frame, value]' % key)
            out.append([float(f), schema.coerce(key, v)])
        out.sort(key=lambda k: k[0])
        return {'keys': out, 'interp': interp}
    return schema.coerce(key, value)


class Layer(object):
    """One settings file's worth of values. kind is show, look or shot."""

    def __init__(self, kind, values=None, name='', notes='', look='', show='', path=None, extra=None, frozen=False):
        if kind not in schema.LAYERS:
            raise SettingError('unknown layer kind %r' % kind)
        self.kind, self.name, self.notes = kind, name, notes
        self.look, self.show = look, show          # paths of the layers a shot builds on (shot layers only)
        self.path = path
        self.extra = dict(extra or {})             # anything else the file held, kept so a round trip loses nothing
        self.frozen = frozen
        self.values = {}
        self.unknown = {}                          # settings this version doesn't know (from a newer version?)
        for k, v in (values or {}).items():
            self.set(k, v)

    def set(self, key, value):
        if key not in BY_KEY:
            raise SettingError('unknown setting %r' % key)
        self.values[key] = coerce_value(key, value)

    def unset(self, key):
        self.values.pop(key, None)

    def to_dict(self):
        order = {p.key: i for i, p in enumerate(schema.PARAMS)}
        vals = {k: _jsonable(self.values[k]) for k in sorted(self.values, key=order.get)}
        d = dict(motefield='settings', format=FORMAT, layer=self.kind)
        if self.name:
            d['name'] = self.name
        if self.kind == 'shot' and not self.frozen:
            if self.look:
                d['look'] = self.look
            if self.show:
                d['show'] = self.show
        if self.frozen:
            d['frozen'] = True
        d['values'] = vals
        if self.unknown:
            d['unknown'] = self.unknown
        if self.notes:
            d['notes'] = self.notes
        d.update(self.extra)
        d['written_by'] = 'motefield %s' % __version__
        return d

    @classmethod
    def from_dict(cls, d, path=None, strict=False):
        if d.get('motefield') != 'settings':
            raise SettingError('%s is not a MoteField settings file' % (path or 'this'))
        if int(d.get('format', 1)) > FORMAT:
            raise SettingError('%s was written by a newer MoteField (format %s); this one reads format %d'
                               % (path or 'this file', d.get('format'), FORMAT))
        known = ('motefield', 'format', 'layer', 'name', 'look', 'show', 'frozen', 'values', 'unknown', 'notes',
                 'written_by')
        layer = cls(d.get('layer', 'shot'), name=d.get('name', ''), notes=d.get('notes', ''), look=d.get('look', ''),
                    show=d.get('show', ''), path=path, frozen=bool(d.get('frozen')),
                    extra={k: v for k, v in d.items() if k not in known})
        layer.unknown = dict(d.get('unknown') or {})
        for k, v in (d.get('values') or {}).items():
            if k in BY_KEY:
                layer.set(k, v)
            elif strict:
                raise SettingError('unknown setting %r in %s' % (k, path or 'settings'))
            else:
                layer.unknown[k] = v
        return layer


def _jsonable(v):
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def read_json(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def write_json(path, d):
    """Writes atomically: a render reading the file never sees half of it."""
    path = os.path.abspath(path)
    tmp = '%s.tmp%d' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, path)


def load_layer(path, strict=False):
    d = read_json(path)
    if d.get('motefield') != 'settings':
        from . import migrate
        if migrate.looks_like_v08(d):
            raise SettingError('%s is a MoteField 0.8 settings file: convert it with '
                               '"python -m motefield migrate %s"' % (path, path))
    return Layer.from_dict(d, path=os.path.abspath(path), strict=strict)


def save_layer(layer, path):
    path = os.path.abspath(path)
    d = layer.to_dict()
    for ref in ('look', 'show'):           # references are stored relative to the file, so a folder can move
        if d.get(ref) and os.path.isabs(d[ref]):
            try:
                d[ref] = os.path.relpath(d[ref], os.path.dirname(path))
            except ValueError:             # another drive on Windows: keep it absolute
                pass
    write_json(path, d)
    layer.path = path
    return path


# ---------------------------------------------------------------------------------------------------------------------
#  The resolved stack
# ---------------------------------------------------------------------------------------------------------------------
ORDER = ('default', 'show', 'look', 'shot')


class Settings(object):
    """defaults <- show <- look <- shot, resolved. Read values with s['key'] (curves come back as curves) or
    s.value('key', frame); s.engine(frame) gives the values a render uses on that frame."""

    def __init__(self, show=None, look=None, shot=None):
        self.layers = {'show': show, 'look': look, 'shot': shot}
        for kind, layer in self.layers.items():
            if layer is not None and layer.kind != kind:
                raise SettingError('a %s layer was given as the %s layer' % (layer.kind, kind))
        self.refresh()

    def refresh(self):
        """Re-resolve after a layer changed."""
        self._raw = dict(schema.DEFAULTS)
        self._src = {k: 'default' for k in self._raw}
        for kind in ORDER[1:]:
            layer = self.layers[kind]
            if layer is None:
                continue
            for k, v in layer.values.items():
                self._raw[k] = v
                self._src[k] = kind
        self._keys = {}

    # ---- reading ----------------------------------------------------------------------------------------------------
    def __getitem__(self, key):
        return self._raw[key]

    def __contains__(self, key):
        return key in self._raw

    def source(self, key):
        """Which layer the value comes from: default, show, look or shot."""
        return self._src[key]

    def is_animated(self, key):
        return curves.is_curve(self._raw[key])

    def value(self, key, frame=None):
        """The plain value on a frame (curves evaluated there, then clamped to the setting's range)."""
        v = self._raw[key]
        if curves.is_curve(v):
            if frame is None:
                raise SettingError('%s is animated: give a frame' % key)
            return schema.coerce(key, curves.curve_at(v, frame))
        return v

    def values(self, frame=None):
        """Every setting's plain value on a frame. Without a frame, animated settings take their first key."""
        out = {}
        for k, v in self._raw.items():
            if curves.is_curve(v):
                out[k] = schema.coerce(k, curves.curve_at(v, v['keys'][0][0] if frame is None else frame))
            else:
                out[k] = v
        return out

    def engine(self, frame=None):
        """The values a simulation or render uses on a frame: animated settings evaluated, the master dials applied
        (Amount, Brightness, Air multiply what they scale), and 'auto' choices made. The dials themselves stay in the
        result for reference, but nothing downstream should apply them again."""
        v = self.values(frame)
        for dial, keys in schema.MASTERS.items():
            m = v[dial]
            for k in keys:
                v[k] = v[k] * m
        if v['shadows'] == 'auto':
            v['shadows'] = 'stand-in geometry' if v['standin'] != 'none' and not v['utility'] else 'utility pass'
        v['scene_unit'] = schema.UNITS[v['units']]      # metres per scene unit
        v['masters_applied'] = True
        return v

    def changed(self):
        """{key: value} for every setting that differs from its default (curves included as curves)."""
        return {k: v for k, v in self._raw.items() if v != schema.DEFAULTS[k]}

    def overrides(self):
        """Settings the shot sets that its look or show also sets: {key: (from layer, its value, the shot's value)}."""
        out = {}
        shot = self.layers['shot']
        if shot is None:
            return out
        for k, v in shot.values.items():
            for kind in ('look', 'show'):
                layer = self.layers[kind]
                if layer is not None and k in layer.values:
                    out[k] = (kind, layer.values[k], v)
                    break
        return out

    # ---- cache keys -------------------------------------------------------------------------------------------------
    def _hash(self, kinds, extra=()):
        keys = schema.keys_affecting(*kinds)
        blob = canonical([MODEL_VERSION, list(kinds), [[k, self._raw[k]] for k in keys], list(extra)])
        return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:20]

    def prep_key(self):
        """Changes when the per-shot preparation must be redone: light map, bounce lights, moving things."""
        if 'prep' not in self._keys:
            self._keys['prep'] = self._hash(('shot', 'prep'))
        return self._keys['prep']

    def sim_key(self):
        """Changes when the motes must be re-simulated. Includes the prep (the light drives the rising warm air) and
        the shapes that push the dust (only those: a shape that hides dust never changes where it is)."""
        if 'sim' not in self._keys:
            self._keys['sim'] = self._hash(('shot', 'prep', 'sim'), extra=[push_shapes(self._raw['shapes'])])
        return self._keys['sim']

    def cache_key(self):
        """Changes whenever a cached frame would come out differently. Goes into every cached EXR's header."""
        if 'cache' not in self._keys:
            self._keys['cache'] = self._hash(schema.HASHED)
        return self._keys['cache']

    # ---- freezing for the farm --------------------------------------------------------------------------------------
    def snapshot(self, name=''):
        """Every non-default value of the resolved stack as one frozen shot file: what the farm renders from. It records
        where each value came from and a fingerprint of each source file, so a render can be traced to its look."""
        layer = Layer('shot', name=name or (self.layers['shot'].name if self.layers['shot'] else ''), frozen=True)
        for k, v in self.changed().items():
            layer.values[k] = v
        sources = {}
        for kind in ORDER[1:]:
            src = self.layers[kind]
            if src is not None:
                sources[kind] = dict(path=src.path or '', fingerprint=_fingerprint(src))
        layer.extra = dict(provenance={k: self._src[k] for k in layer.values}, sources=sources,
                           keys=dict(prep=self.prep_key(), sim=self.sim_key(), cache=self.cache_key(),
                                     model=MODEL_VERSION))
        return layer


def push_shapes(shapes):
    """The part of the shapes that moves dust (push shapes, switched on), in a stable form for hashing."""
    if not isinstance(shapes, list):
        return []
    return [s for s in shapes if isinstance(s, dict) and s.get('mode') == 'push' and s.get('on', True)]


def canonical(v):
    """A stable text form for hashing: the same values always give the same text, whatever order dicts were built in
    and whether a number arrived as 5 or 5.0 (values are coerced by the schema first)."""
    return json.dumps(_jsonable(v), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _fingerprint(layer):
    return hashlib.sha256(canonical(layer.values).encode('utf-8')).hexdigest()[:20]


def resolve(show=None, look=None, shot=None):
    return Settings(show=show, look=look, shot=shot)


def _ref_path(base, ref):
    if not ref:
        return None
    if os.path.isabs(ref):
        return ref
    return os.path.normpath(os.path.join(os.path.dirname(base), ref))


def load(path, strict=False):
    """Settings from a file. A shot file brings in the look and show files it names (paths relative to it); a frozen
    snapshot stands alone; a look or show file on its own resolves over the defaults."""
    layer = load_layer(path, strict=strict)
    if layer.kind == 'shot':
        if layer.frozen:
            return Settings(shot=layer)
        look = show = None
        lp, sp = _ref_path(layer.path, layer.look), _ref_path(layer.path, layer.show)
        if lp:
            look = load_layer(lp, strict=strict)
            if look.kind != 'look':
                raise SettingError('%s names %s as its look, but that is a %s file' % (path, lp, look.kind))
        if sp:
            show = load_layer(sp, strict=strict)
            if show.kind != 'show':
                raise SettingError('%s names %s as its show settings, but that is a %s file' % (path, sp, show.kind))
        return Settings(show=show, look=look, shot=layer)
    return Settings(**{layer.kind: layer})


def defaults():
    return Settings()
