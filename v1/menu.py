# MoteField v1: adds Other > MoteField to the Nodes menu (Nuke runs this in the GUI).
import nuke

nuke.menu('Nodes').addCommand('Other/MoteField', 'import motefield.nuke_node as m; m.create()')
