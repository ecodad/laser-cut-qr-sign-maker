"""3D-printable version of the sign: a flat plate with the QR code and text raised on it,
plus a base block with an angled slot, written as one 3MF.

Two print modes:
  multi  — multi-filament: back plate + a face layer made of three parts at the same height
           (background, QR, text); optional flip so the face prints on the bed.
  single — one filament with colour changes by height: background plate, QR raised
           `qr_layers`, text raised `qr_layers + text_layers`.
All dimensions in mm, Z up, the sign lying flat with its face up (unless flipped).
"""
from dataclasses import dataclass, asdict
import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import box as sbox
from shapely.ops import unary_union

from . import mesh3d
from . import signmaker as sm
from .render import rounded_rect

MODES = ["multi", "single"]


@dataclass
class PrintSettings:
    mode: str = "multi"
    layer_height: float = 0.2
    back_mm: float = 2.0          # background plate thickness
    qr_layers: int = 5            # QR (and, in multi mode, the whole face) thickness in layers
    text_layers: int = 3          # single mode: text this many layers above the QR
    flip: bool = False            # multi mode: face on the bed
    color_background: str = "#F5F0E6"
    color_qr: str = "#111111"
    color_text: str = "#B0262B"
    # base
    base_lean_deg: float = 10.0   # sign lean back from vertical in the slot
    base_depth_mm: float = 40.0
    base_height_mm: float = 14.0
    base_wall_mm: float = 3.0     # side walls beside the slot
    slot_depth_mm: float = 10.0   # how far the sign goes into the base; also the sign's blank zone
    slot_clearance_mm: float = 0.3  # added to the sign thickness for the slot
    slot_side_clearance_mm: float = 0.4  # added to the sign width for the slot
    include_base: bool = True

    @property
    def qr_mm(self):
        return self.qr_layers * self.layer_height

    @property
    def text_mm(self):
        return self.text_layers * self.layer_height

    def sign_thickness(self):
        if self.mode == "multi":
            return self.back_mm + self.qr_mm
        return self.back_mm + self.qr_mm + self.text_mm

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


# ----------------------------------------------------------------------------
# geometry from the laser layout
# ----------------------------------------------------------------------------

def _shapes_from_paths(d_strings, step):
    """SVG path data (already in sign mm, y-down) -> list of shapely polygons (holes by even-odd)."""
    from svgelements import Path as SPath
    from shapely.geometry import Polygon
    shapes = []
    for d in d_strings:
        p = SPath(d)
        rings = []
        for sub in p.as_subpaths():
            pts = sm._subpath_points(sub, step)
            if len(pts) >= 3:
                poly = Polygon(pts).buffer(0)
                if not poly.is_empty:
                    rings.append(poly)
        if not rings:
            continue
        rings.sort(key=lambda r: -r.area)
        shape = rings[0]
        for r in rings[1:]:
            shape = shape.difference(r) if shape.contains(r.representative_point()) else shape.union(r)
        shapes.append(shape)
    return shapes


def layout_geometry(cfg, qr_path, settings=None, log=lambda *a: None):
    """Run the laser layout and return (text_geom, qr_geom, info, cfg), in sign mm with y UP.
    With `settings` (3D print) the thickness, blank zone and slot are taken from the print
    settings; without, the laser config is used as is (for the assembled laser preview)."""
    from svgelements import Path as SPath, Matrix
    cfg = dict(cfg)
    if settings is not None:
        cfg["thickness_mm"] = settings.sign_thickness()
        cfg["base_height_mm"] = settings.slot_depth_mm       # blank zone at the bottom = what sits in the slot
        cfg["angle_deg"], cfg["base_angle_deg"] = 0.0, 90.0  # square crossing: slot width = thickness + clearance
        cfg["slot_clearance_mm"] = settings.slot_clearance_mm
    ts = sm.Typesetter(sm.find_font(cfg["font"]))
    qr_paths, qr_bbox = sm.load_qr(Path(qr_path))
    if cfg["merge_qr"]:
        qr_paths, qr_bbox = sm.merge_paths(qr_paths, qr_bbox, log=log)
    engrave, info = sm.compose(cfg, ts, qr_paths, qr_bbox)
    H = cfg["height_mm"]
    text_d, qr_d = [], []
    for d, tf in engrave:
        if tf:
            p = SPath(d)
            p *= Matrix(tf)
            p.reify()
            qr_d.append(p.d())
        else:
            text_d.append(d)
    step = 0.05
    text = unary_union(_shapes_from_paths(text_d, step)) if text_d else None
    qr = unary_union(_shapes_from_paths(qr_d, step)) if qr_d else None
    # SVG y-down -> y up: y' = H - y
    text = affinity.affine_transform(text, [1, 0, 0, -1, 0, H]) if text is not None else None
    qr = affinity.affine_transform(qr, [1, 0, 0, -1, 0, H]) if qr is not None else None
    return text, qr, info, cfg


