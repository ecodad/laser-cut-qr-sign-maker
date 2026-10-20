"""Headless render of the assembled stand: blender -b -P render_preview.py -- <geom.json> <out.png> [sign-face.png]"""
import sys, math, runpy, bpy
from mathutils import Vector
PREVIEW = "C:/sandbox/laser-qr/preview_blender.py"   # edit for your checkout
argv = sys.argv[sys.argv.index("--") + 1:]
geom, out = argv[0], argv[1]
face = argv[2] if len(argv) > 2 else None
bpy.ops.wm.read_factory_settings(use_empty=True)
runpy.run_path(PREVIEW, init_globals={"GEOM": geom, "FACE_IMAGE": face})
sc = bpy.context.scene
# camera: front-right three-quarter, slightly above, looking at the sign centre
target = Vector((0, 0.03, 0.075))
def cam(name, pos):
    c = bpy.data.cameras.new(name); o = bpy.data.objects.new(name, c)
    sc.collection.objects.link(o); o.location = pos
    o.rotation_euler = (target - pos).to_track_quat('-Z', 'Y').to_euler(); return o
cam_iso = cam("CamIso", Vector((0.28, -0.32, 0.17)))
cam_side = cam("CamSide", Vector((0.5, 0.03, 0.075)))
cam_viewer = cam("CamViewer", Vector((0.15, -0.6, 0.45)))  # a person standing at a table
sun = bpy.data.lights.new("Sun", 'SUN'); sun.energy = 3
so = bpy.data.objects.new("Sun", sun); sc.collection.objects.link(so); so.rotation_euler = (0.9, 0.3, -0.5)
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'STUDIO'; sc.display.shading.color_type = 'TEXTURE'
sc.display.shading.show_shadows = True; sc.display.shading.show_cavity = True
sc.render.resolution_x, sc.render.resolution_y = 1000, 800
sc.render.film_transparent = False
for c, suffix in ((cam_iso, ""), (cam_side, "-side"), (cam_viewer, "-viewer")):
    sc.camera = c
    sc.render.filepath = out.replace(".png", suffix + ".png")
    bpy.ops.render.render(write_still=True)
print("rendered")
