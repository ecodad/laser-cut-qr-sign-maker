"""Engraving as flat black surface patches 0.05 mm above the Sign's face, built directly from the
generator's geometry (output/<name>-engrave-loops.json: outer polyline + holes per shape, mm, SVG
y-down) with TemporaryBRepManager, added through one base feature. No sketch, no booleans.
Rolls the timeline back to before any earlier 'Engrave' sketch / patches first; run place.py after."""
import json

import adsk.core, adsk.fusion

LOOPS = "C:/sandbox/laser-qr/output/medforge-give-engrave-loops.json"
SIGN_W, SIGN_H, THICK = 80.0, 150.0, 3.0   # mm, must match the config
LIFT = 0.005                                 # cm above the face


def run(_context):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    occ = next(o for o in root.occurrences if o.component.name == "Sign")
    comp = occ.component
    tl = design.timeline
    old = next((s for s in comp.sketches if s.name == "Engrave"), None)
    if old is not None:
        tl.markerPosition = old.timelineObject.index
        tl.deleteAllAfterMarker()
    for i in range(comp.features.baseFeatures.count - 1, -1, -1):
        bf = comp.features.baseFeatures.item(i)
        if bf.name == "Engrave patches":
            bf.deleteMe()
    plate = comp.bRepBodies.item(0)
    print("plate: faces", plate.faces.count, "volume", round(plate.volume, 3))
    z = THICK / 20.0 + LIFT

    def P(x, y):
        return adsk.core.Point3D.create(x / 10.0 - SIGN_W / 20.0, (SIGN_H - y) / 10.0, z)

    tmp = adsk.fusion.TemporaryBRepManager.get()

    def wire(poly):
        pts = [P(x, y) for x, y in poly]
        segs = [adsk.core.Line3D.create(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        w, _ = tmp.createWireFromCurves(segs, False)
        return w

    groups = json.load(open(LOOPS, encoding="utf-8"))
    faces, failed = [], 0
    for g in groups:
        try:
            ws = [wire(g["outer"])] + [wire(h) for h in g["holes"]]
            faces.append(tmp.createFaceFromPlanarWires(ws))
        except Exception:
            # retry without holes so at least the outer shape shows
            try:
                faces.append(tmp.createFaceFromPlanarWires([wire(g["outer"])]))
            except Exception:
                failed += 1
    print("patches built", len(faces), "failed", failed, "of", len(groups))
    bf = comp.features.baseFeatures.add()
    bf.name = "Engrave patches"
    bf.startEdit()
    added = 0
    for f in faces:
        try:
            comp.bRepBodies.add(f, bf)
            added += 1
        except Exception:
            pass
    bf.finishEdit()
    lib = app.materialLibraries.itemByName("Fusion Appearance Library")
    src = next((lib.appearances.itemByName(n) for n in ("Paint - Enamel Glossy (Black)", "Paint - Enamel Glossy (Red)")
                if lib.appearances.itemByName(n)), lib.appearances.item(0))

    def tone(name, rgb):
        a = design.appearances.itemByName(name)
        if a is None:
            a = design.appearances.addByCopy(src, name)
            try:
                a.appearanceProperties.itemById("opaque_albedo").value = adsk.core.Color.create(*rgb, 255)
            except Exception as e:
                print("colour skipped:", str(e)[:80])
        return a

    ink, wood = tone("Engrave ink", (15, 15, 15)), tone("Plywood tone", (205, 170, 120))
    for b in comp.bRepBodies:
        b.appearance = wood if b.name == "Sign" else ink
    for o in root.occurrences:
        if o.component.name == "Base":
            o.bRepBodies.item(0).appearance = wood
    print("patch bodies added", added, "| bodies in Sign", comp.bRepBodies.count, "| timeline", tl.count)
