"""A small stand-in for Nuke's Python module: just the parts motefield.nuke_node uses, behaving the way Nuke's do
(as far as they are known without running Nuke). Tests put it in sys.modules['nuke'].

    import fake_nuke; fake_nuke.install(gui=False)
    fake_nuke.reset()
    fake_nuke.change(node, 'density', 300)     # sets a knob the way an artist would (runs knobChanged)
"""

import sys
import types

STARTLINE, NO_ANIMATION = 0x1000, 0x100


class Knob(object):
    def __init__(self, name, label=None, *rest):
        self._name, self.label = name, label
        self.node = None
        self.flags = set()
        self.tooltip, self.visible, self.expression = '', True, None
        self.anim = None                     # {index: {frame: value}} when animated
        self._value = self.default()
        self.flags.add(STARTLINE)

    # what every knob has
    def name(self):
        return self._name

    def default(self):
        return 0.0

    def setFlag(self, f):
        self.flags.add(f)

    def clearFlag(self, f):
        self.flags.discard(f)

    def setTooltip(self, t):
        self.tooltip = t

    def setVisible(self, v):
        self.visible = v

    def setExpression(self, e, index=-1):
        self.expression = e

    def setValue(self, v, index=None):
        self._value = v
        return True

    def value(self, index=None):
        return self._value

    def getValue(self, index=None):
        return self.value(index)

    def evaluate(self):
        return self.value()

    def isAnimated(self, index=None):
        return bool(self.anim)


class Number(Knob):
    size = 1
    cast = float

    def default(self):
        return [0.0] * self.size if self.size > 1 else 0.0

    def setRange(self, a, b):
        self.range = (a, b)

    def setValue(self, v, index=None):
        if NO_ANIMATION in self.flags and self.anim:
            raise RuntimeError('animated a knob that cannot be animated')
        if self.size > 1:
            if index is None:
                vals = list(v) if isinstance(v, (list, tuple)) else [v] * self.size
                self._value = [self.cast(x) for x in vals]
            else:
                self._value[index] = self.cast(v)
        else:
            self._value = self.cast(v)
        return True

    def value(self, index=None):
        if self.size > 1:
            return self._value[index] if index is not None else list(self._value)
        return self._value

    def setAnimated(self, index=-1):
        if NO_ANIMATION in self.flags:
            raise RuntimeError('%s cannot be animated' % self._name)
        self.anim = {i: {} for i in range(self.size)}
        return True

    def clearAnimated(self, index=-1):
        self.anim = None

    def setValueAt(self, v, t, index=0):
        if self.anim is None:
            self.setAnimated()
        self.anim[index][float(t)] = float(v)

    def getValueAt(self, t, index=0):
        if not self.anim:
            return self.value(index if self.size > 1 else None)
        keys = sorted(self.anim[index].items())
        if t <= keys[0][0]:
            return keys[0][1]
        if t >= keys[-1][0]:
            return keys[-1][1]
        for (a, va), (b, vb) in zip(keys, keys[1:]):
            if a <= t <= b:
                return va + (vb - va) * (t - a) / (b - a)


class Double_Knob(Number):
    pass


class Int_Knob(Number):
    cast = int

    def default(self):
        return 0


class XYZ_Knob(Number):
    size = 3


class Color_Knob(Number):
    size = 3


class Boolean_Knob(Number):
    cast = bool

    def default(self):
        return False


class Enumeration_Knob(Knob):
    def __init__(self, name, label, options):
        self.options = list(options)
        Knob.__init__(self, name, label)

    def default(self):
        return 0

    def setValue(self, v, index=None):
        if isinstance(v, str):
            if v not in self.options:
                raise ValueError('%s: no option %r' % (self._name, v))
            v = self.options.index(v)
        self._value = int(v)
        return True

    def value(self, index=None):
        return self.options[self._value]

    def getValue(self, index=None):
        return float(self._value)

    def setAnimated(self, index=-1):
        raise RuntimeError('enumerations cannot be animated')


class String_Knob(Knob):
    def default(self):
        return ''

    def setValue(self, v, index=None):
        self._value = str(v)
        return True


class File_Knob(String_Knob):
    pass


class Multiline_Eval_String_Knob(String_Knob):
    pass


class Text_Knob(String_Knob):
    def __init__(self, name, label='', text=''):
        String_Knob.__init__(self, name, label)
        self._value = text


class Tab_Knob(Knob):
    pass


class PyScript_Knob(Knob):
    def __init__(self, name, label='', command=''):
        Knob.__init__(self, name, label)
        self.command = command
        self.runs = 0

    def execute(self):
        self.runs += 1
        if self.command:
            with _context(self.node, self):
                exec(self.command, {'nuke': sys.modules['nuke']})


# ---------------------------------------------------------------------------------------------------------------------
class Node(object):
    KNOBS = ()

    def __init__(self, cls, name=None, **kw):
        self._class = cls
        self._knobs = {}
        self.inputs = {}
        self.parent = _stack[-1] if _stack else None
        base = name or cls + '1'
        self._name = base
        if self.parent is not None:
            names = {c.name() for c in self.parent.children}
            n = 1
            while self._name in names:
                n += 1
                self._name = base.rstrip('0123456789') + str(n)
            self.parent.children.append(self)
        for kname, kcls in self.KNOBS + (('disable', Boolean_Knob), ('label', String_Knob)):
            self.addKnob(kcls(kname, kname) if kcls is not Enumeration_Knob else kcls(kname, kname, []))
        for k, v in kw.items():
            self[k].setValue(v)

    def Class(self):
        return self._class

    def name(self):
        return self._name

    def fullName(self):
        if self.parent is None or self.parent is ROOT:
            return self._name
        return self.parent.fullName() + '.' + self._name

    def addKnob(self, k):
        k.node = self
        self._knobs[k.name()] = k

    def knob(self, name):
        return self._knobs.get(name)

    def knobs(self):
        return dict(self._knobs)

    def __getitem__(self, name):
        if name not in self._knobs:
            raise NameError('%s has no knob %s' % (self._name, name))
        return self._knobs[name]

    def setInput(self, i, n):
        self.inputs[i] = n
        if self.knob('knobChanged') is not None and self.knob('knobChanged').value():
            fire(self, 'inputChange')
        return True

    def input(self, i):
        return self.inputs.get(i)


