import adsk.core
OUT = "C:/sandbox/laser-qr/output/medforge-give-fusion-front.png"
def run(_c):
    app = adsk.core.Application.get(); vp = app.activeViewport; cam = vp.camera
    cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
    cam.target = adsk.core.Point3D.create(0, 1.5, 7.5)
    cam.eye = adsk.core.Point3D.create(6, -40, 22)
    cam.upVector = adsk.core.Vector3D.create(0, 0, 1); cam.isFitView = True
    vp.camera = cam; vp.refresh()
    print("screenshot:", vp.saveAsImageFile(OUT, 1000, 1000), OUT)
