"""Assembled previews: the laser stand on the table and the printed sign in its base."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qrsign import assembly, print3d, project, render, fonts  # noqa: E402

URL = "https://github.com/ecodad/laser-cut-qr-sign-maker"


def _project():
    names = fonts.font_names()
    font = next((f for f in ("Ravenscroft", "Arial", "DejaVu Sans", "Liberation Sans") if f in names), names[0])
    p = project.Project(name="asm", url=URL, style=render.Style(body="smooth"))
    p.sign.update({"text_above": "Donate to | Example", "text_above_scales": "0.45 | 1", "text_below": "Thanks!", "font": font})
    return p


def test_laser_stand_geometry(tmp_path):
    p = _project()
    cfg = p.sign_config()
    qr = tmp_path / "qr.svg"
    qr.write_text(p.qr_svg(size_mm=50), encoding="utf-8")
    text, qrg, info, cfg2 = print3d.layout_geometry(cfg, qr, None, log=lambda *a: None)
    parts = assembly.laser_stand(cfg2, text, qrg)
    names = [n for n, _, _ in parts]
    assert names[:2] == ["Sign", "Base"] and "Engraving" in names and "Text" in names
    for name, mesh, _ in parts:
        assert mesh.is_closed(), name
    lo, hi = assembly.bounds(parts)
    # on the table, standing about as tall as the sign, leaning back a little
    assert lo[2] > -2.0 and 140 < hi[2] <= cfg["height_mm"] + 1
    sign = parts[0][1]
    assert sign.vertices[:, 1].max() > 10          # top of the leaning sign is behind its bottom
    engr = next(m for n, m, _ in parts if n == "Engraving")
    # the engraving sits on the front face: in front of (more negative y than) the sign's mid-plane
    assert engr.vertices[:, 1].mean() < sign.vertices[:, 1].mean()
    written = assembly.write_parts(parts, tmp_path, "asm")
    assert all(Path(path).stat().st_size > 84 for _, path, _ in written)


def test_printed_stand_geometry(tmp_path):
    p = _project()
    res = p.make_print(tmp_path, log=lambda *a: None)
    parts = assembly.printed_stand(res, p.print)
    names = [n for n, _, _ in parts]
    assert "Background" in names and "Base" in names
    base = next(m for n, m, _ in parts if n == "Base")
    sign = next(m for n, m, _ in parts if n == "Background")
    blo, bhi = base.bounds()
    slo, shi = sign.bounds()
    assert abs(blo[2]) < 1e-6                                   # base on the table
    assert slo[2] > 0 and slo[2] < bhi[2]                      # sign bottom inside the base
    assert shi[2] > bhi[2] + 100                                 # sign stands well above the base
    assert blo[0] - 1 < slo[0] and shi[0] < bhi[0] + 1           # sign within the base's width
    # leaning back: the sign's top is further back (+y) than its bottom
    top = sign.vertices[sign.vertices[:, 2].argmax()]
    bottom = sign.vertices[sign.vertices[:, 2].argmin()]
    assert top[1] > bottom[1]
