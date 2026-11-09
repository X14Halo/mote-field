"""The node's Blink kernels: reads node/kernels/*.blink and fills in what they share.

A kernel's source can say:

    //@include NAME      the code of node/kernels/NAME.blink goes here (helper functions)
    //@params a b c ...  (once, anywhere) the params this kernel takes, from PARAMS below
    //@decl              where their declarations go (in the param: section)
    //@define            where their defineParam calls go (in define())

so every kernel declares a shared param with the same name, type and default, and the node links each one to the
same expression.
"""
import os
import re

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kernels')

# Layout shared by the kernels and the node (the sizes of the internal images)
STATE_COLS = 128          # motes per row of the state image
GROUPS = 5                # pixels per mote (side by side, STATE_COLS apart)
MAX_MOTES = 65536
STATE_W, STATE_H = STATE_COLS * GROUPS, MAX_MOTES // STATE_COLS
TILES_X, TILES_Y = 64, 36                 # screen tiles for binning (their size follows the resolution)
NTILES = TILES_X * TILES_Y
ROWS1 = (MAX_MOTES + 95) // 96            # 96 motes per bin word (4 channels x 24 bits)
ROWS2 = (ROWS1 + 95) // 96
CELLS_X, CELLS_Y = 16, 9                  # bounce light cells
NCELLS = CELLS_X * CELLS_Y
MODES = 60                                # 40 eddy modes, 20 micro-turbulence modes

# name: (Blink type, default). Defaults follow the settings schema; the node drives them all by expression.
PARAMS = {
    # time and units
    'frame': ('float', 1001.0), 'first': ('float', 1001.0), 'fps': ('float', 24.0), 'speed': ('float', 1.0),
    'su': ('float', 0.01),
    # the dust
    'seed': ('int', 1), 'density': ('float', 180.0), 'max_motes': ('int', 65536),
    'box_center': ('float3', (-60.0, 175.0, -130.0)), 'box_size': ('float3', (760.0, 330.0, 900.0)),
    'box_rot': ('float', 0.0), 'fade': ('float', 45.0), 'fade_neg': ('float3', (1.0, 1.0, 1.0)),
    'fade_pos': ('float3', (1.0, 1.0, 1.0)),
    'flakes': ('float', 18.0), 'fibers': ('float', 1.2), 'fluff': ('float', 0.4),
    'size_min': ('float', 4.0), 'size_max': ('float', 60.0), 'size_bias': ('float', 2.4),
    'flake_scale': ('float', 1.0), 'fiber_scale': ('float', 1.0), 'fluff_scale': ('float', 0.6),
    # the air
    'drift': ('float', 1.2), 'drift_dir': ('float', 200.0), 'eddies': ('float', 3.0), 'eddy_size': ('float', 1.4),
    'micro': ('float', 0.8), 'thermal': ('float', 1.5), 'lift': ('float', 0.0), 'settle': ('float', 0.5),
    # the light
    'light_az': ('float', 25.0), 'light_el': ('float', 33.0), 'light_k': ('float', 5000.0),
    'light_rgb': ('float3', (1.0, 1.0, 1.0)), 'light_int': ('float', 6.0), 'bounce': ('float', 1.0),
    'shadows': ('int', 0), 'interior': ('int', 1), 'shadow_soft': ('float', 3.0), 'shadow_thick': ('float', 60.0),
    'albedo': ('float', 0.35), 'use_albedo': ('int', 0),
    # the look
    'brightness': ('float', 1.0), 'forward': ('float', 0.65), 'shadow_fill': ('float', 0.35),
    'glint': ('float', 0.4), 'tumble': ('float', 1.0), 'tint': ('float3', (1.0, 1.0, 1.0)), 'steady': ('float', 0.0),
    'cam_fade_near': ('float', 0.0), 'cam_fade_far': ('float', 0.0),
    # holdouts
    'holdout': ('int', 1), 'fg_near': ('float', 0.0), 'fg_far': ('float', 0.0),
    # the lens (0 = from the camera in the pass header, hdr_*)
    'dof': ('int', 1), 'mb': ('int', 1), 'focal': ('float', 0.0), 'film': ('float', 0.0), 'fstop': ('float', 0.0),
    'focus': ('float', 0.0), 'front_mult': ('float', 1.0), 'back_mult': ('float', 1.0),
    'max_bokeh': ('float', 110.0), 'blades': ('int', 0), 'cateye': ('float', 0.0), 'anamorphic': ('float', 0.0),
    'field_curv': ('float', 0.0), 'fstop_full': ('float', 0.0), 'bokeh_rot': ('float', 0.0),
    'bokeh_falloff': ('float', 0.0),
    'hdr_focal': ('float', 0.0), 'hdr_film': ('float', 0.0), 'hdr_fstop': ('float', 0.0), 'hdr_focus': ('float', 0.0),
    'hdr_blades': ('int', 0), 'hdr_cateye': ('float', 0.0), 'hdr_anamorphic': ('float', 0.0),
    'hdr_field_curv': ('float', 0.0), 'hdr_fstop_full': ('float', 0.0),
    # the shutter
    'shutter': ('float', 180.0), 'shutter_mode': ('int', 0), 'shutter_shape': ('int', 0),
    # the camera on the frame, as the shutter opens and as it closes (th = tan of half the horizontal view angle)
    'cam_pos': ('float3', (0.0, 150.0, 400.0)), 'cam_right': ('float3', (1.0, 0.0, 0.0)),
    'cam_up': ('float3', (0.0, 1.0, 0.0)), 'cam_back': ('float3', (0.0, 0.0, 1.0)), 'cam_th': ('float', 0.5),
    'o_pos': ('float3', (0.0, 150.0, 400.0)), 'o_right': ('float3', (1.0, 0.0, 0.0)),
    'o_up': ('float3', (0.0, 1.0, 0.0)), 'o_back': ('float3', (0.0, 0.0, 1.0)), 'o_th': ('float', 0.5),
    'c_pos': ('float3', (0.0, 150.0, 400.0)), 'c_right': ('float3', (1.0, 0.0, 0.0)),
    'c_up': ('float3', (0.0, 1.0, 0.0)), 'c_back': ('float3', (0.0, 0.0, 1.0)), 'c_th': ('float', 0.5),
    # the utility pass (what the node worked out about it, or what the user set)
    'p_premult': ('int', -1), 'z_kind': ('int', -1), 'z_premult': ('int', -1),
    # output
    'alpha_mode': ('int', 0), 'matte_gain': ('float', 1.0), 'held': ('int', 1),
    # the check node
    'motes': ('int', 400),
}


