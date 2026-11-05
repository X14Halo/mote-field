"""The MoteField node for Nuke 15.

The node is a Group whose output is the dust (and embers) as an ordinary image: view it, then plus or over it onto
the comp. Inputs: 0 the plate (optional: for the merged output), 1 the utility pass Read (used when the Utility pass
knob is blank).

Inside, a Read shows frames from the node's frame cache (cache.py), in a folder named after the settings, so what
you see is never out of date. Frames are made:

  * in the GUI, by a background renderer that starts at the frame you're looking at and works outward through the
    shot, refreshing the viewer as frames arrive (the UI never waits);
  * in any render (a Write, locally or on the farm), by a callback just before each frame, which renders any frame
    the cache lacks.

Install once, in init.py on NUKE_PATH (so farm renders get it too):

    import motefield.nuke_node
    motefield.nuke_node.register()

and to add a menu entry, in menu.py:

    nuke.menu('Nodes').addCommand('Other/MoteField', 'import motefield.nuke_node as m; m.create()')

The node's onCreate also calls register(), so a script with a MoteField node works anywhere motefield imports.
"""

import json
import os
import threading
import time
import traceback

from . import MODEL_VERSION, __version__, cache, nodelink, place, schema, settings
from .curves import is_curve

GROUP_TAG = 'mf_model'           # a hidden knob that marks MoteField nodes (and the model version they were made with)
_applying = threading.local()     # set while the code (not the artist) is changing knobs


def _nuke():
    import nuke
    return nuke


# ---------------------------------------------------------------------------------------------------------------------
#  Knobs
# ---------------------------------------------------------------------------------------------------------------------
BASICS = (('Shot', ('utility', 'extra_passes', 'standin', 'first', 'last')),
          ('Dials', ('master_amount', 'master_brightness', 'master_air', 'steady')),
          ('Output', ('output_mode', 'alpha')))
TABS = (('place', 'Place'), ('look', 'Look'), ('match', 'Match'), ('deliver', 'Deliver'), ('shot', 'Shot'))


def _label(p):
    unit = {'scene': 'scene units', 'x': ''}.get(p.unit, p.unit)
    return '%s (%s)' % (p.label, unit) if unit else p.label


def _make_knob(nuke, p):
    kind = nodelink.knob_kind(p.key)
    lab = _label(p)
    if kind == 'double':
        k = nuke.Double_Knob(p.key, lab)
        if p.soft:
            k.setRange(p.soft[0], p.soft[1])
    elif kind == 'int':
        k = nuke.Int_Knob(p.key, lab)
    elif kind == 'bool':
        k = nuke.Boolean_Knob(p.key, lab)
        k.setFlag(nuke.STARTLINE)
    elif kind == 'enum':
        k = nuke.Enumeration_Knob(p.key, lab, list(p.options))
    elif kind == 'xyz':
        k = nuke.XYZ_Knob(p.key, lab)
    elif kind == 'rgb':
        k = nuke.Color_Knob(p.key, lab)
    elif kind == 'file':
        k = nuke.File_Knob(p.key, lab)
    elif kind == 'json':
        k = nuke.Multiline_Eval_String_Knob(p.key, lab)
        k.setVisible(False)                      # shapes are edited in the viewer
    else:
        k = nuke.String_Knob(p.key, lab)
    k.setTooltip(p.help + ('' if p.anim or kind not in ('double', 'int', 'xyz', 'rgb')
                           else '  (Changes the simulation or the lens, so it can\'t be animated.)'))
    if not p.anim and kind in ('double', 'int', 'xyz', 'rgb', 'bool', 'enum'):
        k.setFlag(nuke.NO_ANIMATION)
    return k


def _button(nuke, name, label, action, startline=False, tip=''):
    k = nuke.PyScript_Knob(name, label, 'import motefield.nuke_node as m; m.button(nuke.thisNode(), %r)' % action)
    if not startline:
        k.clearFlag(nuke.STARTLINE)
    if tip:
        k.setTooltip(tip)
    return k


