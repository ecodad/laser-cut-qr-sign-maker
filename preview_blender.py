"""
preview_blender.py — build the assembled stand in Blender from output/<name>.json.

Run inside Blender (or via the Blender MCP):
    import runpy; runpy.run_path(r"C:/sandbox/laser-qr/preview_blender.py",
                                 init_globals={"GEOM": r"C:/sandbox/laser-qr/output/medforge-give.json"})

World frame: X = sign width, -Y = towards the viewer (front), Z = up. Ground at z=0.
Scene units are metres (1 mm = 0.001).
"""
import json
import math

import bpy
from mathutils import Matrix, Vector

GEOM = globals().get("GEOM", r"C:/sandbox/laser-qr/output/medforge-give.json")
FACE_IMAGE = globals().get("FACE_IMAGE", None)  # optional PNG of the engraved sign face
S = 0.001  # mm -> m

g = json.load(open(GEOM, encoding="utf-8"))
sign, base = g["sign"], g["base"]
t = sign["thickness"]
ts = math.radians(sign["angle_from_vertical_deg"])
tb = math.radians(base["angle_from_vertical_deg"])
L = sign["slot_from_bottom"]
Lb = base["back"]

# clear previous preview objects
for ob in [o for o in bpy.data.objects if o.name.startswith("LQR_")]:
    bpy.data.objects.remove(ob, do_unlink=True)


def plate(name, outline, height, width, thickness, origin, along, normal, color, image=None):
    """Extrude a closed outline (board coords: x = width, y = along, from the SVG's y-down)."""
    xaxis = Vector((1, 0, 0))
    M = Matrix((
        (xaxis.x, along.x, normal.x, origin.x),
        (xaxis.y, along.y, normal.y, origin.y),
        (xaxis.z, along.z, normal.z, origin.z),
        (0, 0, 0, 1),
    ))
    pts2 = [(x - width / 2, height - y) for x, y in outline]  # centre on x, y up along the board
    n = len(pts2)
    verts = []
    for z in (-thickness / 2, thickness / 2):
        for x, y in pts2:
            verts.append((M @ Vector((x * S, y * S, z * S))).to_tuple())
    faces = [list(range(n))[::-1], [n + i for i in range(n)]]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    bsdf = next(nd for nd in mat.node_tree.nodes if nd.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = color
    mat.diffuse_color = color
    if image:
        uv = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            if poly.index != 1:
                continue
            for li in poly.loop_indices:
                x, y = pts2[me.loops[li].vertex_index - n]
                uv.data[li].uv = ((x + width / 2) / width, y / height)
        img = bpy.data.images.load(image)
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    ob.data.materials.append(mat)
    return ob


# sign: bottom edge on the ground at y=0, leaning back (top towards +Y)
u_sign = Vector((0, math.sin(ts), math.cos(ts)))          # along the board, bottom -> top
n_sign = Vector((0, -math.cos(ts), math.sin(ts)))         # front-face normal (towards viewer)
P = u_sign * L                                             # where the base crosses (mm)
# base: back end on the ground behind the sign, leaning forward (front end towards -Y)
u_base = Vector((0, -math.sin(tb), math.cos(tb)))          # along the board, back -> front
n_base = Vector((0, math.cos(tb), math.sin(tb)))
base_origin = P - u_base * Lb

wood = (0.76, 0.55, 0.32, 1.0)
wood2 = (0.62, 0.42, 0.22, 1.0)
plate("LQR_Sign", sign["outline"], sign["height"], sign["width"], t, Vector((0, 0, 0)) * S, u_sign, n_sign, wood, FACE_IMAGE)
plate("LQR_Base", base["outline"], base["front"] + base["back"], base["width"], t, base_origin * S, u_base, n_base, wood2)

# engraving face: a thin dark decal from the SVG layout box, for orientation only
lay = g["layout"]
W, H = sign["width"], sign["height"]
qr = lay["qr_mm"]
# ground
bpy.ops.mesh.primitive_plane_add(size=0.4, location=(0, 0, -0.0001))
gnd = bpy.context.active_object
gnd.name = "LQR_Ground"
gm = bpy.data.materials.new("LQR_Ground_mat")
gm.diffuse_color = (0.85, 0.85, 0.85, 1)
gnd.data.materials.append(gm)

print("built", P, base_origin, "front end z(mm)=", (base_origin + u_base * (base['front'] + base['back'])).z)
