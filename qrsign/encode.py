"""Encode content into a QR module matrix with per-module roles, using segno.

Always error correction level H (the app's rule), never Micro QR.
"""
from dataclasses import dataclass

import segno
from segno import consts

# module roles the renderer cares about
DATA = "data"
FINDER = "finder"          # the three 7x7 eyes (frame + ball)
ALIGNMENT = "alignment"    # 5x5 alignment patterns (version 2+)
TIMING = "timing"
FORMAT = "format"          # format / version bits and the dark module
OTHER = "other"

_DARK_ROLE = {
    consts.TYPE_FINDER_PATTERN_DARK: FINDER,
    consts.TYPE_ALIGNMENT_PATTERN_DARK: ALIGNMENT,
    consts.TYPE_TIMING_DARK: TIMING,
    consts.TYPE_FORMAT_DARK: FORMAT,
    consts.TYPE_VERSION_DARK: FORMAT,
    consts.TYPE_DARKMODULE: FORMAT,
    consts.TYPE_DATA_DARK: DATA,
}


@dataclass
class Matrix:
    size: int                 # modules per side
    dark: list                # size x size list of bool
    role: list                # size x size list of role strings (for dark modules; "" for light)
    version: int
    text: str

    def is_dark(self, r, c):
        return 0 <= r < self.size and 0 <= c < self.size and self.dark[r][c]

    def eye_origins(self):
        """Top-left module of each 7x7 finder pattern: top-left, top-right, bottom-left."""
        n = self.size
        return [(0, 0), (0, n - 7), (n - 7, 0)]

    def in_eye(self, r, c):
        for r0, c0 in self.eye_origins():
            if r0 <= r < r0 + 7 and c0 <= c < c0 + 7:
                return True
        return False

    def alignment_centres(self):
        """Centres of the alignment patterns, found from the module roles."""
        seen, out = set(), []
        for r in range(self.size):
            for c in range(self.size):
                if self.role[r][c] == ALIGNMENT and (r, c) not in seen:
                    # a 5x5 pattern: find its extent
                    r1, c1 = r, c
                    while r1 + 1 < self.size and self.role[r1 + 1][c] == ALIGNMENT:
                        r1 += 1
                    while c1 + 1 < self.size and self.role[r][c1 + 1] == ALIGNMENT:
                        c1 += 1
                    for rr in range(r, r1 + 1):
                        for cc in range(c, c1 + 1):
                            seen.add((rr, cc))
                    out.append(((r + r1) / 2, (c + c1) / 2))
        return out


def encode(text, error="h"):
    """Encode `text` (a URL in v1) and return a Matrix with module roles."""
    if not text or not text.strip():
        raise ValueError("nothing to encode")
    qr = segno.make(text.strip(), error=error, micro=False)
    rows = list(qr.matrix_iter(scale=1, border=0, verbose=True))
    size = len(rows)
    dark = [[m in _DARK_ROLE for m in row] for row in rows]
    role = [[_DARK_ROLE.get(m, "") for m in row] for row in rows]
    return Matrix(size=size, dark=dark, role=role, version=qr.version, text=text.strip())
