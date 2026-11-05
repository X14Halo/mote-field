# MoteField v1. Nuke runs this because this folder is on NUKE_PATH (in the GUI and in batch renders alike): it
# installs the callback that renders any MoteField frame a render needs.
try:
    import motefield.nuke_node
    motefield.nuke_node.register()
except Exception as _e:  # never stop Nuke from starting
    print('MoteField could not load: %s' % _e)
