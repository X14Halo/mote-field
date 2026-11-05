# MoteField settings

Generated from `motefield/schema.py`; do not edit by hand (`python -m motefield schema --md`).

*Affects*: what a change redoes (shot > prep > sim > render > look; node and view never change cached frames). *Layer*: the settings file it normally lives in. ✎ = can be keyed over the shot.


## Shot

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Shot | Utility pass | `utility` |  |  | shot | shot | basic |
| Shot | Extra passes | `extra_passes` |  |  | shot | shot | advanced |
| Shot | Plate | `plate` |  |  | node | shot | basic |
| Shot | Stand-in shot | `standin` | none |  | shot | shot | advanced |
| Shot | First frame | `first` | 1001 |  | shot | shot | basic |
| Shot | Last frame | `last` | 1048 |  | shot | shot | basic |
| Shot | Frame rate | `fps` | 24 | fps | shot | show | advanced |
| Shot | Scene units | `units` | centimetres |  | shot | show | advanced |
| Shot | Up axis | `up_axis` | Y |  | shot | show | advanced |

## Masters

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Masters | Amount | `master_amount` | 1 | x | sim | shot | basic |
| Masters | Brightness ✎ | `master_brightness` | 1 | x | look | shot | basic |
| Masters | Air | `master_air` | 1 | x | sim | shot | basic |

## Place

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Dust volume | Box centre | `box_center` | (-60, 175, -130) | scene | sim | shot | advanced |
| Dust volume | Box size | `box_size` | (760, 330, 900) | scene | sim | shot | advanced |
| Dust volume | Box rotate Y | `box_rot` | 0 | ° | sim | shot | advanced |
| Dust volume | Edge softness | `fade` | 45 | scene | render | shot | basic |
| Dust volume | Edge softness  -X -Y -Z | `fade_neg` | (1, 1, 1) |  | render | shot | advanced |
| Dust volume | Edge softness  +X +Y +Z | `fade_pos` | (1, 1, 1) |  | render | shot | advanced |
| Ember emitter | Embers | `embers` | off |  | sim | shot | basic |
| Ember emitter | Emitter position | `ember_pos` | (-150, 0, -250) | scene | sim | shot | advanced |
| Ember emitter | Emitter radius | `ember_radius` | 30 | scene | sim | shot | basic |
| Ember emitter | Emission height | `ember_height` | 55 | scene | sim | shot | advanced |
| Key light | Light heading | `light_az` | 25 | ° | prep | shot | basic |
| Key light | Light elevation | `light_el` | 33 | ° | prep | shot | basic |
| Key light | Light intensity ✎ | `light_int` | 6 |  | look | shot | basic |
| Key light | Colour temperature ✎ | `light_k` | 5000 | K | look | shot | basic |
| Key light | Light colour ✎ | `light_rgb` | (1, 1, 1) |  | look | shot | advanced |
| Key light | Bounce light ✎ | `bounce` | 1 | x | look | shot | advanced |
| Key light | Shadows from | `shadows` | auto |  | prep | shot | advanced |
| Key light | Light only through openings | `interior` | on |  | prep | shot | advanced |
| Key light | Shadow edge softness | `shadow_soft` | 3 | cm | look | shot | advanced |
| Shapes | Shapes | `shapes` |  |  | look | shot | basic |

## Look

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Dust | Density | `density` | 180 | /m³ | sim | look | advanced |
| Dust | Seed | `seed` | 1 |  | sim | shot | advanced |
| Dust | Flakes | `flakes` | 18 | % | sim | look | basic |
| Dust | Lint fibres | `fibers` | 1.2 | % | sim | look | basic |
| Dust | Fluff | `fluff` | 0.4 | % | sim | look | basic |
| Dust | Finest grit | `size_min` | 4 | µm | sim | look | advanced |
| Dust | Coarsest grit | `size_max` | 60 | µm | sim | look | advanced |
| Dust | Fine-grit bias | `size_bias` | 2.4 |  | sim | look | advanced |
| Dust | Flake size | `flake_scale` | 1 | x | look | look | advanced |
| Dust | Fibre length | `fiber_scale` | 1 | x | look | look | advanced |
| Dust | Fluff size | `fluff_scale` | 0.6 | x | look | look | advanced |
| Dust | Max motes | `max_motes` | 150000 |  | sim | show | advanced |
| Dust look | Dust brightness ✎ | `brightness` | 1 | x | look | look | advanced |
| Dust look | Forward scatter | `forward` | 0.65 |  | look | look | advanced |
| Dust look | Shadow-side fill ✎ | `shadow_fill` | 0.35 |  | look | look | advanced |
| Dust look | Flake glints ✎ | `glint` | 0.4 |  | look | look | advanced |
| Dust look | Tumble rate | `tumble` | 1 | x | look | look | advanced |
| Dust look | Tint ✎ | `tint` | (1, 1, 1) |  | look | look | advanced |
| Dust look | Steady | `steady` | 0 |  | look | look | basic |
| Dust look | Hidden nearer than ✎ | `cam_fade_near` | 0 | scene | look | shot | advanced |
| Dust look | Fully visible from ✎ | `cam_fade_far` | 0 | scene | look | shot | advanced |
| Air | Drift | `drift` | 1.2 | cm/s | sim | look | basic |
| Air | Drift heading | `drift_dir` | 200 | ° | sim | look | basic |
| Air | Eddies | `eddies` | 3 | cm/s | sim | look | basic |
| Air | Eddy size | `eddy_size` | 1.4 | m | sim | look | advanced |
| Air | Micro-turbulence | `micro` | 0.8 | cm/s | sim | look | advanced |
| Air | Rising warm air | `thermal` | 1.5 | cm/s | sim | look | advanced |
| Air | Vertical drift | `lift` | 0 | cm/s | sim | look | advanced |
| Air | Settling | `settle` | 0.5 | cm/s | sim | look | advanced |
| Air | Speed | `speed` | 1 | x | sim | look | advanced |
| Embers | Embers per second | `ember_rate` | 20 | /s | sim | look | basic |
| Embers | Plume speed | `ember_rise` | 2.2 | m/s | sim | look | basic |
| Embers | Turbulence | `ember_turb` | 0.9 |  | sim | look | basic |
| Embers | Pops per second | `ember_pops` | 0.1 | /s | sim | look | basic |
| Embers | Pop size | `ember_pop_size` | 0.12 |  | sim | look | advanced |
| Embers | Lifetime | `ember_life` | 1 | s | sim | look | advanced |
| Embers | Launch spread | `ember_spread` | 1 |  | sim | look | advanced |
| Embers | Flutter | `ember_flutter` | 1 |  | sim | look | advanced |
| Embers | Wind | `ember_wind` | 0.3 | m/s | sim | look | advanced |
| Embers | Wind heading | `ember_wind_dir` | 200 | ° | sim | look | advanced |
| Ember look | Ember brightness ✎ | `ember_gain` | 1 | x | look | look | basic |
| Ember look | Start temperature | `ember_temp` | 1300 | K | look | look | advanced |
| Ember look | Ember size | `ember_size` | 0.6 | mm | look | look | advanced |
| Ember look | Twinkle | `ember_twinkle` | 0.6 |  | look | look | advanced |
| Ember look | Embers hidden nearer than ✎ | `ember_fade_near` | 0 | scene | look | shot | advanced |
| Ember look | Embers fully visible from ✎ | `ember_fade_far` | 0 | scene | look | shot | advanced |

