"""Engraving test ladder: the same QR code at several sizes on one sheet, each labelled.

    python tools/qr_ladder.py --url https://3dgamebits.com --sizes 10 12 15 20 25 30 --out output/qr-ladder.svg

Black = engrave (modules + labels), red = optional cut outline around each tile so the
tiles can be separated (switch that layer off in LightBurn to keep the sheet whole).
Plain square modules, error correction M, 4-module quiet zone, sizes include the quiet zone.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qrsign import encode, render  # noqa: E402
from qrsign import signmaker as sm  # noqa: E402


def build(url, sizes, label_mm=3.0, gap=6.0, margin=3.0, cut_stroke=0.1, error="m"):
    m = encode.encode(url, error=error)
    geom = render.build_geometry(m, render.Style())
    total = m.size + 2 * render.QUIET_ZONE
    d = render.geometry_path_d(geom)
    ts = sm.base_text_typesetter(None)
    tiles, x = [], margin
    tile_h = max(sizes) + 2 + label_mm * 1.3 + 2 * margin
    for size in sizes:
        k = size / total
        tile_w = max(size, 24.0) + 2 * margin
        qx = x + (tile_w - size) / 2
        qy = margin + (max(sizes) - size)          # bottoms aligned so labels sit on one line
        label = f"{size:g} mm"
        _, (x0, y0, x1, y1) = ts.layout(label)
        kk = label_mm / ts.upm
        lw = (x1 - x0) * kk
        baseline = margin + max(sizes) + 2 + y1 * kk
        tiles.append({
            "qr": f'<path transform="translate({qx:.3f},{qy:.3f}) scale({k:.6f})" d="{d}"/>',
            "label": f'<path d="{ts.path(label, label_mm, x + (tile_w - lw) / 2, baseline)}"/>',
            "cut": f'<rect x="{x:.3f}" y="0" width="{tile_w:.3f}" height="{tile_h:.3f}" rx="1.5" fill="none" stroke="#ff0000" stroke-width="{cut_stroke}"/>',
            "size": size, "module_mm": round(k, 3),
        })
        x += tile_w + gap
    sheet_w = x - gap + margin
    inner = ['  <g id="engrave" fill="#000000" stroke="none">']
    inner += ["    " + t["qr"] for t in tiles] + ["    " + t["label"] for t in tiles]
    inner += ['  </g>', '  <g id="cut">'] + ["    " + t["cut"] for t in tiles] + ['  </g>']
    svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w:.3f}mm" height="{tile_h:.3f}mm" viewBox="0 0 {sheet_w:.3f} {tile_h:.3f}">\n'
           f'  <!-- qr ladder: {url} | version {m.version} ({m.size} modules), EC {error.upper()}, quiet zone {render.QUIET_ZONE} | '
           + ", ".join(f"{t['size']:g} mm = {t['module_mm']} mm/module" for t in tiles) + ' -->\n'
           + "\n".join(inner) + '\n</svg>\n')
    return svg, m, tiles, (sheet_w, tile_h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://3dgamebits.com")
    ap.add_argument("--sizes", type=float, nargs="+", default=[10, 12, 15, 20, 25, 30], help="code widths in mm, quiet zone included")
    ap.add_argument("--error", default="m", choices=["l", "m", "q", "h"])
    ap.add_argument("--out", default="output/qr-ladder.svg")
    args = ap.parse_args(argv)
    svg, m, tiles, (w, h) = build(args.url, sorted(args.sizes), error=args.error)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out}: {w:.0f} x {h:.0f} mm sheet, version {m.version} code ({m.size} modules, EC {args.error.upper()})")
    for t in tiles:
        print(f"  {t['size']:>4g} mm -> {t['module_mm']:.2f} mm per module")


if __name__ == "__main__":
    main()
