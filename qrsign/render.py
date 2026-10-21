"""Render a QR module matrix as styled vector geometry, SVG and PNG.

Units inside this module are *modules*: one module = 1.0, origin at the top-left of the
code, y down, quiet zone added only at output time. All geometry is built with shapely so
touching modules union into single filled regions (no seams in laser fills).

Style options (names are the keys the GUI shows):
  body shapes   : square, rounded, dot, diamond, small, smooth, leaf, vbars, hbars, octagon
  eye frames    : square, rounded, circle, leaf, octagon, dotted
  eye balls     : square, rounded, circle, leaf, octagon, dotted
  logo knockout : none, square, circle, octagon  (size as a fraction of the code width)
"""
from dataclasses import dataclass, field
import io
import math

from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely import affinity

from . import encode as enc

BODY_SHAPES = ["square", "rounded", "dot", "diamond", "small", "smooth", "leaf", "vbars", "hbars", "octagon"]
EYE_FRAME_SHAPES = ["square", "rounded", "circle", "leaf", "octagon", "dotted"]
EYE_BALL_SHAPES = ["square", "rounded", "circle", "leaf", "octagon", "dotted"]
LOGO_SHAPES = ["none", "square", "circle", "octagon"]
QUIET_ZONE = 4   # modules, per the QR spec; not user-adjustable in v1


@dataclass
class Style:
    body: str = "square"
    eye_frame: str = "square"
    eye_ball: str = "square"
    logo_path: str = ""          # image file; "" for no logo
    logo_shape: str = "square"   # knockout shape behind the logo
    logo_size: float = 0.22      # fraction of the code width covered by the knockout
    logo_clear: bool = True      # remove modules behind the logo
    logo_svg_paths: list = field(default_factory=list)  # cached traced outlines (pixel coords)
    logo_px_size: tuple = (0, 0)
    logo_bbox: tuple = None                            # traced outline bbox in pixel coords

    def as_dict(self):
        return {"body": self.body, "eye_frame": self.eye_frame, "eye_ball": self.eye_ball,
                "logo_path": self.logo_path, "logo_shape": self.logo_shape,
                "logo_size": self.logo_size, "logo_clear": self.logo_clear}

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k in ("body", "eye_frame", "eye_ball", "logo_path", "logo_shape", "logo_size", "logo_clear"):
            if k in d:
                setattr(s, k, d[k])
        return s


# ----------------------------------------------------------------------------
# primitive shapes (module units)
# ----------------------------------------------------------------------------

def _arc(cx, cy, r, a0, a1, segs=8):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / segs), cy + r * math.sin(a0 + (a1 - a0) * i / segs))
            for i in range(segs + 1)]


def rounded_rect(x, y, w, h, radii):
    """Rectangle with per-corner radii (tl, tr, br, bl); 0 = sharp. y grows downward."""
    tl, tr, br, bl = [min(r, w / 2, h / 2) for r in radii]
    pts = []
    # top-left corner, going clockwise on screen (x right, y down)
    pts += _arc(x + tl, y + tl, tl, math.pi, 1.5 * math.pi) if tl > 0 else [(x, y)]
    pts += _arc(x + w - tr, y + tr, tr, 1.5 * math.pi, 2 * math.pi) if tr > 0 else [(x + w, y)]
    pts += _arc(x + w - br, y + h - br, br, 0, 0.5 * math.pi) if br > 0 else [(x + w, y + h)]
    pts += _arc(x + bl, y + h - bl, bl, 0.5 * math.pi, math.pi) if bl > 0 else [(x, y + h)]
    clean = [pts[0]]
    for q in pts[1:]:
        if abs(q[0] - clean[-1][0]) > 1e-9 or abs(q[1] - clean[-1][1]) > 1e-9:
            clean.append(q)
    poly = Polygon(clean)
    return poly if poly.is_valid else poly.buffer(0)


