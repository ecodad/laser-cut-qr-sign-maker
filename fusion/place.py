"""Position the Sign and Base occurrences (after their geometry exists) and capture the position.
Tries transform2, then transform + snapshot, then a Move feature on the body."""
import json
import math

import adsk.core, adsk.fusion

GEOM = "C:/sandbox/laser-qr/output/medforge-give.json"
MM = 0.1


def frame(origin, along, normal):
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(adsk.core.Point3D.create(*[c * MM for c in origin]),
                              adsk.core.Vector3D.create(1, 0, 0),
                              adsk.core.Vector3D.create(*along),
                              adsk.core.Vector3D.create(*normal))
    return m


def is_identity(m):
    return all(abs(a - b) < 1e-6 for a, b in zip(m.asArray(), adsk.core.Matrix3D.create().asArray()))


def run(_context):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    g = json.load(open(GEOM, encoding="utf-8"))
    sign, base = g["sign"], g["base"]
    ts = math.radians(sign["angle_from_vertical_deg"])
    tb = math.radians(base["angle_from_vertical_deg"])
    L, Lb = sign["slot_from_bottom"], base["back"]
    u_sign = (0, math.sin(ts), math.cos(ts))
    n_sign = (0, -math.cos(ts), math.sin(ts))
    P = tuple(L * c for c in u_sign)
    u_base = (0, -math.sin(tb), math.cos(tb))
    n_base = (0, -math.cos(tb), -math.sin(tb))
    base_origin = tuple(P[i] - Lb * u_base[i] for i in range(3))
    targets = {"Sign": frame((0, 0, 0), u_sign, n_sign), "Base": frame(base_origin, u_base, n_base)}
    for occ in root.occurrences:
        m = targets.get(occ.component.name)
        if m is None:
            continue
        if not is_identity(occ.transform2):
            print(occ.component.name, "already placed")
            continue
        # drop a face sketch that may block the placement; it is re-created by engrave_cut.py
        comp = occ.component
        for i in range(comp.sketches.count - 1, -1, -1):
            if comp.sketches.item(i).name == "Engrave":
                comp.sketches.item(i).deleteMe()
        how = None
        try:
            occ.transform2 = m
            if not is_identity(occ.transform2):
                how = "transform2"
        except Exception as e:
            print("  transform2 raised", e)
        if how is None:
            try:
                occ.transform = m
                if design.snapshots.hasPendingSnapshot:
                    design.snapshots.add()
                if not is_identity(occ.transform2):
                    how = "transform + snapshot"
            except Exception as e:
                print("  transform raised", e)
        if how is None:
            # move the body inside the component instead
            coll = adsk.core.ObjectCollection.create()
            coll.add(comp.bRepBodies.item(0))
            inp = comp.features.moveFeatures.createInput2(coll)
            inp.defineAsFreeMove(m)
            comp.features.moveFeatures.add(inp)
            how = "move feature"
        print(occ.component.name, "placed via", how)
    for occ in root.occurrences:
        if occ.bRepBodies.count:
            b = occ.bRepBodies.item(0).boundingBox
            print("  ", occ.component.name, "world z", round(b.minPoint.z, 2), round(b.maxPoint.z, 2),
                  "y", round(b.minPoint.y, 2), round(b.maxPoint.y, 2))
