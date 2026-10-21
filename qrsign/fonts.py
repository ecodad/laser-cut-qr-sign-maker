"""List installed fonts (family name -> file) on Windows, macOS and Linux."""
import os
import sys
from functools import lru_cache
from pathlib import Path


def font_dirs():
    home = Path.home()
    if sys.platform.startswith("win"):
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Microsoft/Windows/Fonts"]
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local/share/fonts"]
    return [d for d in dirs if d.exists()]


@lru_cache(maxsize=1)
def installed_fonts():
    """{family name: path} for every .ttf/.otf found; the first regular-looking file per family wins."""
    from fontTools.ttLib import TTFont
    found = {}
    for d in font_dirs():
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                tt = TTFont(f, lazy=True)
                name = tt["name"]
                family = name.getDebugName(16) or name.getDebugName(1)
                sub = (name.getDebugName(17) or name.getDebugName(2) or "").lower()
                tt.close()
            except Exception:
                continue
            if not family:
                continue
            regular = sub in ("regular", "normal", "book", "roman", "")
            if family not in found or (regular and not found[family][1]):
                found[family] = (f, regular)
    return {k: v[0] for k, v in sorted(found.items(), key=lambda kv: kv[0].lower())}


def font_names():
    return list(installed_fonts().keys())