def circle(cx, cy, d, segs=32):
    return Point(cx, cy).buffer(d / 2, quad_segs=segs // 4)


def octagon(x, y, size, cut):
    """Square of `size` at (x, y) with the four corners cut back by `cut` along each edge."""
    c = min(cut, size / 2)
    return Polygon([(x + c, y), (x + size - c, y), (x + size, y + c), (x + size, y + size - c),
                    (x + size - c, y + size), (x + c, y + size), (x, y + size - c), (x, y + c)])


def diamond(cx, cy, diag):
    h = diag / 2
    return Polygon([(cx, cy - h), (cx + h, cy), (cx, cy + h), (cx - h, cy)])


# ----------------------------------------------------------------------------
# body modules
# ----------------------------------------------------------------------------

def body_module(shape, r, c, nb):
    """One dark module at row r, column c. nb = dict of neighbour darkness: up, down, left, right."""
    x, y = float(c), float(r)
    if shape == "square":
        return Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])
    if shape == "rounded":
        return rounded_rect(x + 0.03, y + 0.03, 0.94, 0.94, (0.3, 0.3, 0.3, 0.3))
    if shape == "dot":
        return circle(x + 0.5, y + 0.5, 0.88)
    if shape == "diamond":
        return diamond(x + 0.5, y + 0.5, 1.12)
    if shape == "small":
        return Polygon([(x + 0.12, y + 0.12), (x + 0.88, y + 0.12), (x + 0.88, y + 0.88), (x + 0.12, y + 0.88)])
    if shape == "smooth":
        # corners rounded only where that corner is free of neighbours; chains merge into blobs
        return rounded_rect(x, y, 1, 1, (
            0.5 if not nb["up"] and not nb["left"] else 0,
            0.5 if not nb["up"] and not nb["right"] else 0,
            0.5 if not nb["down"] and not nb["right"] else 0,
            0.5 if not nb["down"] and not nb["left"] else 0))
    if shape == "leaf":
        # top-left and bottom-right corners rounded when free, the other two always sharp
        return rounded_rect(x, y, 1, 1, (
            0.5 if not nb["up"] and not nb["left"] else 0, 0,
            0.5 if not nb["down"] and not nb["right"] else 0, 0))
    if shape == "vbars":
        w = 0.82
        return rounded_rect(x + (1 - w) / 2, y, w, 1, (
            0 if nb["up"] else w / 2, 0 if nb["up"] else w / 2,
            0 if nb["down"] else w / 2, 0 if nb["down"] else w / 2))
    if shape == "hbars":
        h = 0.82
        return rounded_rect(x, y + (1 - h) / 2, 1, h, (
            0 if nb["left"] else h / 2, 0 if nb["right"] else h / 2,
            0 if nb["right"] else h / 2, 0 if nb["left"] else h / 2))
    if shape == "octagon":
        return octagon(x, y, 1, 0.27)
    raise ValueError(f"unknown body shape {shape!r}")


# ----------------------------------------------------------------------------
# eyes
# ----------------------------------------------------------------------------

def _flip(geom, x0, y0, flip_x, flip_y):
    """Mirror a 7x7 eye built in local coords (0..7) about its centre, then translate to (x0, y0)."""
    g = geom
    if flip_x:
        g = affinity.scale(g, xfact=-1, yfact=1, origin=(3.5, 3.5))
    if flip_y:
        g = affinity.scale(g, xfact=1, yfact=-1, origin=(3.5, 3.5))
    return affinity.translate(g, x0, y0)


def eye_frame(shape):
    """7x7 ring, 1 module thick, in local coords."""
    if shape == "square":
        return rounded_rect(0, 0, 7, 7, (0, 0, 0, 0)).difference(rounded_rect(1, 1, 5, 5, (0, 0, 0, 0)))
    if shape == "rounded":
        return rounded_rect(0, 0, 7, 7, (2, 2, 2, 2)).difference(rounded_rect(1, 1, 5, 5, (1, 1, 1, 1)))
    if shape == "circle":
        return circle(3.5, 3.5, 7).difference(circle(3.5, 3.5, 5))
    if shape == "leaf":
        return rounded_rect(0, 0, 7, 7, (2.5, 0, 2.5, 0)).difference(rounded_rect(1, 1, 5, 5, (1.5, 0, 1.5, 0)))
    if shape == "octagon":
        return octagon(0, 0, 7, 1.6).difference(octagon(1, 1, 5, 1.6 - 0.414))
    if shape == "dotted":
        dots = [circle(i + 0.5, 0.5, 0.9) for i in range(7)] + [circle(i + 0.5, 6.5, 0.9) for i in range(7)]
        dots += [circle(0.5, j + 0.5, 0.9) for j in range(1, 6)] + [circle(6.5, j + 0.5, 0.9) for j in range(1, 6)]
        return unary_union(dots)
    raise ValueError(f"unknown eye frame shape {shape!r}")


