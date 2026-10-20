#!/usr/bin/env python
"""
make-laser-qr.py — generate a two-piece laser-cut QR-code stand as an SVG.

    python make-laser-qr.py --config configs/medforge-give.txt

Output: <output_dir>/<config-name>.svg       both pieces side by side (cut lines red, engraving black)
        <output_dir>/<config-name>-sign.svg  sign piece alone, <config-name>-base.svg base piece alone
        <output_dir>/<config-name>.json (geometry summary for 3D previews)

The stand is two boards that slide together from the side:
  * SIGN  — W x H, leans back `angle_deg` from vertical. Engraved face:
            text above, QR code, text below, all inside `border_mm` and
            above the slot where the base passes through.
  * BASE  — passes through the sign `base_height_mm` up from the sign's
            bottom edge, extending `base_front_mm` forward past the sign
            face and `base_back_mm` back to the ground. Seen from the side
            the two boards make a non-symmetric X.

Config file: `key: value` lines, `#` comments. Text lines separated by `|`.
See configs/medforge-give.txt for every key and its default.
"""
import argparse
import base64
import io
import json
import math
import re
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

DEFAULTS = {
    "width_mm": 80.0,
    "height_mm": 150.0,
    "thickness_mm": 3.0,
    "angle_deg": 15.0,          # sign lean, from vertical
    "crossing_angle_deg": None, # alternative to angle_deg: angle between the boards (90 =
                                # plain thickness-wide slots); the sign lean is then solved
    "text_above": "",
    "qr_file": "",
    "text_below": "",
    "font": "Ravenscroft",
    "border_mm": 5.0,
    # optional
    "text_above_scales": "",    # relative size per line, e.g. "0.45 | 1" (default all 1)
    "text_below_scales": "",
    "gap_mm": None,             # gap between text blocks and QR (default border/2)
    "base_height_mm": None,     # slot position up the sign (default 0.3 * height)
    "base_front_mm": None,      # base past the sign face (default = base_height)
    "base_back_mm": None,       # base behind the sign to the ground (default 1.3 * base_height)
    "base_width_mm": None,      # default = width
    "base_angle_deg": None,     # base lean from vertical (default: computed so the
                                # back edge of the base lands on the ground)
    "corner_radius_mm": 2.5,    # radius on the four outer corners of each piece (0 = square)
    "merge_qr": 1,              # 1 = union abutting QR modules / logo into single filled regions
    "slot_clearance_mm": 0.1,   # added to every slot width (kerf / fit)
    "qr_fraction": 0.55,        # share of the content height reserved for the QR code
    "line_spacing": 1.15,       # multiple of font size between baselines
    "cut_stroke_mm": 0.1,
    "piece_spacing_mm": 5.0,
    "output_dir": "output",
}

TEXT_KEYS = {"text_above", "text_below", "qr_file", "font", "output_dir",
             "text_above_scales", "text_below_scales"}


