"""Builds dist/MoteField_diagnose.nk: every kernel of the node, each fed four-channel inputs of the sizes the node
gives it, plus small probes of single Blink features, all on the CPU. Check builds reads which failed to build
(rendering nothing); Test next renders one node at a time and always shows which is next, so if a node crashes
Nuke, the panel said which one.

    python3 node/build_diagnose.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from node import blinklib as bl, nk                 # noqa: E402
from node.build_node import GRAPH, SCALAR           # noqa: E402

PROBES = ('basic', 'float3_param', 'float3_function', 'bool_local', 'loops', 'random_component',
          'array_in_function', 'one_value_vector', 'while_loop', 'int_division')
# features the node itself avoids (to know about, not to worry about)
INFO_ONLY = ('array_in_function', 'one_value_vector', 'while_loop', 'int_division')

PIC = (320, 180)                                    # the stand-in picture size for full-frame inputs
SIZES = {'mf_space_modes': (2, 64), 'mf_space_facts': (3, 1), 'mf_space_bounds': (2, 1),
         'mf_space_bounce': (3, bl.NCELLS), 'mf_space_place': (2 * bl.STATE_COLS, bl.STATE_H),
         'mf_space_motes': (bl.STATE_W, bl.STATE_H), 'mf_space_bins': (bl.NTILES, bl.ROWS1),
         'mf_space_rows': (bl.NTILES, bl.ROWS2),
         'mf_pos': PIC, 'mf_depth': PIC, 'mf_nrm': PIC, 'mf_alb': PIC, 'mf_surface': PIC,
         'mf_facts': (3, 1), 'mf_bounds': (2, 1), 'mf_modes': (2, 64), 'mf_bounce': (3, bl.NCELLS),
         'mf_place': (2 * bl.STATE_COLS, bl.STATE_H), 'mf_motes': (bl.STATE_W, bl.STATE_H),
         'mf_bins': (bl.NTILES, bl.ROWS1), 'mf_rows': (bl.NTILES, bl.ROWS2)}

def order():
    """The nodes in the order to test them: the simple feature probes, then the kernels as the node runs them, then
    the probes of features the node doesn't use."""
    out = ['mfdiag_probe_' + p for p in PROBES if p not in INFO_ONLY]
    out += ['mfdiag_kernel_' + kfile for node, kfile, space, inputs in GRAPH]
    out += ['mfdiag_info_' + p for p in INFO_ONLY]
    return out


# Reads each node's error state without rendering anything (it can't crash Nuke)
CHECK_BUILDS = """import nuke
names = %r
lines = []
for i, name in enumerate(names):
    n = nuke.toNode(name)
    err = False
    try:
        err = n.hasError()
    except Exception:
        pass
    lines.append('%%d %%s: %%s' %% (i + 1, name[7:], 'ERROR' if err else 'no error yet'))
me = nuke.thisNode()
i = max(int(me['step'].value()) - 1, 0)
nxt = ('<b>Next to test: %%d %%s</b>' %% (i + 1, names[i][7:])) if i < len(names) else '<b>All tested.</b>'
me['result'].setValue('<br>'.join(['Builds (nothing rendered):'] + lines) + '<br><br>' + me['log'].value() + nxt)
"""

# Renders one node, then says which is next: if Nuke crashes, the panel said which node did it
TEST_NEXT = """import nuke
names = %r
me = nuke.thisNode()
i = max(int(me['step'].value()) - 1, 0)
if i >= len(names):
    nuke.message('All %%d tested.' %% len(names))
else:
    n = nuke.toNode(names[i])
    try:
        n.sample('rgba.red', 0.5, 0.5)
    except Exception:
        pass
    err = False
    try:
        err = n.hasError()
    except Exception:
        pass
    log = me['log'].value()
    log += '%%d %%s: %%s<br>' %% (i + 1, names[i][7:], 'ERROR' if err else 'ok')
    me['log'].setValue(log)
    me['step'].setValue(i + 2)
    nxt = ('<b>Next to test: %%d %%s</b>' %% (i + 2, names[i + 1][7:])) if i + 1 < len(names) else '<b>All tested.</b>'
    me['result'].setValue(log + nxt)
"""

def constant(name, w, h, x, y):
    return ['Constant {', ' inputs 0', ' channels rgba', ' color {0 0 0 0}',
            ' format "%d %d 0 0 %d %d 1 %s_fmt"' % (w, h, w, h, name), ' name ' + name,
            ' xpos %d' % x, ' ypos %d' % y, '}']


def blink(name, src, n_inputs, x, y, vectorize=True):
    names = order()
    label = ' label %s' % nk.q(str(names.index(name) + 1)) if name in names else ''
    return ['BlinkScript {', ' inputs %d' % n_inputs, ' ProgramGroup 1', ' kernelSource ' + nk.q(src),
            ' rebuild ""', ' rebuild_finalise ""', ' useGPUIfAvailable false'] + \
        ([] if vectorize else [' vectorize false']) + [' name ' + name] + \
        ([label] if label else []) + [' xpos %d' % x, ' ypos %d' % y, '}']


def build(version='12.2 v1'):
    L = ['set cut_paste_input [stack 0]', 'version ' + version]
    x = 0
    for p in PROBES:
        name = ('mfdiag_info_' if p in INFO_ONLY else 'mfdiag_probe_') + p
        L += constant(name + '_in', 64, 64, x, -200)
        L += blink(name, bl.source('probes/' + p), 1, x, -120)
        x += 120
    x = 0
    for node, kfile, space, inputs in GRAPH:
        # every input its own black four-channel Constant of the size the node would give it, the last input first
        # (Nuke takes a node's inputs off the stack with input 0 on top)
        for i, src in reversed(list(enumerate(inputs))):
            w, h = SIZES[src]
            L += constant('mfdiag_%s_in%d' % (kfile, i), w, h, x + 20 * i, 40)
        L += blink('mfdiag_kernel_' + kfile, bl.source(kfile), len(inputs), x, 140, kfile not in SCALAR)
        x += 140
    names = order()
    L += ['NoOp {', ' inputs 0', ' name MoteField_diagnose',
          ' label "Test next"',
          ' addUserKnob {20 diagnose l Diagnose}',
          ' addUserKnob {26 about l "" +STARTLINE T %s}' % nk.q(
              'Every MoteField kernel, and small probes of single Blink features, on the CPU. '
              '<b>Check builds</b> reads which failed to build, without rendering anything. '
              '<b>Test next</b> renders one node at a time: before each press, the line below says which node is '
              'next, so if Nuke crashes, that is the one. After a crash, set Next step to carry on past it. For a '
              'node that says ERROR, view it: Nuke shows the error text in the viewer.'),
          ' addUserKnob {22 check_builds l "Check builds" T %s +STARTLINE}' % nk.q(CHECK_BUILDS % (names,)),
          ' addUserKnob {22 test_next l "Test next" T %s}' % nk.q(TEST_NEXT % (names,)),
          ' addUserKnob {26 result l "" +STARTLINE T %s}' % nk.q('<b>Next to test: 1 %s</b>' % names[0][7:]),
          ' addUserKnob {3 step l "Next step" t %s}' % nk.q('Which node Test next renders next (1 = the first). '
                                                             'Set it to carry on after a crash.'),
          ' addUserKnob {1 log +INVISIBLE}',
          ' step 1',
          ' xpos 0', ' ypos -320', '}', '']
    return '\n'.join(L)


if __name__ == '__main__':
    out = os.path.join(ROOT, 'dist', 'MoteField_diagnose.nk')
    text = build()
    open(out, 'w').write(text)
    print('%s (%d KB)' % (out, len(text) // 1024))