# ----------------------------------------------------------------------------
# builders
# ----------------------------------------------------------------------------

def manifold_safe(geom, grow=0.01, tolerance=0.005):
    """Grow raised geometry by a hair (0.01 mm, mitred) so shapes that touch at a single point
    become joined by a tiny neck (point contacts give the shell a non-manifold vertex), then
    simplify by 0.005 mm: glyph outlines sampled every 0.05 mm carry thousands of near-collinear
    points that make the ear-clipping triangulator bridge across holes."""
    if geom is None or geom.is_empty:
        return geom
    return geom.buffer(grow, join_style=2).simplify(tolerance)


def build_sign(cfg, text, qr, s: PrintSettings):
    """-> list of mesh3d.Volume for the sign, lying flat, face up (or flipped)."""
    W, H, r = cfg["width_mm"], cfg["height_mm"], cfg["corner_radius_mm"] or 0.0
    plate = rounded_rect(0, 0, W, H, (r, r, r, r))
    qr, text = manifold_safe(qr), manifold_safe(text)
    raised = unary_union([g for g in (qr, text) if g is not None]) if (qr is not None or text is not None) else None
    vols = []
    if s.mode == "multi":
        z_face = s.back_mm + s.qr_mm
        bg = mesh3d.extrude(plate, 0, s.back_mm)
        face_bg = plate.difference(raised.buffer(0.001)) if raised is not None else plate
        bg.append(mesh3d.extrude(face_bg, s.back_mm, z_face))
        vols.append(mesh3d.Volume("Background", bg, s.color_background, 1))
        if qr is not None:
            vols.append(mesh3d.Volume("QR code", mesh3d.extrude(qr, s.back_mm, z_face), s.color_qr, 2))
        if text is not None:
            vols.append(mesh3d.Volume("Text", mesh3d.extrude(text, s.back_mm, z_face), s.color_text, 3))
        if s.flip:
            vols = [mesh3d.Volume(v.name, v.mesh.mirrored_z(z_face), v.color, v.extruder) for v in vols]
    else:
        vols.append(mesh3d.Volume("Background", mesh3d.extrude(plate, 0, s.back_mm), s.color_background, 1))
        if qr is not None:
            vols.append(mesh3d.Volume("QR code", mesh3d.extrude(qr, s.back_mm, s.back_mm + s.qr_mm), s.color_qr, 1))
        if text is not None:
            vols.append(mesh3d.Volume("Text", mesh3d.extrude(text, s.back_mm, s.back_mm + s.qr_mm + s.text_mm), s.color_text, 1))
    return vols


def slot_profile(s: PrintSettings, sign_thickness):
    """Side view (y = depth from the front, z = up) of the base block minus the angled slot."""
    D, Hb = s.base_depth_mm, s.base_height_mm
    block = sbox(0, 0, D, Hb)
    t = sign_thickness + s.slot_clearance_mm
    length = s.slot_depth_mm
    y_open = D * 0.45
    # a rectangle hanging down from the top face, then leaned back (bottom towards +y)
    rect = sbox(y_open - t / 2, Hb - length, y_open + t / 2, Hb + 5)
    slot = affinity.rotate(rect, -s.base_lean_deg, origin=(y_open, Hb))
    return block.difference(slot), slot


def build_base(cfg, s: PrintSettings):
    """-> mesh3d.Volume of the base: side walls + slotted middle, as three closed shells."""
    W = cfg["width_mm"]
    wall = s.base_wall_mm
    ws = W + s.slot_side_clearance_mm
    profile, _ = slot_profile(s, s.sign_thickness())
    solid = sbox(0, 0, s.base_depth_mm, s.base_height_mm)

    def along_x(geom, x0, x1):
        m = mesh3d.extrude(geom, x0, x1)             # built in (y, z) plane with "z" = x
        v = m.vertices[:, [2, 0, 1]]                  # (u=y, v=z, w=x) -> (x, y, z): cyclic, orientation kept
        return mesh3d.Mesh(v.copy(), m.triangles.copy())

    mesh = along_x(solid, 0, wall)
    mesh.append(along_x(profile, wall, wall + ws))
    mesh.append(along_x(solid, wall + ws, wall + ws + wall))
    return mesh3d.Volume("Base", mesh, s.color_background, 1)