class Group(Node):
    KNOBS = (('tile_color', Int_Knob), ('knobChanged', String_Knob), ('onCreate', String_Knob))

    def __init__(self, cls='Group', name=None, **kw):
        self.children = []
        Node.__init__(self, cls, name, **kw)

    def begin(self):
        _stack.append(self)

    def end(self):
        _stack.pop()

    def node(self, name):
        for c in self.children:
            if c.name() == name:
                return c
        return None


class Read(Node):
    KNOBS = (('file', File_Knob), ('first', Int_Knob), ('last', Int_Knob), ('origfirst', Int_Knob),
             ('origlast', Int_Knob), ('raw', Boolean_Knob), ('reload', PyScript_Knob))

    def __init__(self, cls='Read', name=None, **kw):
        Node.__init__(self, cls, name, **kw)
        self.addKnob(Enumeration_Knob('on_error', 'on error', ['error', 'black', 'checkerboard', 'nearest frame']))


class Merge2(Node):
    def __init__(self, cls='Merge2', name=None, **kw):
        op = kw.pop('operation', 'over')
        Node.__init__(self, cls, name, **kw)
        self.addKnob(Enumeration_Knob('operation', 'operation', ['over', 'plus', 'screen', 'multiply']))
        self['operation'].setValue(op)


class Generic(Node):
    pass


def _factory(cls, knobs=()):
    klass = {'Group': Group, 'Read': Read, 'Merge2': Merge2}.get(cls)
    if klass is not None:
        return lambda name=None, **kw: klass(cls, name, **kw)

    def make(name=None, **kw):
        n = Generic.__new__(Generic)
        Generic.KNOBS = knobs
        Node.__init__(n, cls, name, **kw)
        Generic.KNOBS = ()
        return n
    return make


class _Nodes(object):
    def __getattr__(self, cls):
        extra = {'Input': (('number', Int_Knob),), 'Switch': (('which', Int_Knob),),
                 'Expression': (('expr0', String_Knob),), 'Write': (('file', File_Knob),)}.get(cls, ())
        return _factory(cls, extra)


ROOT = None
_stack = []
_this = []
_main_calls = []
_callbacks = {'beforeFrameRender': []}
env = {'gui': False}
messages = []
current_frame = [1001]
nodes = _Nodes()


class _context(object):
    def __init__(self, node, knob):
        self.item = (node, knob)

    def __enter__(self):
        _this.append(self.item)

    def __exit__(self, *a):
        _this.pop()


def thisNode():
    return _this[-1][0]


def thisKnob():
    return _this[-1][1]


def fire(node, knob_name):
    """Runs the node's knobChanged as Nuke does after a knob changes."""
    code = node.knob('knobChanged').value() if node.knob('knobChanged') is not None else ''
    if not code:
        return
    k = node.knob(knob_name) or Knob(knob_name)
    with _context(node, k):
        exec(code, {'nuke': sys.modules['nuke']})


def change(node, knob, value, index=None):
    """An artist setting a knob in the properties panel."""
    node[knob].setValue(value, index) if index is not None else node[knob].setValue(value)
    fire(node, knob)


def frame():
    return current_frame[0]


def root():
    return ROOT


def toNode(name):
    parts = name.split('.')
    n = ROOT
    for p in parts:
        n = n.node(p) if n is not None else None
    return n


def allNodes(cls=None, group=None, recurseGroups=False):
    out = []

    def walk(g):
        for c in g.children:
            out.append(c)
            if recurseGroups and isinstance(c, Group):
                walk(c)
    walk(group or ROOT)
    return [n for n in out if cls is None or n.Class() == cls]


def selectedNode():
    raise ValueError('no node selected')


def filename(node, *a):
    return node['file'].value()


def executeInMainThread(fn, args=(), kwargs=None):
    _main_calls.append(fn)
    return fn(*args, **(kwargs or {}))


def executeInMainThreadWithResult(fn, args=(), kwargs=None):
    return fn(*args, **(kwargs or {}))


def addBeforeFrameRender(fn, args=(), kwargs=None, nodeClass='Write'):
    _callbacks['beforeFrameRender'].append(fn)


def render_frame(f):
    """What a Write render does before each frame."""
    current_frame[0] = f
    for fn in _callbacks['beforeFrameRender']:
        fn()


def message(text):
    messages.append(text)


def getFilename(*a, **k):
    return None


def reset():
    global ROOT
    del _stack[:]
    ROOT = Group('Root', 'Root')
    ROOT.children = []
    _stack.append(ROOT)
    del messages[:]
    _callbacks['beforeFrameRender'] = []


def install(gui=False):
    mod = sys.modules[__name__]
    env['gui'] = gui
    sys.modules['nuke'] = mod
    reset()
    return mod


Root = types.SimpleNamespace(name=lambda: 'Root')