def read_config(path):
    cfg = dict(DEFAULTS)
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                sys.exit(f"config: cannot parse line: {raw!r}")
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_").replace("-", "_")
            value = re.sub(r"\s+#.*$", "", value).strip()  # trailing comment
            if key not in DEFAULTS:
                sys.exit(f"config: unknown key {key!r} (known: {', '.join(DEFAULTS)})")
            if key in TEXT_KEYS:
                cfg[key] = value
            else:
                cfg[key] = float(value) if value else None
    # derived defaults
    W, H = cfg["width_mm"], cfg["height_mm"]
    if cfg["gap_mm"] is None:
        cfg["gap_mm"] = cfg["border_mm"] / 2
    if cfg["base_height_mm"] is None:
        cfg["base_height_mm"] = round(0.3 * H, 1)
    if cfg["base_front_mm"] is None:
        cfg["base_front_mm"] = cfg["base_height_mm"]
    if cfg["base_back_mm"] is None:
        cfg["base_back_mm"] = round(1.3 * cfg["base_height_mm"], 1)
    if cfg["base_width_mm"] is None:
        cfg["base_width_mm"] = W
    if cfg["corner_radius_mm"] is None:
        cfg["corner_radius_mm"] = 0.0
    L, Lb = cfg["base_height_mm"], cfg["base_back_mm"]
    if cfg["crossing_angle_deg"] is not None:
        # solve the sign lean so that lean + base lean = crossing and the base's back
        # edge lands on the ground: Lb*cos(crossing - lean) = L*cos(lean)
        c = math.radians(cfg["crossing_angle_deg"])
        f = lambda t: Lb * math.cos(c - t) - L * math.cos(t)
        lo, hi = 0.0, c
        if f(lo) > 0 or f(hi) < 0:
            sys.exit("crossing_angle_deg: no sign lean satisfies it with these base_height/base_back lengths")
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
        cfg["angle_deg"] = round(math.degrees((lo + hi) / 2), 2)
        cfg["base_angle_deg"] = round(cfg["crossing_angle_deg"] - cfg["angle_deg"], 2)
    if cfg["base_angle_deg"] is None:
        # back end of the base must reach the ground: Lb*cos(tb) = L*cos(ts)
        k = L * math.cos(math.radians(cfg["angle_deg"])) / Lb
        if k > 1:
            sys.exit("base_back_mm is too short to reach the ground; increase it")
        cfg["base_angle_deg"] = round(math.degrees(math.acos(k)), 2)
    return cfg


# ----------------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------------

def find_font(name):
    import os
    p = Path(name)
    if p.suffix.lower() in (".ttf", ".otf") and p.exists():
        return p
    dirs = [Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path.home() / ".fonts", Path("/usr/share/fonts"), Path("/Library/Fonts")]
    want = name.lower().replace(" ", "")
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() in (".ttf", ".otf") and f.stem.lower().replace(" ", "").replace("-", "") == want:
                return f
    for d in dirs:  # fall back to the font's internal family name
        if not d.exists():
            continue
        for f in d.rglob("*.ttf"):
            try:
                if TTFont(f)["name"].getDebugName(1).lower() == name.lower():
                    return f
            except Exception:
                pass
    sys.exit(f"font not found: {name!r} (give a .ttf/.otf path or install it)")


class Typesetter:
    """Turns text into SVG path data using the glyph outlines of a TTF/OTF."""

    def __init__(self, font_path):
        self.font = TTFont(font_path)
        self.upm = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.kern = {}
        if "kern" in self.font:
            for table in self.font["kern"].kernTables:
                if hasattr(table, "kernTable"):
                    self.kern.update(table.kernTable)

    def _names(self, text):
        out = []
        for ch in text:
            g = self.cmap.get(ord(ch))
            if g is None:
                print(f"warning: no glyph for {ch!r}, skipped", file=sys.stderr)
                continue
            out.append(g)
        return out

    def layout(self, text):
        """Per-glyph x offsets in font units plus the ink bounding box (font units, y up)."""
        names = self._names(text)
        pen_x, placed = 0, []
        xmin = ymin = math.inf
        xmax = ymax = -math.inf
        for i, g in enumerate(names):
            if i:
                pen_x += self.kern.get((names[i - 1], g), 0)
            bp = BoundsPen(self.glyphs)
            self.glyphs[g].draw(bp)
            if bp.bounds:
                x0, y0, x1, y1 = bp.bounds
                xmin, ymin = min(xmin, pen_x + x0), min(ymin, y0)
                xmax, ymax = max(xmax, pen_x + x1), max(ymax, y1)
            placed.append((g, pen_x))
            pen_x += self.hmtx[g][0]
        if xmin is math.inf:
            xmin = ymin = xmax = ymax = 0
        return placed, (xmin, ymin, xmax, ymax)

    def path(self, text, size_mm, x_left_mm, baseline_mm):
        """SVG path data for `text` with its ink left edge at x_left_mm (mm, y down)."""
        placed, (xmin, _, _, _) = self.layout(text)
        k = size_mm / self.upm
        parts = []
        for g, pen_x in placed:
            sp = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.3f}")
            tp = TransformPen(sp, (k, 0, 0, -k, x_left_mm + (pen_x - xmin) * k, baseline_mm))
            self.glyphs[g].draw(tp)
            d = sp.getCommands()
            if d:
                parts.append(d)
        return " ".join(parts)


