"""
MoteField lights: get the key light from Houdini (or any USD file) into the MoteField node.

In Houdini (Python Shell or a shelf tool), select your lights, or the LOP node whose stage has them, and run:
    import sys; sys.path.append('/folder/with/motefield'); import mflights
    mflights.export_selected('/path/to/shot_lights.json')
In Nuke: MoteField node > Light tab > "Import lights (Houdini / USD)" and pick that .json (or a .usd file directly,
if this Nuke has the pxr module). The distant light you pick becomes MoteField's key light: its direction, intensity
(x 2^exposure), colour and colour temperature.

The .json is plain and small, so it is easy to write from anything else too:
    {"version": 1, "source": "...", "lights": [
        {"name": "key", "type": "distant", "direction": [x, y, z],   # unit vector pointing TOWARD the light, world space
         "position": [x, y, z], "color": [r, g, b], "intensity": 6.0, "exposure": 0.0,
         "temperature": 5000.0 or null, "angle": 0.53}]}
Types: distant, sphere (point), rect, disk, cylinder, dome, other. MoteField uses one distant key light for now;
the others are exported too, for when point / area lights are added (see TD_GUIDE.md).
"""
import json, math, os

LIGHT_TYPES = {'DistantLight': 'distant', 'SphereLight': 'sphere', 'RectLight': 'rect', 'DiskLight': 'disk',
               'CylinderLight': 'cylinder', 'DomeLight': 'dome', 'PortalLight': 'other', 'GeometryLight': 'other'}


def _norm(v):
    l = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / l for x in v]


# ---- USD (Solaris, or any .usd file) -----------------------------------------------------------------------
def from_usd_stage(stage, time=None):
    """Every UsdLux light on a stage, in world space."""
    from pxr import Usd, UsdGeom
    t = Usd.TimeCode.Default() if time is None else Usd.TimeCode(time)
    out = []

    def get(prim, names, default):
        for n in names:            # UsdLux >= 21 uses inputs:*, older files the bare names
            a = prim.GetAttribute(n)
            if a and a.HasAuthoredValue():
                return a.Get(t)
        for n in names:
            a = prim.GetAttribute(n)
            if a and a.Get(t) is not None:
                return a.Get(t)
        return default
    for prim in stage.Traverse():
        tn = prim.GetTypeName()
        if tn not in LIGHT_TYPES:
            continue
        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(t)
        z = m.TransformDir((0.0, 0.0, 1.0))          # lights shine down -Z: +Z points back toward the light
        pos = m.ExtractTranslation()
        use_k = bool(get(prim, ['inputs:enableColorTemperature', 'enableColorTemperature'], False))
        out.append(dict(name=str(prim.GetPath()), type=LIGHT_TYPES[tn], direction=_norm([z[0], z[1], z[2]]),
                        position=[pos[0], pos[1], pos[2]], color=list(get(prim, ['inputs:color', 'color'], (1.0, 1.0, 1.0))),
                        intensity=float(get(prim, ['inputs:intensity', 'intensity'], 1.0)),
                        exposure=float(get(prim, ['inputs:exposure', 'exposure'], 0.0)),
                        temperature=float(get(prim, ['inputs:colorTemperature', 'colorTemperature'], 6500.0)) if use_k else None,
                        angle=float(get(prim, ['inputs:angle', 'angle'], 0.53))))
    return out


# ---- Houdini -------------------------------------------------------------------------------------------------
def from_houdini(nodes=None):
    """Lights from the selected nodes: LOP nodes (their stage's UsdLux lights) or OBJ-level lights."""
    import hou
    nodes = list(nodes if nodes is not None else hou.selectedNodes())
    out = []
    for n in nodes:
        if isinstance(n, hou.LopNode):
            out += from_usd_stage(n.stage(), hou.frame())
            continue
        if not n.parm('light_type') and 'light' not in n.type().name():
            continue
        lt = n.parm('light_type').evalAsString() if n.parm('light_type') else ''
        m = n.worldTransform()
        z = hou.Vector4(0.0, 0.0, 1.0, 0.0) * m
        p = hou.Vector4(0.0, 0.0, 0.0, 1.0) * m
        col = [n.evalParm(c) for c in ('light_colorr', 'light_colorg', 'light_colorb')] if n.parm('light_colorr') else [1.0, 1.0, 1.0]
        use_k = bool(n.parm('light_usetemp') and n.evalParm('light_usetemp'))
        out.append(dict(name=n.path(), type='distant' if lt in ('distant', 'sun') else 'sphere' if lt in ('point', 'sphere') else 'other',
                        direction=_norm([z[0], z[1], z[2]]), position=[p[0], p[1], p[2]], color=col,
                        intensity=float(n.evalParm('light_intensity')) if n.parm('light_intensity') else 1.0,
                        exposure=float(n.evalParm('light_exposure')) if n.parm('light_exposure') else 0.0,
                        temperature=float(n.evalParm('light_temp')) if use_k and n.parm('light_temp') else None, angle=0.53))
    return out


def export(path, lights, source=''):
    with open(path, 'w') as fh:
        json.dump(dict(version=1, source=source, lights=lights), fh, indent=1)
    return path


def export_selected(path):
    """Houdini: export the selected lights (or the selected LOP node's lights) for MoteField."""
    import hou
    lights = from_houdini()
    if not lights:
        raise RuntimeError('No lights in the selection: select light nodes, or the LOP node whose stage has your lights.')
    export(path, lights, source=hou.hipFile.path())
    print('MoteField: exported %d lights to %s: %s' % (len(lights), path, ', '.join('%s (%s)' % (l['name'], l['type']) for l in lights)))
    return path


# ---- Nuke side -----------------------------------------------------------------------------------------------
def load(path):
    """Lights from an export .json, or straight from a .usd file (needs the pxr module)."""
    if os.path.splitext(path)[1].lower() == '.json':
        with open(path) as fh:
            return json.load(fh)['lights']
    from pxr import Usd
    return from_usd_stage(Usd.Stage.Open(path))


def to_settings(light):
    """A light -> MoteField key-light knobs: light_az, light_el, light_int, light_k, light_rgb."""
    import motefield as mf
    az, el = mf.light_from_direction(light['direction'])
    col = [max(float(c), 0.0) for c in light.get('color', (1.0, 1.0, 1.0))]
    lum = 0.2126 * col[0] + 0.7152 * col[1] + 0.0722 * col[2] or 1.0
    col = [c / lum for c in col]
    k = light.get('temperature') or 6500.0
    if not light.get('temperature'):          # no temperature: make the temperature neutral so the colour comes through as is
        kr = mf.kelvin_rgb(6500.0)
        col = [c / max(kr[i], 1e-6) for i, c in enumerate(col)]
    inten = float(light.get('intensity', 1.0)) * 2.0 ** float(light.get('exposure', 0.0)) * lum
    return dict(light_az=round(float(az), 3), light_el=round(float(el), 3), light_int=round(float(inten), 5), light_k=float(k),
                light_rgb=tuple(round(float(c), 5) for c in col))