def _literal(t, v):
    if t == 'int':
        return str(int(v))
    if t == 'float':
        return _f(v)
    return '%s(%s)' % (t, ', '.join(_f(x) for x in v))


def _f(x):
    s = repr(float(x))
    if 'e' in s or 'E' in s:
        s = '%.9f' % float(x)
    return s + 'f'


def _log2(n):
    """The shift for a power of two (the kernels divide by shifting: see rule_breaks)."""
    k = n.bit_length() - 1
    assert 1 << k == n, '%d is not a power of two' % n
    return k


def source(name):
    """The full source of kernel `name` (node/kernels/NAME.blink), includes and params filled in."""
    text = open(os.path.join(HERE, name + '.blink')).read()

    def inc(m):
        return source(m.group(1)).rstrip('\n')
    text = re.sub(r'^//@include (\w+)\s*$', inc, text, flags=re.M)
    pm = re.search(r'^//@params((?:[ \t]+\w+)+(?:\n//@params(?:[ \t]+\w+)+)*)\s*$', text, flags=re.M)
    if pm:
        names = re.findall(r'\w+', pm.group(1).replace('//@params', ' '))
        names = [n for n in names if n != 'params']
        for n in names:
            if n not in PARAMS:
                raise KeyError('%s: unknown param %s' % (name, n))
        decl = '\n'.join('    %s %s;' % (PARAMS[n][0], n) for n in names)
        define = '\n'.join('    defineParam(%s, "%s", %s);' % (n, n, _literal(*PARAMS[n])) for n in names)
        text = text[:pm.start()] + text[pm.end():]
        text = re.sub(r'^\s*//@decl\s*$', decl, text, count=1, flags=re.M)
        text = re.sub(r'^\s*//@define\s*$', define, text, count=1, flags=re.M)
    consts = dict(STATE_COLS=STATE_COLS, GROUPS=GROUPS, MAX_MOTES=MAX_MOTES, TILES_X=TILES_X, TILES_Y=TILES_Y,
                  NTILES=NTILES, ROWS1=ROWS1, ROWS2=ROWS2, CELLS_X=CELLS_X, CELLS_Y=CELLS_Y, NCELLS=NCELLS,
                  MODES=MODES, STATE_COLS_SHIFT=_log2(STATE_COLS), TILES_X_SHIFT=_log2(TILES_X),
                  CELLS_X_SHIFT=_log2(CELLS_X))
    text = re.sub(r'@(\w+)@', lambda m: str(consts[m.group(1)]), text)
    return text


