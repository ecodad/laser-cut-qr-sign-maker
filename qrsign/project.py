"""Project files (*.qrsign.json) and the stage-2 hand-off from a designed QR to the sign generator.

A project holds everything needed to regenerate a sign: the QR content, the style choices,
the logo file and the sign config keys. Paths inside the project are stored relative to the
project file where possible so a project folder can be moved.
"""
import json
from pathlib import Path

from . import encode, render, print3d
from . import signmaker as sm

PROJECT_SUFFIX = ".qrsign.json"

# the sign config keys shown on the main panel vs. the advanced panel
MAIN_KEYS = ["width_mm", "height_mm", "thickness_mm", "text_above", "text_above_scales",
             "text_below", "text_below_scales", "font", "border_mm"]
ADVANCED_KEYS = ["crossing_angle_deg", "angle_deg", "base_height_mm", "base_front_mm", "base_back_mm",
                 "base_width_mm", "base_angle_deg", "corner_radius_mm", "slot_clearance_mm", "qr_fraction",
                 "gap_mm", "line_spacing", "merge_qr", "cut_stroke_mm", "piece_spacing_mm"]

DEFAULT_SIGN = {
    "width_mm": 80, "height_mm": 150, "thickness_mm": 3,
    "crossing_angle_deg": 90, "base_height_mm": 10, "base_front_mm": 10, "base_back_mm": 58.5,
    "text_above": "", "text_above_scales": "", "text_below": "", "text_below_scales": "",
    "font": "", "border_mm": 5, "corner_radius_mm": 2.5, "qr_fraction": 0.62,
}


class Project:
    def __init__(self, name="untitled", url="", style=None, sign=None, path=None):
        self.name = name
        self.url = url
        self.style = style or render.Style()
        self.sign = dict(DEFAULT_SIGN)
        if sign:
            self.sign.update(sign)
        self.print = print3d.PrintSettings()
        self.path = Path(path) if path else None

    # ---- persistence ------------------------------------------------------
    def to_dict(self):
        d = {"format": "qrsign-project", "version": 2, "name": self.name, "url": self.url,
             "style": self.style.as_dict(), "sign": self.sign, "print": self.print.as_dict()}
        if self.path and self.style.logo_path:
            try:
                d["style"]["logo_path"] = str(Path(self.style.logo_path).resolve().relative_to(self.path.parent.resolve()))
            except ValueError:
                pass
        return d

    def save(self, path=None):
        if path:
            self.path = Path(path)
        if self.path is None:
            raise ValueError("no project path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path):
        path = Path(path)
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("format") != "qrsign-project":
            raise ValueError("not a qrsign project file")
        style = render.Style.from_dict(d.get("style", {}))
        if style.logo_path and not Path(style.logo_path).is_absolute():
            style.logo_path = str((path.parent / style.logo_path).resolve())
        p = cls(name=d.get("name", path.stem.replace(".qrsign", "")), url=d.get("url", ""),
                style=style, sign=d.get("sign", {}), path=path)
        p.print = print3d.PrintSettings.from_dict(d.get("print", {}))
        p.ensure_logo()
        return p

    # ---- QR stage ---------------------------------------------------------
    def ensure_logo(self):
        """Trace the logo once and cache the outlines on the style."""
        if self.style.logo_path and not self.style.logo_svg_paths:
            paths, size, bbox = render.load_logo(self.style.logo_path)
            self.style.logo_svg_paths, self.style.logo_px_size, self.style.logo_bbox = paths, size, bbox
        if not self.style.logo_path:
            self.style.logo_svg_paths, self.style.logo_bbox = [], None

    def render_qr(self):
        """-> (matrix, geometry) for the current URL and style."""
        self.ensure_logo()
        return render.render(self.url, self.style)

    def qr_svg(self, size_mm=None, size_px=None):
        m, g = self.render_qr()
        return render.to_svg(g, m.size, size_mm=size_mm, size_px=size_px)

    def qr_png(self, size_px=1000):
        m, g = self.render_qr()
        return render.png_bytes(g, m.size, size_px)

    def verify(self, size_px=600):
        m, g = self.render_qr()
        return render.verify(render.to_png(g, m.size, size_px), m.text)

    # ---- sign stage -------------------------------------------------------
    def sign_config(self):
        """Complete, normalised sign config for the generator (angles solved)."""
        values = {k: v for k, v in self.sign.items() if v not in (None, "")}
        if "crossing_angle_deg" in values and "angle_deg" in values:
            values.pop("angle_deg")   # the crossing angle wins; the lean is solved from it
        return sm.normalize_config(values)

    def make_sign(self, out_dir, log=print):
        """Stage 2: write the styled QR as an SVG next to the outputs and run the sign generator on it.
        Returns the generator's result dict (paths, info, geometry, SVG texts)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cfg = self.sign_config()
        qr_path = out_dir / f"{self.name}-qr.svg"
        qr_path.write_text(self.qr_svg(size_mm=50), encoding="utf-8")
        cfg["qr_file"] = qr_path.name
        res = sm.generate(cfg, self.name, out_dir, qr_path, log=log)
        res["qr_svg"] = qr_path
        # a plain config file next to the outputs, so the command-line generator reproduces the sign
        cfg_path = out_dir / f"{self.name}.txt"
        sm.write_config_file(cfg, cfg_path)
        res["config_txt"] = cfg_path
        return res

    # ---- 3D print stage ---------------------------------------------------
    def make_print(self, out_dir, log=print):
        """Stage 3: the 3D-printable sign (and base) as <out_dir>/<name>.3mf, using the same
        layout rules as the laser sign. Returns print3d.make_print's result dict."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cfg = self.sign_config()
        qr_path = out_dir / f"{self.name}-qr.svg"
        qr_path.write_text(self.qr_svg(size_mm=50), encoding="utf-8")
        return print3d.make_print(cfg, qr_path, self.print, self.name, out_dir, log=log)