## Match

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Holdouts | Depth holdouts | `holdout` | on |  | render | shot | basic |
| Holdouts | Never hold out | `holdout_skip` |  |  | render | shot | basic |
| Holdouts | No holdout nearer than | `fg_near` | 0 | scene | render | shot | advanced |
| Holdouts | Full holdout beyond | `fg_far` | 0 | scene | render | shot | advanced |
| Lens | Defocus | `dof` | on |  | render | shot | basic |
| Lens | Motion blur | `mb` | on |  | render | shot | basic |
| Lens | Focal length | `focal` | 0 | mm | render | shot | advanced |
| Lens | Film back width | `film` | 0 | mm | render | shot | advanced |
| Lens | f-stop | `fstop` | 0 |  | render | shot | advanced |
| Lens | Focus distance | `focus` | 0 | scene | render | shot | advanced |
| Lens | Front defocus | `front_mult` | 1 | x | render | shot | advanced |
| Lens | Back defocus | `back_mult` | 1 | x | render | shot | advanced |
| Bokeh | Largest bokeh | `max_bokeh` | 110 | px | render | show | advanced |
| Bokeh | Blade count | `blades` | 0 |  | render | show | advanced |
| Bokeh | Cat-eye | `cateye` | 0 |  | render | show | advanced |
| Bokeh | Anamorphic aspect | `anamorphic` | 0 |  | render | show | advanced |
| Bokeh | Field curvature | `field_curv` | 0 |  | render | show | advanced |
| Bokeh | f-stop full aperture | `fstop_full` | 0 |  | render | show | advanced |
| Bokeh | Custom bokeh shape | `bokeh_image` |  |  | render | show | advanced |
| Bokeh | Bokeh rotation | `bokeh_rot` | 0 | ° | render | show | advanced |
| Bokeh | Bokeh centre / rim | `bokeh_falloff` | 0 |  | render | show | advanced |
| Shutter | Shutter angle | `shutter` | 180 | ° | render | show | advanced |
| Shutter | Shutter timing | `shutter_mode` | backward |  | render | show | advanced |
| Shutter | Shutter shape | `shutter_shape` | backwardsShape |  | render | show | advanced |

## Deliver

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Output | Node output | `output_mode` | dust only |  | node | shot | basic |
| Output | Alpha | `alpha` | none |  | render | shot | basic |
| Output | Matte strength | `matte_gain` | 1 | x | render | shot | advanced |
| Output | Cache folder | `cache` |  |  | node | shot | advanced |
| Output | Resolution | `proxy` | 1 | x | render | shot | advanced |
| Channels | noholdout channels | `out_noholdout` | on |  | render | show | advanced |
| Channels | embers channels | `out_embers` | on |  | render | show | advanced |
| Channels | dust_key and dust_fill channels | `out_keyfill` | off |  | render | show | advanced |

## View

| Section | Setting | Key | Default | Unit | Affects | Layer | Level |
|---|---|---|---|---|---|---|---|
| Viewer | Defocus in the viewer | `dof_view` | on |  | view | shot | advanced |
| Viewer | Motion blur in the viewer | `mb_view` | on |  | view | shot | advanced |
| Viewer | Hold camera on frame | `hold_frame` | 0 |  | view | shot | advanced |

## Master dials

- **Amount** multiplies `density`, `ember_rate`.
- **Brightness** multiplies `brightness`, `ember_gain`.
- **Air** multiplies `drift`, `lift`, `eddies`, `micro`, `thermal`, `tumble`, `ember_turb`, `ember_flutter`.
