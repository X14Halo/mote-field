"""Where a studio pipeline connects to the MoteField reference package (used by the tests and the command line;
the Nuke node itself takes no files and no setup).

Every hook here has a working default. A pipeline TD changes them without editing this package: put a module named
motefield_pipeline anywhere on the Python path (NUKE_PATH works) defining any of the names below, and it is used
instead. For example:

    # motefield_pipeline.py
    CAMERA_KEYS = {'toworld': 'myRenderer/camToWorld', 'focal': 'myRenderer/focalLength'}
    LAYER_NAMES = {'P': ['worldPosition'], 'Zraw': ['depthUnfiltered']}

    CAMERA_KEYS   exact header names to read the camera from first (camera.from_headers matches loosely otherwise)
    LAYER_NAMES   extra layer names per role (shot.ROLES lists the roles and the names already known)
"""

CAMERA_KEYS = None
LAYER_NAMES = {}


try:                                    # the studio's overrides, if there are any
    from motefield_pipeline import *    # noqa: F401,F403
except ImportError:
    pass
