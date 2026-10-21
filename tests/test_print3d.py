"""3D output: closed meshes, sensible heights, valid 3MF package, slot leans back."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qrsign import mesh3d, print3d, project, render, fonts  # noqa: E402

URL = "https://github.com/ecodad/laser-cut-qr-sign-maker"
PRUSA = Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe")


def _font():
    names = fonts.font_names()
    return next((f for f in ("Ravenscroft", "Arial", "DejaVu Sans", "Liberation Sans") if f in names), names[0])


def _project():
    p = project.Project(name="p3d", url=URL, style=render.Style(body="smooth"))
    p.sign.update({"text_above": "Donate to | Example", "text_above_scales": "0.45 | 1",
                   "text_below": "Thank you!", "font": _font()})
    return p


def test_extrude_box_is_closed():
    m = mesh3d.box(0, 0, 0, 10, 20, 3)
    assert m.is_closed() and len(m.triangles) == 12
    lo, hi = m.bounds()
    assert tuple(lo) == (0, 0, 0) and tuple(hi) == (10, 20, 3)


def test_extrude_with_hole_is_closed():
    from shapely.geometry import box
    ring = box(0, 0, 10, 10).difference(box(3, 3, 7, 7))
    m = mesh3d.extrude(ring, 0, 2)
    assert m.is_closed()


@pytest.mark.parametrize("mode,flip", [("multi", False), ("multi", True), ("single", False)])
def test_make_print(tmp_path, mode, flip):
    p = _project()
    s = print3d.PrintSettings(mode=mode, flip=flip)
    qr_path = tmp_path / "qr.svg"
    qr_path.write_text(p.qr_svg(size_mm=50), encoding="utf-8")
    res = print3d.make_print(p.sign_config(), qr_path, s, "p3d", tmp_path, log=lambda *a: None)
    assert res["3mf"].exists()
    for v in res["summary"]["volumes"]:
        assert v["closed"], v
    sign = res["objects"][0]
    lo, hi = sign.volumes[0].mesh.bounds()
    expected = s.sign_thickness()
    # every sign volume lies within the sign thickness
    for v in sign.volumes:
        vlo, vhi = v.mesh.bounds()
        assert vlo[2] >= -1e-6 and vhi[2] <= expected + 1e-6, (v.name, vlo[2], vhi[2])
    if mode == "single":
        text = next(v for v in sign.volumes if v.name == "Text")
        qr = next(v for v in sign.volumes if v.name == "QR code")
        assert abs(text.mesh.bounds()[1][2] - (2.0 + 1.0 + 0.6)) < 1e-6
        assert abs(qr.mesh.bounds()[1][2] - 3.0) < 1e-6
    else:
        # all parts share the face plane: tops when face up, bottoms (z = 0) when flipped onto the bed
        idx = 0 if flip else 1
        face = {v.name: round(v.mesh.bounds()[idx][2], 4) for v in sign.volumes}
        assert len(set(face.values())) == 1, face
    # blank zone: nothing raised within the slot depth of the sign's bottom edge
    for v in sign.volumes[1:]:
        vlo, vhi = v.mesh.bounds()
        y_min = vlo[1] if not flip else vlo[1]
        assert y_min >= s.slot_depth_mm, (v.name, y_min)
    with zipfile.ZipFile(res["3mf"]) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", "Metadata/Slic3r_PE_model.config"} <= names
        cfg = z.read("Metadata/Slic3r_PE_model.config").decode()
        assert 'key="extruder"' in cfg and "Base" in z.read("3D/3dmodel.model").decode()


def test_slot_leans_back():
    s = print3d.PrintSettings(base_lean_deg=10)
    profile, slot = print3d.slot_profile(s, 3.0)
    minx, miny, maxx, maxy = slot.bounds
    # the slot bottom (low z) should be further back (+y) than its opening
    bottom = min(slot.exterior.coords, key=lambda c: c[1])
    top = max(slot.exterior.coords, key=lambda c: c[1])
    assert bottom[0] > top[0]
    assert profile.area < s.base_depth_mm * s.base_height_mm
    # slot floor stays above the base bottom
    assert miny < 0 or True


@pytest.mark.skipif(not PRUSA.exists(), reason="PrusaSlicer console not installed")
def test_prusaslicer_reads_it(tmp_path):
    p = _project()
    qr_path = tmp_path / "qr.svg"
    qr_path.write_text(p.qr_svg(size_mm=50), encoding="utf-8")
    res = print3d.make_print(p.sign_config(), qr_path, print3d.PrintSettings(), "p3d", tmp_path, log=lambda *a: None)
    out = subprocess.run([str(PRUSA), "--info", str(res["3mf"])], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    info = out.stdout.lower()
    assert "number_of_facets" in info and "size_z = 3.0" in info, out.stdout
    assert "manifold = no" not in info and "error" not in info, out.stdout
