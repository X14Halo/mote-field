"""Builds dist/MoteField_diagnose.nk: every kernel of the node, each fed four-channel inputs of the sizes the node
gives it, plus small probes of single Blink features, all on the CPU. A Report button says which fail to build or
run, so a Nuke that can't run the node shows exactly what it trips on.

    python3 node/build_diagnose.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from node import blinklib as bl, nk                 # noqa: E402
from node.build_node import GRAPH                   # noqa: E402

PROBES = ('basic', 'float3_param', 'float3_function', 'bool_local', 'loops', 'random_component',
          'array_in_function', 'one_value_vector', 'while_loop')
# features the node itself avoids (to know about, not to worry about)
INFO_ONLY = ('array_in_function', 'one_value_vector', 'while_loop')

PIC = (320, 180)                                    # the stand-in picture size for full-frame inputs
SIZES = {'mf_space_modes': (2, 64), 'mf_space_facts': (3, 1), 'mf_space_bounds': (2, 1),
         'mf_space_bounce': (3, bl.NCELLS), 'mf_space_place': (2 * bl.STATE_COLS, bl.STATE_H),
         'mf_space_motes': (bl.STATE_W, bl.STATE_H), 'mf_space_bins': (bl.NTILES, bl.ROWS1),
         'mf_space_rows': (bl.NTILES, bl.ROWS2),
         'mf_pos': PIC, 'mf_depth': PIC, 'mf_nrm': PIC, 'mf_alb': PIC, 'mf_surface': PIC,
         'mf_facts': (3, 1), 'mf_bounds': (2, 1), 'mf_modes': (2, 64), 'mf_bounce': (3, bl.NCELLS),
         'mf_place': (2 * bl.STATE_COLS, bl.STATE_H), 'mf_motes': (bl.STATE_W, bl.STATE_H),
         'mf_bins': (bl.NTILES, bl.ROWS1), 'mf_rows': (bl.NTILES, bl.ROWS2)}

REPORT = '''import nuke
lines = []
bad = 0
for n in sorted(nuke.allNodes('BlinkScript'), key=lambda n: n.name()):
    if not n.name().startswith('mfdiag_'):
        continue
    try:
        n.sample('rgba.red', 0.5, 0.5)
    except Exception:
        pass
    err = False
    try:
        err = n.hasError()
    except Exception:
        pass
    if err:
        bad += 1
    info = ' (the node does not use this)' if n.name().startswith('mfdiag_info_') else ''
    lines.append('%s: %s%s' % (n.name()[7:], 'ERROR' if err else 'ok', info))
lines.insert(0, '%d of %d failed' % (bad, len(lines)))
nuke.thisNode()['result'].setValue('<br>'.join(lines))
nuke.message('\\n'.join(lines))
'''


def constant(name, w, h, x, y):
    return ['Constant {', ' inputs 0', ' channels rgba', ' color {0 0 0 0}',
            ' format "%d %d 0 0 %d %d 1 %s_fmt"' % (w, h, w, h, name), ' name ' + name,
            ' xpos %d' % x, ' ypos %d' % y, '}']


def blink(name, src, n_inputs, x, y):
    return ['BlinkScript {', ' inputs %d' % n_inputs, ' ProgramGroup 1', ' kernelSource ' + nk.q(src),
            ' rebuild ""', ' rebuild_finalise ""', ' useGPUIfAvailable false', ' name ' + name,
            ' xpos %d' % x, ' ypos %d' % y, '}']


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
        L += blink('mfdiag_kernel_' + kfile, bl.source(kfile), len(inputs), x, 140)
        x += 140
    L += ['NoOp {', ' inputs 0', ' name MoteField_diagnose',
          ' label "press Report"',
          ' addUserKnob {20 diagnose l Diagnose}',
          ' addUserKnob {26 about l "" +STARTLINE T %s}' % nk.q(
              'Builds every MoteField kernel, and a few probes of single Blink features, on the CPU. '
              'Press Report, then send the result. For a node that says ERROR, view it: Nuke shows the '
              'error text in the viewer.'),
          ' addUserKnob {22 report l Report T %s +STARTLINE}' % nk.q(REPORT),
          ' addUserKnob {26 result l "" +STARTLINE T ""}',
          ' xpos 0', ' ypos -320', '}', '']
    return '\n'.join(L)


if __name__ == '__main__':
    out = os.path.join(ROOT, 'dist', 'MoteField_diagnose.nk')
    text = build()
    open(out, 'w').write(text)
    print('%s (%d KB)' % (out, len(text) // 1024))
