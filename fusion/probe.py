import adsk.core, adsk.fusion


def run(_context):
    app = adsk.core.Application.get()
    doc = app.activeDocument
    print("active doc:", doc.name if doc else None, "| saved:", doc.isSaved if doc else None)
    print("open docs:", [d.name for d in app.documents])
    des = adsk.fusion.Design.cast(app.activeProduct) if doc else None
    if des:
        print("design type:", des.designType, "| bodies in root:", des.rootComponent.bRepBodies.count,
              "| occurrences:", des.rootComponent.occurrences.count)
        print("units:", des.unitsManager.defaultLengthUnits)
    print("active command:", app.userInterface.activeCommand)
