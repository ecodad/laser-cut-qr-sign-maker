"""Every style must still scan. Run: python -m pytest tests -q"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qrsign import encode, render  # noqa: E402

URL = "https://github.com/ecodad/laser-cut-qr-sign-maker"


def _scan(style):
    m = encode.encode(URL)
    geom = render.build_geometry(m, style)
    im = render.to_png(geom, m.size, size_px=600)
    ok, text = render.verify(im, URL)
    return ok, text


def test_matrix_roles():
    m = encode.encode(URL)
    assert m.size >= 21 and m.dark[0][0] and m.role[0][0] == encode.FINDER
    assert len(m.alignment_centres()) >= 1


@pytest.mark.parametrize("body", render.BODY_SHAPES)
def test_body_shapes_scan(body):
    ok, text = _scan(render.Style(body=body))
    assert ok, f"{body}: decoded {text!r}"


@pytest.mark.parametrize("frame,ball", list(itertools.product(render.EYE_FRAME_SHAPES, render.EYE_BALL_SHAPES)))
def test_eye_shapes_scan(frame, ball):
    ok, text = _scan(render.Style(body="smooth", eye_frame=frame, eye_ball=ball))
    assert ok, f"{frame}/{ball}: decoded {text!r}"


def test_svg_output():
    m = encode.encode(URL)
    geom = render.build_geometry(m, render.Style(body="dot", eye_frame="circle", eye_ball="circle"))
    svg = render.to_svg(geom, m.size, size_mm=50)
    assert svg.startswith("<svg") and 'width="50mm"' in svg and "<path" in svg


def test_icons():
    for b in render.BODY_SHAPES:
        assert "<path" in render.body_icon_svg(b)
    for f in render.EYE_FRAME_SHAPES:
        assert "<path" in render.eye_icon_svg(frame=f)
    for b in render.EYE_BALL_SHAPES:
        assert "<path" in render.eye_icon_svg(ball=b)