def kernel_name(src):
    return re.search(r'kernel\s+(\w+)\s*:', src).group(1)


def params(src):
    """(type, name) of each param, in order."""
    m = re.search(r'param\s*:(.*?)(?:local\s*:|void\s+define)', src, re.S)
    return re.findall(r'(\w+)\s+(\w+)\s*;', m.group(1)) if m else []


def images(src):
    """Names of the kernel's images in order (the inputs, then the output)."""
    return re.findall(r'Image\s*<[^>]*>\s*(\w+)\s*;', src)


KERNELS = ('modes', 'facts', 'surface', 'bounds', 'bounce', 'place', 'motes', 'bins', 'rows', 'draw')


# ---------------------------------------------------------------------------------------------------------------------
#  Rules the kernels keep, so older Blink (Nuke 12) builds and runs them too
# ---------------------------------------------------------------------------------------------------------------------
def functions(src):
    """{name: body} of each function in a kernel (user functions and define / init / process)."""
    out = {}
    for m in re.finditer(r'\n  (?:float[234]?|int[234]?|bool|void)\s+(\w+)\s*\([^)]*\)\s*\{', src):
        i, depth = m.end(), 1
        while depth:
            depth += {'{': 1, '}': -1}.get(src[i], 0)
            i += 1
        out[m.group(1)] = src[m.end():i - 1]
    return out


def rule_breaks(src):
    """What in a kernel's source older Blink may reject or crash on: [text]. Empty means fine.

    * images read in a function that another user function calls (Blink reads images only in process() or in the
      functions it calls directly; Nuke 12.2 crashes otherwise);
    * images read in init();
    * integer division outside init() (Blink's CPU vectoriser can't always do it);
    * input images not named in_... (Nuke 12.2 crashed reading pixels from an input named "surf"), and an image's
      bounds used inside a read of that image;
    * arrays, while loops, one-value vector constructors."""
    out = []
    imgs = images(src)
    fns = functions(src)
    reads = set(f for f, body in fns.items() if any(re.search(r'\b%s\s*\(' % im, body) for im in imgs))
    for f, body in fns.items():
        for g in reads:
            if g != f and f != 'process' and re.search(r'\b%s\s*\(' % g, body):
                out.append('%s reads images and is called from %s' % (g, f))
    if 'init' in reads:
        out.append('init reads images')
    for im in imgs[:-1]:
        if not im.startswith('in_'):
            out.append('an input not named in_...: %s' % im)
    for im in imgs:
        for m in re.finditer(r'\b%s\s*\(' % im, src):
            depth, i = 1, m.end()
            while depth and i < len(src):
                depth += {'(': 1, ')': -1}.get(src[i], 0)
                i += 1
            if '.bounds' in src[m.end():i]:
                out.append('bounds inside a read of %s' % im)
    code = re.sub(r'//[^\n]*', '', src)
    head = code[:code.find('void define')] if 'void define' in code else ''
    kernel_ints = set(re.findall(r'\bint\s+(\w+)\s*;', head))           # int params and locals
    sigs = dict((m.group(1), m.group(2)) for m in
                re.finditer(r'\n  (?:float[234]?|int[234]?|bool|void)\s+(\w+)\s*\(([^)]*)\)\s*\{', code))
    for f, body in fns.items():
        if f in ('init', 'define'):
            continue
        body = re.sub(r'//[^\n]*', '', body)
        ints = kernel_ints | set(re.findall(r'\bint\s+(\w+)', body + ' ' + sigs.get(f, '')))
        floats = set(re.findall(r'\bfloat[234]?\s+(\w+)', body + ' ' + sigs.get(f, '')))
        ints -= floats
        for m in re.finditer(r'/\s*(\(?\s*[\w.]+)', body):
            d = m.group(1).lstrip('( ').strip()
            if re.match(r'^\d+$', d) or d in ints:
                out.append('integer division in %s (/ %s)' % (f, d))
    if re.search(r'\w\s*\[', code):
        out.append('an array')
    if re.search(r'\bwhile\s*\(', code):
        out.append('a while loop')
    if re.search(r'\bfloat[234]\(\s*[^,()]*\s*\)', code):
        out.append('a one-value vector constructor')
    return out