def add_knobs(g):
    nuke = _nuke()
    by = schema.BY_KEY
    placed = set()
    g.addKnob(nuke.Tab_Knob('mf_tab_main', 'MoteField'))
    g.addKnob(nuke.Text_Knob('mf_title', '', '<b>MoteField</b> %s: dust and embers from the utility pass. '
                                             'Plus (or, with Alpha set to matte, over) onto the comp.' % __version__))
    g.addKnob(nuke.Text_Knob('mf_status', 'Status', 'Starting...'))
    for section, keys in BASICS:
        g.addKnob(nuke.Text_Knob('mf_div_' + section.lower(), section))
        for k in keys:
            g.addKnob(_make_knob(nuke, by[k]))
            placed.add(k)
        if section == 'Shot':
            g.addKnob(_button(nuke, 'mf_check', 'Shot check', 'check', startline=True,
                              tip='What is in the utility pass, and what MoteField will do with it.'))
            g.addKnob(_button(nuke, 'mf_fit', 'Fit box to shot', 'fit',
                              tip='Places the dust box over the floor the camera sees, from the lens to the set.'))
    g.addKnob(nuke.Text_Knob('mf_div_files', 'Settings files'))
    look = nuke.File_Knob('mf_look_file', 'Look file')
    look.setTooltip('The sequence\'s shared look (.json). Knobs show its values until you change them here.')
    g.addKnob(look)
    show = nuke.File_Knob('mf_show_file', 'Show file')
    show.setTooltip('The show\'s conventions (.json): units, lens, shutter, channels.')
    g.addKnob(show)
    g.addKnob(_button(nuke, 'mf_reload', 'Reload files', 'reload', startline=True,
                      tip='Read the look and show files again (knobs you changed in this node keep your values).'))
    g.addKnob(_button(nuke, 'mf_save_look', 'Save look...', 'save_look',
                      tip='Write this node\'s look settings to a look file to share across the sequence.'))
    g.addKnob(nuke.Text_Knob('mf_div_frames', 'Frames'))
    g.addKnob(_button(nuke, 'mf_fill', 'Render all frames now', 'fill', startline=True,
                      tip='Render the whole shot in the background instead of following the viewer.'))
    g.addKnob(_button(nuke, 'mf_pause', 'Pause', 'pause', tip='Stop rendering in the background (renders still '
                                                              'make the frames they need).'))
    for ws, title in TABS:
        g.addKnob(nuke.Tab_Knob('mf_tab_' + ws, title))
        section = None
        for p in schema.PARAMS:
            if p.ws != ws or p.key in placed or p.key not in nodelink.NODE_KEYS:
                continue
            if p.section != section:
                section = p.section
                g.addKnob(nuke.Text_Knob('mf_sec_%s_%s' % (ws, section.lower().replace(' ', '_')), section))
            g.addKnob(_make_knob(nuke, p))
            placed.add(p.key)
    for name in ('mf_from_files', 'mf_edited', GROUP_TAG):
        k = nuke.String_Knob(name, name)
        k.setVisible(False)
        g.addKnob(k)
    g[GROUP_TAG].setValue(str(MODEL_VERSION))
    g['mf_from_files'].setValue('{}')
    g['mf_edited'].setValue('[]')


def _set_knob(g, key, value):
    """Sets a schema knob (or animates it, for a curve) without it counting as the artist's edit."""
    k = g.knob(key)
    if k is None:
        return
    kind = nodelink.knob_kind(key)
    _applying.on = True
    try:
        if is_curve(value):
            if k.isAnimated():
                k.clearAnimated()
            k.setAnimated()
            for f, v in value['keys']:
                if kind in ('xyz', 'rgb'):
                    for i in range(3):
                        k.setValueAt(float(v[i]), f, i)
                else:
                    k.setValueAt(float(v), f)
        elif kind in ('xyz', 'rgb'):
            for i in range(3):
                k.setValue(float(value[i]), i)
        elif kind == 'json':
            k.setValue(json.dumps(value))
        elif kind == 'bool':
            k.setValue(bool(value))
        elif kind == 'int':
            k.setValue(int(value))
        elif kind == 'double':
            k.setValue(float(value))
        else:
            k.setValue(str(value))
    finally:
        _applying.on = False


