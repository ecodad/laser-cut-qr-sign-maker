"""Triangle meshes from extruded shapely polygons, and a 3MF writer with PrusaSlicer/Orca
per-volume extruder metadata. Pure Python + numpy + mapbox_earcut; no slicer or CAD app.

Units: millimetres. Z up. A Mesh is (vertices Nx3 float, triangles Mx3 int) with outward
winding (counter-clockwise seen from outside).
"""
import io
import zipfile
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry.polygon import orient

try:
    import mapbox_earcut as _earcut
except ImportError:  # pragma: no cover
    _earcut = None


@dataclass
class Mesh:
    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    triangles: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), dtype=np.int64))

    def append(self, other: "Mesh"):
        off = len(self.vertices)
        self.vertices = np.vstack([self.vertices, other.vertices]) if len(self.vertices) else other.vertices.copy()
        self.triangles = (np.vstack([self.triangles, other.triangles + off]) if len(self.triangles)
                          else other.triangles.copy())
        return self

    def transformed(self, fn):
        m = Mesh(fn(self.vertices.copy()), self.triangles.copy())
        return m

    def mirrored_z(self, z_total):
        """Flip upside down (face to the bed): z -> z_total - z, winding reversed."""
        v = self.vertices.copy()
        v[:, 2] = z_total - v[:, 2]
        return Mesh(v, self.triangles[:, [0, 2, 1]].copy())

    def translated(self, dx=0.0, dy=0.0, dz=0.0):
        return Mesh(self.vertices + np.array([dx, dy, dz]), self.triangles.copy())

    def bounds(self):
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def is_closed(self):
        """Every edge used exactly twice, in opposite directions."""
        e = {}
        for a, b, c in self.triangles:
            for u, v in ((a, b), (b, c), (c, a)):
                e[(u, v)] = e.get((u, v), 0) + 1
        return all(e.get((v, u), 0) == 1 and n == 1 for (u, v), n in e.items())


def _polys(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]


