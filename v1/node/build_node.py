"""Builds dist/MoteField.nk: the whole tool as one Group node to paste or drag into a Nuke 15 script.

Nothing to install and nothing written to disk: the node carries its kernels (BlinkScript, which plain Nuke runs but
cannot edit) and its Python (in a hidden knob, run from memory).

    python3 node/build_node.py [out.nk]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from motefield import __version__, schema       # noqa: E402
from node import blinklib as bl, nk              # noqa: E402

# ---------------------------------------------------------------------------------------------------------------------
#  The internal graph
# ---------------------------------------------------------------------------------------------------------------------
# kernel node: (kernel file, space size or None, inputs in the kernel's image order)
GRAPH = [
    ('mf_modes', 'modes', (2, 64), ['mf_space_modes']),
    ('mf_facts', 'facts', (3, 1), ['mf_space_facts', 'mf_pos', 'mf_depth']),
    ('mf_surface', 'surface', None, ['mf_pos', 'mf_depth', 'mf_facts']),
    ('mf_bounds', 'bounds', (2, 1), ['mf_space_bounds', 'mf_surface']),
    ('mf_bounce', 'bounce', (3, bl.NCELLS), ['mf_space_bounce', 'mf_surface', 'mf_bounds', 'mf_nrm', 'mf_alb']),
    ('mf_place', 'place', (2 * bl.STATE_COLS, bl.STATE_H), ['mf_space_place', 'mf_modes', 'mf_surface', 'mf_bounds']),
    ('mf_motes', 'motes', (bl.STATE_W, bl.STATE_H), ['mf_space_motes', 'mf_place', 'mf_surface', 'mf_bounce']),
    ('mf_bins', 'bins', (bl.NTILES, bl.ROWS1), ['mf_space_bins', 'mf_motes', 'mf_surface']),
    ('mf_rows', 'rows', (bl.NTILES, bl.ROWS2), ['mf_space_rows', 'mf_bins']),
    ('mf_draw', 'draw', None, ['mf_surface', 'mf_motes', 'mf_bins', 'mf_rows']),
]

UNITS_EXPR = 'parent.units==0?0.01:parent.units==1?1:parent.units==2?0.001:parent.units==3?0.0254:0.3048'
AIR = ('drift', 'lift', 'eddies', 'micro', 'thermal', 'tumble')
VEC3 = {'box_center': 'xyz', 'box_size': 'xyz', 'fade_neg': 'xyz', 'fade_pos': 'xyz', 'light_rgb': 'rgb',
        'tint': 'rgb'}
CAMS = {'cam': '', 'o': '(frame+parent.mf_t_open)', 'c': '(frame+parent.mf_t_close)'}


def expression(param):
    """The expression a kernel param is linked to (a string, or one per component)."""
    if param == 'frame':
        return 'frame'
    if param == 'su':
        return UNITS_EXPR
    if param == 'density':
        return 'parent.density*parent.master_amount'
    if param == 'brightness':
        return 'parent.brightness*parent.master_brightness'
    if param in AIR:
        return 'parent.%s*parent.master_air' % param
    if param in VEC3:
        return ['parent.%s.%s' % (param, c) for c in VEC3[param]]
    if param in ('p_premult', 'z_kind', 'z_premult'):
        return 'parent.%s-1' % param
    if param == 'alpha_mode':
        return 'parent.alpha_out'
    if param == 'use_albedo':
        return 'parent.mf_use_albedo'
    if param == 'held':
        return None
    for prefix, at in CAMS.items():
        for f in ('pos', 'right', 'up', 'back'):
            if param == prefix + '_' + f:
                return ['parent.cam_%s.%s%s' % (f, c, at) for c in 'xyz']
        if param == prefix + '_th':
            return 'parent.cam_th' + at
    return 'parent.' + param


# ---------------------------------------------------------------------------------------------------------------------
#  The node's knobs
# ---------------------------------------------------------------------------------------------------------------------
B = schema.BY_KEY

# knobs that are not settings in the schema: name -> (type, default, label, help, extra)
EXTRA = {
    'shadows': ('enum', 0, 'Shadows from', 'Where shadows on the dust come from: what the utility pass shows of the set, '
                'or none (all dust in the light).', ['utility pass', 'none']),
    'output_mode': ('enum', 0, 'Output', 'What the node puts out: the dust alone (to plus or over in your comp), or '
                    'over / plus the plate on the plate input, to judge it.', ['dust', 'dust over plate',
                                                                                'dust plus plate']),
    'alpha_out': ('enum', 0, 'Alpha', B['alpha'].help, ['none', 'matte']),
    'use_gpu': ('bool', True, 'Use GPU', 'Render on the GPU when there is one (much faster). The dust is the same '
                'either way.', None),
    'p_premult': ('enum', 0, 'Positions premultiplied', 'Whether the position layer was premultiplied by coverage. '
                  'auto = worked out from the pass (by reprojecting edge pixels).', ['auto', 'no', 'yes']),
    'z_kind': ('enum', 0, 'Depth is distance along the view axis', 'auto = worked out by comparing with the '
               'positions; no = distance along each pixel\'s ray.', ['auto', 'no', 'yes']),
    'z_premult': ('enum', 0, 'Depth premultiplied', 'Whether the depth layers were premultiplied by coverage. auto '
                  '= worked out from the pass.', ['auto', 'no', 'yes']),
}

TABS = [
    ('MoteField', [
        ('status',),
        ('buttons', [('read_camera', 'Read camera'), ('fit_box', 'Fit box'), ('check_shot', 'Check shot')]),
        ('div', 'Masters'),
        'master_amount', 'master_brightness', 'master_air', 'steady',
        ('div', 'Key light'),
        'light_az', 'light_el', 'light_int', 'light_k', 'light_rgb',
        ('div', 'Dust'),
        'flakes', 'fibers', 'fluff', 'fade',
        ('div', 'Output'),
        'output_mode', 'alpha_out', 'holdout', 'dof', 'mb', 'use_gpu',
        ('report',),
    ]),
    ('Dust & air', [
        ('div', 'Dust volume'), 'box_center', 'box_size', 'box_rot', 'fade_neg', 'fade_pos',
        ('div', 'Dust'), 'density', 'seed', 'size_min', 'size_max', 'size_bias', 'flake_scale', 'fiber_scale',
        'fluff_scale', 'max_motes',
        ('div', 'Dust look'), 'brightness', 'forward', 'shadow_fill', 'glint', 'tumble', 'tint', 'cam_fade_near',
        'cam_fade_far',
        ('div', 'Air'), 'drift', 'drift_dir', 'eddies', 'eddy_size', 'micro', 'thermal', 'lift', 'settle', 'speed',
    ]),
    ('Light & lens', [
        ('div', 'Light'), 'bounce', 'shadows', 'interior', 'shadow_soft', 'shadow_thick', 'albedo',
        ('div', 'Holdouts'), 'fg_near', 'fg_far',
        ('div', 'Lens (0 = from the camera in the pass)'), 'focal', 'film', 'fstop', 'focus', 'front_mult',
        'back_mult',
        ('div', 'Bokeh'), 'max_bokeh', 'blades', 'cateye', 'anamorphic', 'field_curv', 'fstop_full', 'bokeh_rot',
        'bokeh_falloff',
        ('div', 'Shutter'), 'shutter', 'shutter_mode', 'shutter_shape', 'matte_gain',
    ]),
    ('Shot', [
        ('div', 'Frames and units'), 'first', 'last', 'fps', 'units',
        ('div', 'Layers of the utility pass'),
        ('link', 'layer_pos', 'Position', 'mf_pos.in'),
        ('link', 'layer_cov', 'Coverage', 'mf_pos.in2'),
        ('link', 'layer_depth', 'Depth', 'mf_depth.in'),
        ('link', 'layer_zraw', 'Unfiltered depth', 'mf_depth.in2'),
        ('link', 'layer_nrm', 'Normals', 'mf_nrm.in'),
        ('link', 'layer_alb', 'Surface colour', 'mf_alb.in'),
        ('buttons', [('find_layers', 'Find layers')]),
        'p_premult', 'z_kind', 'z_premult',
        ('div', 'Camera (read from the pass header)'),
        ('cam',),
        ('buttons', [('repair', 'Repair node')]),
        ('version',),
    ]),
]

HIDDEN = [('mf_cam_src', 'string', ''), ('mf_layers_set', 'bool', False), ('mf_shutter_read', 'bool', False),
          ('mf_use_albedo', 'int', 0), ('mf_version', 'string', '')]


LOADER = '''import sys, types, nuke
def _mf(grp):
    src = grp['mf_py'].value()
    m = sys.modules.get('_motefield_node')
    if m is None or getattr(m, '_mf_src', None) != src:
        m = types.ModuleType('_motefield_node')
        exec(compile(src, 'MoteField', 'exec'), m.__dict__)
        m._mf_src = src
        sys.modules['_motefield_node'] = m
    return m
'''


def _tooltip(p):
    t = p.help
    if p.unit:
        t += ' (%s)' % p.unit
    return t


def _range(p):
    lo, hi = p.soft if p.soft else (p.hard if p.hard else (0, 1))
    return lo, hi


def knob_lines(key):
    """addUserKnob line(s) and value line for one setting."""
    if key in EXTRA:
        t, default, label, help_, opts = EXTRA[key]
        soft = None
    else:
        p = B[key]
        t, default, label, help_, opts = p.type, p.default, p.label, _tooltip(p), p.options
        if key == 'shutter_mode':
            opts = p.options
        soft = _range(p)
    head = '%s l %s t %s' % (key, nk.word(label), nk.q(help_))
    if t == 'float':
        lo, hi = soft if soft else (0, 1)
        return ['addUserKnob {7 %s R %s %s}' % (head, nk.num(lo), nk.num(hi)), '%s %s' % (key, nk.num(default))]
    if t == 'int':
        return ['addUserKnob {3 %s}' % head, '%s %d' % (key, int(default))]
    if t == 'bool':
        return ['addUserKnob {6 %s +STARTLINE}' % head, '%s %s' % (key, 'true' if default else 'false')]
    if t == 'enum':
        menu = ' '.join(nk.word(o) for o in opts)
        idx = default if isinstance(default, int) else opts.index(default)
        return ['addUserKnob {4 %s M {%s}}' % (head, menu), '%s %s' % (key, nk.word(opts[idx]))]
    if t == 'xyz':
        return ['addUserKnob {13 %s}' % head, '%s {%s}' % (key, ' '.join(nk.num(v) for v in default))]
    if t == 'rgb':
        return ['addUserKnob {18 %s}' % head, '%s {%s}' % (key, ' '.join(nk.num(v) for v in default))]
    raise ValueError('no knob type for %s (%s)' % (key, t))


def button_cmd(which):
    return LOADER + "_mf(nuke.thisNode()).button(nuke.thisNode(), %r)\n" % which


def group_knobs(py_source):
    out = []
    for tab, items in TABS:
        out.append('addUserKnob {20 %s l %s}' % (nk.word(tab.replace(' & ', '_').replace(' ', '_').lower()),
                                                 nk.word(tab)))
        div = 0
        for it in items:
            if isinstance(it, str):
                out += knob_lines(it)
            elif it[0] == 'div':
                div += 1
                out.append('addUserKnob {26 div_%s_%d l %s}' % (tab[:4].replace(' ', '').lower(), div,
                                                               nk.q('<b>%s</b>' % it[1])))
            elif it[0] == 'status':
                out.append('addUserKnob {26 mf_status l "" +STARTLINE T %s}' %
                           nk.q('connect the utility pass to the <b>utility</b> input'))
            elif it[0] == 'report':
                out.append('addUserKnob {26 mf_report l "" +STARTLINE T ""}')
            elif it[0] == 'buttons':
                for i, (key, label) in enumerate(it[1]):
                    out.append('addUserKnob {22 %s l %s T %s%s}' % (key, nk.word(label), nk.q(button_cmd(key)),
                                                                   ' +STARTLINE' if i == 0 else ''))
            elif it[0] == 'link':
                _, key, label, target = it
                out.append('addUserKnob {41 %s l %s T %s}' % (key, nk.word(label), target))
            elif it[0] == 'cam':
                for key, label in (('cam_pos', 'Position'), ('cam_right', 'Right'), ('cam_up', 'Up'),
                                   ('cam_back', 'Back')):
                    out.append('addUserKnob {13 %s l %s}' % (key, nk.word(label)))
                out.append('cam_pos {0 150 400}')
                out.append('cam_right {1 0 0}')
                out.append('cam_up {0 1 0}')
                out.append('cam_back {0 0 1}')
                out.append('addUserKnob {7 cam_th l "tan(half view angle)" R 0 2}')
                out.append('cam_th 0.5')
                for key, label in (('hdr_focal', 'Focal length (mm)'), ('hdr_film', 'Film back width (mm)'),
                                   ('hdr_fstop', 'f-stop'), ('hdr_focus', 'Focus distance'),
                                   ('hdr_cateye', 'Cat-eye'), ('hdr_anamorphic', 'Anamorphic aspect'),
                                   ('hdr_field_curv', 'Field curvature'), ('hdr_fstop_full', 'Full-aperture f-stop')):
                    out.append('addUserKnob {7 %s l %s R 0 100}' % (key, nk.word(label)))
                out.append('addUserKnob {3 hdr_blades l "Blade count"}')
            elif it[0] == 'version':
                out.append('addUserKnob {26 mf_about l "" +STARTLINE T %s}' %
                           nk.q('MoteField %s: dust motes in comp from the utility pass. Nothing to install; '
                                'it writes no files.' % __version__))
    # hidden
    for key, t, default in HIDDEN:
        code = {'string': 1, 'bool': 6, 'int': 3}[t]
        out.append('addUserKnob {%d %s +INVISIBLE}' % (code, key))
        if t == 'string':
            out.append('%s %s' % (key, nk.q(default)))
        elif t == 'bool':
            out.append('%s %s' % (key, 'true' if default else 'false'))
        else:
            out.append('%s %d' % (key, default))
    out.append('addUserKnob {7 mf_t_open +INVISIBLE}')
    out.append('mf_t_open {{mb?(shutter_mode==0?-shutter/360:shutter_mode==1?-shutter/720:0):0}}')
    out.append('addUserKnob {7 mf_t_close +INVISIBLE}')
    out.append('mf_t_close {{mb?(shutter_mode==0?0:shutter_mode==1?shutter/720:shutter/360):0}}')
    out.append('addUserKnob {1 mf_py +INVISIBLE}')
    out.append('mf_py ' + nk.q(py_source))
    return out


def python_source():
    links, kernels, wires = {}, {}, {}
    for node, kfile, space, inputs in GRAPH:
        src = bl.source(kfile)
        kernels[node] = bl.kernel_name(src)
        imgs = bl.images(src)[:-1]
        assert len(imgs) == len(inputs), (node, imgs, inputs)
        wires[node] = list(inputs)
        lk = {}
        for t, p in bl.params(src):
            e = expression(p)
            if e is not None:
                lk[p] = e
        links[node] = lk
    wires.update({'mf_pos': ['utility'], 'mf_depth': ['utility'], 'mf_nrm': ['utility'], 'mf_alb': [None],
                  'mf_crop': ['mf_draw'], 'mf_over': ['plate', 'mf_crop'], 'mf_plus': ['plate', 'mf_crop'],
                  'mf_output': ['mf_crop', 'mf_over', 'mf_plus']})
    text = open(os.path.join(HERE, 'nodepy.py')).read()
    text = text.replace("VERSION = '@VERSION@'", 'VERSION = %r' % __version__)
    text = text.replace('LINKS = {}            ', 'LINKS = %r  ' % links, 1)
    text = text.replace('KERNELS = {}          ', 'KERNELS = %r  ' % kernels, 1)
    text = text.replace('WIRES = {}            ', 'WIRES = %r  ' % wires, 1)
    return text


def blink_node(name, kfile, n_inputs, xpos, ypos):
    src = bl.source(kfile)
    on_create = LOADER + ("_g = nuke.thisGroup()\n"
                          "try:\n"
                          "    _mf(_g).link_kernel(_g, %r)\n"
                          "except Exception:\n"
                          "    pass\n" % name)
    return ['BlinkScript {',
            ' inputs %d' % n_inputs,
            ' kernelSource ' + nk.q(src),
            ' rebuild ""',
            ' rebuild_finalise ""',
            ' useGPUIfAvailable {{parent.use_gpu}}',
            ' name ' + name,
            ' onCreate ' + nk.q(on_create),
            ' xpos %d' % xpos,
            ' ypos %d' % ypos,
            '}']


def constant(name, w, h, xpos, ypos):
    return ['Constant {',
            ' inputs 0',
            ' channels rgba',
            ' color {0 0 0 0}',
            ' format "%d %d 0 0 %d %d 1 %s_fmt"' % (w, h, w, h, name),
            ' name ' + name,
            ' xpos %d' % xpos,
            ' ypos %d' % ypos,
            '}']


def shuffle(name, src_in, in2, mapping, xpos, ypos):
    r, g, b, a = mapping
    return ['Shuffle {',
            ' in ' + src_in,
            ' in2 ' + in2,
            ' red %s' % r,
            ' green %s' % g,
            ' blue %s' % b,
            ' alpha %s' % a,
            ' name ' + name,
            ' xpos %d' % xpos,
            ' ypos %d' % ypos,
            '}']


def build(version='15.0 v1'):
    py = python_source()
    L = ['set cut_paste_input [stack 0]', 'version ' + version, 'push 0', 'push 0', 'push $cut_paste_input', 'Group {',
         ' inputs 3',
         ' name MoteField1',
         ' tile_color 0x6b5a3aff',
         ' label "\\[value output_mode]"',
         ' note_font "Verdana Bold"',
         ' help %s' % nk.q('MoteField %s: drifting dust motes for comp, lit and placed from the shot\'s utility pass '
                          '(position, depth, normals; the camera from the EXR header), with the lens\'s defocus and '
                          'motion blur and holdouts against the set. Plus or over it on the plate. Inputs: utility (the '
                          'utility pass Read), plate (optional, to judge the look), colour (optional: a pass with a '
                          'surface colour layer, for bounce light).' % __version__),
         ' onCreate ' + nk.q(LOADER + "_n = nuke.thisNode()\ntry:\n    _mf(_n).created(_n)\nexcept Exception:\n    pass\n"),
         ' knobChanged ' + nk.q(LOADER + "_n = nuke.thisNode()\n_k = nuke.thisKnob()\n"
                                          "if _k.name() in ('inputChange', 'showPanel', 'density', 'master_amount', "
                                          "'box_size', 'units', 'max_motes'):\n"
                                          "    _mf(_n).knob_changed(_n, _k)\n"),
         ]
    L += [' ' + line for line in group_knobs(py)]
    L.append('}')
    # inside the group
    L += ['Input {', ' inputs 0', ' name colour', ' number 2', ' xpos 400', ' ypos -600', '}', 'set Ncolour [stack 0]',
          'Input {', ' inputs 0', ' name plate', ' number 1', ' xpos 900', ' ypos -600', '}', 'set Nplate [stack 0]',
          'Input {', ' inputs 0', ' name utility', ' xpos 0', ' ypos -600', '}', 'set Nutility [stack 0]']
    L += shuffle('mf_pos', 'none', 'rgba', ('red', 'green', 'blue', 'alpha2'), 0, -500) + ['set Nmf_pos [stack 0]']
    L += ['push $Nutility'] + shuffle('mf_depth', 'none', 'none', ('red', 'red2', 'black', 'black'), 120, -500) + \
        ['set Nmf_depth [stack 0]']
    L += ['push $Nutility'] + shuffle('mf_nrm', 'none', 'none', ('red', 'green', 'blue', 'black'), 240, -500) + \
        ['set Nmf_nrm [stack 0]']
    L += ['push $Nutility'] + shuffle('mf_alb', 'none', 'none', ('red', 'green', 'blue', 'black'), 360, -500) + \
        ['set Nmf_alb [stack 0]']
    y = -400
    for node, kfile, space, inputs in GRAPH:
        x = 0
        # Nuke takes a node's inputs off the stack with input 0 on top: push the last input first
        for src in reversed(inputs[1:]):
            L.append('push $N' + src)
        if space is not None:
            L += constant('mf_space_' + kfile, space[0], space[1], -200, y)
            L.append('set Nmf_space_%s [stack 0]' % kfile)
        else:
            L.append('push $N' + inputs[0])
        L += blink_node(node, kfile, len(inputs), x, y)
        L.append('set N%s [stack 0]' % node)
        y += 60
    L += ['Crop {', ' box {0 0 {{input.width}} {{input.height}}}', ' crop true', ' name mf_crop', ' xpos 0',
          ' ypos %d' % y, '}', 'set Nmf_crop [stack 0]']
    y += 60
    for name, op, x in (('mf_over', 'over', 200), ('mf_plus', 'plus', 400)):
        L += ['push $Nmf_crop', 'push $Nplate', 'Merge2 {', ' inputs 2', ' operation ' + op, ' name ' + name,
              ' xpos %d' % x, ' ypos %d' % y, '}', 'set N%s [stack 0]' % name]
    y += 60
    L += ['push $Nmf_plus', 'push $Nmf_over', 'push $Nmf_crop', 'Switch {', ' inputs 3',
          ' which {{parent.output_mode}}', ' name mf_output', ' xpos 0', ' ypos %d' % y, '}']
    y += 60
    L += ['Output {', ' name Output1', ' onCreate ' + nk.q(
        LOADER + "_g = nuke.thisGroup()\ntry:\n    _mf(_g).created(_g)\nexcept Exception:\n    pass\n"), ' xpos 0', ' ypos %d' % y, '}', 'end_group', '']
    return '\n'.join(L)


# the same node labelled for older Nuke (12.2 and later), so they load it without the newer-version warning
OLDER = ('MoteField_nuke12.nk', '12.2 v1')

if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'dist', 'MoteField.nk')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    text = build()
    open(out, 'w').write(text)
    print('%s (%d KB)' % (out, len(text) // 1024))
    if len(sys.argv) <= 1:
        older = os.path.join(ROOT, 'dist', OLDER[0])
        open(older, 'w').write(build(OLDER[1]))
        print(older)