def eye_ball(shape):
    """3x3 centre of the eye, in the same local 7x7 coords (occupies 2..5)."""
    if shape == "square":
        return rounded_rect(2, 2, 3, 3, (0, 0, 0, 0))
    if shape == "rounded":
        return rounded_rect(2, 2, 3, 3, (0.9, 0.9, 0.9, 0.9))
    if shape == "circle":
        return circle(3.5, 3.5, 3)
    if shape == "leaf":
        return rounded_rect(2, 2, 3, 3, (1.3, 0, 1.3, 0))
    if shape == "octagon":
        return octagon(2, 2, 3, 0.8)
    if shape == "dotted":
        # a solid core with dots on the edge: nine separate dots break finder detection
        return unary_union([rounded_rect(2.2, 2.2, 2.6, 2.6, (0, 0, 0, 0))]
                           + [circle(2.5 + i, 2.5 + j, 1.0) for i in range(3) for j in range(3)])
    raise ValueError(f"unknown eye ball shape {shape!r}")


# ----------------------------------------------------------------------------
# logo
# ----------------------------------------------------------------------------

def load_logo(path):
    """Logo -> (outline paths, (w, h), bbox) in the logo's own coordinates.
    Raster images (PNG/JPG/BMP) are traced with potrace; SVG logos keep their dark vector shapes."""
    from .signmaker import raster_paths, load_qr_svg
    if str(path).lower().endswith(".svg"):
        paths, bbox = load_qr_svg(path)   # dark fills only, embedded images traced, white ignored
        return paths, (bbox[2] - bbox[0], bbox[3] - bbox[1]), bbox
    from PIL import Image
    im = Image.open(path)
    paths, bbox = raster_paths(im)
    return paths, (im.width, im.height), bbox


def _logo_polygons(paths, bbox, box_x, box_y, box_w, box_h):
    """Shapely polygons of the traced logo scaled uniformly into the given box (module units)."""
    from svgelements import Path as SPath
    from .signmaker import _subpath_points
    from shapely.geometry.polygon import orient as _orient
    bx0, by0, bx1, by1 = bbox
    k = min(box_w / max(bx1 - bx0, 1e-9), box_h / max(by1 - by0, 1e-9))
    ox = box_x + (box_w - (bx1 - bx0) * k) / 2 - bx0 * k
    oy = box_y + (box_h - (by1 - by0) * k) / 2 - by0 * k
    shapes = []
    for d in paths:
        p = SPath(d)
        rings = []
        for sub in p.as_subpaths():
            pts = _subpath_points(sub, 0.5)
            if len(pts) >= 3:
                poly = Polygon([(x * k + ox, y * k + oy) for x, y in pts]).buffer(0)
                if not poly.is_empty:
                    rings.append(poly)
        if not rings:
            continue
        rings.sort(key=lambda g: -g.area)
        shape = rings[0]
        for g in rings[1:]:
            shape = shape.difference(g) if shape.contains(g.representative_point()) else shape.union(g)
        shapes.append(shape)
    return shapes


# ----------------------------------------------------------------------------
# assembly
# ----------------------------------------------------------------------------

def build_geometry(matrix: enc.Matrix, style: Style):
    """All black geometry of the styled code as one (Multi)Polygon in module units."""
    n = matrix.size
    pieces = []
    for r in range(n):
        for c in range(n):
            if not matrix.dark[r][c] or matrix.in_eye(r, c):
                continue
            nb = {"up": matrix.is_dark(r - 1, c) and not matrix.in_eye(r - 1, c),
                  "down": matrix.is_dark(r + 1, c) and not matrix.in_eye(r + 1, c),
                  "left": matrix.is_dark(r, c - 1) and not matrix.in_eye(r, c - 1),
                  "right": matrix.is_dark(r, c + 1) and not matrix.in_eye(r, c + 1)}
            pieces.append(body_module(style.body, r, c, nb))
    frame, ball = eye_frame(style.eye_frame), eye_ball(style.eye_ball)
    for (r0, c0), (fx, fy) in zip(matrix.eye_origins(), [(False, False), (True, False), (False, True)]):
        pieces.append(_flip(frame, c0, r0, fx, fy))
        pieces.append(_flip(ball, c0, r0, fx, fy))
    geom = unary_union(pieces)

    if style.logo_path and style.logo_svg_paths:
        side = max(0.05, min(0.4, style.logo_size)) * n
        cx = cy = n / 2
        margin = 0.6
        if style.logo_shape == "circle":
            knock = circle(cx, cy, side + 2 * margin)
            box = side / math.sqrt(2) * 0.95
        elif style.logo_shape == "octagon":
            knock = octagon(cx - side / 2 - margin, cy - side / 2 - margin, side + 2 * margin, (side + 2 * margin) * 0.29)
            box = side * 0.8
        elif style.logo_shape == "square":
            knock = rounded_rect(cx - side / 2 - margin, cy - side / 2 - margin, side + 2 * margin, side + 2 * margin, (0.5,) * 4)
            box = side * 0.92
        else:  # "none": no knockout, logo drawn over the modules
            knock, box = None, side * 0.92
        if knock is not None and style.logo_clear:
            geom = geom.difference(knock)
        logo = _logo_polygons(style.logo_svg_paths, style.logo_bbox, cx - box / 2, cy - box / 2, box, box)
        if logo:
            geom = unary_union([geom, *logo])
    return geom