def make_print(cfg, qr_path, settings: PrintSettings, name, out_dir, log=lambda *a: None):
    """Build the sign (and base) and write <out_dir>/<name>.3mf. Returns a result dict."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    text, qr, info, cfg3 = layout_geometry(cfg, qr_path, settings, log=log)
    objects = [mesh3d.Object3MF("Sign", build_sign(cfg3, text, qr, settings))]
    if settings.include_base:
        base = build_base(cfg3, settings)
        base.mesh = base.mesh.translated(dx=cfg3["width_mm"] + 10.0)
        objects.append(mesh3d.Object3MF("Base", [base]))
    path = out_dir / f"{name}.3mf"
    mesh3d.write_3mf(objects, path, title=name)
    t = settings.sign_thickness()
    summary = {
        "mode": settings.mode, "sign_thickness_mm": round(t, 3),
        "face_layers": {"background_top": settings.back_mm,
                        "qr_top": round(settings.back_mm + settings.qr_mm, 3),
                        "text_top": round(settings.back_mm + settings.qr_mm + (settings.text_mm if settings.mode == "single" else 0), 3)},
        "colour_change_heights_mm": ([settings.back_mm, round(settings.back_mm + settings.qr_mm, 3)]
                                     if settings.mode == "single" else []),
        "base": {"width": cfg3["width_mm"] + 2 * settings.base_wall_mm + settings.slot_side_clearance_mm,
                 "depth": settings.base_depth_mm, "height": settings.base_height_mm,
                 "slot_width": round(t + settings.slot_clearance_mm, 3), "lean_deg": settings.base_lean_deg},
        "layout": info,
        "volumes": [{"object": o.name, "volume": v.name, "extruder": v.extruder, "color": v.color,
                     "triangles": int(len(v.mesh.triangles)), "closed": bool(v.mesh.is_closed())}
                    for o in objects for v in o.volumes],
    }
    return {"3mf": path, "summary": summary, "text": text, "qr": qr, "cfg": cfg3, "objects": objects}


def preview_svg(cfg, text, qr, s: PrintSettings, px=360):
    """Top view of the printed sign with the chosen colours, plus the base seen from the side
    with the sign in its slot, as one SVG string."""
    from .render import geometry_path_d
    W, H, r = cfg["width_mm"], cfg["height_mm"], cfg["corner_radius_mm"] or 0.0
    plate = rounded_rect(0, 0, W, H, (r, r, r, r))
    flip = [1, 0, 0, -1, 0, H]  # back to y-down for SVG
    parts = [f'<path fill="{s.color_background}" stroke="#999" stroke-width="0.3" d="{geometry_path_d(plate, offset=0)}"/>']
    for g, c in ((qr, s.color_qr), (text, s.color_text)):
        if g is not None:
            parts.append(f'<path fill="{c}" d="{geometry_path_d(affinity.affine_transform(g, flip), offset=0)}"/>')
    # side view of the base: profile in (y, z) drawn with z up, placed to the right of the sign
    gap = 8.0
    ox = W + gap
    D, Hb = s.base_depth_mm, s.base_height_mm
    if s.include_base:
        profile, slot = slot_profile(s, s.sign_thickness())
        sv = [1, 0, 0, -1, ox, H]   # y -> x, z -> up
        parts.append(f'<path fill="{s.color_background}" stroke="#999" stroke-width="0.3" fill-rule="evenodd" '
                     f'd="{geometry_path_d(affinity.affine_transform(profile, sv), offset=0)}"/>')
        # the sign standing in the slot: a thin leaning rectangle
        y_open = D * 0.45
        t = s.sign_thickness()
        sign_side = sbox(y_open - t / 2, Hb - s.slot_depth_mm, y_open + t / 2, Hb - s.slot_depth_mm + H)
        sign_side = affinity.rotate(sign_side, -s.base_lean_deg, origin=(y_open, Hb))
        parts.append(f'<path fill="{s.color_qr}" fill-opacity="0.35" d="{geometry_path_d(affinity.affine_transform(sign_side, sv), offset=0)}"/>')
        parts.append(f'<text x="{ox:.1f}" y="{H + 4:.1f}" font-size="3" fill="#666">base, side view ({s.base_lean_deg:g}° lean)</text>')
    total_w = W + (gap + D if s.include_base else 0)
    total_h = H + 6
    scale = px / total_w
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w * scale:.0f}" height="{total_h * scale:.0f}" '
            f'viewBox="-1 -{H * 0.6 + 1:.1f} {total_w + 2} {total_h + H * 0.6 + 2:.1f}">' + "".join(parts) + "</svg>")