# ----------------------------------------------------------------------------
# QR code import
# ----------------------------------------------------------------------------

def load_qr(path):
    """Return (list of SVG path 'd' strings in source coords, bbox)."""
    ext = Path(path).suffix.lower()
    if ext == ".svg":
        return load_qr_svg(path)
    if ext in (".png", ".jpg", ".jpeg"):
        from PIL import Image
        paths, bbox = raster_paths(Image.open(path))
        if not paths:
            sys.exit("qr_file: no dark pixels found")
        return paths, bbox
    sys.exit(f"qr_file: unsupported type {ext}")


def raster_rects(im, threshold=128):
    """Dark pixels of a PIL image as merged rectangles (x0, y0, x1, y1) in pixel coords."""
    if im.mode in ("RGBA", "LA", "P"):
        # composite onto white so transparent pixels count as light
        from PIL import Image
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    im = im.convert("L")
    w, h = im.size
    px = im.load()
    rects, open_rects = [], {}
    for y in range(h):
        runs, x = [], 0
        while x < w:
            if px[x, y] < threshold:
                x0 = x
                while x < w and px[x, y] < threshold:
                    x += 1
                runs.append((x0, x))
            else:
                x += 1
        new_open = {}
        for r in runs:
            new_open[r] = open_rects.pop(r, y)
        for r, y0 in open_rects.items():
            rects.append((r[0], y0, r[1], y))
        open_rects = new_open
    for r, y0 in open_rects.items():
        rects.append((r[0], y0, r[1], h))
    return rects


def raster_paths(im, threshold=128):
    """Dark areas of a PIL image as SVG path data in pixel coords (origin top-left).
    Uses potrace (smooth outlines, one path per connected shape incl. holes) when the
    `potracer` package is installed; otherwise falls back to row rectangles, which render
    with hairline seams on sloped edges."""
    if im.mode in ("RGBA", "LA", "P"):
        from PIL import Image
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    im = im.convert("L")
    try:
        import numpy as np
        import potrace
    except ImportError:
        rects = raster_rects(im, threshold)
        if not rects:
            return [], None
        return [f"M{x0},{y0}H{x1}V{y1}H{x0}Z" for x0, y0, x1, y1 in rects], rects_bbox(rects)
    # potracer traces the False pixels, so dark pixels must be False
    bitmap = np.array(im) >= threshold
    if bitmap.all():
        return [], None
    paths, bbox = [], None
    for curve in potrace.Bitmap(bitmap).trace(turdsize=2, alphamax=1.0, opttolerance=0.2):
        sp = curve.start_point
        d = [f"M{sp.x:.3f},{sp.y:.3f}"]
        pts = [(sp.x, sp.y)]
        for seg in curve.segments:
            e = seg.end_point
            if seg.is_corner:
                c = seg.c
                d.append(f"L{c.x:.3f},{c.y:.3f} L{e.x:.3f},{e.y:.3f}")
                pts += [(c.x, c.y), (e.x, e.y)]
            else:
                c1, c2 = seg.c1, seg.c2
                d.append(f"C{c1.x:.3f},{c1.y:.3f} {c2.x:.3f},{c2.y:.3f} {e.x:.3f},{e.y:.3f}")
                pts += [(c1.x, c1.y), (c2.x, c2.y), (e.x, e.y)]
        d.append("Z")
        paths.append(" ".join(d))
        b = (min(q[0] for q in pts), min(q[1] for q in pts), max(q[0] for q in pts), max(q[1] for q in pts))
        bbox = _union(bbox, b)
    # potrace orients holes opposite to outers, so one combined path with the default
    # nonzero fill rule renders holes correctly and keeps each logo a single shape
    return [" ".join(paths)], bbox


