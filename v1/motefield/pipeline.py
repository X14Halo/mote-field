"""Where a studio pipeline connects to MoteField.

Every hook here has a working default. A pipeline TD changes them without editing this package: put a module named
motefield_pipeline anywhere on the Python path (NUKE_PATH works) defining any of the names below, and it is used
instead. For example:

    # motefield_pipeline.py
    CAMERA_KEYS = {'toworld': 'myRenderer/camToWorld', 'focal': 'myRenderer/focalLength'}
    LAYER_NAMES = {'P': ['worldPosition'], 'Zraw': ['depthUnfiltered']}
    def cache_root():
        return '/studio/cache/motefield'

    CAMERA_KEYS   exact header names to read the camera from first (camera.from_headers matches loosely otherwise)
    LAYER_NAMES   extra layer names per role (shot.ROLES lists the roles and the names already known)
    cache_root()  where the node keeps the frames it renders when its Cache folder is blank
"""

import os
import tempfile

CAMERA_KEYS = None
LAYER_NAMES = {}


def cache_root():
    """Default: $MOTEFIELD_CACHE, else Nuke's temp folder ($NUKE_TEMP_DIR), else the system temp folder."""
    for var in ('MOTEFIELD_CACHE', 'NUKE_TEMP_DIR'):
        if os.environ.get(var):
            return os.path.join(os.environ[var], 'motefield') if var == 'NUKE_TEMP_DIR' else os.environ[var]
    return os.path.join(tempfile.gettempdir(), 'motefield')


try:                                    # the studio's overrides, if there are any
    from motefield_pipeline import *    # noqa: F401,F403
except ImportError:
    pass
