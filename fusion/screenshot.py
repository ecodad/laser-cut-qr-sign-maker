import adsk.core, adsk.fusion

OUT = "C:/sandbox/laser-qr/output/medforge-give-fusion.png"


def run(_context):
    app = adsk.core.Application.get()
    vp = app.activeViewport
    cam = vp.camera
    cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
    cam.target = adsk.core.Point3D.create(0, 2, 7)
    cam.eye = adsk.core.Point3D.create(28, -32, 20)
    cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()
    ok = vp.saveAsImageFile(OUT, 1200, 900)
    print("screenshot:", ok, OUT)