def _polys(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def geometry_path_d(geom, offset=QUIET_ZONE, precision=3):
    """SVG path data for the geometry (exterior CCW, holes CW; works with nonzero and evenodd)."""
    parts = []
    for poly in _polys(geom):
        poly = orient(poly, 1.0)
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)[:-1]
            parts.append("M" + " L".join(f"{x + offset:.{precision}f},{y + offset:.{precision}f}" for x, y in coords) + " Z")
    return " ".join(parts)


def to_svg(geom, n, size_mm=None, size_px=None, background=False):
    """Standalone SVG of the styled code. Size in mm (for cutting) or px (for screens); default 1 module = 1 unit."""
    total = n + 2 * QUIET_ZONE
    if size_mm:
        wh = f'width="{size_mm}mm" height="{size_mm}mm"'
    elif size_px:
        wh = f'width="{size_px}" height="{size_px}"'
    else:
        wh = f'width="{total}" height="{total}"'
    bg = f'  <rect width="{total}" height="{total}" fill="#ffffff"/>\n' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" {wh} viewBox="0 0 {total} {total}">\n'
            f'{bg}  <path fill="#000000" fill-rule="nonzero" d="{geometry_path_d(geom)}"/>\n</svg>\n')


def to_png(geom, n, size_px=1000, supersample=4):
    """Rasterise with Pillow (no external renderer): white background, black code."""
    from PIL import Image, ImageDraw
    total = n + 2 * QUIET_ZONE
    big = size_px * supersample
    k = big / total
    im = Image.new("L", (big, big), 255)
    draw = ImageDraw.Draw(im)
    polys = sorted(_polys(geom), key=lambda p: -p.area)   # big first so inner shapes survive hole fills
    for poly in polys:
        draw.polygon([((x + QUIET_ZONE) * k, (y + QUIET_ZONE) * k) for x, y in poly.exterior.coords], fill=0)
        for ring in poly.interiors:
            draw.polygon([((x + QUIET_ZONE) * k, (y + QUIET_ZONE) * k) for x, y in ring.coords], fill=255)
    return im.resize((size_px, size_px), Image.LANCZOS)


def png_bytes(geom, n, size_px=1000):
    buf = io.BytesIO()
    to_png(geom, n, size_px).save(buf, format="PNG")
    return buf.getvalue()


def verify(png_image, expected_text):
    """Decode the rendered image with zxing-cpp and compare. Returns (ok, decoded_text_or_None)."""
    try:
        import zxingcpp
    except ImportError:
        return None, "zxing-cpp not installed"
    results = zxingcpp.read_barcodes(png_image)
    for res in results:
        if res.text == expected_text:
            return True, res.text
    return False, results[0].text if results else None


def render(text, style: Style):
    """Convenience: text + style -> (matrix, geometry)."""
    m = enc.encode(text)
    return m, build_geometry(m, style)


# ----------------------------------------------------------------------------
# picker thumbnails
# ----------------------------------------------------------------------------

_SAMPLE = [
    "1101",
    "1011",
    "0110",
    "1101",
]


def body_icon_svg(shape, px=48):
    """Small SVG showing a body shape on a 4x4 sample pattern."""
    n = len(_SAMPLE)
    dark = [[ch == "1" for ch in row] for row in _SAMPLE]
    pieces = []
    for r in range(n):
        for c in range(n):
            if not dark[r][c]:
                continue
            nb = {"up": r > 0 and dark[r - 1][c], "down": r < n - 1 and dark[r + 1][c],
                  "left": c > 0 and dark[r][c - 1], "right": c < n - 1 and dark[r][c + 1]}
            pieces.append(body_module(shape, r, c, nb))
    geom = unary_union(pieces)
    d = geometry_path_d(geom, offset=0.5)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="0 0 {n + 1} {n + 1}"><path fill="#111" d="{d}"/></svg>'


def eye_icon_svg(frame=None, ball=None, px=48):
    """Small SVG of an eye frame (with a plain square ball) or an eye ball (with a plain frame)."""
    geom = unary_union([eye_frame(frame or "square") if frame else eye_frame("square").buffer(-0.35),
                        eye_ball(ball or "square") if ball else eye_ball("square").buffer(-0.35)])
    d = geometry_path_d(geom, offset=0.5)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="0 0 8 8"><path fill="#111" d="{d}"/></svg>'
