"""Export the engraving of a generated sign SVG as grouped polylines for engrave_patches.py.

    python export_loops.py ../output/<name>-sign.svg

Writes ../output/<name>-engrave-loops.json: a list of {"outer": [[x, y], ...], "holes": [...]}
in mm, SVG y-down, one entry per filled shape (holes assigned by even-odd containment).
"""
import json
import math
import sys
from pathlib import Path

from svgelements import SVG, Shape, Path as SPath, Move, Close, Line

PX_PER_MM = 96 / 25.4   # svgelements reports a "80mm" wide SVG in 96 dpi pixels


def sample(seg):
    L = seg.length(error=1e-3)
    n = max(1, min(64, int(L / 0.5) + 1))   # chord every ~0.5 px = 0.13 mm
    return [seg.point(i / n) for i in range(1, n + 1)]


def main(svg_path):
    svg_path = Path(svg_path)
    svg = SVG.parse(str(svg_path), reify=True)
    loops = []
    for el in svg.elements():
        if not (isinstance(el, Shape) and el.id != "sign-cut" and el.fill is not None and el.fill.value is not None):
            continue
        p = SPath(el)
        p.reify()
        for sub in p.as_subpaths():
            pts = []
            for seg in sub:
                if isinstance(seg, Move):
                    pts.append(seg.end)
                elif isinstance(seg, Close):
                    continue
                elif isinstance(seg, Line):
                    pts.append(seg.end)
                else:
                    pts.extend(sample(seg))
            clean = []
            for q in pts:
                if q is None:
                    continue
                xy = (round(float(q.x) / PX_PER_MM, 4), round(float(q.y) / PX_PER_MM, 4))
                if not clean or math.dist(clean[-1], xy) > 1e-4:
                    clean.append(xy)
            if len(clean) >= 3 and math.dist(clean[0], clean[-1]) <= 1e-4:
                clean.pop()
            if len(clean) >= 3:
                loops.append(clean)

    def inside(pt, poly):
        x, y = pt
        c = False
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            if (y1 > y) != (y2 > y) and x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
                c = not c
        return c

    def bbox(poly):
        xs = [q[0] for q in poly]
        ys = [q[1] for q in poly]
        return min(xs), min(ys), max(xs), max(ys)

    bbs = [bbox(l) for l in loops]
    depth, parent = [], []
    for i, l in enumerate(loops):
        bi = bbs[i]
        d, par, par_area = 0, None, None
        for j, m in enumerate(loops):
            if i == j:
                continue
            bj = bbs[j]
            if bj[0] <= bi[0] and bj[1] <= bi[1] and bj[2] >= bi[2] and bj[3] >= bi[3] and inside(l[0], m):
                d += 1
                a = (bj[2] - bj[0]) * (bj[3] - bj[1])
                if par is None or a < par_area:
                    par, par_area = j, a
        depth.append(d)
        parent.append(par)
    groups = {}
    for i, l in enumerate(loops):
        if depth[i] % 2 == 0:
            groups.setdefault(i, {"outer": l, "holes": []})
    for i, l in enumerate(loops):
        if depth[i] % 2 == 1:
            groups.setdefault(parent[i], {"outer": loops[parent[i]], "holes": []})["holes"].append(l)
    out = list(groups.values())
    name = svg_path.stem.removesuffix("-sign")
    dest = svg_path.with_name(f"{name}-engrave-loops.json")
    dest.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {dest}: {len(out)} shapes, {sum(len(g['holes']) for g in out)} holes")


if __name__ == "__main__":
    main(sys.argv[1])
