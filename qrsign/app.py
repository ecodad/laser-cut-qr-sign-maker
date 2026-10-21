"""QR Sign Maker — NiceGUI app. Stage 1 designs a styled static QR code; stage 2 turns it
into the two-piece laser-cut sign. Run with `qrsign` (console script) or `python -m qrsign.app`.

Single-user local app: one Project object holds the state; every control change re-renders.
"""
import inspect
import io
import sys
import time
from pathlib import Path

from nicegui import app as ng_app, ui

from . import __version__, assembly, encode, fonts, print3d, project, render
from . import signmaker as sm

PROJECTS_DIR = Path.home() / "qrsign-projects"
PREVIEW_PX = 380


class State:
    def __init__(self):
        self.project = project.Project(name="my-sign", url="https://github.com/ecodad/laser-cut-qr-sign-maker")
        self.sign_result = None
        self.print_result = None
        self.widgets = {}

    @property
    def folder(self) -> Path:
        return PROJECTS_DIR / self.project.name

    @property
    def project_file(self) -> Path:
        return self.folder / f"{self.project.name}{project.PROJECT_SUFFIX}"


def _svg_box(svg: str, px: int) -> str:
    """Wrap an SVG so it scales into a px-wide box regardless of its own width/height."""
    import re
    svg = re.sub(r'\swidth="[^"]+"', "", svg, count=1)
    svg = re.sub(r'\sheight="[^"]+"', "", svg, count=1)
    return f'<div style="width:{px}px;max-width:100%;background:#fff;padding:8px;border:1px solid #ddd;border-radius:8px">{svg}</div>'


def _fill_scene(scene, parts, out_dir, prefix, folder_name, look_height):
    """Write the parts as STL files under the project folder and load them into the scene."""
    scene.clear()
    written = assembly.write_parts(parts, out_dir, prefix)
    stamp = int(time.time())
    with scene:
        for name, path, color in written:
            url = f"/projects/{folder_name}/output/assembly/{path.name}?v={stamp}"
            scene.stl(url).material(color)
    lo, hi = assembly.bounds(parts)
    cx, cy, cz = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, look_height
    span = max(hi[0] - lo[0], hi[2] - lo[2])
    scene.move_camera(x=cx + span * 0.55, y=cy - span * 1.05, z=cz + span * 0.45,
                      look_at_x=cx, look_at_y=cy, look_at_z=cz, duration=0)


