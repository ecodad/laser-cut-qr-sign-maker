"""Fusion 360 script: build the two-piece QR stand from output/<name>.json, assembled.

Sent through the Fusion MCP (fusion_mcp_execute, featureType "script"). API units are cm.
World frame: X = sign width, -Y = towards the viewer, Z = up, table top at z = 0.
"""
import json
import math

import adsk.core
import adsk.fusion

GEOM = "C:/sandbox/laser-qr/output/medforge-give.json"
MM = 0.1  # mm -> cm


def outline_verts(kind, g):
    """(x, y, round?) vertices in board coords, mm, y down, origin top-left — same as make-laser-qr."""
    if kind == "sign":
        W, H, sw = g["width"], g["height"], g["slot_width"]
        yc = H - g["slot_from_bottom"]
        half = W / 2
        return [(0, 0, True), (W, 0, True), (W, H, True), (0, H, True),
                (0, yc + sw / 2, False), (half, yc + sw / 2, False), (half, yc - sw / 2, False), (0, yc - sw / 2, False)]
    Wb, sw = g["width"], g["slot_width"]
    Lf, Lb = g["front"], g["back"]
    Hb = Lf + Lb
    yc = Lf
    half = Wb / 2
    return [(0, 0, True), (Wb, 0, True), (Wb, yc - sw / 2, False), (half, yc - sw / 2, False),
            (half, yc + sw / 2, False), (Wb, yc + sw / 2, False), (Wb, Hb, True), (0, Hb, True)]


def make_plate(design, root, name, verts, height, width, thickness, radius, origin, along, normal):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = name
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = name + " outline"
    pts = [adsk.core.Point3D.create((x - width / 2) * MM, (height - y) * MM, 0) for x, y, _ in verts]
    lines = sk.sketchCurves.sketchLines
    n = len(pts)
    segs = [lines.addByTwoPoints(pts[i], pts[(i + 1) % n]) for i in range(n)]
    # fillet the flagged corners: corner i joins segs[i-1] (ends there) and segs[i] (starts there)
    for i, (_, _, rnd) in enumerate(verts):
        if rnd and radius > 0:
            a, b = segs[i - 1], segs[i]
            sk.sketchCurves.sketchArcs.addFillet(a, a.endSketchPoint.geometry, b, b.startSketchPoint.geometry, radius * MM)
    prof = max((sk.profiles.item(i) for i in range(sk.profiles.count)), key=lambda p: p.areaProperties().area)
    ext = comp.features.extrudeFeatures.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.setSymmetricExtent(adsk.core.ValueInput.createByReal(thickness * MM), True)
    body = comp.features.extrudeFeatures.add(ext).bodies.item(0)
    body.name = name
    # place the component: board x -> world X, board y (up the board) -> along, board z -> normal
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(adsk.core.Point3D.create(*[c * MM for c in origin]),
                              adsk.core.Vector3D.create(1, 0, 0),
                              adsk.core.Vector3D.create(*along),
                              adsk.core.Vector3D.create(*normal))
    occ.transform = m
    return occ


def run(_context):
    app = adsk.core.Application.get()
    g = json.load(open(GEOM, encoding="utf-8"))
    sign, base = g["sign"], g["base"]
    r = g.get("corner_radius", 0)
    t = sign["thickness"]
    ts = math.radians(sign["angle_from_vertical_deg"])
    tb = math.radians(base["angle_from_vertical_deg"])
    L, Lb = sign["slot_from_bottom"], base["back"]

    # close any earlier (unsaved) build of this stand so reruns don't pile up documents
    for i in range(app.documents.count - 1, -1, -1):
        d = app.documents.item(i)
        try:
            prod = adsk.fusion.Design.cast(d.products.itemByProductType("DesignProductType"))
            if prod and not d.isSaved and prod.attributes.itemByName("laserqr", "config"):
                d.close(False)
        except Exception:
            pass
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = design.rootComponent
    design.attributes.add("laserqr", "config", g["config"])

    u_sign = (0, math.sin(ts), math.cos(ts))           # bottom -> top of the sign
    n_sign = (0, -math.cos(ts), math.sin(ts))          # front-face normal (towards the viewer)
    P = tuple(L * c for c in u_sign)                   # crossing point, mm
    u_base = (0, -math.sin(tb), math.cos(tb))          # back -> front of the base
    n_base = (0, -math.cos(tb), -math.sin(tb))         # = X x u_base, keeps the frame right-handed
    base_origin = tuple(P[i] - Lb * u_base[i] for i in range(3))

    make_plate(design, root, "Sign", outline_verts("sign", sign), sign["height"], sign["width"], t, r,
               (0, 0, 0), u_sign, n_sign)
    make_plate(design, root, "Base", outline_verts("base", base), base["front"] + base["back"], base["width"], t, r,
               base_origin, u_base, n_base)

    # a table slab under it for the picture
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = "Table"
    sk = occ.component.sketches.add(occ.component.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addCenterPointRectangle(adsk.core.Point3D.create(0, 2, 0), adsk.core.Point3D.create(10, 10, 0))
    ext = occ.component.features.extrudeFeatures.createInput(sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.setDistanceExtent(False, adsk.core.ValueInput.createByReal(-0.5))
    occ.component.features.extrudeFeatures.add(ext)

    app.activeViewport.fit()
    print("built:", doc.name, "| sign lean", sign["angle_from_vertical_deg"], "| base lean", base["angle_from_vertical_deg"],
          "| crossing", g["crossing_angle_deg"], "| slot", sign["slot_width"], "mm | corner r", r)
    print("base front tip z (mm):", round(base_origin[2] + (base["front"] + base["back"]) * u_base[2], 2),
          "| base back z:", round(base_origin[2], 2))