def rects_bbox(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _is_dark(color):
    try:
        if color is None or color.value is None:
            return False
        return (color.red + color.green + color.blue) / 3 < 128 and color.alpha > 0
    except AttributeError:
        return False


def _union(a, b):
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def load_qr_svg(path):
    """Vector shapes with a dark fill, plus embedded <image> elements (e.g. a centre logo)
    thresholded into rectangles and placed with the image's transform. An embedded image
    is treated as opaque: vector shapes mostly covered by its rectangle are dropped, so a
    logo on a white square really covers the QR modules beneath it (as it does on screen)."""
    from svgelements import SVG, Shape, Path as SPath, Image as SImage, Point, Matrix
    svg = SVG.parse(path, reify=True)
    vectors, image_paths, covers, bbox = [], [], [], None
    for el in svg.elements():
        if isinstance(el, SImage):
            href = getattr(el, "url", None) or el.values.get("href") or el.values.get("{http://www.w3.org/1999/xlink}href")
            if not href or not href.startswith("data:"):
                print("warning: linked (non-embedded) image skipped", file=sys.stderr)
                continue
            from PIL import Image
            data = base64.b64decode(href.split(",", 1)[1])
            im = Image.open(io.BytesIO(data))
            sx, sy = el.width / im.width, el.height / im.height
            M = el.transform

            def to_src(px_, py_):
                q = M.point_in_matrix_space(Point(el.x + px_ * sx, el.y + py_ * sy))
                return q.x, q.y

            # the image's footprint in source coords (transformed corners)
            fp = [to_src(0, 0), to_src(im.width, 0), to_src(im.width, im.height), to_src(0, im.height)]
            covers.append((min(q[0] for q in fp), min(q[1] for q in fp), max(q[0] for q in fp), max(q[1] for q in fp)))
            px_paths, _ = raster_paths(im)
            to_box = Matrix(sx, 0, 0, sy, el.x, el.y)          # pixel -> image box (pre-transform)
            for d in px_paths:
                q = SPath(d)
                q *= to_box
                q *= M
                q.reify()
                b = q.bbox()
                if b is None:
                    continue
                bbox = _union(bbox, b)
                image_paths.append(q.d())
            continue
        if not isinstance(el, Shape) or not _is_dark(el.fill):
            continue  # skip the white background and anything not black
        p = SPath(el) if not isinstance(el, SPath) else el
        p.reify()
        d = p.d()
        b = p.bbox()
        if d and b is not None:
            vectors.append((d, b))
    kept, dropped = [], 0
    for d, b in vectors:
        area = max((b[2] - b[0]) * (b[3] - b[1]), 1e-9)
        covered = 0.0
        for c in covers:
            ox = max(0.0, min(b[2], c[2]) - max(b[0], c[0]))
            oy = max(0.0, min(b[3], c[3]) - max(b[1], c[1]))
            covered = max(covered, ox * oy / area)
        if covered >= 0.5:
            dropped += 1
            continue
        kept.append(d)
        bbox = _union(bbox, b)
    paths = kept + image_paths  # image content drawn last (on top)
    if not paths:
        sys.exit("qr_file: no dark shapes found in the SVG")
    if covers:
        print(f"  embedded image(s) in the QR SVG vectorised: {len(covers)}; "
              f"{dropped} vector shape(s) underneath them removed")
    return paths, bbox


def _subpath_points(sub, step):
    """Sample one closed subpath into a list of (x, y); curves chorded about every `step` units."""
    from svgelements import Move, Close, Line
    pts = []
    for seg in sub:
        if isinstance(seg, Move):
            pts.append((seg.end.x, seg.end.y))
        elif isinstance(seg, Close):
            continue
        elif isinstance(seg, Line):
            pts.append((seg.end.x, seg.end.y))
        else:
            n = max(2, min(96, int(seg.length(error=1e-3) / step) + 1))
            pts += [(q.x, q.y) for q in (seg.point(i / n) for i in range(1, n + 1))]
    out = []
    for q in pts:
        if not out or abs(out[-1][0] - q[0]) > 1e-9 or abs(out[-1][1] - q[1]) > 1e-9:
            out.append(q)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


def merge_paths(paths, bbox):
    """Union every shape (QR modules, logo) into as few filled regions as possible, so abutting
    modules become one outline instead of hundreds of touching shapes. Curves are chorded at
    ~0.3 % of the code's width (about 0.02 mm on a 70 mm code). Needs shapely."""
    try:
        from shapely.geometry import Polygon
        from shapely.geometry.polygon import orient
        from shapely.ops import unary_union
    except ImportError:
        print("warning: shapely not installed, QR shapes left separate", file=sys.stderr)
        return paths, bbox
    from svgelements import Path as SPath
    step = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.003
    shapes = []
    for d in paths:
        p = SPath(d)
        rings = []
        for sub in p.as_subpaths():
            pts = _subpath_points(sub, step)
            if len(pts) >= 3:
                poly = Polygon(pts).buffer(0)
                if not poly.is_empty:
                    rings.append(poly)
        if not rings:
            continue
        # within one path element the smaller rings inside a bigger one are its holes (even-odd)
        rings.sort(key=lambda r: -r.area)
        shape = rings[0]
        for r in rings[1:]:
            shape = shape.difference(r) if shape.contains(r.representative_point()) else shape.union(r)
        shapes.append(shape)
    merged = unary_union(shapes)
    polys = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    out = []
    for poly in polys:
        poly = orient(poly, 1.0)  # exterior counter-clockwise, holes clockwise -> nonzero fill works
        parts = []
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)[:-1]
            parts.append("M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in coords) + " Z")
        out.append(" ".join(parts))
    b = merged.bounds
    print(f"  merged {len(paths)} QR shapes into {len(out)} region(s)")
    return out, (b[0], b[1], b[2], b[3])


# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------

def split_lines(text):
    return [t.strip() for t in re.split(r"\s*\|\s*|\n", text) if t.strip()] if text else []


def line_scales(spec, n):
    vals = [float(v) for v in re.split(r"\s*\|\s*|,|\s+", spec.strip()) if v] if spec else []
    if vals and len(vals) != n:
        sys.exit(f"text scales {spec!r}: expected {n} values, one per line")
    return vals or [1.0] * n


def block_metrics(ts, lines, scales, size, spacing):
    """Ink height of a multi-line block at base `size` mm; per-line sizes are size*scale.
    Lines stack by ink: next baseline = previous ink bottom + next ink top + lead,
    where lead = (spacing - 1) * next line size. Returns (height, data, baselines)."""
    if not lines:
        return 0.0, [], []
    data = [ts.layout(l)[1] for l in lines]
    k = size / ts.upm
    baselines = [data[0][3] * k * scales[0]]  # first line's ink top sits at 0
    for i in range(1, len(lines)):
        ink_bottom = baselines[i - 1] - data[i - 1][1] * k * scales[i - 1]
        lead = (spacing - 1) * size * scales[i]
        baselines.append(ink_bottom + lead + data[i][3] * k * scales[i])
    height = baselines[-1] - data[-1][1] * k * scales[-1]
    return height, data, baselines


def fit_block(ts, lines, scales, avail_w, avail_h, spacing):
    """Largest base font size (mm) so every line fits avail_w and the block fits avail_h."""
    if not lines:
        return 0.0
    size = math.inf
    for l, sc in zip(lines, scales):
        _, (x0, _, x1, _) = ts.layout(l)
        wu = (x1 - x0) / ts.upm * sc
        if wu > 0:
            size = min(size, avail_w / wu)
    h1, _, _ = block_metrics(ts, lines, scales, 1.0, spacing)
    if h1 > 0:
        size = min(size, avail_h / h1)
    return size


def compose(cfg, ts, qr_paths, qr_bbox):
    """Return engraving elements (list of (d, transform)) positioned on the sign (mm, y down)."""
    W, H, b, g = cfg["width_mm"], cfg["height_mm"], cfg["border_mm"], cfg["gap_mm"]
    spacing = cfg["line_spacing"]
    slot_w = slot_width(cfg)
    # content region (y measured from the TOP of the sign, SVG style)
    region_top = b
    region_bottom = H - (cfg["base_height_mm"] + slot_w / 2 + b)
    region_h = region_bottom - region_top
    avail_w = W - 2 * b
    above, below = split_lines(cfg["text_above"]), split_lines(cfg["text_below"])
    sc_above = line_scales(cfg["text_above_scales"], len(above))
    sc_below = line_scales(cfg["text_below_scales"], len(below))
    n_gaps = (1 if above else 0) + (1 if below else 0)
    text_budget = region_h - n_gaps * g

    # 1. QR gets its target share of the height, text is sized to fit the rest
    qr = min(avail_w, cfg["qr_fraction"] * text_budget)
    s_above = fit_block(ts, above, sc_above, avail_w, math.inf, spacing)
    s_below = fit_block(ts, below, sc_below, avail_w, math.inf, spacing)
    h_above = block_metrics(ts, above, sc_above, s_above, spacing)[0]
    h_below = block_metrics(ts, below, sc_below, s_below, spacing)[0]
    text_h = text_budget - qr
    total = h_above + h_below
    if total > text_h and total > 0:
        # 2. shrink both text blocks proportionally into what the QR leaves
        f = text_h / total
        s_above *= f
        s_below *= f
        h_above = block_metrics(ts, above, sc_above, s_above, spacing)[0]
        h_below = block_metrics(ts, below, sc_below, s_below, spacing)[0]
    else:
        # 3. text is width-limited and leaves room: let the QR grow into it
        qr = min(avail_w, text_budget - total)
    stack_h = h_above + h_below + qr + n_gaps * g
    y = region_top + (region_h - stack_h) / 2  # centre the stack in the region
    elements = []

    def place_block(lines, scales, size, y_top):
        if not lines:
            return y_top
        height, data, baselines = block_metrics(ts, lines, scales, size, spacing)
        k = size / ts.upm
        for i, (line, (x0, y0, x1, y1)) in enumerate(zip(lines, data)):
            w = (x1 - x0) * k * scales[i]
            x_left = b + (avail_w - w) / 2
            elements.append((ts.path(line, size * scales[i], x_left, y_top + baselines[i]), None))
        return y_top + height

    y = place_block(above, sc_above, s_above, y)
    if above:
        y += g
    # QR code, scaled uniformly into a qr x qr box, centred
    bx0, by0, bx1, by1 = qr_bbox
    k = qr / max(bx1 - bx0, by1 - by0)
    qx = b + (avail_w - (bx1 - bx0) * k) / 2
    qy = y + (qr - (by1 - by0) * k) / 2
    tf = f"translate({qx:.3f},{qy:.3f}) scale({k:.6f}) translate({-bx0:.3f},{-by0:.3f})"
    for d in qr_paths:
        elements.append((d, tf))
    y += qr
    if below:
        y += g
    place_block(below, sc_below, s_below, y)
    info = {"qr_mm": round(qr, 2),
            "font_above_mm": [round(s_above * s, 2) for s in sc_above],
            "font_below_mm": [round(s_below * s, 2) for s in sc_below],
            "region_top_mm": region_top, "region_bottom_mm": round(region_bottom, 2)}
    return elements, info


# ----------------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------------

def crossing_angle(cfg):
    return math.radians(cfg["angle_deg"] + cfg["base_angle_deg"])


def slot_width(cfg):
    """Width of the slot a board of thickness t needs when the other board crosses it at angle a."""
    return cfg["thickness_mm"] / math.sin(crossing_angle(cfg)) + cfg["slot_clearance_mm"]


def sign_outline(cfg):
    """Sign piece as (x, y, round?) vertices (mm, y down, origin top-left). Slot opens on the LEFT."""
    W, H, sw = cfg["width_mm"], cfg["height_mm"], slot_width(cfg)
    yc = H - cfg["base_height_mm"]
    half = W / 2
    return [(0, 0, True), (W, 0, True), (W, H, True), (0, H, True),
            (0, yc + sw / 2, False), (half, yc + sw / 2, False), (half, yc - sw / 2, False), (0, yc - sw / 2, False)]


def base_outline(cfg):
    """Base piece: front end at the top, back end at the bottom. Slot opens on the RIGHT."""
    Wb, sw = cfg["base_width_mm"], slot_width(cfg)
    Lf, Lb = cfg["base_front_mm"], cfg["base_back_mm"]
    Hb = Lf + Lb
    yc = Lf  # the intersection line, measured from the front end
    half = Wb / 2
    return [(0, 0, True), (Wb, 0, True), (Wb, yc - sw / 2, False), (half, yc - sw / 2, False),
            (half, yc + sw / 2, False), (Wb, yc + sw / 2, False), (Wb, Hb, True), (0, Hb, True)]


def _unit(ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def _corner(verts, i, r):
    """Arc start, arc end, centre and radius for the rounded corner at vertex i."""
    n = len(verts)
    x, y, _ = verts[i]
    px, py, _ = verts[i - 1]
    nx, ny, _ = verts[(i + 1) % n]
    ux, uy = _unit(x, y, px, py)
    vx, vy = _unit(x, y, nx, ny)
    rr = min(r, math.hypot(px - x, py - y) / 2, math.hypot(nx - x, ny - y) / 2)
    a = (x + ux * rr, y + uy * rr)      # arc start, towards the previous vertex
    b = (x + vx * rr, y + vy * rr)      # arc end, towards the next vertex
    c = (x + (ux + vx) * rr, y + (uy + vy) * rr)  # centre (90-degree corners)
    sweep = 1 if (ux * vy - uy * vx) < 0 else 0   # turn direction, SVG y-down
    return a, b, c, rr, sweep


def outline_path(verts, r):
    """SVG path with circular arcs of radius r at the vertices flagged round."""
    cmds = []
    for i, (x, y, rnd) in enumerate(verts):
        mv = "M" if not cmds else "L"
        if not rnd or r <= 0:
            cmds.append(f"{mv}{x:.3f},{y:.3f}")
            continue
        (ax, ay), (bx, by), _, rr, sweep = _corner(verts, i, r)
        cmds.append(f"{mv}{ax:.3f},{ay:.3f}")
        cmds.append(f"A{rr:.3f},{rr:.3f} 0 0 {sweep} {bx:.3f},{by:.3f}")
    return " ".join(cmds) + " Z"


def outline_polygon(verts, r, segs=6):
    """Same outline as a plain polygon (arcs sampled), for the 3D preview."""
    pts = []
    for i, (x, y, rnd) in enumerate(verts):
        if not rnd or r <= 0:
            pts.append((x, y))
            continue
        (ax, ay), (bx, by), (cx, cy), rr, _ = _corner(verts, i, r)
        a0 = math.atan2(ay - cy, ax - cx)
        a1 = math.atan2(by - cy, bx - cx)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for s in range(segs + 1):
            a = a0 + da * s / segs
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

def _svg_doc(w, h, inner, comment):
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" viewBox="0 0 {w:.3f} {h:.3f}">',
        f'  <!-- {comment} -->',
        *inner,
        '</svg>',
    ])


