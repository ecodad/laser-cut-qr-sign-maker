"""Project files, logo knockout and the stage-2 hand-off into the sign generator."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qrsign import project, render, fonts  # noqa: E402

URL = "https://github.com/ecodad/laser-cut-qr-sign-maker"
LOGO = ROOT / "qr" / "medforge-logo.png"


def _font():
    names = fonts.font_names()
    for pref in ("Ravenscroft", "Arial", "DejaVu Sans", "Liberation Sans", "Helvetica"):
        if pref in names:
            return pref
    return names[0]


def test_project_roundtrip(tmp_path):
    p = project.Project(name="test", url=URL, style=render.Style(body="dot", eye_frame="circle", eye_ball="circle"))
    path = p.save(tmp_path / "test.qrsign.json")
    q = project.Project.load(path)
    assert q.url == URL and q.style.body == "dot" and q.sign["width_mm"] == 80
    ok, text = q.verify()
    assert ok, text


@pytest.mark.skipif(not LOGO.exists(), reason="local logo asset not present")
@pytest.mark.parametrize("shape", render.LOGO_SHAPES)
def test_logo_knockout_scans(shape):
    st = render.Style(body="smooth", logo_path=str(LOGO), logo_shape=shape, logo_size=0.22)
    p = project.Project(name="logo", url=URL, style=st)
    ok, text = p.verify()
    assert ok, f"{shape}: {text!r}"


def test_logo_size_limit_still_scans():
    if not LOGO.exists():
        pytest.skip("no logo asset")
    st = render.Style(body="square", logo_path=str(LOGO), logo_shape="square", logo_size=0.3)
    ok, text = project.Project(name="big", url=URL, style=st).verify()
    assert ok, text


def test_make_sign(tmp_path):
    p = project.Project(name="sign", url=URL, style=render.Style(body="smooth", eye_frame="rounded", eye_ball="rounded"))
    p.sign.update({"text_above": "Donate to | Example", "text_above_scales": "0.45 | 1",
                   "text_below": "Thank you!", "font": _font()})
    res = p.make_sign(tmp_path, log=lambda *a: None)
    for key in ("svg", "sign_svg", "base_svg", "json", "qr_svg", "config_txt"):
        assert Path(res[key]).exists(), key
    assert 'stroke="#ff0000"' in res["sign_svg_text"] and 'fill="#000000"' in res["sign_svg_text"]
    cfg = p.sign_config()
    assert abs(cfg["angle_deg"] + cfg["base_angle_deg"] - 90) < 0.05
    assert res["info"]["qr_mm"] > 40