def _get_knob(g, key, frames):
    """A schema knob's value: a curve sampled at every frame of the shot when it is animated."""
    k = g.knob(key)
    p = schema.BY_KEY[key]
    kind = nodelink.knob_kind(key)
    if p.anim and k.isAnimated():
        if kind in ('xyz', 'rgb'):
            samples = [(f, [k.getValueAt(f, i) for i in range(3)]) for f in frames]
        else:
            samples = [(f, k.getValueAt(f)) for f in frames]
        return nodelink.curve_from_samples(samples)
    if kind in ('xyz', 'rgb'):
        return tuple(float(k.value(i)) for i in range(3))
    if kind == 'json':
        try:
            return json.loads(k.value() or '[]')
        except ValueError:
            return []
    if kind == 'bool':
        return bool(k.value())
    if kind == 'int':
        return int(k.value())
    if kind == 'double':
        return float(k.value())
    return str(k.value())


def _json_knob(g, name, default):
    try:
        return json.loads(g[name].value() or json.dumps(default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------------------------------------------------
#  Settings from the node
# ---------------------------------------------------------------------------------------------------------------------
def _script_dir():
    try:
        name = _nuke().root().name()
        return os.path.dirname(name) if name and name != 'Root' else os.getcwd()
    except Exception:
        return os.getcwd()


def _resolve_path(g, name):
    k = g.knob(name)
    path = (k.evaluate() if hasattr(k, 'evaluate') else k.value()) or ''
    if path and not os.path.isabs(path):
        path = os.path.join(_script_dir(), path)
    return path


def _file_layers(g):
    look = show = None
    lp, sp = _resolve_path(g, 'mf_look_file'), _resolve_path(g, 'mf_show_file')
    if lp:
        look = settings.load_layer(lp)
    if sp:
        show = settings.load_layer(sp)
    return look, show


def upstream_file(g, index):
    """The file pattern of the Read feeding an input of the node (through any nodes in between), or None."""
    nuke = _nuke()
    n, seen = g.input(index), 0
    while n is not None and seen < 100:
        if n.Class() == 'Read':
            try:
                path = nuke.filename(n)
            except Exception:
                path = None
            path = path or n['file'].value()
            return path.replace('\\', '/') if path else None
        n, seen = n.input(0), seen + 1
    return None


def node_settings(g):
    """The node's settings: its own knobs over its look and show files, with the utility pass from the input when
    the knob is blank."""
    first, last = int(g['first'].value()), int(g['last'].value())
    frames = list(range(first - 1, last + 2))
    values = {k: _get_knob(g, k, frames) for k in nodelink.NODE_KEYS if g.knob(k) is not None}
    if not values.get('utility') and values.get('standin', 'none') == 'none':
        up = upstream_file(g, 1)
        if up:
            values['utility'] = up
    for k in ('utility', 'extra_passes', 'cache'):
        kn = g.knob(k)
        if values.get(k) and '[' in values[k] and hasattr(kn, 'evaluate'):
            values[k] = kn.evaluate()
    look, show = _file_layers(g)
    return nodelink.settings_for(values, _json_knob(g, 'mf_from_files', {}), _json_knob(g, 'mf_edited', []),
                                 look, show, name=g.name())


# ---------------------------------------------------------------------------------------------------------------------
#  Creating the node
# ---------------------------------------------------------------------------------------------------------------------
def create():
    """A MoteField node in the current script, wired to the selected node as its utility input if it is a Read."""
    nuke = _nuke()
    sel = None
    try:
        sel = nuke.selectedNode()
    except Exception:
        pass
    g = nuke.nodes.Group(name='MoteField1')
    g['tile_color'].setValue(0xe4ba6bff)
    add_knobs(g)
    for k in nodelink.NODE_KEYS:
        if g.knob(k) is not None:
            _set_knob(g, k, schema.DEFAULTS[k])
    g.begin()
    plate_in = nuke.nodes.Input(name='plate')
    util_in = nuke.nodes.Input(name='utility')
    plate_in['number'].setValue(0)
    util_in['number'].setValue(1)
    rd = nuke.nodes.Read(name='mf_frames')
    rd['first'].setExpression('parent.first')
    rd['last'].setExpression('parent.last')
    rd['origfirst'].setExpression('parent.first')
    rd['origlast'].setExpression('parent.last')
    rd['on_error'].setValue('black')
    rd['raw'].setValue(True)
    over = nuke.nodes.Merge2(name='mf_over_plate', operation='plus')
    over.setInput(0, plate_in)
    over.setInput(1, rd)
    chk = nuke.nodes.Expression(name='mf_holdout_check')
    chk.setInput(0, rd)
    chk['expr0'].setValue('r + 3 * max(0, noholdout.red + noholdout.green + noholdout.blue - r - g - b)')
    chk_over = nuke.nodes.Merge2(name='mf_check_over_plate', operation='plus')
    chk_over.setInput(0, plate_in)
    chk_over.setInput(1, chk)
    sw = nuke.nodes.Switch(name='mf_output')
    for i, n in enumerate((rd, over, chk_over)):
        sw.setInput(i, n)
    sw['which'].setExpression('parent.output_mode')
    out = nuke.nodes.Output()
    out.setInput(0, sw)
    g.end()
    g['knobChanged'].setValue('import motefield.nuke_node as m; m.knob_changed()')
    g['onCreate'].setValue('import motefield.nuke_node as m; m.on_create()')
    if sel is not None and sel.Class() == 'Read':
        g.setInput(1, sel)
    register()
    update(g)
    return g


# ---------------------------------------------------------------------------------------------------------------------
#  Keeping the node up to date
# ---------------------------------------------------------------------------------------------------------------------
_state = {}            # node full name -> {'key': folder, 'error': ...}
_paused = set()


def _status(g, msg):
    nuke = _nuke()

    def f():
        try:
            g['mf_status'].setValue(msg)
        except Exception:
            pass
    if threading.current_thread() is threading.main_thread():
        f()
    else:
        nuke.executeInMainThread(f)


def update(g):
    """Works out the node's settings, points its Read at the right cache folder and asks for frames. Cheap: called
    on every knob change."""
    try:
        s = node_settings(g)
        e = s.engine(s['first'])
        if not e['utility'] and e['standin'] == 'none':
            _status(g, 'Connect the utility pass Read to the node\'s utility input, or set Utility pass.')
            return None
        fc = cache.FrameCache(s, root=e['cache'] or None)
    except Exception as ex:
        _status(g, 'Problem: %s' % ex)
        return None
    rd = g.node('mf_frames')
    if rd is not None and rd['file'].value() != fc.pattern():
        rd['file'].setValue(fc.pattern())
    over = g.node('mf_over_plate')
    if over is not None:
        over['operation'].setValue('over' if e['alpha'] == 'matte' else 'plus')
    _state[g.fullName()] = dict(folder=fc.folder)
    if _nuke().env.get('gui') and g.fullName() not in _paused:
        _worker().want(g.fullName(), s, fc)
    else:
        n = len(fc.missing(range(s['first'], s['last'] + 1)))
        _status(g, 'Frames: %d of %d rendered.' % (s['last'] - s['first'] + 1 - n, s['last'] - s['first'] + 1))
    return fc


def knob_changed():
    nuke = _nuke()
    g, k = nuke.thisNode(), nuke.thisKnob()
    name = k.name()
    if name in ('mf_look_file', 'mf_show_file'):
        reload_files(g)
        update(g)
        return
    if name == 'inputChange':
        update(g)
        return
    if name not in schema.BY_KEY:
        return
    if not getattr(_applying, 'on', False):          # the artist changed it: a shot override from now on
        edited = _json_knob(g, 'mf_edited', [])
        if name not in edited:
            edited.append(name)
            g['mf_edited'].setValue(json.dumps(edited))
        from_files = _json_knob(g, 'mf_from_files', {})
        if from_files.pop(name, None):
            g['mf_from_files'].setValue(json.dumps(from_files))
    if schema.BY_KEY[name].affects == 'view':
        return
    update(g)


def reload_files(g):
    """Brings the look and show files' values onto the knobs the artist hasn't changed."""
    try:
        look, show = _file_layers(g)
    except Exception as ex:
        _status(g, 'Can\'t read the settings files: %s' % ex)
        return
    first, last = int(g['first'].value()), int(g['last'].value())
    values = {k: _get_knob(g, k, range(first, last + 1)) for k in nodelink.NODE_KEYS if g.knob(k) is not None}
    changes, from_files = nodelink.sync_from_files(values, _json_knob(g, 'mf_from_files', {}),
                                                   _json_knob(g, 'mf_edited', []), look, show)
    for k, v in changes.items():
        _set_knob(g, k, v)
    g['mf_from_files'].setValue(json.dumps(from_files))


def on_create():
    register()
    try:
        update(_nuke().thisNode())
    except Exception:
        traceback.print_exc()


def button(g, action):
    nuke = _nuke()
    if action == 'check':
        from . import check
        s = node_settings(g)
        e = s.engine(s['first'])
        if e['standin'] != 'none' and not e['utility']:
            nuke.message('This node uses the built-in %s stand-in shot.' % e['standin'])
            return
        rep = check.check(e['utility'], extra=e['extra_passes'], units=e['units'], settings=s)
        nuke.message(check.text(rep))
    elif action == 'fit':
        from .standin import open_shot
        s = node_settings(g)
        e = s.engine(s['first'])
        c, size = place.fit_box(e, open_shot(e), frame=nuke.frame())
        for key, v in (('box_center', c), ('box_size', size)):
            _set_knob(g, key, v)
            edited = _json_knob(g, 'mf_edited', [])
            if key not in edited:
                g['mf_edited'].setValue(json.dumps(edited + [key]))
        update(g)
    elif action == 'reload':
        reload_files(g)
        update(g)
    elif action == 'save_look':
        path = nuke.getFilename('Save look', '*.json')
        if path:
            s = node_settings(g)
            vals = {p.key: s[p.key] for p in schema.PARAMS if p.layer == 'look' and p.key in nodelink.NODE_KEYS}
            settings.save_layer(settings.Layer('look', vals, name=os.path.splitext(os.path.basename(path))[0]), path)
            nuke.message('Saved the look to %s' % path)
    elif action == 'fill':
        _paused.discard(g.fullName())
        fc = update(g)
        if fc is not None:
            _worker().fill(g.fullName())
    elif action == 'pause':
        _paused.add(g.fullName())
        _worker().drop(g.fullName())
        _status(g, 'Paused. Renders still make the frames they need.')


# ---------------------------------------------------------------------------------------------------------------------
#  The background renderer (GUI)
# ---------------------------------------------------------------------------------------------------------------------
class Worker(object):
    """One background thread rendering frames for every MoteField node: the frame you're looking at first, then
    outward through the shot. A change to a node's setup replaces its job at once."""

    def __init__(self, start=True):
        self.jobs = {}                  # node name -> dict(settings, cache, fill)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.renderers = {}             # (node, sim key) -> Renderer
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self.run, name='MoteField', daemon=True)
            self.thread.start()

    def want(self, name, s, fc):
        with self.lock:
            old = self.jobs.get(name)
            self.jobs[name] = dict(settings=s, cache=fc, fill=bool(old and old.get('fill') and
                                                                   old['cache'].folder == fc.folder))
        self.wake.set()

    def fill(self, name):
        with self.lock:
            if name in self.jobs:
                self.jobs[name]['fill'] = True
        self.wake.set()

    def drop(self, name):
        with self.lock:
            self.jobs.pop(name, None)

    def _current_frame(self):
        nuke = _nuke()
        try:
            return int(nuke.executeInMainThreadWithResult(nuke.frame))
        except Exception:
            return None

    def _next(self):
        """(node name, job, frame) for the next frame to render, or None when everything wanted is there."""
        with self.lock:
            jobs = list(self.jobs.items())
        if not jobs:
            return None
        cur = self._current_frame()
        for name, job in jobs:
            s, fc = job['settings'], job['cache']
            first, last = s['first'], s['last']
            c = min(max(cur if cur is not None else first, first), last)
            reach = (last - first + 1) if job['fill'] else 12      # follow the viewer: the frames around it
            order = [c]
            for d in range(1, reach + 1):
                order += [f for f in (c + d, c - d) if first <= f <= last]
            for f in order:
                if not fc.has(f):
                    return name, job, f
        return None

    def run(self):
        from . import render
        nuke = _nuke()
        while True:
            nxt = self._next()
            if nxt is None:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            name, job, f = nxt
            s, fc = job['settings'], job['cache']
            node = nuke.executeInMainThreadWithResult(nuke.toNode, args=(name,))
            if node is None:
                self.drop(name)
                continue
            try:
                key = (name, s.sim_key())
                r = self.renderers.get(key)
                if r is None or r.settings.cache_key() != s.cache_key():
                    r = render.Renderer(s, r.shot if r is not None else None)
                    self.renderers = {key: r}
                _status(node, 'Rendering frame %d...' % f)
                t = time.time()
                fc.render(f, r)
                left = len(fc.missing(range(s['first'], s['last'] + 1)))
                _status(node, 'Frame %d in %.1f s. %s' % (f, time.time() - t, 'All %d frames ready.' % (
                    s['last'] - s['first'] + 1) if not left else '%d frames to go.' % left))
                nuke.executeInMainThread(_reload_read, args=(name,))
            except Exception as ex:
                traceback.print_exc()
                _status(node, 'Problem rendering frame %d: %s' % (f, ex))
                self.drop(name)


def _reload_read(name):
    nuke = _nuke()
    g = nuke.toNode(name)
    rd = g.node('mf_frames') if g is not None else None
    if rd is not None:
        rd['reload'].execute()


_WORKER = []


def _worker():
    if not _WORKER:
        _WORKER.append(Worker())
    return _WORKER[0]


# ---------------------------------------------------------------------------------------------------------------------
#  Renders (local or farm): make each frame before it's needed
# ---------------------------------------------------------------------------------------------------------------------
_REGISTERED = []
_BATCH_RENDERERS = {}


def mf_nodes():
    nuke = _nuke()
    return [n for n in nuke.allNodes(recurseGroups=True) if n.knob(GROUP_TAG) is not None]


def before_frame():
    """Called by Nuke before it renders each frame of a Write: renders any MoteField frame that isn't there."""
    nuke = _nuke()
    f = int(nuke.frame())
    for g in mf_nodes():
        if g['disable'].value():
            continue
        try:
            s = node_settings(g)
            if not (s['first'] <= f <= s['last']):
                continue
            from . import render
            r = _BATCH_RENDERERS.get(g.fullName())
            if r is None or r.settings.cache_key() != s.cache_key():
                r = render.Renderer(s)
                _BATCH_RENDERERS[g.fullName()] = r
            fc = cache.FrameCache(s, r.shot, root=s['cache'] or None)
            if not fc.has(f):
                fc.render(f, r)
                rd = g.node('mf_frames')
                if rd is not None and rd['file'].value() != fc.pattern():
                    rd['file'].setValue(fc.pattern())
                if rd is not None:
                    rd['reload'].execute()
        except Exception:
            traceback.print_exc()
            raise


def register():
    """Installs the render callback (once per Nuke session)."""
    if _REGISTERED:
        return
    nuke = _nuke()
    nuke.addBeforeFrameRender(before_frame)
    _REGISTERED.append(True)