def write_svg(cfg, out_path, engrave, info, config_name):
    """Writes <name>.svg (both pieces side by side) plus <name>-sign.svg and <name>-base.svg."""
    W, H = cfg["width_mm"], cfg["height_mm"]
    Wb, Hb = cfg["base_width_mm"], cfg["base_front_mm"] + cfg["base_back_mm"]
    gap, r = cfg["piece_spacing_mm"], cfg["corner_radius_mm"]
    cut = cfg["cut_stroke_mm"]
    comment = (f'make-laser-qr: {config_name} | sign {W}x{H}x{cfg["thickness_mm"]} mm, lean {cfg["angle_deg"]} deg, '
               f'base lean {cfg["base_angle_deg"]} deg, slot {slot_width(cfg):.2f} mm, corner r {r} mm, '
               f'QR {info["qr_mm"]} mm, font above {info["font_above_mm"]} mm, below {info["font_below_mm"]} mm')
    sign_lines = ['    <g id="engrave" fill="#000000" stroke="none">']
    for d, tf in engrave:
        t = f' transform="{tf}"' if tf else ""
        sign_lines.append(f'      <path{t} d="{d}"/>')
    sign_lines += ['    </g>',
                   f'    <path id="sign-cut" fill="none" stroke="#ff0000" stroke-width="{cut}" d="{outline_path(sign_outline(cfg), r)}"/>']
    base_lines = [f'    <path id="base-cut" fill="none" stroke="#ff0000" stroke-width="{cut}" d="{outline_path(base_outline(cfg), r)}"/>']
    out_path = Path(out_path)
    out_path.write_text(_svg_doc(W + gap + Wb, max(H, Hb),
                                 ['  <g id="sign">', *sign_lines, '  </g>',
                                  f'  <g id="base" transform="translate({W + gap:.3f},0)">', *base_lines, '  </g>'],
                                 comment), encoding="utf-8")
    sign_path = out_path.with_name(out_path.stem + "-sign.svg")
    base_path = out_path.with_name(out_path.stem + "-base.svg")
    sign_path.write_text(_svg_doc(W, H, ['  <g id="sign">', *sign_lines, '  </g>'], comment + " | sign piece"), encoding="utf-8")
    base_path.write_text(_svg_doc(Wb, Hb, ['  <g id="base">', *base_lines, '  </g>'], comment + " | base piece"), encoding="utf-8")
    return sign_path, base_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="parameter file (key: value lines)")
    ap.add_argument("--out", help="output SVG path (default <output_dir>/<config-name>.svg)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = read_config(cfg_path)
    name = cfg_path.stem
    out_dir = Path(args.out).parent if args.out else (cfg_path.parent.parent / cfg["output_dir"]
                                                       if cfg_path.parent.name == "configs" else Path(cfg["output_dir"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_svg = Path(args.out) if args.out else out_dir / f"{name}.svg"

    font_path = find_font(cfg["font"])
    ts = Typesetter(font_path)
    qr_path = Path(cfg["qr_file"])
    if not qr_path.is_absolute():
        qr_path = (cfg_path.parent / qr_path).resolve()
    qr_paths, qr_bbox = load_qr(qr_path)
    if cfg["merge_qr"]:
        qr_paths, qr_bbox = merge_paths(qr_paths, qr_bbox)
    engrave, info = compose(cfg, ts, qr_paths, qr_bbox)
    sign_svg, base_svg = write_svg(cfg, out_svg, engrave, info, name)

    r = cfg["corner_radius_mm"]
    geom = {
        "config": name, "font_file": str(font_path),
        "sign": {"width": cfg["width_mm"], "height": cfg["height_mm"], "thickness": cfg["thickness_mm"],
                 "angle_from_vertical_deg": cfg["angle_deg"], "slot_from_bottom": cfg["base_height_mm"],
                 "slot_width": round(slot_width(cfg), 3), "slot_opens": "left",
                 "outline": outline_polygon(sign_outline(cfg), r)},
        "base": {"width": cfg["base_width_mm"], "front": cfg["base_front_mm"], "back": cfg["base_back_mm"],
                 "thickness": cfg["thickness_mm"], "angle_from_vertical_deg": cfg["base_angle_deg"],
                 "slot_width": round(slot_width(cfg), 3), "slot_opens": "right",
                 "outline": outline_polygon(base_outline(cfg), r)},
        "corner_radius": r,
        "crossing_angle_deg": round(math.degrees(crossing_angle(cfg)), 2),
        "layout": info,
    }
    (out_dir / f"{name}.json").write_text(json.dumps(geom, indent=2), encoding="utf-8")
    print(f"wrote {out_svg}  (+ {sign_svg.name}, {base_svg.name})")
    print(f"  sign {cfg['width_mm']}x{cfg['height_mm']} mm leaning {cfg['angle_deg']} deg; "
          f"base {cfg['base_width_mm']}x{cfg['base_front_mm'] + cfg['base_back_mm']} mm leaning {cfg['base_angle_deg']} deg; "
          f"crossing {geom['crossing_angle_deg']} deg, slot {geom['sign']['slot_width']} mm, corner r {r} mm")
    print(f"  QR {info['qr_mm']} mm, font above {info['font_above_mm']} mm, font below {info['font_below_mm']} mm")


if __name__ == "__main__":
    main()
