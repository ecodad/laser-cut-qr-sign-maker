"""Optional QR + URL engraved on the top of the laser base, with the base drawing mirrored."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qrsign import assembly, encode, print3d, project, render, fonts  # noqa: E402
from qrsign import signmaker as sm  # noqa: E402

URL = "https://github.com/ecodad/laser-cut-qr-sign-maker"


def _project(**sign):
    names = fonts.font_names()
    font = next((f for f in ("Ravenscroft", "Arial", "DejaVu Sans", "Liberation Sans") if f in names), names[0])
    p = project.Project(name="bq", url=URL, style=render.Style(body="smooth"))
    p.sign.update({"text_above": "Donate to | Example", "text_above_scales": "0.45 | 1", "text_below": "Thanks!", "font": font})
    p.sign.update(sign)
    return p


def test_base_engraving_present_and_placed(tmp_path):
    p = _project()
    res = p.make_sign(tmp_path, log=lambda *a: None)
    base = res["base_svg_text"]
    assert 'id="base-engrave"' in base and base.count("<path") >= 3     # QR, URL text, cut
    assert 'transform="matrix(-1 0 0 1' in base                           # the cut outline is mirrored
    info = res["info"]
    assert info["base_qr_mm"] == 30 and info["base_qr_url"].startswith("github.com/")
    # the engraving sits behind the slot, inside the border
    cfg = p.sign_config()
    m = re.search(r'translate\(([\d.]+),([\d.]+)\) scale\(([\d.]+)\)', base)
    x, y, k = float(m.group(1)), float(m.group(2)), float(m.group(3))
    assert y > cfg["base_front_mm"] + sm.slot_width(cfg) / 2 + cfg["border_mm"] - 1e-6
    assert x + 30 <= cfg["base_width_mm"] - cfg["border_mm"] + 1e-6


def test_base_engraving_off(tmp_path):
    p = _project(base_qr=0)
    res = p.make_sign(tmp_path, log=lambda *a: None)
    assert 'id="base-engrave"' in res["base_svg_text"]
    assert res["base_svg_text"].count("<path") == 1                      # only the cut outline
    assert "base_qr_mm" not in res["info"]


def test_base_code_decodes_at_30mm():
    """The plain square code at 30 mm has modules of about 0.8 mm; make sure it is a small version and decodes."""
    m = encode.encode("github.com/ecodad/laser-cut-qr-sign-maker", error="m")
    assert m.version <= 3
    geom = render.build_geometry(m, render.Style())
    ok, text = render.verify(render.to_png(geom, m.size, 300), m.text)
    assert ok, text


def test_custom_url_and_text(tmp_path):
    p = _project(base_qr_url="example.org/x", base_qr_text="scan me", base_qr_size_mm=20)
    res = p.make_sign(tmp_path, log=lambda *a: None)
    assert res["info"]["base_qr_mm"] == 20 and res["info"]["base_qr_text"] == "scan me"


def test_assembled_base_engraving_on_top(tmp_path):
    p = _project()
    res = p.make_sign(tmp_path, log=lambda *a: None)
    cfg = p.sign_config()
    text, qr, _, cfg3 = print3d.layout_geometry(cfg, res["qr_svg"], None, log=lambda *a: None)
    parts = assembly.laser_stand(cfg3, text, qr, res["base_engrave"])
    names = [n for n, _, _ in parts]
    assert "Base engraving" in names
    base = next(m for n, m, _ in parts if n == "Base")
    eng = next(m for n, m, _ in parts if n == "Base engraving")
    assert eng.is_closed()
    # the engraving lies on the base plate's upper face: measured along the plate's drawing-face
    # normal (which points down in the assembly) it sits just beyond the top surface, and it is
    # behind the sign (y > 0)
    import math
    import numpy as np
    tb = math.radians(cfg3["base_angle_deg"])
    n_base = np.array([0.0, -math.cos(tb), -math.sin(tb)])
    ts_ = math.radians(cfg3["angle_deg"])
    u_sign = np.array([0.0, math.sin(ts_), math.cos(ts_)])
    u_base = np.array([0.0, -math.sin(tb), math.cos(tb)])
    base_origin = u_sign * cfg3["base_height_mm"] - u_base * cfg3["base_back_mm"]
    d = (eng.vertices - base_origin) @ n_base
    t = cfg3["thickness_mm"]
    assert d.max() <= -t / 2 + 1e-6 and d.min() >= -t / 2 - assembly.ENGRAVE_MM - 1e-6, (d.min(), d.max())
    assert eng.vertices[:, 1].min() > 5


def test_base_text_uses_plain_sans():
    ts = sm.base_text_typesetter(None)
    assert ts is not None
    assert ts.font["name"].getDebugName(1) in sm.BASE_TEXT_FONTS