def _clean_ring(coords, eps=1e-7):
    """Drop duplicate and collinear points: the cap triangulator skips collinear vertices,
    and the walls must use exactly the vertices the caps use or the shell has T-junctions."""
    pts = [tuple(c[:2]) for c in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        out = []
        n = len(pts)
        for i in range(n):
            p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
            if abs(p1[0] - p0[0]) < eps and abs(p1[1] - p0[1]) < eps:
                changed = True
                continue
            cross = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
            if abs(cross) < eps:
                changed = True
                continue
            out.append(p1)
        pts = out
    return pts


def _rings(poly):
    """Exterior CCW then holes CW, closing points dropped, collinear points removed."""
    poly = orient(poly, 1.0)
    rings = [_clean_ring(poly.exterior.coords)]
    for h in poly.interiors:
        r = _clean_ring(h.coords)
        if len(r) >= 3:
            rings.append(r)
    return [r for r in rings if len(r) >= 3]


def _triangulate(rings):
    """Cap triangulation (indices into the concatenated ring vertices), CCW in XY."""
    if _earcut is None:
        raise RuntimeError("mapbox_earcut is required for 3D output (pip install mapbox_earcut)")
    pts = np.array([p for r in rings for p in r], dtype=np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = _earcut.triangulate_float64(pts, ends).reshape(-1, 3).astype(np.int64)
    # earcut returns clockwise or counter-clockwise depending on input; force CCW (+Z normal)
    a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    tri[cross < 0] = tri[cross < 0][:, [0, 2, 1]]
    return pts, tri


def extrude(geom, z0, z1):
    """Solid between z0 and z1 under every polygon of `geom` (holes supported). One closed shell per polygon.
    The walls are built on the cap triangulation's own boundary edges, so caps and walls always agree."""
    out = Mesh()
    for poly in _polys(geom):
        poly = poly.buffer(0)
        for part in _polys(poly):
            rings = _rings(part)
            if not rings:
                continue
            pts, cap = _triangulate(rings)
            if len(cap) == 0:
                continue
            n = len(pts)
            verts = np.vstack([np.column_stack([pts, np.full(n, z0)]), np.column_stack([pts, np.full(n, z1)])])
            directed = set()
            for a, b, c in cap:
                directed.update(((a, b), (b, c), (c, a)))
            boundary = [(a, b) for (a, b) in directed if (b, a) not in directed]   # CCW outside, CW around holes
            tris = [cap[:, [0, 2, 1]], cap + n]        # bottom cap faces down, top cap faces up
            walls = np.array([[[a, b, b + n], [a, b + n, a + n]] for a, b in boundary]).reshape(-1, 3)
            tris.append(walls)
            out.append(Mesh(verts, np.vstack(tris)))
    return out


def box(x0, y0, z0, x1, y1, z1):
    from shapely.geometry import box as _box
    return extrude(_box(x0, y0, x1, y1), z0, z1)


# ----------------------------------------------------------------------------
# 3MF
# ----------------------------------------------------------------------------

@dataclass
class Volume:
    name: str
    mesh: Mesh
    color: str = "#808080"   # "#rrggbb"
    extruder: int = 1        # 1-based, PrusaSlicer/Orca metadata


@dataclass
class Object3MF:
    name: str
    volumes: list


def _hex(c):
    c = c.lstrip("#")
    return "#" + (c if len(c) == 6 else "808080").upper()


def write_3mf(objects, path_or_buf, title="qrsign"):
    """Write objects (each a list of coloured volumes) as a 3MF.

    Each 3MF object has one mesh holding all its volumes' triangles consecutively; the
    PrusaSlicer/Orca config part maps triangle ranges to named volumes with an extruder each,
    so a multi-material file opens with the parts already assigned. Plain slicers see one
    object per sign/base with per-triangle material colours.
    """
    model = io.StringIO()
    model.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    model.write('<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
                'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">\n')
    model.write(f' <metadata name="Title">{title}</metadata>\n <metadata name="Application">qrsign</metadata>\n')
    # materials: one entry per distinct colour
    colors = []
    for obj in objects:
        for v in obj.volumes:
            if _hex(v.color) not in colors:
                colors.append(_hex(v.color))
    model.write(' <resources>\n  <basematerials id="1">\n')
    for c in colors:
        model.write(f'   <base name="{c}" displaycolor="{c}FF" />\n')
    model.write('  </basematerials>\n')
    config = io.StringIO()
    config.write('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n')
    next_id = 2
    build = []
    for obj in objects:
        oid = next_id
        next_id += 1
        model.write(f'  <object id="{oid}" name="{obj.name}" type="model">\n   <mesh>\n    <vertices>\n')
        config.write(f' <object id="{oid}" instances_count="1">\n  <metadata type="object" key="name" value="{obj.name}"/>\n')
        voff, toff = 0, 0
        tri_chunks = []
        for v in obj.volumes:
            m = v.mesh
            for x, y, z in m.vertices:
                model.write(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}" />\n')
            pid = colors.index(_hex(v.color))
            tri_chunks.append((m.triangles + voff, pid))
            first = toff
            toff += len(m.triangles)
            config.write(f'  <volume firstid="{first}" lastid="{toff - 1}">\n'
                         f'   <metadata type="volume" key="name" value="{v.name}"/>\n'
                         f'   <metadata type="volume" key="volume_type" value="ModelPart"/>\n'
                         f'   <metadata type="volume" key="extruder" value="{v.extruder}"/>\n'
                         f'   <metadata type="volume" key="source_object_id" value="{oid}"/>\n'
                         f'   <metadata type="volume" key="source_volume_id" value="{obj.volumes.index(v)}"/>\n'
                         f'   <mesh edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0" open_edges="0"/>\n'
                         f'  </volume>\n')
            voff += len(m.vertices)
        model.write('    </vertices>\n    <triangles>\n')
        for tris, pid in tri_chunks:
            for a, b, c in tris:
                model.write(f'     <triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{pid}" />\n')
        model.write('    </triangles>\n   </mesh>\n  </object>\n')
        config.write(' </object>\n')
        build.append(oid)
    model.write(' </resources>\n <build>\n')
    for oid in build:
        model.write(f'  <item objectid="{oid}" />\n')
    model.write(' </build>\n</model>\n')
    config.write('</config>\n')

    content_types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                     ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />\n'
                     ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />\n'
                     ' <Default Extension="config" ContentType="text/xml" />\n</Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />\n'
            '</Relationships>\n')
    with zipfile.ZipFile(path_or_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model.getvalue())
        z.writestr("Metadata/Slic3r_PE_model.config", config.getvalue())
    return path_or_buf
