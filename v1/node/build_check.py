"""Builds MoteField_check.nk: a two-node script that shows whether BlinkScript runs in this Nuke and does exact
integer maths (a green square in the corner) or not (red)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from node import blinklib, nk      # noqa: E402


def build(version='15.0 v1'):
    src = blinklib.source('check')
    return '\n'.join([
        'set cut_paste_input [stack 0]',
        'version ' + version,
        'Constant {',
        ' inputs 0',
        ' channels rgb',
        ' color {0.02 0.02 0.02 1}',
        ' format "1920 1080 0 0 1920 1080 1 HD_1080"',
        ' name MoteField_check_input',
        ' selected true',
        ' xpos 0',
        ' ypos -100',
        '}',
        'BlinkScript {',
        ' ProgramGroup 1',
        ' kernelSource ' + nk.q(src),
        ' rebuild ""',
        ' rebuild_finalise ""',
        ' name MoteField_check',
        ' label "green square = good"',
        ' selected true',
        ' xpos 0',
        ' ypos 0',
        '}',
        ''])


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'MoteField_check.nk'
    open(out, 'w').write(build())
    older = os.path.join(os.path.dirname(os.path.abspath(out)), 'MoteField_check_nuke12.nk')
    open(older, 'w').write(build('12.2 v1'))
    print(out, older)
