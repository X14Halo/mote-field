"""Loads a .nk script the way Nuke reads one (as far as the node needs), into small stand-in nodes, so the tests can
check the built MoteField.nk without Nuke: its quoting, its stack wiring, its knobs, and its embedded Python.

    import nkload
    nk = nkload.Script(text)            # nodes, with knobs and inputs; BlinkScript nodes 'compile' their kernels
    nuke = nkload.fake_nuke(nk)         # a module to put in sys.modules['nuke'] for the embedded Python
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from node import blinklib  # noqa: E402

ESC = {'n': '\n', 't': '\t', '\\': '\\', '"': '"', '[': '[', ']': ']', '{': '{', '}': '}', '$': '$'}


def tokens(text):
    """Tcl-ish words: quoted strings (escapes undone), brace groups (kept raw, without the outer braces), bare words.
    Newlines are returned as '\\n' tokens."""
    i, n = 0, len(text)
    out = []
    while i < n:
        c = text[i]
        if c == '\n':
            out.append(('nl', '\n'))
            i += 1
        elif c in ' \t\r':
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while text[j] != '"':
                if text[j] == '\\':
                    nxt = text[j + 1]
                    if nxt not in ESC:
                        raise ValueError('unknown escape \\%s at %d' % (nxt, j))
                    buf.append(ESC[nxt])
                    j += 2
                else:
                    if text[j] == '[':
                        raise ValueError('unescaped [ inside a quoted string at %d: Tcl would run it' % j)
                    if text[j] == '$':
                        raise ValueError('unescaped $ inside a quoted string at %d' % j)
                    buf.append(text[j])
                    j += 1
            out.append(('str', ''.join(buf)))
            i = j + 1
        elif c == '{':
            depth, j = 1, i + 1
            while depth:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                j += 1
            out.append(('brace', text[i + 1:j - 1]))
            i = j
        elif c == '}':
            out.append(('close', '}'))
            i += 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            out.append(('word', text[i:j]))
            i = j
    return out


def lines(text):
    """Statements: lists of tokens, split at newlines; a word followed by '{' at line end opens a node."""
    cur, out = [], []
    for t in tokens(text):
        if t[0] == 'nl':
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


class Anim(object):
    def __init__(self):
        self.k = {}
        self.interp = None

    def keys(self):
        return sorted(self.k)

    def changeInterpolation(self, keys, how):
        self.interp = how


class Knob(object):
    def __init__(self, name, kind='value', size=1, default=None):
        self._name, self.kind, self.size = name, kind, size
        self._value = default if default is not None else ([0.0] * size if size > 1 else 0.0)
        self.expr = {}
        self.anim = {}

    def name(self):
        return self._name

    def value(self, index=None):
        if isinstance(self._value, list) and index is not None:
            return self._value[index]
        return self._value

    def getValue(self, index=None):
        return self.value(index)

    def setValue(self, v, index=None):
        if index is not None and isinstance(self._value, list):
            self._value[index] = v
        else:
            self._value = list(v) if isinstance(v, (list, tuple)) else v
        return True

    def setExpression(self, e, index=-1):
        if index >= self.size:
            raise IndexError('%s has %d values' % (self._name, self.size))
        self.expr[max(index, 0)] = e
        return True

    def clearAnimated(self, index=None):
        if index is None:
            self.anim = {}
        else:
            self.anim.pop(index, None)

    def setAnimated(self, index=None):
        self.anim.setdefault(index or 0, Anim())

    def setValueAt(self, v, frame, index=None):
        self.anim.setdefault(index or 0, Anim()).k[frame] = v

    def animation(self, index):
        return self.anim.get(index)

    def isAnimated(self, index=None):
        return bool(self.anim)

    def execute(self):
        return run_python(self.node, self.value(), self)


KNOB_TYPES = {7: ('float', 1), 3: ('int', 1), 6: ('bool', 1), 4: ('enum', 1), 13: ('xyz', 3), 18: ('rgb', 3),
              1: ('string', 1), 26: ('text', 1), 22: ('python', 1), 20: ('tab', 1), 41: ('link', 1)}


class Node(object):
    def __init__(self, cls, script):
        self.cls, self.script = cls, script
        self._knobs = {}
        self.inputs = []
        self.children = None            # a Group's nodes, by name
        self.parent = None
        self.kernel = None
        self.add('name', Knob('name', 'string', default=''))

    def add(self, name, k):
        k.node = self
        self._knobs[name] = k
        return k

    def Class(self):
        return self.cls

    def name(self):
        return self._knobs['name'].value()

    def fullName(self):
        return (self.parent.fullName() + '.' if self.parent is not None and self.parent.parent is not None else '') + \
            self.name()

    def knob(self, name):
        return self._knobs.get(name)

    def knobs(self):
        return dict(self._knobs)

    def __getitem__(self, name):
        if name not in self._knobs:
            raise NameError('%s has no knob %s' % (self.name(), name))
        return self._knobs[name]

    def input(self, i):
        if self.cls == 'Group':
            return self.script.outside_inputs.get(i)
        return self.inputs[i] if i < len(self.inputs) else None

    def setInput(self, i, n):
        while len(self.inputs) <= i:
            self.inputs.append(None)
        self.inputs[i] = n
        return True

    def node(self, name):
        return (self.children or {}).get(name)

    # what the embedded Python asks of image nodes; tests can override
    def channels(self):
        return getattr(self, '_channels', [])

    def metadata(self, key=None, time=None, view=None):
        md = self.script.metadata(self, time)
        return md if key is None else md.get(key)

    def firstFrame(self):
        return getattr(self, '_first', 1)

    def lastFrame(self):
        return getattr(self, '_last', 1)

    def sample(self, chan, x, y, *a, **k):
        return self.script.sample(self, chan, x, y)

    def width(self):
        return self.script.size(self)[0]

    def height(self):
        return self.script.size(self)[1]


class Script(object):
    """A loaded script. metadata(), sample() and size() are hooks the tests replace to stand in for images."""

    def metadata(self, node, frame):
        return {}

    def sample(self, node, chan, x, y):
        return 0.0

    def size(self, node):
        return (1920, 1080)


def parse_node_blocks(text):
    """Splits the script into statements, with a node's body as a list of knob statements: [(kind, data)].
    kind: 'set', 'push', 'version', 'node' (cls, [knob statements]), 'end_group'."""
    out = []
    st = lines(text)
    i = 0
    while i < len(st):
        s = st[i]
        w = s[0][1]
        if w == 'set':
            out.append(('set', s[1][1]))
        elif w == 'push':
            out.append(('push', s[1][1]))
        elif w == 'version':
            out.append(('version', ' '.join(t[1] for t in s[1:])))
        elif w == 'end_group':
            out.append(('end_group', None))
        elif len(s) == 2 and s[0][0] == 'word' and s[1][0] == 'brace':
            # the whole body was read as one brace group: split it into knob statements
            out.append(('node', (w, lines(s[1][1]))))
        else:
            raise ValueError('cannot read statement %r' % (s[:3],))
        i += 1
    return out


def load(text, outside_inputs=None):
    """A Script with the nodes of an .nk snippet: Script.root.children, Script.groups."""
    sc = Script.__new__(Script)
    sc.text = text
    sc.root = Node('Root', sc)
    sc.root.children = {}
    sc.outside_inputs = dict(outside_inputs or {})
    sc.created = []
    stack, vars_ = [], {}
    group = sc.root
    for kind, data in parse_node_blocks(text):
        if kind == 'set':
            vars_[data] = stack[-1] if stack else None
        elif kind == 'push':
            stack.append(None if data == '0' else vars_[data[1:]] if data.startswith('$') else None)
        elif kind == 'end_group':
            group = group.parent
            if group is None:
                raise ValueError('end_group without a group')
        elif kind == 'node':
            cls, body = data
            n = Node(cls, sc)
            n.parent = group
            ninputs = 1 if cls not in ('Input', 'Constant') else 0
            knobs = []
            for ks in body:
                if ks[0][1] == 'inputs':
                    ninputs = int(ks[1][1].split('+')[0])
                else:
                    knobs.append(ks)
            ins = []
            for _ in range(ninputs):
                ins.append(stack.pop() if stack else None)
            n.inputs = ins                     # input 0 = what was on top of the stack
            _apply_knobs(n, knobs)
            group.children[n.name()] = n
            stack.append(n)
            sc.created.append(n)
            if cls == 'Group':
                n.children = {}
                group = n
                stack = stack[:-1] + [n]
                # a group's insides start a fresh stack in Nuke; keep going with ours (nothing inside uses it)
        else:
            pass
    sc.stack = stack
    return sc


def _value(tok):
    kind, v = tok
    if kind == 'brace':
        inner = v.strip()
        if inner.startswith('{') and inner.endswith('}') and inner.count('{') == 1:
            return ('expr', inner[1:-1].strip().strip('"'))
        parts = [p for p in re.findall(r'\{\{[^}]*\}\}|[^\s]+', inner)]
        vals = []
        for p in parts:
            if p.startswith('{{'):
                vals.append(('expr', p[2:-2]))
            else:
                try:
                    vals.append(float(p))
                except ValueError:
                    vals.append(p)
        return vals
    if kind == 'word':
        if v in ('true', 'false'):
            return v == 'true'
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v
    return v


def _apply_knobs(n, statements):
    for ks in statements:
        name = ks[0][1]
        if name == 'addUserKnob':
            spec = lines(ks[1][1])[0]
            code = int(spec[0][1])
            kname = spec[1][1]
            kind, size = KNOB_TYPES.get(code, ('other', 1))
            k = Knob(kname, kind, size)
            k.code = code
            j = 2
            while j < len(spec):
                tag = spec[j][1]
                if tag in ('l', 't', 'T', 'M', 'R', 'n') and j + 1 < len(spec):
                    if tag == 'R':
                        k.range = (float(spec[j + 1][1]), float(spec[j + 2][1]))
                        j += 3
                        continue
                    setattr(k, {'l': 'label', 't': 'tooltip', 'T': 'payload', 'M': 'menu', 'n': 'group'}[tag],
                            spec[j + 1][1])
                    j += 2
                else:
                    if tag.startswith('+'):
                        k.flags = getattr(k, 'flags', set()) | {tag[1:]}
                    j += 1
            if kind == 'text':
                k._value = getattr(k, 'payload', '')
            if kind == 'python':
                k._value = getattr(k, 'payload', '')
            if kind == 'enum':
                k.options = lines(k.menu)[0] if getattr(k, 'menu', '') else []
                k.options = [t[1] for t in k.options]
                k._value = 0
            if kind in ('bool',):
                k._value = False
            if kind == 'string':
                k._value = ''
            if kind == 'link':
                k.target = k.payload
            n.add(kname, k)
            continue
        if len(ks) < 2:
            raise ValueError('%s: knob %s without a value' % (n.cls, name))
        v = _value(ks[1])
        k = n.knob(name)
        if k is None:
            k = n.add(name, Knob(name, 'builtin', len(v) if isinstance(v, list) else 1))
        if isinstance(v, tuple) and v[0] == 'expr':
            k.expr[0] = v[1]
        elif isinstance(v, list):
            vals = []
            for i, x in enumerate(v):
                if isinstance(x, tuple):
                    k.expr[i] = x[1]
                    vals.append(0.0)
                else:
                    vals.append(x)
            k._value = vals
        elif getattr(k, 'kind', '') == 'enum' and isinstance(v, str):
            if v not in k.options:
                raise ValueError('%s: %r is not one of %s' % (name, v, k.options))
            k._value = k.options.index(v)
        else:
            k._value = v
        if n.cls == 'BlinkScript' and name == 'rebuild':
            _compile(n)


def _compile(n):
    """What 'rebuild' does in Nuke: compiles kernelSource and makes a knob per param (KernelName_param)."""
    src = n['kernelSource'].value()
    kname = blinklib.kernel_name(src)
    n.kernel = kname
    n.source = src
    sizes = {'float': 1, 'int': 1, 'bool': 1, 'float2': 2, 'float3': 3, 'float4': 4, 'int2': 2}
    for t, p in blinklib.params(src):
        n.add('%s_%s' % (kname, p), Knob('%s_%s' % (kname, p), 'param', sizes[t]))
    n.add('useGPUIfAvailable', Knob('useGPUIfAvailable', 'bool', 1, True)) if n.knob('useGPUIfAvailable') is None \
        else None


# ---------------------------------------------------------------------------------------------------------------------
#  The nuke module the embedded Python sees
# ---------------------------------------------------------------------------------------------------------------------
_CTX = {'node': None, 'knob': None, 'group': None, 'script': None, 'messages': []}


def run_python(node, code, knob=None, group=None):
    old = dict(_CTX)
    _CTX.update(node=node, knob=knob, group=group if group is not None else (node if node.cls == 'Group' else
                                                                            node.parent))
    try:
        exec(compile(code, '<%s>' % (knob.name() if knob else 'script'), 'exec'), {'__name__': '__nk__'})
    finally:
        _CTX.update(node=old['node'], knob=old['knob'], group=old['group'])


def fake_nuke(script):
    m = types.ModuleType('nuke')
    m.LINEAR = 'linear'
    m.thisNode = lambda: _CTX['node']
    m.thisKnob = lambda: _CTX['knob']
    m.thisGroup = lambda: _CTX['group']
    m.message = lambda t: _CTX['messages'].append(t)
    m.toNode = lambda name: script.root.children.get(name)
    _CTX['script'] = script
    return m


def messages():
    return _CTX['messages']