def build(state: State):
    p = state.project
    w = state.widgets

    # ---------------------------------------------------------------- QR stage
    def refresh_qr():
        try:
            if not p.url.strip():
                w["qr_preview"].set_content('<div class="text-grey">Enter a URL</div>')
                w["qr_status"].set_text("")
                return
            m, g = p.render_qr()
            w["qr_preview"].set_content(_svg_box(render.to_svg(g, m.size, size_px=PREVIEW_PX), PREVIEW_PX))
            ok, text = render.verify(render.to_png(g, m.size, 600), m.text)
            if ok:
                w["qr_status"].set_text(f"Scans OK · version {m.version}, {m.size}×{m.size} modules, error correction H")
                w["qr_status"].classes(remove="text-negative", add="text-positive")
            elif ok is None:
                w["qr_status"].set_text("Scan check unavailable (zxing-cpp not installed)")
                w["qr_status"].classes(remove="text-positive text-negative")
            else:
                w["qr_status"].set_text("Does not scan with this styling — try a plainer body shape, a smaller logo or a larger knockout")
                w["qr_status"].classes(remove="text-positive", add="text-negative")
        except Exception as e:  # keep the UI alive on bad input
            w["qr_preview"].set_content(f'<div class="text-negative">{e}</div>')
            w["qr_status"].set_text("")

    def refresh_qr_debounced():
        """URL typing: wait 300 ms after the last keystroke before re-rendering."""
        timer = w.get("url_timer")
        if timer is not None:
            timer.cancel()
        w["url_timer"] = ui.timer(0.3, refresh_qr, once=True)

    def set_style(attr, value):
        setattr(p.style, attr, value)
        mark_selected(attr)
        refresh_qr()

    def mark_selected(attr):
        for (a, name), card in w["cards"].items():
            if a == attr:
                if name == getattr(p.style, attr):
                    card.classes(add="border-primary bg-blue-1", remove="border-grey-4")
                else:
                    card.classes(add="border-grey-4", remove="border-primary bg-blue-1")

    def picker(attr, names, icon_fn):
        with ui.row().classes("gap-2"):
            for name in names:
                card = ui.element("div").classes(
                    "cursor-pointer border-2 rounded p-1 border-grey-4").on("click", lambda e, n=name: set_style(attr, n))
                with card:
                    ui.html(icon_fn(name), sanitize=False)
                card.tooltip(name)
                w["cards"][(attr, name)] = card
        mark_selected(attr)

    async def on_logo_upload(e):
        f = e.file
        name = getattr(f, "name", None) or "logo.png"
        reader = getattr(f, "read", None)
        data = await reader() if inspect.iscoroutinefunction(reader) else reader()
        dest = state.folder / "logo" / Path(name).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        p.style.logo_path = str(dest)
        p.style.logo_svg_paths = []
        try:
            p.ensure_logo()
        except Exception as ex:
            ui.notify(f"Could not trace the logo: {ex}", type="negative")
            p.style.logo_path = ""
            return
        w["logo_name"].set_text(f"Logo: {dest.name}")
        refresh_qr()

    def clear_logo():
        p.style.logo_path, p.style.logo_svg_paths = "", []
        w["logo_name"].set_text("No logo")
        refresh_qr()

    # ---------------------------------------------------------------- sign stage
    def sign_values():
        for key in project.MAIN_KEYS + project.ADVANCED_KEYS:
            if key in w["sign"]:
                val = w["sign"][key].value
                p.sign[key] = int(bool(val)) if key == "base_qr" else val

    def refresh_sign():
        sign_values()
        try:
            res = p.make_sign(state.folder / "output", log=lambda *a: None)
        except sm.ConfigError as e:
            ui.notify(str(e), type="negative")
            return
        except Exception as e:
            ui.notify(f"Sign generation failed: {e}", type="negative")
            return
        state.sign_result = res
        w["sign_preview"].set_content(_svg_box(res["combined_svg_text"], 640))
        try:
            cfg3 = p.sign_config()
            text, qr, _, cfg3 = print3d.layout_geometry(cfg3, res["qr_svg"], None, log=lambda *a: None)
            parts = assembly.laser_stand(cfg3, text, qr, res.get("base_engrave", ()))
            _fill_scene(w["sign_scene"], parts, state.folder / "output" / "assembly", "laser", p.name, cfg3["height_mm"] * 0.45)
        except Exception as e:
            ui.notify(f"3D view failed: {e}", type="warning")
        info, cfg = res["info"], p.sign_config()
        w["sign_status"].set_text(
            f"QR {info['qr_mm']} mm · text above {info['font_above_mm']} mm · below {info['font_below_mm']} mm · "
            f"sign lean {cfg['angle_deg']}°, base lean {cfg['base_angle_deg']}°, slot {sm.slot_width(cfg):.2f} mm · "
            f"files in {res['svg'].parent}")

    def download_sign(key):
        if not state.sign_result:
            ui.notify("Generate the sign first", type="warning")
            return
        path = Path(state.sign_result[key])
        ui.download(path.read_bytes(), path.name)

    # ---------------------------------------------------------------- 3D print stage
    def print_values():
        ps = p.print
        for key, widget in w["print"].items():
            val = widget.value
            if key in ("qr_layers", "text_layers"):
                val = int(val or 0)
            setattr(ps, key, val)

    def refresh_print():
        sign_values()
        print_values()
        try:
            res = p.make_print(state.folder / "output", log=lambda *a: None)
        except sm.ConfigError as e:
            ui.notify(str(e), type="negative")
            return
        except Exception as e:
            ui.notify(f"3D build failed: {e}", type="negative")
            return
        state.print_result = res
        w["print_preview"].set_content(_svg_box(print3d.preview_svg(res["cfg"], res["text"], res["qr"], p.print), 320))
        try:
            parts = assembly.printed_stand(res, p.print)
            _fill_scene(w["print_scene"], parts, state.folder / "output" / "assembly", "print", p.name, res["cfg"]["height_mm"] * 0.45)
        except Exception as e:
            ui.notify(f"3D view failed: {e}", type="warning")
        summary = res["summary"]
        heights = summary["face_layers"]
        changes = summary["colour_change_heights_mm"]
        msg = (f"{summary['mode']} - sign {summary['sign_thickness_mm']} mm thick - background top {heights['background_top']} mm, "
               f"QR top {heights['qr_top']} mm, text top {heights['text_top']} mm")
        if changes:
            msg += f" - colour changes at z = {changes[0]} mm (QR colour) and {changes[1]} mm (text colour)"
        base = summary["base"]
        msg += (f" - base {base['width']:.1f} x {base['depth']} x {base['height']} mm, slot {base['slot_width']} mm "
                f"leaning {base['lean_deg']} deg - {res['3mf'].name} in {res['3mf'].parent}")
        bad = [v["volume"] for v in summary["volumes"] if not v["closed"]]
        if bad:
            msg += " - WARNING open shells: " + ", ".join(bad)
        w["print_status"].set_text(msg)

    def download_print():
        if not state.print_result:
            ui.notify("Generate the 3D files first", type="warning")
            return
        path = Path(state.print_result["3mf"])
        ui.download(path.read_bytes(), path.name)

    # ---------------------------------------------------------------- project
    def save_project():
        sign_values()
        print_values()
        p.save(state.project_file)
        ui.notify(f"Saved {state.project_file}", type="positive")
        refresh_project_list()

    def load_project(path):
        if not path:
            return
        try:
            q = project.Project.load(path)
        except Exception as e:
            ui.notify(f"Could not open project: {e}", type="negative")
            return
        state.project = q
        ui.navigate.reload()

    def refresh_project_list():
        files = sorted(PROJECTS_DIR.glob(f"*/*{project.PROJECT_SUFFIX}")) if PROJECTS_DIR.exists() else []
        w["projects"].set_options({str(f): f.parent.name for f in files})

    def rename(e):
        p.name = (e.value or "my-sign").strip().replace(" ", "-")

    def new_project():
        state.project = project.Project(name="new-sign", url="")
        ui.navigate.reload()

    # ---------------------------------------------------------------- layout
    w["cards"] = {}
    w["sign"] = {}
    w["print"] = {}
    ui.page_title("QR Sign Maker")
    with ui.header().classes("items-center gap-4"):
        ui.label("QR Sign Maker").classes("text-xl font-bold")
        ui.input("Project name", value=p.name, on_change=rename).props("dense dark").classes("w-48")
        ui.button("Save project", icon="save", on_click=save_project).props("flat color=white")
        ui.button("New", icon="add", on_click=new_project).props("flat color=white")
        w["projects"] = ui.select({}, label="Open project", on_change=lambda e: load_project(e.value)).props("dense dark").classes("w-56")
        ui.space()
        ui.label(f"v{__version__} · projects in {PROJECTS_DIR}").classes("text-xs")
    refresh_project_list()

    with ui.tabs().classes("w-full") as tabs:
        tab_qr = ui.tab("1 · QR code", icon="qr_code_2")
        tab_sign = ui.tab("2 · Laser-cut sign", icon="carpenter")
        tab_print = ui.tab("3 · 3D-printed sign", icon="view_in_ar")
    with ui.tab_panels(tabs, value=tab_qr).classes("w-full"):
        with ui.tab_panel(tab_qr):
            with ui.row().classes("w-full gap-8 items-start"):
                with ui.column().classes("gap-3").style("min-width:420px;max-width:640px"):
                    ui.input("URL", value=p.url, placeholder="https://…",
                             on_change=lambda e: (setattr(p, "url", e.value), refresh_qr_debounced())).classes("w-full")
                    with ui.expansion("Logo", icon="image", value=bool(p.style.logo_path)).classes("w-full"):
                        w["logo_name"] = ui.label(f"Logo: {Path(p.style.logo_path).name}" if p.style.logo_path else "No logo")
                        ui.upload(label="Upload a logo: PNG / JPG (dark on light, traced) or SVG (dark fills kept)", auto_upload=True,
                                  on_upload=on_logo_upload).props("accept=.png,.jpg,.jpeg,.svg,.bmp").classes("w-full")
                        with ui.row().classes("items-center gap-4"):
                            ui.select(render.LOGO_SHAPES, label="Knockout shape", value=p.style.logo_shape,
                                      on_change=lambda e: set_style("logo_shape", e.value)).classes("w-40")
                            ui.checkbox("Clear modules behind the logo", value=p.style.logo_clear,
                                        on_change=lambda e: set_style("logo_clear", e.value))
                            ui.button("Remove logo", icon="delete", on_click=clear_logo).props("flat")
                        ui.label("Logo size (fraction of the code width)")
                        ui.slider(min=0.1, max=0.35, step=0.01, value=p.style.logo_size,
                                  on_change=lambda e: set_style("logo_size", e.value)).props("label")
                    ui.label("Body shape").classes("text-subtitle2 mt-2")
                    picker("body", render.BODY_SHAPES, render.body_icon_svg)
                    ui.label("Eye frame shape").classes("text-subtitle2 mt-2")
                    picker("eye_frame", render.EYE_FRAME_SHAPES, lambda n: render.eye_icon_svg(frame=n))
                    ui.label("Eye ball shape").classes("text-subtitle2 mt-2")
                    picker("eye_ball", render.EYE_BALL_SHAPES, lambda n: render.eye_icon_svg(ball=n))
                with ui.column().classes("gap-3 items-start"):
                    w["qr_preview"] = ui.html("", sanitize=False)
                    w["qr_status"] = ui.label("").classes("text-sm")
                    with ui.row().classes("items-center gap-2"):
                        w["png_px"] = ui.number("PNG size (px)", value=1000, min=100, max=4000, step=100).classes("w-32")
                        ui.button("Download SVG", icon="download",
                                  on_click=lambda: ui.download(p.qr_svg(size_mm=50).encode(), f"{p.name}-qr.svg"))
                        ui.button("Download PNG", icon="download",
                                  on_click=lambda: ui.download(p.qr_png(int(w["png_px"].value or 1000)), f"{p.name}-qr.png"))
                    ui.label("SVG is sized 50 mm; scale it freely, it is vector. Black only in this version.").classes("text-xs text-grey")
        with ui.tab_panel(tab_sign):
            with ui.row().classes("w-full gap-8 items-start"):
                with ui.column().classes("gap-2").style("min-width:420px;max-width:520px"):
                    font_names = fonts.font_names()
                    if not p.sign.get("font") and font_names:
                        p.sign["font"] = next((f for f in ("Ravenscroft", "Arial", "DejaVu Sans", "Liberation Sans") if f in font_names), font_names[0])
                    with ui.row().classes("gap-2"):
                        w["sign"]["width_mm"] = ui.number("Width (mm)", value=p.sign["width_mm"], min=20, max=600).classes("w-28")
                        w["sign"]["height_mm"] = ui.number("Height (mm)", value=p.sign["height_mm"], min=20, max=600).classes("w-28")
                        w["sign"]["thickness_mm"] = ui.number("Thickness (mm)", value=p.sign["thickness_mm"], min=0.5, max=20, step=0.1).classes("w-32")
                    w["sign"]["text_above"] = ui.input("Text above (lines separated by |)", value=p.sign["text_above"]).classes("w-full")
                    w["sign"]["text_above_scales"] = ui.input("Relative line sizes above, e.g. 0.45 | 1", value=p.sign["text_above_scales"]).classes("w-full")
                    w["sign"]["text_below"] = ui.input("Text below (lines separated by |)", value=p.sign["text_below"]).classes("w-full")
                    w["sign"]["text_below_scales"] = ui.input("Relative line sizes below", value=p.sign["text_below_scales"]).classes("w-full")
                    w["sign"]["font"] = ui.select(font_names, label="Font (installed)", value=p.sign.get("font"), with_input=True).classes("w-full")
                    w["sign"]["border_mm"] = ui.number("Border (mm)", value=p.sign["border_mm"], min=0, max=50, step=0.5).classes("w-32")
                    with ui.expansion("Base engraving: a QR code and URL on top of the base, behind the sign", icon="qr_code", value=True).classes("w-full"):
                        w["sign"]["base_qr"] = ui.checkbox("Engrave a QR code + URL on the base", value=bool(p.sign.get("base_qr", 1)))
                        w["sign"]["base_qr_url"] = ui.input("URL the code encodes", value=p.sign.get("base_qr_url", "")).classes("w-full")
                        w["sign"]["base_qr_text"] = ui.input("Text line under it (blank = the URL)", value=p.sign.get("base_qr_text", "")).classes("w-full")
                        with ui.row().classes("gap-2"):
                            w["sign"]["base_qr_size_mm"] = ui.number("Code size (mm)", value=p.sign.get("base_qr_size_mm", 30), min=8, max=200).classes("w-32")
                            w["sign"]["base_qr_text_mm"] = ui.number("Text height (mm)", value=p.sign.get("base_qr_text_mm", 2.8), min=1, max=20, step=0.1).classes("w-32")
                        ui.label("Reads from behind the stand. The base is drawn mirrored so this face ends up on top.").classes("text-xs text-grey")
                    with ui.expansion("Advanced", icon="tune").classes("w-full"):
                        labels = {
                            "crossing_angle_deg": "Crossing angle between the boards (°); blank to use the sign lean below",
                            "angle_deg": "Sign lean from vertical (°), used when the crossing angle is blank",
                            "base_height_mm": "Base crosses the sign this far up from its bottom (mm)",
                            "base_front_mm": "Base in front of the sign face (mm)",
                            "base_back_mm": "Base behind the sign to the table (mm)",
                            "base_width_mm": "Base width (mm), blank = sign width",
                            "base_angle_deg": "Base lean (°), blank = computed so the back edge sits on the table",
                            "corner_radius_mm": "Outer corner radius (mm)",
                            "slot_clearance_mm": "Slot clearance / kerf allowance (mm)",
                            "qr_fraction": "Share of the content height reserved for the QR (0–1)",
                            "gap_mm": "Gap between text and QR (mm), blank = border / 2",
                            "line_spacing": "Line lead as a multiple of the next line's size",
                            "merge_qr": "Union touching QR shapes (1 = yes, 0 = no)",
                            "cut_stroke_mm": "Cut line stroke width (mm)",
                            "piece_spacing_mm": "Gap between pieces in the combined SVG (mm)",
                        }
                        for key in project.ADVANCED_KEYS:
                            w["sign"][key] = ui.number(labels[key], value=p.sign.get(key), step=0.1).classes("w-full")
                    ui.button("Generate sign", icon="build", on_click=refresh_sign).props("color=primary")
                with ui.column().classes("gap-3 items-start"):
                    w["sign_preview"] = ui.html('<div class="text-grey">Generate to preview the cut file (sign left, base right)</div>', sanitize=False)
                    w["sign_status"] = ui.label("").classes("text-sm")
                    with ui.row().classes("gap-2"):
                        ui.button("Sign SVG", icon="download", on_click=lambda: download_sign("sign_svg"))
                        ui.button("Base SVG", icon="download", on_click=lambda: download_sign("base_svg"))
                        ui.button("Both on one sheet", icon="download", on_click=lambda: download_sign("svg"))
                        ui.button("Config file", icon="download", on_click=lambda: download_sign("config_txt"))
                    ui.label("Red = cut, black = engrave. Measure your stock and set the thickness before cutting; "
                             "test the slot fit on scrap and adjust the clearance.").classes("text-xs text-grey")
                    ui.label("Assembled view (drag to orbit, wheel to zoom)").classes("text-subtitle2 mt-2")
                    w["sign_scene"] = ui.scene(width=560, height=420, grid=(400, 20)).classes("border rounded")
        with ui.tab_panel(tab_print):
            ps = p.print
            with ui.row().classes("w-full gap-8 items-start"):
                with ui.column().classes("gap-2").style("min-width:420px;max-width:520px"):
                    ui.label("Uses the size, text, font and border from the sign tab; the QR and text are raised on a flat plate.").classes("text-xs text-grey")
                    w["print"]["mode"] = ui.select({"multi": "Multi-filament (parts side by side, same height)",
                                                    "single": "Single filament with colour changes (by height)"},
                                                   label="Print mode", value=ps.mode).classes("w-full")
                    with ui.row().classes("gap-2 items-center"):
                        w["print"]["color_background"] = ui.color_input("Background", value=ps.color_background).classes("w-36")
                        w["print"]["color_qr"] = ui.color_input("QR code", value=ps.color_qr).classes("w-36")
                        w["print"]["color_text"] = ui.color_input("Text", value=ps.color_text).classes("w-36")
                    with ui.row().classes("gap-2"):
                        w["print"]["back_mm"] = ui.number("Back plate (mm)", value=ps.back_mm, min=0.6, max=10, step=0.2).classes("w-32")
                        w["print"]["layer_height"] = ui.number("Layer height (mm)", value=ps.layer_height, min=0.05, max=0.6, step=0.05).classes("w-32")
                        w["print"]["qr_layers"] = ui.number("QR layers", value=ps.qr_layers, min=1, max=20, step=1).classes("w-28")
                        w["print"]["text_layers"] = ui.number("Text layers above QR (single)", value=ps.text_layers, min=1, max=10, step=1).classes("w-52")
                    w["print"]["flip"] = ui.checkbox("Flip: face on the bed (multi-filament only)", value=ps.flip)
                    with ui.expansion("Base", icon="foundation", value=True).classes("w-full"):
                        w["print"]["include_base"] = ui.checkbox("Include the base", value=ps.include_base)
                        with ui.row().classes("gap-2"):
                            w["print"]["base_lean_deg"] = ui.number("Lean (deg)", value=ps.base_lean_deg, min=0, max=45, step=1).classes("w-24")
                            w["print"]["base_depth_mm"] = ui.number("Depth (mm)", value=ps.base_depth_mm, min=15, max=200).classes("w-28")
                            w["print"]["base_height_mm"] = ui.number("Height (mm)", value=ps.base_height_mm, min=5, max=100).classes("w-28")
                            w["print"]["base_wall_mm"] = ui.number("Side wall (mm)", value=ps.base_wall_mm, min=1, max=20).classes("w-28")
                        with ui.row().classes("gap-2"):
                            w["print"]["slot_depth_mm"] = ui.number("Slot depth (mm)", value=ps.slot_depth_mm, min=3, max=60).classes("w-32")
                            w["print"]["slot_clearance_mm"] = ui.number("Slot clearance (mm)", value=ps.slot_clearance_mm, min=0, max=2, step=0.05).classes("w-36")
                            w["print"]["slot_side_clearance_mm"] = ui.number("Side clearance (mm)", value=ps.slot_side_clearance_mm, min=0, max=3, step=0.1).classes("w-36")
                    ui.button("Generate 3D files", icon="view_in_ar", on_click=refresh_print).props("color=primary")
                with ui.column().classes("gap-3 items-start"):
                    w["print_preview"] = ui.html('<div class="text-grey">Generate to preview the printed sign (top view)</div>', sanitize=False)
                    w["print_status"] = ui.label("").classes("text-sm")
                    ui.button("Download 3MF", icon="download", on_click=download_print)
                    ui.label("Multi-filament: the 3MF opens in PrusaSlicer / Orca / Bambu Studio with the background, QR and text "
                             "parts assigned to extruders 1, 2 and 3. Single filament: add colour changes at the heights shown.").classes("text-xs text-grey")
                    ui.label("Assembled view: the printed sign in its base").classes("text-subtitle2 mt-2")
                    w["print_scene"] = ui.scene(width=560, height=420, grid=(400, 20)).classes("border rounded")
    refresh_qr()


def main():
    state = State()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    ng_app.add_static_files("/projects", str(PROJECTS_DIR))

    @ui.page("/")
    def index():
        build(state)

    # local-only: bind to the loopback address so the designer is not exposed on the network
    port = 8765
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ui.run(title="QR Sign Maker", host="127.0.0.1", port=port, reload=False,
           show="--no-browser" not in sys.argv, favicon="🔲")


if __name__ in {"__main__", "__mp_main__"}:
    main()
