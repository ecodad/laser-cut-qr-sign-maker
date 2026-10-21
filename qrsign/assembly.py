"""Assembled views: the laser-cut stand (sign leaning on its slotted base) and the printed sign
standing in its printed base, as placed meshes for the app's three.js scene or any viewer.

World frame: X across the sign, -Y towards the viewer, Z up, table at z = 0. Millimetres.
"""
import math
import struct
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

from . import mesh3d, print3d
from . import signmaker as sm

WOOD = "#C9A66B"
WOOD_DARK = "#A8824A"
INK = "#111111"
ENGRAVE_MM = 0.15   # how far the engraving patches stand off the face in the preview


def place(mesh, origin, along, normal):
    """Board coords (x across, y along the board, z normal) -> world: origin + x*X + y*along + z*normal."""
    X = np.array([1.0, 0.0, 0.0])
    u, n, o = np.array(along, float), np.array(normal, float), np.array(origin, float)
    v = mesh.vertices
    world = o + np.outer(v[:, 0], X) + np.outer(v[:, 1], u) + np.outer(v[:, 2], n)
    return mesh3d.Mesh(world, mesh.triangles.copy())


def _board(outline_pts, length, width, thickness):
    """A plate from an SVG-space outline (x, y-down from the top end): centred on x, y up from the bottom end."""
    poly = Polygon([(x - width / 2, length - y) for x, y in outline_pts])
    return mesh3d.extrude(poly, -thickness / 2, thickness / 2)


def laser_stand(cfg, text=None, qr=None):
    """-> list of (name, Mesh, colour) for the laser-cut stand assembled on the table."""
    W, H, t = cfg["width_mm"], cfg["height_mm"], cfg["thickness_mm"]
    r = cfg["corner_radius_mm"] or 0.0
    ts, tb = math.radians(cfg["angle_deg"]), math.radians(cfg["base_angle_deg"])
    L, Lb, Lf = cfg["base_height_mm"], cfg["base_back_mm"], cfg["base_front_mm"]
    u_sign = (0.0, math.sin(ts), math.cos(ts))
    n_sign = (0.0, -math.cos(ts), math.sin(ts))          # front face towards the viewer (-Y)
    P = np.array(u_sign) * L                              # crossing point
    u_base = (0.0, -math.sin(tb), math.cos(tb))
    n_base = (0.0, -math.cos(tb), -math.sin(tb))          # = X x u_base
    base_origin = P - np.array(u_base) * Lb
    parts = []
    sign = _board(sm.outline_polygon(sm.sign_outline(cfg), r), H, W, t)
    parts.append(("Sign", place(sign, (0, 0, 0), u_sign, n_sign), WOOD))
    base = _board(sm.outline_polygon(sm.base_outline(cfg), r), Lf + Lb, cfg["base_width_mm"], t)
    parts.append(("Base", place(base, base_origin, u_base, n_base), WOOD_DARK))
    for name, g in (("Engraving", qr), ("Text", text)):
        if g is not None and not g.is_empty:
            # layout geometry is in sign mm, x from the left edge, y up from the bottom
            m = mesh3d.extrude(print3d.manifold_safe(g), t / 2, t / 2 + ENGRAVE_MM)
            m = m.translated(dx=-W / 2)
            parts.append((name, place(m, (0, 0, 0), u_sign, n_sign), INK))
    return parts


def printed_stand(print_result, settings: print3d.PrintSettings):
    """-> list of (name, Mesh, colour): the printed base at the origin and the printed sign in its slot."""
    cfg = print_result["cfg"]
    W = cfg["width_mm"]
    s = settings
    th = math.radians(s.base_lean_deg)
    wall, ws = s.base_wall_mm, W + s.slot_side_clearance_mm
    y_open = s.base_depth_mm * 0.45
    t = s.sign_thickness()
    # the slot's centre line: from the opening on the top face down into the base, leaning so the
    # sign's top goes towards the back (+Y); must match print3d.slot_profile
    u = (0.0, math.sin(th), math.cos(th))                 # up the sign
    n = (0.0, -math.cos(th), math.sin(th))                # face towards the viewer
    bottom = np.array([wall + ws / 2, y_open - s.slot_depth_mm * math.sin(th), s.base_height_mm - s.slot_depth_mm * math.cos(th)])
    origin = bottom - np.array(n) * (t / 2)               # sign volumes have z from 0 (back) to t (face)
    parts = []
    objects = print_result["objects"]
    sign_obj = next(o for o in objects if o.name == "Sign")
    for v in sign_obj.volumes:
        m = v.mesh
        if s.mode == "multi" and s.flip:
            m = m.mirrored_z(t)                            # show it the right way up even when printed face-down
        m = m.translated(dx=-W / 2)
        parts.append((v.name, place(m, origin, u, n), v.color))
    base_obj = next((o for o in objects if o.name == "Base"), None)
    if base_obj is not None:
        bv = base_obj.volumes[0]
        base_mesh = bv.mesh.translated(dx=-(W + 10.0))   # undo the side-by-side offset used in the 3MF
        parts.append(("Base", base_mesh, bv.color))
    return parts


def write_stl(mesh, path):
    """Binary STL."""
    v, tri = mesh.vertices, mesh.triangles
    a, b, c = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
    nrm = np.cross(b - a, c - a)
    lens = np.linalg.norm(nrm, axis=1)
    lens[lens == 0] = 1
    nrm = nrm / lens[:, None]
    with open(path, "wb") as fh:
        fh.write(b"qrsign".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(tri)))
        data = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3), ("attr", "<u2")])
        data["n"], data["a"], data["b"], data["c"] = nrm, a, b, c
        fh.write(data.tobytes())
    return Path(path)


def write_parts(parts, out_dir, prefix):
    """Write each part as <out_dir>/<prefix>-<name>.stl; returns [(name, path, colour)]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name, mesh, color in parts:
        p = out_dir / f"{prefix}-{name.lower().replace(' ', '_')}.stl"
        write_stl(mesh, p)
        out.append((name, p, color))
    return out


def bounds(parts):
    lo = np.min([m.vertices.min(axis=0) for _, m, _ in parts], axis=0)
    hi = np.max([m.vertices.max(axis=0) for _, m, _ in parts], axis=0)
    return lo, hi
