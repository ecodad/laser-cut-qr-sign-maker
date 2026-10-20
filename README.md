# Laser Cut QR Sign Maker

Generate a LightBurn-ready SVG for a small tabletop sign: a QR code with a line or two of
text above and below, engraved on a board that leans back on a second board slid through it
from the side. One short config file per sign.

```
python make-laser-qr.py --config configs/medforge-give.txt
```

Outputs land in `output/`:

| file | contents |
|---|---|
| `<name>.svg` | both pieces side by side |
| `<name>-sign.svg` | the sign piece alone |
| `<name>-base.svg` | the base piece alone |
| `<name>.json` | geometry summary (angles, slot, layout) used by the 3D previews |

Cut lines are red (`#ff0000`, 0.1 mm stroke) and engraving is black fill. Text is converted
to glyph outlines from the font file, so LightBurn needs no font installed. Adjacent QR
modules and the logo are unioned into single filled regions, so a fill engrave has no seams.

## Install

Python 3.10 or newer.

```
pip install fonttools svgelements pillow potracer numpy shapely
```

`potracer` traces raster logos and `.png`/`.jpg` QR codes into smooth outlines; `shapely`
unions the QR modules. Both are optional: without them the script falls back to row
rectangles and separate shapes, which render with hairline seams.

## The stand

```
      sign (leans back `angle_deg`)
       |
       |   engraved face: text above, QR code, text below,
       |   inside `border_mm` and above the slot
       |
   ====|==== base passes through a slot `base_height_mm` up the sign,
       |     extends `base_front_mm` past the face and `base_back_mm`
       |     back to the table
  table
```

* The sign's bottom edge and the base's back edge both sit on the table.
* Crossing angle = sign lean + base lean. The base lean is computed so the base's back edge
  lands on the table, or give `crossing_angle_deg` and let the script solve the sign lean.
* With a 90° crossing the slots are plain thickness-wide (the laser cuts square through the
  board); any other crossing makes them `thickness / sin(crossing)` wide.
* The sign's slot opens on its left edge and the base's on its right edge, each half the
  width, so the two pieces slide together sideways.

## Config file

`key: value` lines, `#` comments (inline comments after a value are fine). Text lines are
separated by `|`. See [configs/medforge-give.txt](configs/medforge-give.txt) for a complete
example; the QR file it points to is not part of this repository, so drop your own SVG or
PNG next to it and update `qr_file`.

### Required

| key | meaning |
|---|---|
| `width_mm`, `height_mm`, `thickness_mm` | the sign board; measure your actual stock for the thickness |
| `angle_deg` | sign lean back from vertical, **or** give `crossing_angle_deg` instead |
| `text_above`, `text_below` | lines separated by `\|`; sized to fit, so fewer lines means bigger text |
| `qr_file` | `.svg` (vector paths kept; an embedded logo image is traced), `.png` or `.jpg` (traced); relative to the config file |
| `font` | an installed font name or a path to a `.ttf`/`.otf` |
| `border_mm` | margin from the sign edges and from the base slot |

### Optional (defaults in parentheses)

| key | meaning |
|---|---|
| `text_above_scales`, `text_below_scales` | relative size per line, e.g. `0.45 \| 1` makes the first line under half the size of the second (all 1) |
| `qr_fraction` | share of the content height reserved for the QR code; text fits the rest, and the QR grows into any leftover (0.55) |
| `gap_mm` | gap between the text blocks and the QR code (border / 2) |
| `line_spacing` | lead between stacked lines as a multiple of the next line's size (1.15) |
| `base_height_mm` | where the base crosses the sign, up from its bottom edge (0.3 × height) |
| `base_front_mm` | base extending forward past the sign face (= base_height) |
| `base_back_mm` | base behind the sign down to the table (1.3 × base_height) |
| `base_width_mm` | (= width) |
| `base_angle_deg` | base lean from vertical (computed so the back edge sits on the table) |
| `crossing_angle_deg` | alternative to `angle_deg`: the angle between the boards; the sign lean is solved from the base lengths |
| `corner_radius_mm` | radius on the four outer corners of each piece, 0 for square (2.5) |
| `slot_clearance_mm` | added to every slot width for fit / kerf (0.1) |
| `merge_qr` | 1 unions the QR modules and logo into single regions, 0 keeps the source shapes (1) |
| `cut_stroke_mm` | stroke width of the red cut lines (0.1) |
| `piece_spacing_mm` | gap between the pieces in the combined SVG (5) |
| `output_dir` | where the files go (`output`) |

### Layout rules

The engraving region is the sign face inside the border and above the slot. The QR code
first gets `qr_fraction` of that height (capped at the full width); each text block is sized
to fit the width, then shrunk proportionally if the two blocks together need more than what
the QR leaves. If the text is width-limited and leaves room, the QR grows into it. Lines
stack by their actual ink height, so a small line over a big one has no dead space.

### QR code tips

* Use error correction H if the code carries a logo; the logo covers modules and the script
  removes the modules under it, as a browser would render them.
* Export the code as SVG where you can; raster input is traced and works, but SVG keeps the
  module shapes exact.
* Scan-test the engraved piece before making more.

## Before cutting

1. Set `thickness_mm` to the measured stock thickness.
2. Cut one on scrap and check the slide fit; adjust `slot_clearance_mm` for your kerf.
3. In LightBurn, put the red layer on a cut setting and the black layer on a fill setting.

## 3D previews (optional)

Two ways to see the assembled stand. Both read `output/<name>.json`.

**Blender, headless.** `preview_blender.py` builds the two boards and `render_preview.py`
renders three views (three-quarter, side, a standing viewer):

```
blender -b -P render_preview.py -- output/<name>.json output/<name>-assembled.png [output/<name>-face.png]
```

The optional face PNG textures the engraving onto the sign; a rasterised
`output/<name>-sign.svg` works (for example a headless Edge or Chrome screenshot).

**Fusion 360, through its native MCP.** Enable the API server in Fusion (Preferences →
General → API), then run the scripts in `fusion/` through `fusion/mcpcall.py`, a minimal
JSON-RPC client for `http://127.0.0.1:27182/mcp`:

```
cd fusion
python mcpcall.py script build_stand.py        # components, fillets, extrudes, a table slab
python mcpcall.py script place.py              # leans the pieces into place
python export_loops.py ../output/<name>-sign.svg   # engraving outlines -> output/<name>-engrave-loops.json
python mcpcall.py script engrave_patches.py    # engraving as black surface patches on the face
python mcpcall.py script screenshot.py         # viewport PNG into output/
```

The scripts carry absolute paths near the top; edit them for your checkout. Notes for anyone
extending them: set an occurrence's transform only after its geometry exists and re-run
`place.py` after any timeline rollback; don't try to extrude-cut the imported SVG profiles,
the touching QR modules make the boolean fail, which is why the engraving is built as
surface patches through a base feature.

## License

MIT, see [LICENSE](LICENSE).
