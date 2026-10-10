#!/usr/bin/env python3
"""Logo geometry: font-outlined wordmarks, boolean-resolved symbols, lockups and delivery variants.

A logo here is built, not drawn freehand. The model writes a symbol as a few primitives on a 100-unit grid
(circle, rect, rounded rect, polygon, short paths, each with a semantic id); this module turns it into clean filled
outlines. Wordmarks and monogram letters come from the set's display font, shaped by HarfBuzz and outlined, never
live <text>. Everything downstream (lockups, colour versions, favicons, app icon, PNGs) is computed from those
outlines, so the same input always yields the same files. See docs/architecture.md section 4.4 and references/logo.md.

Symbol SVG authoring rules (what resolve_symbol understands):
  <svg viewBox="0 0 100 100">                       any viewBox is normalised onto the 100-unit grid
  <circle id="disc" cx="50" cy="50" r="46" data-color="primary"/>
  <rect id="slot" x="0" y="58" width="100" height="8" data-op="subtract"/>
  <path id="cut" d="..." data-op="intersect" data-target="disc"/>
  <path id="letter" data-glyphs="a" data-x="50" data-baseline="80" data-cap="40" data-anchor="middle"
        data-op="subtract"/>                       glyph outlines from the set's display font (needs font_path)
  <g data-color="accent"> ... </g>                  a group is its own boolean scope; a group with data-op is
                                                    unioned first and then applied as one shape
  - data-op: union (default) | subtract | intersect. subtract/intersect act on every earlier shape in the same
    scope, or only on the ids listed in data-target. Later filled shapes knock out earlier shapes of another colour,
    so colour parts never stack.
  - data-color: a palette role key (primary, accent, text ...) or a brand id (brand-1). Raw fills are reported.
  - Strokes are outlined (stroke-width, stroke-linecap, stroke-linejoin); fill-rule="evenodd" is honoured.
  - No <text>, <image>, filter or <foreignObject>: they are reported by logo_audit and ignored here.

Optical corrections supported in code (references/logo.md, practice; amounts measured from the font):
  - overshoot: font_optics() measures the O-over-H overshoot; lockup() lets round/pointed symbol edges pass the
    cap-height band by that ratio, flat edges sit exactly on it;
  - cap-height alignment: lockup() aligns the symbol to the cap-height band, never to the font's em box;
  - horizontals: font_optics() reports the font's H crossbar/stem ratio for symbol horizontals;
  - optical centre: the app icon content sits slightly above the geometric centre (heuristic, uncalibrated);
  - light-on-dark: reversed versions use a negative GRAD when the font has that axis and inset the symbol outline
    slightly (both amounts heuristic, uncalibrated; calibrate side by side at 32 px).

Public API (docs/architecture.md section 11): wordmark_svg, text_path, resolve_symbol, lockup, variants, sync_symbols.
Helpers: font_optics, inspect_symbol, parse_parts, rasterize, write_png, bitmap, resolve_color, font_for, LogoError.
Needs fontTools, uharfbuzz and skia-pathops (brand.py check).
"""
import copy
import hashlib
import importlib
import itertools
import json
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pathops  # noqa: E402
import uharfbuzz as hb  # noqa: E402
from fontTools.pens.transformPen import TransformPen  # noqa: E402

__all__ = [
    "wordmark_svg", "text_path", "resolve_symbol", "lockup", "variants", "sync_symbols", "font_optics",
    "inspect_symbol", "parse_parts", "rasterize", "write_png", "bitmap", "resolve_color", "font_for", "apply_case",
    "LogoError", "GROUND_ROLES", "FORBIDDEN_TAGS",
]

SVG_NS = "http://www.w3.org/2000/svg"
FORBIDDEN_TAGS = ("text", "image", "foreignObject", "filter")
UNSUPPORTED_TAGS = ("use", "mask", "clipPath", "pattern", "symbol", "marker", "linearGradient", "radialGradient",
                    "tspan", "textPath", "switch", "a", "style", "script")
SHAPE_TAGS = ("circle", "ellipse", "rect", "polygon", "polyline", "line", "path")
SKIP_TAGS = ("title", "desc", "metadata", "defs")
GROUND_ROLES = ("background", "surface", "surfaceAlt")
KAPPA = 0.5522847498307936  # cubic approximation of a quarter circle

# Heuristic, uncalibrated constants (docs/architecture.md section 0: labelled in code and docs).
HORIZONTAL_SYMBOL_CAPS = 1.25    # horizontal lockup: symbol height in cap heights, centred on the cap-height band
HORIZONTAL_GAP = 0.5             # horizontal lockup: symbol-to-wordmark gap, fraction of the symbol height
STACKED_SYMBOL_CAPS = 2.6        # symbol height in a stacked lockup, in cap heights
STACKED_GAP_CAPS = 0.8           # symbol-to-wordmark gap in a stacked lockup, in cap heights
ROUND_EDGE_COVER = 0.4           # an edge whose 3%-inset scan covers < 40% of the width counts as round/pointed
DEFAULT_OVERSHOOT = 0.015        # used when the font has no O to measure
APP_ICON_CONTENT = 0.58          # app icon: mark fits this fraction of the tile (safe area)
APP_ICON_LIFT = 0.015            # app icon: optical-centre lift, fraction of the tile
FAVICON_MARGIN = 1 / 16          # favicon: margin on each side, fraction of the square
DARK_INSET_UNITS = 0.5           # light-on-dark: symbol outline inset, units of the 100 grid
DARK_GRAD = -25                  # light-on-dark: GRAD value when the font has a GRAD axis (clamped to its range)
DELIVERY_PX = 1024               # long edge of the delivery PNGs
DEVICE_TILE_PAD = 0.18           # logo.device tile: padding around the symbol, fraction of its larger side
DEVICE_TILE_RADIUS = 0.22        # logo.device tile: corner radius, fraction of the tile's larger side
DEVICE_OUTLINE = 0.03            # logo.device outline: contour width, fraction of the symbol's larger side
DEVICE_CONTRAST = 3.0            # a device is drawn where a symbol part is below this on its ground (WCAG 1.4.11)
DEVICE_ROLES = ("text", "primary", "onPrimary", "accent", "background", "surface")
GENERIC_WORDS = {"the", "a", "an", "and", "of", "studio", "studios", "domaine", "maison", "casa", "house", "atelier",
                 "la", "le", "les", "el", "il", "lo", "das", "der", "die", "de", "du", "von", "van", "co", "company",
                 "group", "café", "cafe", "club", "bar", "hotel", "shop", "store", "my", "our"}
LOGO_EXTS = ("svg", "png")


class LogoError(ValueError):
    """A symbol, font or identity field cannot be turned into logo geometry. The message names the cause."""


# ---------------------------------------------------------------- small helpers

def _num(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", str(v))
    if not m:
        return default
    return float(m.group(1))


def _fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


# ---------------------------------------------------------------- affine transforms

def _mul(m, n):
    """Compose affine matrices (a b c d e f): result applies n first, then m."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
            a * e2 + c * f2 + e, b * e2 + d * f2 + f)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(text):
    """SVG transform attribute -> affine tuple; also returns the rotate() calls seen as (angle, cx, cy)."""
    m = IDENTITY
    rotations = []
    if not text:
        return m, rotations
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text):
        v = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0] if v else 0, v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0] if v else 1
            t = (sx, 0, 0, v[1] if len(v) > 1 else sx, 0, 0)
        elif name == "rotate":
            a = math.radians(v[0] if v else 0)
            cx, cy = (v[1], v[2]) if len(v) >= 3 else (0.0, 0.0)
            rotations.append((v[0] if v else 0.0, cx, cy))
            ca, sa = math.cos(a), math.sin(a)
            r = (ca, sa, -sa, ca, 0, 0)
            t = _mul((1, 0, 0, 1, cx, cy), _mul(r, (1, 0, 0, 1, -cx, -cy)))
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0] if v else 0)), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0] if v else 0)), 0, 1, 0, 0)
        else:
            continue
        m = _mul(m, tuple(float(x) for x in t))
    return m, rotations


def _scale_of(m):
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0


def _xf(path, m):
    if m == IDENTITY:
        return path
    return path.transform(*m)


# ---------------------------------------------------------------- path construction

class _Builder:
    """Collects subpaths as (start, [segments], closed); emits a pathops Path, optionally closing every subpath."""

    def __init__(self):
        self.subs = []
        self.cur = None

    def move(self, p):
        self.cur = [p, [], False]
        self.subs.append(self.cur)

    def _ensure(self, p):
        if self.cur is None:
            self.move(p)

    def line(self, p0, p):
        self._ensure(p0)
        self.cur[1].append(("L", p))

    def cubic(self, p0, c1, c2, p):
        self._ensure(p0)
        self.cur[1].append(("C", c1, c2, p))

    def quad(self, p0, c, p):
        self._ensure(p0)
        self.cur[1].append(("Q", c, p))

    def close(self):
        if self.cur is not None:
            self.cur[2] = True
            start = self.cur[0]
            self.cur = None
            return start
        return None

    def path(self, close_all=False):
        out = pathops.Path()
        for start, segs, closed in self.subs:
            if not segs:
                continue
            out.moveTo(*start)
            for s in segs:
                if s[0] == "L":
                    out.lineTo(*s[1])
                elif s[0] == "C":
                    out.cubicTo(*s[1], *s[2], *s[3])
                else:
                    out.quadTo(*s[1], *s[2])
            if closed or close_all:
                out.close()
        return out

    def has_open(self):
        return any(segs and not closed for _s, segs, closed in self.subs)


def _arc_to_cubics(p0, rx, ry, phi_deg, large, sweep, p):
    """SVG elliptical arc (endpoint form) -> list of cubic control tuples (c1, c2, end)."""
    x1, y1 = p0
    x2, y2 = p
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [((x1, y1), (x2, y2), (x2, y2))]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    # endpoint -> centre parameterisation (SVG 1.1 implementation notes, appendix F.6.5)
    u = cp * dx + sp * dy            # start point in the ellipse's rotated frame
    w = cp * dy - sp * dx
    scale = (u / rx) ** 2 + (w / ry) ** 2
    if scale > 1:                    # radii too small for the chord: grow them uniformly
        grow = math.sqrt(scale)
        rx, ry = rx * grow, ry * grow
    rx2, ry2, u2, w2 = rx * rx, ry * ry, u * u, w * w
    den = rx2 * w2 + ry2 * u2
    k = math.sqrt(max(0.0, (rx2 * ry2 - den) / den)) if den else 0.0
    if large == sweep:
        k = -k
    cu, cw = k * rx * w / ry, -k * ry * u / rx     # centre in the rotated frame
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    cx, cy = mx + cp * cu - sp * cw, my + sp * cu + cp * cw

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (u - cu) / rx, (w - cw) / ry)
    dt = ang((u - cu) / rx, (w - cw) / ry, (-u - cu) / rx, (-w - cw) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []

    def pt(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (cp * x - sp * y + cx, sp * x + cp * y + cy)

    def der(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cp * x - sp * y, sp * x + cp * y)

    t = t1
    for i in range(n):
        a, b = t, t + step
        pa, pb = pt(a), pt(b)
        da, db = der(a), der(b)
        c1 = (pa[0] + k * da[0], pa[1] + k * da[1])
        c2 = (pb[0] - k * db[0], pb[1] - k * db[1])
        if i == n - 1:
            pb = (x2, y2)
        out.append((c1, c2, pb))
        t = b
    return out


_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def parse_d(d):
    """SVG path data -> _Builder (all commands, relative and absolute; arcs become cubics)."""
    toks = [(c, n) for c, n in _TOKEN.findall(d or "")]
    b = _Builder()
    i = 0
    cmd = None
    cur = (0.0, 0.0)
    last_c = None  # last cubic control point (for S)
    last_q = None  # last quadratic control point (for T)

    def has_num():
        return i < len(toks) and toks[i][1] != ""

    def num():
        nonlocal i
        if not has_num():
            raise LogoError(f"path data: number expected near token {i} in {d[:60]!r}")
        v = float(toks[i][1])
        i += 1
        return v

    def flag():
        # arc flags may be written without separators ("a10 10 0 01 20 0")
        nonlocal i
        if not has_num():
            raise LogoError(f"path data: arc flag expected in {d[:60]!r}")
        s = toks[i][1]
        if s[0] in "01" and len(s) > 1:
            toks[i] = ("", s[1:])
            return int(s[0])
        i += 1
        return 1 if float(s) else 0

    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                s = b.close()
                cur = s if s is not None else cur
                last_c = last_q = None
                cmd = None
                continue
        elif cmd is None:
            raise LogoError(f"path data must start with a command: {d[:60]!r}")
        rel = cmd.islower()
        C = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if C == "M":
            p = (num() + ox, num() + oy)
            b.move(p)
            cur = p
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif C == "L":
            p = (num() + ox, num() + oy)
            b.line(cur, p)
            cur = p
            last_c = last_q = None
        elif C == "H":
            p = (num() + (cur[0] if rel else 0.0), cur[1])
            b.line(cur, p)
            cur = p
            last_c = last_q = None
        elif C == "V":
            p = (cur[0], num() + (cur[1] if rel else 0.0))
            b.line(cur, p)
            cur = p
            last_c = last_q = None
        elif C == "C":
            c1 = (num() + ox, num() + oy)
            c2 = (num() + ox, num() + oy)
            p = (num() + ox, num() + oy)
            b.cubic(cur, c1, c2, p)
            cur, last_c, last_q = p, c2, None
        elif C == "S":
            c1 = (2 * cur[0] - last_c[0], 2 * cur[1] - last_c[1]) if last_c else cur
            c2 = (num() + ox, num() + oy)
            p = (num() + ox, num() + oy)
            b.cubic(cur, c1, c2, p)
            cur, last_c, last_q = p, c2, None
        elif C == "Q":
            c = (num() + ox, num() + oy)
            p = (num() + ox, num() + oy)
            b.quad(cur, c, p)
            cur, last_q, last_c = p, c, None
        elif C == "T":
            c = (2 * cur[0] - last_q[0], 2 * cur[1] - last_q[1]) if last_q else cur
            p = (num() + ox, num() + oy)
            b.quad(cur, c, p)
            cur, last_q, last_c = p, c, None
        elif C == "A":
            rx, ry, rot = num(), num(), num()
            large, sweep = flag(), flag()
            p = (num() + ox, num() + oy)
            for c1, c2, e in _arc_to_cubics(cur, rx, ry, rot, large, sweep, p):
                b.cubic(cur, c1, c2, e)
                cur = e
            cur = p
            last_c = last_q = None
    return b


def _ellipse(b, cx, cy, rx, ry):
    k = KAPPA
    b.move((cx + rx, cy))
    b.cubic((cx + rx, cy), (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry))
    b.cubic((cx, cy + ry), (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy))
    b.cubic((cx - rx, cy), (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry))
    b.cubic((cx, cy - ry), (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy))
    b.close()


def _rect(b, x, y, w, h, rx, ry):
    if rx <= 0 or ry <= 0:
        b.move((x, y))
        b.line((x, y), (x + w, y))
        b.line((x + w, y), (x + w, y + h))
        b.line((x + w, y + h), (x, y + h))
        b.close()
        return
    k = KAPPA
    b.move((x + rx, y))
    b.line((x + rx, y), (x + w - rx, y))
    b.cubic((x + w - rx, y), (x + w - rx + k * rx, y), (x + w, y + ry - k * ry), (x + w, y + ry))
    b.line((x + w, y + ry), (x + w, y + h - ry))
    b.cubic((x + w, y + h - ry), (x + w, y + h - ry + k * ry), (x + w - rx + k * rx, y + h), (x + w - rx, y + h))
    b.line((x + w - rx, y + h), (x + rx, y + h))
    b.cubic((x + rx, y + h), (x + rx - k * rx, y + h), (x, y + h - ry + k * ry), (x, y + h - ry))
    b.line((x, y + h - ry), (x, y + ry))
    b.cubic((x, y + ry), (x, y + ry - k * ry), (x + rx - k * rx, y), (x + rx, y))
    b.close()


def _points(text):
    v = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", text or "")]
    return list(zip(v[0::2], v[1::2]))


def _element_builder(el, tag):
    """Primitive element -> (_Builder, facts dict for the audit)."""
    b = _Builder()
    facts = {}
    g = el.get
    if tag == "circle":
        r = _num(g("r"))
        facts["radii"] = [r]
        if r > 0:
            _ellipse(b, _num(g("cx")), _num(g("cy")), r, r)
    elif tag == "ellipse":
        rx, ry = _num(g("rx")), _num(g("ry"))
        facts["radii"] = [rx, ry]
        if rx > 0 and ry > 0:
            _ellipse(b, _num(g("cx")), _num(g("cy")), rx, ry)
    elif tag == "rect":
        x, y, w, h = _num(g("x")), _num(g("y")), _num(g("width")), _num(g("height"))
        rx, ry = g("rx"), g("ry")
        rx = _num(rx if rx is not None else ry)
        ry = _num(ry if ry is not None else rx)
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        facts["rect"] = (x, y, w, h, rx)
        if rx > 0:
            facts["radii"] = [rx]
        if w > 0 and h > 0:
            _rect(b, x, y, w, h, rx, ry)
    elif tag in ("polygon", "polyline"):
        pts = _points(g("points"))
        facts["vertices"] = pts
        if len(pts) >= 2:
            b.move(pts[0])
            for p0, p1 in zip(pts, pts[1:]):
                b.line(p0, p1)
            if tag == "polygon":
                b.close()
    elif tag == "line":
        p0 = (_num(g("x1")), _num(g("y1")))
        p1 = (_num(g("x2")), _num(g("y2")))
        b.move(p0)
        b.line(p0, p1)
    elif tag == "path":
        b = parse_d(g("d") or "")
        segs = [s for _st, ss, _c in b.subs for s in ss]
        if segs and all(s[0] == "L" for s in segs) and len(b.subs) == 1:
            facts["vertices"] = [b.subs[0][0]] + [s[1] for s in segs]
    return b, facts


# ---------------------------------------------------------------- path output and measurement

def _iter_segments(path):
    """Yield ('M', p) / ('L', p) / ('C', c1, c2, p) / ('Q', c, p) / ('Z',) with multi-point quads decomposed."""
    for verb, pts in path.segments:
        if verb == "moveTo":
            yield ("M", pts[0])
        elif verb == "lineTo":
            yield ("L", pts[0])
        elif verb == "curveTo":
            yield ("C", pts[0], pts[1], pts[2])
        elif verb == "qCurveTo":
            if pts[-1] is None:  # closed contour made only of off-curve points (TrueType style)
                off = list(pts[:-1])
                start = ((off[-1][0] + off[0][0]) / 2, (off[-1][1] + off[0][1]) / 2)
                yield ("M", start)
                pts = tuple(off) + (start,)
            for c, p in pathops.decompose_quadratic_segment(pts):
                yield ("Q", c, p)
        elif verb == "closePath":
            yield ("Z",)


def path_to_d(path):
    out = []
    for s in _iter_segments(path):
        if s[0] == "Z":
            out.append("Z")
        else:
            out.append(s[0] + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in s[1:]))
    return "".join(out)


def anchor_count(path):
    return sum(1 for s in _iter_segments(path) if s[0] in ("M", "L", "C", "Q"))


def _union(paths):
    paths = [p for p in paths if p is not None]
    if not paths:
        return pathops.Path()
    acc = pathops.Path()
    for p in paths:
        acc.addPath(p)
    return pathops.simplify(acc, fix_winding=True, clockwise=False)


def _op(a, b, kind):
    return pathops.op(a, b, kind, fix_winding=True)


def _empty(path):
    try:
        return abs(path.area) < 1e-6
    except Exception:  # noqa: BLE001 - an empty path can raise on area
        return True


def _bounds(path):
    try:
        b = path.bounds
    except Exception:  # noqa: BLE001
        return None
    if b is None or (b[0] == b[2] and b[1] == b[3]):
        return None
    return b


def _union_bounds(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _flatten(path, scale=1.0, m=IDENTITY):
    """Path -> list of polygons (closed point lists) in output space (affine m applied)."""
    polys = []
    cur = []
    last = None
    a, b, c, d, e, f = m

    def T(p):
        return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)

    for s in _iter_segments(path):
        if s[0] == "M":
            if len(cur) > 2:
                polys.append(cur)
            last = T(s[1])
            cur = [last]
        elif s[0] == "L":
            last = T(s[1])
            cur.append(last)
        elif s[0] == "C":
            p0 = last
            c1, c2, p3 = T(s[1]), T(s[2]), T(s[3])
            ln = math.dist(p0, c1) + math.dist(c1, c2) + math.dist(c2, p3)
            n = max(2, min(96, int(math.sqrt(ln * scale) * 1.6) + 1))
            for i in range(1, n + 1):
                t = i / n
                mt = 1 - t
                x = mt ** 3 * p0[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * p3[0]
                y = mt ** 3 * p0[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * p3[1]
                cur.append((x, y))
            last = p3
        elif s[0] == "Q":
            p0 = last
            c1, p2 = T(s[1]), T(s[2])
            ln = math.dist(p0, c1) + math.dist(c1, p2)
            n = max(2, min(96, int(math.sqrt(ln * scale) * 1.6) + 1))
            for i in range(1, n + 1):
                t = i / n
                mt = 1 - t
                cur.append((mt * mt * p0[0] + 2 * mt * t * c1[0] + t * t * p2[0],
                            mt * mt * p0[1] + 2 * mt * t * c1[1] + t * t * p2[1]))
            last = p2
        elif s[0] == "Z":
            if len(cur) > 2:
                polys.append(cur)
            cur = []
    if len(cur) > 2:
        polys.append(cur)
    return polys


def _edges(polys):
    edges = []
    for poly in polys:
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            d = 1
            if y0 > y1:
                x0, y0, x1, y1, d = x1, y1, x0, y0, -1
            edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), d))
    edges.sort(key=lambda t: t[0])
    return edges


def _spans_at(edges, y, evenodd=False):
    """Inside spans [(xa, xb)] of the edge set on the horizontal line y."""
    xs = []
    for y0, y1, x0, k, d in edges:
        if y0 > y:
            break
        if y < y1:
            xs.append((x0 + (y - y0) * k, d))
    xs.sort()
    spans = []
    w = 0
    start = None
    for x, d in xs:
        before = (w & 1) if evenodd else (w != 0)
        w += d
        after = (w & 1) if evenodd else (w != 0)
        if not before and after:
            start = x
        elif before and not after and start is not None:
            spans.append((start, x))
    return spans


def _merge(iv):
    iv = sorted(i for i in iv if i[1] > i[0])
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return out


def _intersect(p, q):
    out, i, j = [], 0, 0
    while i < len(p) and j < len(q):
        a, b = max(p[i][0], q[j][0]), min(p[i][1], q[j][1])
        if a < b:
            out.append([a, b])
        if p[i][1] < q[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(p, q):
    out = []
    for a, b in p:
        cur = a
        for c, d in q:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append([cur, c])
            cur = max(cur, d)
        if cur < b:
            out.append([cur, b])
    return out


def coverage(polys, width, height, samples=4, evenodd=False):
    """Anti-aliased coverage (list of bytearrays, one value 0..255 per pixel) of polygons already in pixel space.
    Pixels covered on every sample line are filled in bulk; only edge pixels are integrated."""
    edges = _edges(polys)
    rows = []
    active_from = 0
    inv = 1.0 / samples
    zero = bytes(width)
    for row in range(height):
        ymax = row + 1
        while active_from < len(edges) and edges[active_from][1] <= row:
            active_from += 1
        cand = []
        for e in itertools.islice(edges, active_from, None):
            if e[0] >= ymax:
                break
            if e[1] > row:
                cand.append(e)
        out = bytearray(zero)
        if not cand:
            rows.append(out)
            continue
        per_sample = []
        full = None
        touched = []
        for k in range(samples):
            y = row + (k + 0.5) * inv
            spans = []
            interior = []
            for xa, xb in _spans_at(cand, y, evenodd):
                xa, xb = max(0.0, xa), min(float(width), xb)
                if xb <= xa:
                    continue
                spans.append((xa, xb))
                interior.append([math.ceil(xa), math.floor(xb)])
                touched.append([int(xa), min(width, math.ceil(xb))])
            per_sample.append(spans)
            interior = _merge(interior)
            full = interior if full is None else _intersect(full, interior)
        for a, b in full or []:
            out[a:b] = b"\xff" * (b - a)
        for a, b in _subtract(_merge(touched), full or []):
            for x in range(a, b):
                c = 0.0
                for spans in per_sample:
                    for xa, xb in spans:
                        if xb <= x or xa >= x + 1:
                            continue
                        c += min(xb, x + 1) - max(xa, x)
                c *= inv
                out[x] = 255 if c >= 0.998 else (0 if c <= 0.002 else int(c * 255 + 0.5))
        rows.append(out)
    return rows


def bitmap(path, n, view=(0.0, 0.0, 100.0, 100.0), evenodd=False):
    """Binary raster (list of int bitmasks, bit x = column x) of `path`, view box mapped onto n x n cells,
    sampled at cell centres."""
    x0, y0, w, h = view
    s = n / max(w, h)
    polys = _flatten(path, s, (s, 0, 0, s, -x0 * s, -y0 * s))
    edges = _edges(polys)
    rows = []
    for r in range(n):
        y = r + 0.5
        bits = 0
        for xa, xb in _spans_at(edges, y, evenodd):
            a = max(0, math.ceil(xa - 0.5))
            b = min(n - 1, math.floor(xb - 0.5))
            if b >= a:
                bits |= ((1 << (b - a + 1)) - 1) << a
        rows.append(bits)
    return rows


def rasterize(layers, width, height, view, ground=None, samples=4):
    """Paint filled layers [(path, hex)] (view box `view` = (x, y, w, h) fitted and centred into width x height)
    over `ground` (hex or None for transparent). Returns RGBA bytes (straight alpha)."""
    vx, vy, vw, vh = view
    s = min(width / vw, height / vh)
    ox = (width - vw * s) / 2 - vx * s
    oy = (height - vh * s) / 2 - vy * s
    m = (s, 0, 0, s, ox, oy)
    g = _rgb(ground) if ground else None
    base = bytes((*g, 255)) if g else b"\x00\x00\x00\x00"
    img = None
    for path, hexcol in layers:
        col = _rgb(hexcol)
        cov = coverage(_flatten(path, s, m), width, height, samples)
        if img is None:
            if g:
                table = [bytes((*(int(c * a / 255 + d * (255 - a) / 255 + 0.5) for c, d in zip(col, g)), 255))
                         for a in range(256)]
            else:
                table = [bytes((*col, a)) for a in range(256)]
            img = [bytearray(b"".join([table[v] for v in row])) for row in cov]
            continue
        solid = bytes((*col, 255))
        for y, row in enumerate(cov):
            r = img[y]
            for mt in re.finditer(rb"\xff+", row):
                a0, a1 = mt.span()
                r[4 * a0:4 * a1] = solid * (a1 - a0)
            for mt in re.finditer(rb"[\x01-\xfe]", row):
                x = mt.start()
                a = row[x]
                i = 4 * x
                da = r[i + 3] / 255
                sa = a / 255
                oa = sa + da * (1 - sa)
                for c in range(3):
                    r[i + c] = min(255, int((col[c] * sa + r[i + c] * da * (1 - sa)) / oa + 0.5))
                r[i + 3] = min(255, int(oa * 255 + 0.5))
    if img is None:
        return base * (width * height)
    return b"".join(bytes(r) for r in img)


def write_png(path, width, height, rgba):
    """Write 8-bit RGBA bytes as a PNG (stdlib only)."""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)


def _rgb(hexcol):
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def edge_is_round(path, top=True, box=None):
    """True when the top (or bottom) edge of the ink is round or pointed, i.e. a scan 3% inside the ink box covers
    less than ROUND_EDGE_COVER of its width (heuristic, uncalibrated). Such edges get overshoot in lockups."""
    box = box or _bounds(path)
    if not box:
        return False
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    if h <= 0 or w <= 0:
        return False
    y = y0 + 0.03 * h if top else y1 - 0.03 * h
    edges = _edges(_flatten(path, 4.0))
    cover = sum(b - a for a, b in _spans_at(edges, y))
    return cover / w < ROUND_EDGE_COVER


# ---------------------------------------------------------------- fonts

_FONTS = {}


def _font(font_path, location=None):
    key = (os.path.abspath(str(font_path)), tuple(sorted((location or {}).items())))
    rec = _FONTS.get(key)
    if rec:
        return rec
    if not os.path.isfile(str(font_path)):
        raise LogoError(f"font file not found: {font_path}")
    blob = hb.Blob.from_file_path(str(font_path))
    face = hb.Face(blob)
    font = hb.Font(face)
    axes = {}
    try:
        for ax in face.axis_infos:
            axes[ax.tag] = (ax.min_value, ax.default_value, ax.max_value)
    except Exception:  # noqa: BLE001 - static fonts have no axis infos
        axes = {}
    loc = {k: float(v) for k, v in (location or {}).items() if k in axes}
    if loc:
        font.set_variations(loc)
    upm = face.upem

    def top_of(ch):
        gid = font.get_nominal_glyph(ord(ch))
        if not gid:
            return None
        ext = font.get_glyph_extents(gid)
        return (ext.y_bearing, ext.y_bearing + ext.height) if ext and ext.height else None

    cap = top_of("H")
    cap_h = cap[0] if cap else None
    if not cap_h:
        try:
            from fontTools.ttLib import TTFont
            cap_h = TTFont(str(font_path), lazy=True)["OS/2"].sCapHeight or None
        except Exception:  # noqa: BLE001
            cap_h = None
    cap_h = float(cap_h or 0.7 * upm)
    xh = top_of("x")
    o = top_of("O")
    rec = {"font": font, "face": face, "upm": upm, "cap": cap_h, "x_height": float(xh[0]) if xh else None,
           "axes": axes, "location": loc, "path": str(font_path),
           "overshoot_top": ((o[0] - cap_h) / cap_h) if o else DEFAULT_OVERSHOOT,
           "overshoot_bottom": (-o[1] / cap_h) if o else DEFAULT_OVERSHOOT}
    _FONTS[key] = rec
    return rec


def font_axes(font_path):
    """{axis tag: (min, default, max)} of a font file ({} for static fonts)."""
    return dict(_font(font_path)["axes"])


_TURKIC = ("tr", "az", "crh", "tt", "ba", "kk")


def apply_case(text, case="as-is", lang=None):
    """Locale-aware upper/lower casing (Turkic dotted/dotless i). Uses typelib.case_text when installed, so the
    wordmark, specimens and CSS agree; the local fallback follows the same rule."""
    try:
        helper = getattr(importlib.import_module("typelib"), "case_text", None)
    except ImportError:
        helper = None
    if callable(helper):
        try:
            return helper(text, case, lang or "en")
        except ValueError as e:
            raise LogoError(str(e)) from None
    if case in (None, "", "as-is"):
        return text
    primary = (lang or "").replace("_", "-").split("-")[0].lower()
    if case == "upper":
        if primary in _TURKIC:
            text = text.replace("i", "\u0130")
        return text.upper()
    if case == "lower":
        if primary in _TURKIC:
            text = text.replace("I", "\u0131").replace("\u0130", "i")
        return text.lower()
    raise LogoError(f"case must be as-is, upper or lower, got {case!r}")


_LIGATURES = ("liga", "clig", "dlig", "hlig", "rlig")


def _features(features):
    feats = {"kern": True}
    for tag in _LIGATURES:
        if tag != "rlig":  # required ligatures stay on (scripts depend on them)
            feats[tag] = False
    for item in features or []:
        s = str(item).strip()
        if not s:
            continue
        if s.startswith("-"):
            feats[s[1:5]] = False
        elif "=" in s:
            k, v = s.split("=", 1)
            feats[k.strip()[:4]] = int(v)
        else:
            feats[s.lstrip("+")[:4]] = True
    return feats


def _outline_run(text, font_path, location=None, features=None, tracking=0.0, lang=None, per_char=None,
                 origins=None):
    """Shape and outline one line of text. Coordinates: cap height = 100 units, cap top at y=0, baseline at y=100,
    pen origin at x=0. Returns (path, advance, rec). per_char: a dict filled with {character index: glyph path}
    (glyphs grouped by their HarfBuzz cluster), for per-letter wordmark details. origins: a dict filled with
    {character index: [pen x, advance]} in the same units."""
    if not text:
        raise LogoError("wordmark text is empty")
    rec = _font(font_path, location)
    font = rec["font"]
    s = 100.0 / rec["cap"]
    buf = hb.Buffer()
    buf.add_str(text)
    if lang:
        buf.language = str(lang).replace("_", "-")
    buf.guess_segment_properties()
    hb.shape(font, buf, _features(features))
    infos, positions = buf.glyph_infos, buf.glyph_positions
    missing = [text[i.cluster] for i in infos if i.codepoint == 0 and i.cluster < len(text)]
    if missing:
        raise LogoError(f"{os.path.basename(str(font_path))} has no glyph for {''.join(sorted(set(missing)))!r}; "
                        "choose a face that covers the brand's languages")
    track = float(tracking or 0) / 1000.0 * rec["upm"]
    pen = pathops.Path()
    x = 0.0
    for n, (info, pos) in enumerate(zip(infos, positions)):
        glyph = pathops.Path()
        m = (s, 0, 0, -s, (x + pos.x_offset) * s, 100.0 - pos.y_offset * s)
        font.draw_glyph_with_pen(info.codepoint, TransformPen(glyph.getPen(), m))
        pen.addPath(glyph)
        if per_char is not None:
            per_char.setdefault(info.cluster, pathops.Path()).addPath(glyph)
        if origins is not None:
            o = origins.setdefault(info.cluster, [x * s, 0.0])
            o[1] += pos.x_advance * s
        x += pos.x_advance
        last = n == len(infos) - 1
        if not last and infos[n + 1].cluster != info.cluster:
            x += track
    path = pathops.simplify(pen, fix_winding=True, clockwise=False)
    return path, x * s, rec


def _stems(path, y):
    """Vertical stems crossed by the horizontal line y: [[x0, x1]] spans narrower than 35 units."""
    edges = _edges(_flatten(path, 4.0))
    return [[round(a, 2), round(b, 2)] for a, b in _spans_at(edges, y) if b - a < 35.0]


def glyph_table(text, per_char, origins, x_height=None, transform=None):
    """Per-character geometry for model-written details: [{index (1-based, = data-glyph), char, x0, x1 (ink),
    pen_x, advance, cap_top 0, baseline 100, stems: [[x0, x1]] vertical stems}] in wordmark units (cap height 100,
    baseline y=100). transform (a, b, c, d, e, f) maps them into another space (a symbol's 100 grid)."""
    rows = []
    y_stem = 100.0 - 0.5 * (x_height or 50.0)
    for idx in sorted(per_char):
        path = per_char[idx]
        pen_x, adv = (origins or {}).get(idx, [None, None])
        box = _bounds(path)
        row = {"index": idx + 1, "char": text[idx] if idx < len(text) else ""}
        if transform:
            a, _b, _c, d, e, f = transform
            tp = path.transform(*transform)
            tb = _bounds(tp)
            row.update({"x0": round(tb[0], 2) if tb else None, "x1": round(tb[2], 2) if tb else None,
                        "cap_top": round(f, 2), "baseline": round(100.0 * d + f, 2),
                        "stems": _stems(tp, y_stem * d + f) if tb else []})
            if pen_x is not None:
                row.update({"pen_x": round(pen_x * a + e, 2), "advance": round(adv * a, 2)})
        else:
            row.update({"x0": round(box[0], 2) if box else None, "x1": round(box[2], 2) if box else None,
                        "pen_x": round(pen_x, 2) if pen_x is not None else None,
                        "advance": round(adv, 2) if adv is not None else None, "cap_top": 0, "baseline": 100,
                        "stems": _stems(path, y_stem) if box else []})
        rows.append(row)
    return rows


def text_path(text, font_path, location=None, features=None, lang=None, cap=100.0, x=0.0, baseline=100.0,
              anchor="start", tracking=0.0):
    """Outline letters (monogram, glyphs inside a symbol) as SVG path data. Default placement: cap height 100,
    baseline y=100, starting at x=0; `cap`, `x`, `baseline` and `anchor` (start|middle|end, by ink box) place it."""
    path, _adv, _rec = _outline_run(text, font_path, location, features, tracking, lang)
    return path_to_d(_place_glyphs(path, cap, x, baseline, anchor))


def _place_glyphs(path, cap, x, baseline, anchor, with_matrix=False):
    k = cap / 100.0
    placed = path.transform(k, 0, 0, k, 0, baseline - 100.0 * k)
    box = _bounds(placed)
    dx = 0.0
    if box:
        if anchor == "middle":
            dx = x - (box[0] + box[2]) / 2
        elif anchor == "end":
            dx = x - box[2]
        else:
            dx = x - box[0]
        placed = placed.transform(1, 0, 0, 1, dx, 0)
    if with_matrix:
        return placed, (k, 0.0, 0.0, k, dx, baseline - 100.0 * k)
    return placed


def wordmark_svg(text, font_path, location=None, features=None, tracking=0, case="as-is", lang=None,
                 color_role=None, detail_svg=None, glyphs_out=None):
    """HarfBuzz-shaped, outlined, overlap-free wordmark. Returns an SVG document holding one <g id="wordmark">
    with data-cap-height, data-baseline, data-advance (viewBox units, cap height = 100), data-x-height and
    data-overshoot (the font's O-over-H overshoot top/bottom, used by lockup()).
    detail_svg: the model's ownable detail, applied to the outlines (see apply_wordmark_detail); shapes with their
    own data-color stay separate coloured paths inside the group. glyphs_out: a list filled with glyph_table()."""
    shaped = apply_case(text, case, lang)
    per_char, origins = {}, {}
    path, advance, rec = _outline_run(shaped, font_path, location, features, tracking, lang, per_char, origins)
    s = 100.0 / rec["cap"]
    xh = rec["x_height"] * s if rec["x_height"] else None
    if glyphs_out is not None:
        glyphs_out.extend(glyph_table(shaped, per_char, origins, xh))
    coloured = []
    if detail_svg:
        path, coloured = apply_wordmark_detail(per_char, detail_svg, len(shaped))
    box = _union_bounds([_bounds(path)] + [_bounds(p) for _r, _i, p in coloured]) if _bounds(path) else \
        (0, 0, advance, 100)
    vb = f"{_fmt(box[0])} {_fmt(box[1])} {_fmt(box[2] - box[0])} {_fmt(box[3] - box[1])}"
    role = f' data-color="{color_role}"' if color_role else ""
    attrs = (f'data-cap-height="100" data-baseline="100" data-advance="{_fmt(advance)}"'
             f' data-overshoot="{rec["overshoot_top"]:.4f} {rec["overshoot_bottom"]:.4f}"'
             + (f' data-x-height="{_fmt(xh)}"' if xh else "") + f' data-text="{_xml_attr(shaped)}"'
             + (' data-detail="1"' if detail_svg else ""))
    extra = "".join(f'<path data-color="{_xml_attr(r)}" data-parts="wordmark-detail-{_xml_attr(i)}" '
                    f'd="{path_to_d(p)}"/>' for r, i, p in coloured)
    return (f'<svg xmlns="{SVG_NS}" viewBox="{vb}" data-kind="wordmark">'
            f'<g id="wordmark" {attrs}{role}><path d="{path_to_d(path)}"/>{extra}</g></svg>')


def apply_wordmark_detail(per_char, detail_svg, n_chars=None):
    """Apply the model's wordmark detail to outlined letters. Returns (wordmark path, [(role, id, path)]).

    detail_svg is drawn in the wordmark's own coordinates: cap height = 100 units, baseline y = 100, cap top y = 0,
    x from the pen origin (the scale of data-advance; logo/build/wordmark-glyphs.json lists each letter's x range
    and stems). Each top-level element (or <g>, unioned first) is one step, in document order: data-op="union"
    (default) adds the shape, "subtract" cuts it out, "intersect" keeps only the overlap. data-glyph="n" limits
    the step to the n-th character of the cased text (1-based, spaces count). A shape with its own data-color
    (palette role or brand id) is not merged: it stays a separate coloured path on top (the letters are cut under
    it, so colours never stack). No <text>, <image>, filter or foreignObject."""
    r = _Resolver()
    try:
        root = ET.fromstring(detail_svg)
    except ET.ParseError as e:
        raise LogoError(f"wordmark detail SVG is not valid XML: {e}") from None
    glyphs = dict(per_char)
    extra = []
    coloured = []
    ctx = _ctx_child({"m": IDENTITY, "data-color": None, "fill": None}, root)
    for el in root:
        tag = _local(el.tag)
        if not tag or tag in SKIP_TAGS:
            continue
        if tag in FORBIDDEN_TAGS or tag in UNSUPPORTED_TAGS:
            raise LogoError(f"wordmark detail: <{tag}> is not allowed; use primitives with data-op")
        cctx = _ctx_child(ctx, el)
        if tag == "g":
            operand = _union([it.path for it in r.scope(el, cctx)])
        elif tag in SHAPE_TAGS:
            operand = _union([it.path for it in r.shape(el, tag, cctx, cut=True)])
        else:
            raise LogoError(f"wordmark detail: <{tag}> is not supported")
        if r.report["forbidden"] or r.report["errors"]:
            raise LogoError("wordmark detail: " + "; ".join(r.report["errors"] + [
                f"<{t}> is not allowed" for t in r.report["forbidden"]]))
        op = (el.get("data-op") or "union").strip().lower()
        color = (cctx.get("data-color") or "").strip()
        n = el.get("data-glyph")
        if n is not None:
            try:
                idx = int(n) - 1
            except ValueError:
                raise LogoError(f"wordmark detail: data-glyph must be a number, got {n!r}") from None
            if idx not in glyphs:
                raise LogoError(f"wordmark detail: data-glyph={n} is outside the text "
                                f"(1..{n_chars or len(glyphs)})")
            targets = [idx]
        else:
            targets = list(glyphs)
        if op == "union" and color:
            for i in glyphs:  # knock out: the coloured shape sits in a real hole, not on top of ink
                glyphs[i] = _op(glyphs[i], operand, pathops.PathOp.DIFFERENCE)
            extra = [_op(x, operand, pathops.PathOp.DIFFERENCE) for x in extra]
            coloured.append([color, el.get("id") or f"detail-{len(coloured) + 1}", operand])
        elif op == "union":
            if n is not None:
                glyphs[idx] = _union([glyphs[idx], operand])
            else:
                extra.append(operand)
            for c in coloured:
                c[2] = _op(c[2], operand, pathops.PathOp.DIFFERENCE)
        elif op in ("subtract", "intersect"):
            kind = pathops.PathOp.DIFFERENCE if op == "subtract" else pathops.PathOp.INTERSECTION
            for i in targets:
                if not _empty(glyphs[i]):
                    glyphs[i] = _op(glyphs[i], operand, kind)
            if n is None:
                extra = [_op(x, operand, kind) for x in extra]
                for c in coloured:
                    c[2] = _op(c[2], operand, kind)
        else:
            raise LogoError(f"wordmark detail: data-op must be union, subtract or intersect, got {op!r}")
    out = _union(list(glyphs.values()) + extra)
    if _empty(out):
        raise LogoError("wordmark detail removed every letter")
    return out, [(c, i, p) for c, i, p in coloured if not _empty(p)]


def is_wordmark_part(ids):
    """True for parts of the wordmark (letters and coloured wordmark details), False for symbol parts."""
    return bool(ids) and str(ids[0]).startswith("wordmark")


def _xml_attr(s):
    return (s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))


def font_optics(font_path, location=None):
    """Optical amounts measured from the font (references/logo.md): O/H overshoot top and bottom
    (fraction of cap height), o/x overshoot, H crossbar/stem ratio, cap and x height (font units)."""
    rec = _font(font_path, location)
    out = {"cap_height": rec["cap"], "x_height": rec["x_height"], "upm": rec["upm"],
           "overshoot_top": round(rec["overshoot_top"], 4), "overshoot_bottom": round(rec["overshoot_bottom"], 4)}
    font = rec["font"]
    gid_o = font.get_nominal_glyph(ord("o"))
    if gid_o and rec["x_height"]:
        ext = font.get_glyph_extents(gid_o)
        out["x_overshoot_top"] = round((ext.y_bearing - rec["x_height"]) / rec["x_height"], 4)
    gid_h = font.get_nominal_glyph(ord("H"))
    if gid_h:
        try:
            path, _adv, _r = _outline_run("H", font_path, location)
            box = _bounds(path)
            edges = _edges(_flatten(path, 4.0))
            stems = [b - a for a, b in _spans_at(edges, 25.0)]
            vedges = _edges([[(y, x) for x, y in poly] for poly in _flatten(path, 4.0)])
            mid = (box[0] + box[2]) / 2
            bars = [b - a for a, b in _spans_at(vedges, mid)]
            if stems and bars:
                out["stem"] = round(min(stems), 3)
                out["crossbar"] = round(min(bars), 3)
                out["crossbar_stem_ratio"] = round(min(bars) / min(stems), 3)
        except LogoError:
            pass
    return out


# ---------------------------------------------------------------- symbol resolution

class _Item:
    __slots__ = ("ids", "color", "path", "stroke_only", "tags")

    def __init__(self, ids, color, path, stroke_only=False):
        self.ids = list(ids)
        self.color = color
        self.path = path
        self.stroke_only = stroke_only
        self.tags = set(self.ids)  # own ids plus the ids of enclosing groups


def _style(el):
    out = {}
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    return out


_INHERIT = ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "fill-rule",
            "data-color", "data-stroke-color")


def _ctx_child(ctx, el):
    st = _style(el)
    new = dict(ctx)
    for k in _INHERIT:
        v = st.get(k, el.get(k))
        if v is not None:
            new[k] = v
    m, rots = parse_transform(el.get("transform"))
    new["m"] = _mul(ctx["m"], m)
    if rots:
        new["rotations"] = ctx.get("rotations", []) + rots
    return new


_RAW_OK = ("none", "currentcolor", "inherit", "")


def _is_raw(v):
    return v is not None and v.strip().lower() not in _RAW_OK


class _Resolver:
    def __init__(self, font=None, default_color="primary"):
        self.font = font or {}
        self.default_color = default_color
        self.report = {"forbidden": [], "unsupported": [], "raw_fills": [], "strokes": [], "opacity": [],
                       "gradients": [], "fill_rule_ambiguous": [], "overlaps": [], "missing_ids": 0,
                       "radii": [], "weights": [], "angles": [], "elements": [], "glyphs": [], "rotations": [],
                       "has_viewbox": False, "viewbox": None, "errors": [], "glyph_tables": []}
        self.counter = 0

    def run(self, svg_text):
        try:
            root = ET.fromstring(svg_text)
        except ET.ParseError as e:
            raise LogoError(f"symbol SVG is not valid XML: {e}") from None
        if _local(root.tag) != "svg":
            raise LogoError("symbol SVG must have <svg> as its root element")
        vb = root.get("viewBox")
        if vb:
            v = [float(x) for x in re.split(r"[\s,]+", vb.strip()) if x]
            if len(v) != 4 or v[2] <= 0 or v[3] <= 0:
                raise LogoError(f"symbol viewBox {vb!r} must be 'x y width height' with positive size")
            self.report["has_viewbox"] = True
        else:
            v = [0.0, 0.0, _num(root.get("width"), 100.0), _num(root.get("height"), 100.0)]
        self.report["viewbox"] = v
        if root.get("filter") or "filter" in _style(root):
            self.report["forbidden"].append("filter")
        s = 100.0 / max(v[2], v[3])
        tx = (100.0 - v[2] * s) / 2 - v[0] * s
        ty = (100.0 - v[3] * s) / 2 - v[1] * s
        self.root_m = (s, 0, 0, s, tx, ty)
        ctx = _ctx_child({"m": IDENTITY, "data-color": None, "fill": None}, root)
        ctx["m"] = _mul(self.root_m, ctx["m"])
        items = self.scope(root, ctx)
        return items

    def _id(self, el):
        i = el.get("id")
        if not i:
            self.counter += 1
            self.report["missing_ids"] += 1
            return f"{_local(el.tag)}-{self.counter}"
        return i

    def scope(self, parent, ctx):
        items = []
        for el in parent:
            tag = _local(el.tag)
            if not tag or tag in SKIP_TAGS:
                if tag == "defs":
                    for sub in el.iter():
                        t = _local(sub.tag)
                        if t in ("linearGradient", "radialGradient"):
                            self.report["gradients"].append(sub.get("id") or t)
                        elif t in FORBIDDEN_TAGS:
                            self.report["forbidden"].append(t)
                continue
            if tag in FORBIDDEN_TAGS:
                self.report["forbidden"].append(tag)
                continue
            if tag in UNSUPPORTED_TAGS:
                if tag in ("linearGradient", "radialGradient"):
                    self.report["gradients"].append(el.get("id") or tag)
                else:
                    self.report["unsupported"].append(tag)
                continue
            if el.get("filter") or "filter" in _style(el):
                self.report["forbidden"].append("filter")
            for k in ("opacity", "fill-opacity", "stroke-opacity"):
                if el.get(k) is not None or k in _style(el):
                    self.report["opacity"].append(el.get("id") or tag)
            cctx = _ctx_child(ctx, el)
            op = (el.get("data-op") or "union").strip().lower()
            if op not in ("union", "subtract", "intersect"):
                self.report["errors"].append(f"{el.get('id') or tag}: data-op must be union, subtract or intersect")
                op = "union"
            targets = (el.get("data-target") or "").split()
            if tag == "g":
                sub = self.scope(el, cctx)
                if el.get("data-op"):
                    if op == "union":
                        color = self._color(cctx, el, fill=True)
                        shape = _union([it.path for it in sub])
                        self.apply(items, [_Item([self._id(el)], color, shape)], op, targets)
                    else:
                        self.apply(items, [_Item([self._id(el)], None, _union([it.path for it in sub]))], op,
                                   targets)
                else:
                    if el.get("id"):
                        for it in sub:
                            it.tags.add(el.get("id"))
                    self.apply(items, sub, "union", [])
                continue
            if tag not in SHAPE_TAGS:
                self.report["unsupported"].append(tag)
                continue
            new = self.shape(el, tag, cctx, cut=op != "union")
            if op == "union":
                self.apply(items, new, "union", [])
            else:
                self.apply(items, [_Item(new[0].ids if new else [self._id(el)], None,
                                         _union([it.path for it in new]))], op, targets)
        return items

    def _color(self, ctx, el, fill=True):
        key = "data-color" if fill else "data-stroke-color"
        role = ctx.get(key) or (ctx.get("data-color") if not fill else None)
        if role:
            return role.strip()
        raw = ctx.get("fill" if fill else "stroke")
        if _is_raw(raw) and not raw.strip().lower().startswith("url("):
            self.report["raw_fills"].append((el.get("id") or _local(el.tag), raw.strip()))
            return "raw:" + raw.strip()
        if raw and raw.strip().lower().startswith("url("):
            self.report["gradients"].append(el.get("id") or _local(el.tag))
        return self.default_color

    def shape(self, el, tag, ctx, cut=False):
        """Element -> [_Item] (fill part, stroke part). cut=True: a subtract/intersect operand, colour ignored."""
        eid = self._id(el)
        m = ctx["m"]
        sc = _scale_of(m)
        glyphs = el.get("data-glyphs")
        if glyphs is not None:
            if not self.font.get("path"):
                raise LogoError(f"{eid}: data-glyphs needs the set's display font (font_path)")
            base = dict(self.font.get("location") or {})
            loc = dict(base)
            if el.get("data-location"):
                try:
                    loc.update({k: float(v) for k, v in json.loads(el.get("data-location")).items()})
                except (ValueError, TypeError, AttributeError):
                    raise LogoError(f'{eid}: data-location must be JSON like {{"wght": 800}}') from None
            for ax in ("wght", "opsz", "wdth", "GRAD"):
                if el.get(f"data-{ax.lower()}") is not None:
                    loc[ax] = _num(el.get(f"data-{ax.lower()}"))
            fpath = self.font["path"]
            if loc != base:
                changed = {k for k in loc if loc.get(k) != base.get(k)}
                if self.font.get("resolver"):
                    fpath = self.font["resolver"](loc)
                elif not changed <= set(font_axes(fpath)):
                    raise LogoError(f"{eid}: {', '.join(sorted(changed))} needs a variable font or the set's font "
                                    "resolver (brand.py logos provides it)")
            per_char, origins = {}, {}
            path, _adv, rec = _outline_run(glyphs, fpath, loc, self.font.get("features"), 0, self.font.get("lang"),
                                           per_char, origins)
            placed, pm = _place_glyphs(path, _num(el.get("data-cap"), 60.0), _num(el.get("data-x"), 50.0),
                                       _num(el.get("data-baseline"), 80.0), el.get("data-anchor") or "middle",
                                       with_matrix=True)
            self.report["glyphs"].append(eid)
            xh = rec["x_height"] * 100.0 / rec["cap"] if rec["x_height"] else None
            self.report["glyph_tables"].append({"id": eid, "text": glyphs, "location": loc,
                                                "glyphs": glyph_table(glyphs, per_char, origins, xh, _mul(m, pm))})
            return [_Item([eid], None if cut else self._color(ctx, el), _xf(placed, m))]
        b, facts = _element_builder(el, tag)
        self.report["elements"].append({"id": eid, "tag": tag, **{k: v for k, v in facts.items()
                                                                     if k in ("rect",)},
                                        "vertices": [_apply(m, p) for p in facts.get("vertices", [])]})
        for r in facts.get("radii", []):
            self.report["radii"].append((eid, r * sc))
        for (a, _cx, _cy) in ctx.get("rotations", []):
            self.report["angles"].append((eid, a % 180.0))
        verts = facts.get("vertices") or []
        for p0, p1 in zip(verts, verts[1:] + verts[:1] if tag == "polygon" else verts[1:]):
            if p0 != p1:
                q0, q1 = _apply(m, p0), _apply(m, p1)
                self.report["angles"].append((eid, math.degrees(math.atan2(q1[1] - q0[1], q1[0] - q0[0])) % 180.0))
        for rot in ctx.get("rotations", []):
            self.report["rotations"].append((eid, rot))
        out = []
        fill = ctx.get("fill")
        has_fill = tag != "line" and (fill is None or fill.strip().lower() != "none")
        stroke = ctx.get("stroke")
        sw = _num(ctx.get("stroke-width"), 1.0) if stroke is not None else 0.0
        has_stroke = stroke is not None and stroke.strip().lower() != "none" and sw > 0
        evenodd = (ctx.get("fill-rule") or "").strip().lower() == "evenodd"
        if has_fill:
            fp = b.path(close_all=True)
            if evenodd:
                fp.fillType = pathops.FillType.EVEN_ODD
            elif tag in ("path", "polygon", "polyline") and self._ambiguous(fp):
                self.report["fill_rule_ambiguous"].append(eid)
            fp = _xf(pathops.simplify(fp, fix_winding=True, clockwise=False), m)
            out.append(_Item([eid], None if cut else self._color(ctx, el, fill=True), fp))
        if has_stroke:
            cap = {"round": pathops.LineCap.ROUND_CAP, "square": pathops.LineCap.SQUARE_CAP}.get(
                (ctx.get("stroke-linecap") or "butt").strip(), pathops.LineCap.BUTT_CAP)
            join = {"round": pathops.LineJoin.ROUND_JOIN, "bevel": pathops.LineJoin.BEVEL_JOIN}.get(
                (ctx.get("stroke-linejoin") or "miter").strip(), pathops.LineJoin.MITER_JOIN)
            sp = b.path(close_all=False)
            sp.stroke(sw, cap, join, _num(ctx.get("stroke-miterlimit"), 4.0))
            sp.convertConicsToQuads(0.02)  # round caps/joins arrive as conics
            sp = _xf(pathops.simplify(sp, fix_winding=True, clockwise=False), m)
            role = ctx.get("data-stroke-color") or ctx.get("data-color")
            if cut:
                color = None
            elif role:
                color = role.strip()
            elif _is_raw(stroke):
                self.report["raw_fills"].append((eid, stroke.strip()))
                color = "raw:" + stroke.strip()
            else:
                color = self.default_color
            self.report["strokes"].append({"id": eid, "width": sw * sc, "cap": (ctx.get("stroke-linecap") or
                                                                                "butt").strip(),
                                           "fill": has_fill, "color": color})
            self.report["weights"].append((eid, sw * sc))
            out.append(_Item([eid], color, sp, stroke_only=not has_fill))
        return out

    def _ambiguous(self, path):
        """Non-zero and even-odd fills of the authored path differ: a hole that depends on the fill rule."""
        box = _bounds(path)
        if not box:
            return False
        w = max(box[2] - box[0], box[3] - box[1])
        view = (box[0], box[1], w, w)
        a = bitmap(path, 64, view, evenodd=False)
        b = bitmap(path, 64, view, evenodd=True)
        diff = sum(bin(x ^ y).count("1") for x, y in zip(a, b))
        return diff > 4

    def apply(self, items, new, op, targets):
        if op == "union":
            for it in new:
                if _empty(it.path):
                    continue
                for lower in items:
                    if lower.color != it.color and not _empty(lower.path):
                        inter = _op(lower.path, it.path, pathops.PathOp.INTERSECTION)
                        if not _empty(inter) and abs(inter.area) > 0.25:
                            self.report["overlaps"].append({"upper": it.ids[0], "upper_color": it.color,
                                                            "lower": lower.ids[0], "lower_color": lower.color,
                                                            "area": round(abs(inter.area), 2)})
                            lower.path = _op(lower.path, it.path, pathops.PathOp.DIFFERENCE)
                items.append(it)
            return
        shape = _union([it.path for it in new])
        kind = pathops.PathOp.DIFFERENCE if op == "subtract" else pathops.PathOp.INTERSECTION
        hit = False
        for it in items:
            if targets and not set(targets) & set(it.ids):
                continue
            it.path = _op(it.path, shape, kind)
            hit = True
        if not hit:
            self.report["errors"].append(f"{new[0].ids[0] if new else '?'}: data-op={op} has nothing to act on "
                                         "(it must come after the shapes it cuts, inside the same group)")


def _apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _merge_by_color(items):
    order, groups = [], {}
    for it in items:
        if _empty(it.path):
            continue
        if it.color not in groups:
            order.append(it.color)
            groups[it.color] = []
        groups[it.color].append(it)
    out = []
    for color in order:
        its = groups[color]
        path = _union([it.path for it in its])
        if _empty(path):
            continue
        ids = []
        for it in its:
            for i in it.ids:
                if i not in ids:
                    ids.append(i)
        out.append((color, ids, path))
    return out


def _resolve(svg_text, font_path=None, location=None, features=None, lang=None, default_color="primary",
             font_resolver=None):
    r = _Resolver({"path": font_path, "location": location, "features": features, "lang": lang,
                   "resolver": font_resolver}, default_color)
    items = r.run(svg_text)
    return _merge_by_color(items), r.report


def resolve_symbol(svg_text, font_path=None, location=None, features=None, lang=None, default_color="primary",
                   font_resolver=None, report_out=None):
    """Model-authored symbol -> resolved symbol SVG on the 100-unit grid: booleans applied, strokes outlined,
    overlaps removed, one <path> per colour role (data-color kept, data-parts lists the source ids).
    The root carries data-ink (ink box) and data-anchors (on-curve point count).
    data-glyphs elements may pick another instance of the face: data-wght, data-opsz, data-wdth, data-grad or
    data-location='{"wght": 800}' (font_resolver(location) -> font path does the instancing; a variable font
    file works without it). report_out: a dict that receives the resolver report (glyph_tables etc.)."""
    parts, report = _resolve(svg_text, font_path, location, features, lang, default_color, font_resolver)
    if report_out is not None:
        report_out.update(report)
    if report["errors"]:
        raise LogoError("; ".join(report["errors"]))
    if not parts:
        raise LogoError("symbol resolves to nothing visible (every shape was cut away or the SVG is empty)")
    return _parts_svg(parts, (0.0, 0.0, 100.0, 100.0), kind="symbol")


def inspect_symbol(svg_text, font_path=None, location=None, features=None, lang=None, default_color="primary",
                   font_resolver=None):
    """Facts about a model-authored symbol for the audit: forbidden/unsupported elements, raw fills, strokes,
    fill-rule-dependent holes, overlapping colour parts, primitive radii/weights/angles, resolved parts."""
    parts, report = _resolve(svg_text, font_path, location, features, lang, default_color, font_resolver)
    report["parts"] = [{"color": c, "ids": ids, "area": round(abs(p.area), 2), "path": p} for c, ids, p in parts]
    return report


def _merge_painted(parts, paint):
    """Union parts that end up painted the same hex (one-colour versions, roles that resolve alike). Knock-outs
    leave such parts edge to edge; drawn apart they show hairline seams in rasters and vector viewers."""
    order, groups = [], {}
    for r, ids, p in parts:
        key = str(paint.get(r, r)).lower()
        if key not in groups:
            order.append(key)
            groups[key] = (r, [], [])
        groups[key][1].extend(i for i in ids if i not in groups[key][1])
        groups[key][2].append(p)
    return [(r, ids, ps[0] if len(ps) == 1 else _union(ps)) for r, ids, ps in (groups[k] for k in order)]


def _parts_svg(parts, view, kind, extra_attrs="", ground=None, colors=None):
    """parts: [(role, ids, path)] -> SVG text. colors: {role: hex} paints fills (else data-color only)."""
    boxes = [_bounds(p) for _c, _i, p in parts]
    ink = _union_bounds(boxes)
    anchors = sum(anchor_count(p) for _c, _i, p in parts)
    vx, vy, vw, vh = view
    out = [f'<svg xmlns="{SVG_NS}" viewBox="{_fmt(vx)} {_fmt(vy)} {_fmt(vw)} {_fmt(vh)}" data-kind="{kind}" '
           f'data-ink="{" ".join(_fmt(v) for v in ink)}" data-anchors="{anchors}"{extra_attrs}>']
    if ground:
        out.append(f'<rect id="ground" x="{_fmt(vx)}" y="{_fmt(vy)}" width="{_fmt(vw)}" height="{_fmt(vh)}" '
                   f'fill="{ground}"/>')
    for role, ids, path in parts:
        fill = f' fill="{colors[role]}"' if colors and role in colors else ""
        pid = ids[0] if len(ids) == 1 else f"part-{re.sub(r'[^A-Za-z0-9_-]+', '-', str(role))}"
        out.append(f'<path id="{_xml_attr(pid)}" data-color="{_xml_attr(str(role))}" '
                   f'data-parts="{_xml_attr(" ".join(ids))}"{fill} d="{path_to_d(path)}"/>')
    out.append("</svg>")
    return "".join(out)


def parse_parts(svg_text):
    """Read an SVG written by this module (or any SVG of filled paths) -> (viewBox tuple, root attrs,
    [(role, ids, path)]) with group data-color inherited and transforms applied."""
    root = ET.fromstring(svg_text)
    vb = [float(x) for x in re.split(r"[\s,]+", (root.get("viewBox") or "0 0 100 100").strip()) if x]
    parts = []

    def walk(el, role, m, gid):
        for ch in el:
            tag = _local(ch.tag)
            mm, _r = parse_transform(ch.get("transform"))
            m2 = _mul(m, mm)
            r2 = ch.get("data-color") or role
            if tag == "g":
                walk(ch, r2, m2, ch.get("id") or gid)
            elif tag == "path" and ch.get("id") != "ground":
                p = parse_d(ch.get("d") or "").path(close_all=True)
                ids = (ch.get("data-parts") or ch.get("id") or gid or "part").split()
                parts.append((r2, ids, _xf(p, m2)))
            elif tag in SHAPE_TAGS and ch.get("id") != "ground":
                b, _f = _element_builder(ch, tag)
                parts.append((r2, [ch.get("id") or gid or tag], _xf(b.path(close_all=True), m2)))

    walk(root, root.get("data-color"), IDENTITY, None)
    return tuple(vb), dict(root.attrib), parts


# ---------------------------------------------------------------- lockups

def _wordmark_meta(svg_text):
    root = ET.fromstring(svg_text)
    g = None
    for el in root.iter():
        if _local(el.tag) == "g" and el.get("id") == "wordmark":
            g = el
            break
    if g is None:
        raise LogoError("wordmark SVG has no <g id=\"wordmark\">")
    ot, ob = (float(x) for x in (g.get("data-overshoot") or f"{DEFAULT_OVERSHOOT} {DEFAULT_OVERSHOOT}").split())
    return {"cap": float(g.get("data-cap-height", 100)), "baseline": float(g.get("data-baseline", 100)),
            "advance": float(g.get("data-advance", 0)), "overshoot": (ot, ob), "role": g.get("data-color"),
            "text": g.get("data-text", "")}


def lockup(symbol_svg, wordmark_svg, kind="horizontal", optics=None, symbol_scale=None, gap=None):
    """Combine a resolved symbol and a wordmark. Units: the wordmark's cap height (100).
    horizontal: the symbol is symbol_scale cap heights tall (default HORIZONTAL_SYMBOL_CAPS; 1.0 = exactly cap top
    to baseline), centred on the cap-height band; round or pointed symbol edges overshoot by the font's O/H
    overshoot, flat edges sit on the computed line. gap: HORIZONTAL_GAP of the symbol height.
    stacked: symbol STACKED_SYMBOL_CAPS x symbol_scale/1.25 cap heights tall, centred over the wordmark ink.
    symbol_scale and gap are in cap-height units (heuristic defaults, uncalibrated).
    Returns SVG with <g id="symbol"> and <g id="wordmark"> (paths baked, no transforms) and data-band,
    data-symbol-box, data-cap-height, data-baseline on the root."""
    if kind not in ("horizontal", "stacked"):
        raise LogoError(f"lockup kind must be horizontal or stacked, got {kind!r}")
    meta = _wordmark_meta(wordmark_svg)
    _wvb, _wa, wparts = parse_parts(wordmark_svg)
    _svb, _sa, sparts = parse_parts(symbol_svg)
    if not sparts:
        raise LogoError("symbol SVG has no paths")
    sym_union = _union([p for _r, _i, p in sparts])
    sbox = _bounds(sym_union)
    wbox = _union_bounds([_bounds(p) for _r, _i, p in wparts])
    cap = meta["cap"]
    band_top, band_bottom = meta["baseline"] - cap, meta["baseline"]
    if isinstance(optics, dict):
        optics = (optics.get("overshoot_top", DEFAULT_OVERSHOOT), optics.get("overshoot_bottom", DEFAULT_OVERSHOOT))
    ot, ob = optics if optics else meta["overshoot"]
    round_top = edge_is_round(sym_union, True, sbox)
    round_bottom = edge_is_round(sym_union, False, sbox)
    sh = sbox[3] - sbox[1]
    if kind == "horizontal":
        symbol_scale = HORIZONTAL_SYMBOL_CAPS if symbol_scale is None else symbol_scale
        target = cap * symbol_scale
        mid = (band_top + band_bottom) / 2
        top = mid - target / 2 - (ot * cap if round_top else 0.0)
        bottom = mid + target / 2 + (ob * cap if round_bottom else 0.0)
        k = (bottom - top) / sh
        g = target * HORIZONTAL_GAP if gap is None else gap * cap
        dx = wbox[0] - g - sbox[2] * k
        dy = top - sbox[1] * k
    else:
        rel = 1.0 if symbol_scale is None else symbol_scale / HORIZONTAL_SYMBOL_CAPS
        target = cap * STACKED_SYMBOL_CAPS * rel
        k = target / sh
        g = cap * STACKED_GAP_CAPS if gap is None else gap * cap
        cx = (wbox[0] + wbox[2]) / 2
        dx = cx - (sbox[0] + sbox[2]) / 2 * k
        dy = band_top - g - sbox[3] * k
    m = (k, 0, 0, k, dx, dy)
    placed = [(r, ids, p.transform(*m)) for r, ids, p in sparts]
    pbox = _union_bounds([_bounds(p) for _r, _i, p in placed])
    ink = _union_bounds([pbox, wbox])
    out = [f'<svg xmlns="{SVG_NS}" viewBox="{_fmt(ink[0])} {_fmt(ink[1])} {_fmt(ink[2] - ink[0])} '
           f'{_fmt(ink[3] - ink[1])}" data-kind="lockup-{kind}" data-cap-height="{_fmt(cap)}" '
           f'data-baseline="{_fmt(meta["baseline"])}" data-band="{_fmt(band_top)} {_fmt(band_bottom)}" '
           f'data-symbol-box="{" ".join(_fmt(v) for v in pbox)}" '
           f'data-overshoot-applied="{int(round_top)} {int(round_bottom)}" '
           f'data-ink="{" ".join(_fmt(v) for v in ink)}">', '<g id="symbol">']
    for r, ids, p in placed:
        out.append(f'<path data-color="{_xml_attr(str(r))}" data-parts="{_xml_attr(" ".join(ids))}" '
                   f'd="{path_to_d(p)}"/>')
    role = f' data-color="{meta["role"]}"' if meta["role"] else ""
    out.append(f'</g><g id="wordmark" data-cap-height="{_fmt(cap)}" data-baseline="{_fmt(meta["baseline"])}" '
               f'data-advance="{_fmt(meta["advance"])}"{role}>')
    for r, ids, p in wparts:
        col = f' data-color="{_xml_attr(str(r))}" data-parts="{_xml_attr(" ".join(ids))}"' \
            if r and r != meta["role"] else ""
        out.append(f'<path{col} d="{path_to_d(p)}"/>')
    out.append("</g></svg>")
    return "".join(out)


# ---------------------------------------------------------------- colours

def resolve_color(palette, ref, mode="light"):
    """Palette role key (resolved in `mode`), brand id or extended id -> hex; None when unknown."""
    if not ref or not palette:
        return None
    if ref.startswith("raw:"):
        return None
    modes = palette.get("modes") or {}
    m = modes.get(mode) or {}
    if ref in m:
        return m[ref]
    for b in palette.get("brand", []) or []:
        if b.get("id") == ref:
            return b.get("hex")
    for e in palette.get("extended", []) or []:
        if e.get("id") == ref:
            if isinstance(e.get("modes"), dict) and mode in e["modes"]:
                v = e["modes"][mode]
                return v if isinstance(v, str) else v.get("hex") if isinstance(v, dict) else e.get("hex")
            return e.get("hex")
    return None


def _raw_hex(ref):
    if ref and ref.startswith("raw:"):
        v = ref[4:].strip().lower()
        named = {"white": "#ffffff", "black": "#000000"}
        if v in named:
            return named[v]
        if re.match(r"^#[0-9a-f]{3}([0-9a-f]{3})?$", v):
            return v if len(v) == 7 else "#" + "".join(c * 2 for c in v[1:])
    return None


def _paint(parts, palette, mode, one_color=None):
    """{role: hex} for parts in `mode`; one_color paints every part with that hex."""
    out = {}
    for role, _ids, _p in parts:
        if one_color:
            out[role] = one_color
        else:
            out[role] = resolve_color(palette, role, mode) or _raw_hex(role) or "#ff00ff"
    return out


# ---------------------------------------------------------------- fonts for an identity

def font_for(identity, set_dir, role="display", location=None):
    """Font file for a type role of the identity: type.<role>.file (relative to the set folder or absolute), else
    typelib.resolve_font(family, location, source, file) when typelib is installed. Raises LogoError otherwise."""
    ty = identity.get("type") or {}
    face = ty.get(role) or ty.get("display")
    if not face:
        raise LogoError(f"type.{role} is missing; the wordmark needs a font")
    loc = dict(location if location is not None else _face_location(identity, role))
    f = face.get("file")
    if f:
        p = f if os.path.isabs(f) else os.path.normpath(os.path.join(set_dir, f))
        if not os.path.isfile(p):
            raise LogoError(f"type.{role}.file not found: {p}")
        try:
            typelib = importlib.import_module("typelib")
            if hasattr(typelib, "resolve_font"):
                return str(typelib.resolve_font(face.get("family"), loc, face.get("source"), p))
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 - fall back to the file as given (variations applied by HarfBuzz)
            pass
        return p
    try:
        typelib = importlib.import_module("typelib")
    except ImportError:
        raise LogoError(f"type.{role}: no font file given and typelib (font resolver) is not installed; "
                        f"set type.{role}.file to a font path") from None
    try:
        return str(typelib.resolve_font(face.get("family"), loc, face.get("source"), None))
    except Exception as e:  # noqa: BLE001 - report the resolver's message
        raise LogoError(f"type.{role}: could not resolve {face.get('family')!r}: {e}") from None


def font_resolver(identity, set_dir, font_path=None):
    """location -> font file for the set's wordmark face (other weights/optical sizes of the same family), used by
    data-glyphs data-wght/data-opsz/... Every logo build and audit path passes it to the symbol resolver."""
    role = ((identity.get("logo") or {}).get("wordmark") or {}).get("role", "display")

    def resolve(location):
        if font_path and set(location) <= set(font_axes(font_path)):
            return font_path
        return font_for(identity, set_dir, role, location)
    return resolve


def _face_location(identity, role):
    ty = identity.get("type") or {}
    face = ty.get(role) or ty.get("display") or {}
    loc = dict(face.get("location") or {})
    wm = ((identity.get("logo") or {}).get("wordmark") or {})
    if wm.get("location"):
        loc.update(wm["location"])
    elif face.get("weights") and "wght" not in loc:
        loc["wght"] = face["weights"][0]
    return loc


# ---------------------------------------------------------------- variants

def _write(path, text):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _pad_view(box, pad):
    return (box[0] - pad, box[1] - pad, box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad)


def _square_view(box, margin_frac):
    w, h = box[2] - box[0], box[3] - box[1]
    side = max(w, h) / (1 - 2 * margin_frac)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (cx - side / 2, cy - side / 2, side, side)


def _png_size(view, long_edge):
    vw, vh = view[2], view[3]
    if vw >= vh:
        return long_edge, max(1, int(round(long_edge * vh / vw)))
    return max(1, int(round(long_edge * vw / vh))), long_edge


def _inset(path, amount):
    """Shrink a filled outline by `amount` units (light-on-dark irradiation correction)."""
    if amount <= 0 or _empty(path):
        return path
    ring = pathops.Path(path)
    ring.stroke(2 * amount, pathops.LineCap.BUTT_CAP, pathops.LineJoin.MITER_JOIN, 4)
    ring.convertConicsToQuads(0.02)  # round caps/joins arrive as conics
    out = _op(path, ring, pathops.PathOp.DIFFERENCE)
    return path if _empty(out) else out


def _symbol_of(identity, set_dir, font_path, loc, features, lang, small=False, resolver=None, report_out=None):
    lg = identity.get("logo") or {}
    default = (lg.get("colors") or {}).get("symbol") or "primary"
    name = "symbol-small.svg" if small else None
    if small:
        p = os.path.join(set_dir, "logo", name)
        if not os.path.isfile(p):
            return None, None
    else:
        rel = lg.get("symbol")
        if not rel:
            return None, None
        p = rel if os.path.isabs(rel) else os.path.join(set_dir, rel)
        if not os.path.isfile(p):
            raise LogoError(f"logo.symbol file not found: {p}")
    with open(p, encoding="utf-8") as fh:
        src = fh.read()
    return src, resolve_symbol(src, font_path, loc, features, lang, default, resolver, report_out)


def _letters_symbol(letters, font_path, loc, features, lang, role, table_out=None):
    per_char, origins = {}, {}
    raw, _adv, rec = _outline_run(letters, font_path, loc, features, 0, lang, per_char, origins)
    path, pm = _place_glyphs(raw, 56.0, 50.0, 78.0, "middle", with_matrix=True)
    box = _bounds(path)
    if box:  # centre the ink optically on the grid (vertical centre of the ink box)
        dy = 50 - (box[1] + box[3]) / 2
        path = path.transform(1, 0, 0, 1, 0, dy)
        pm = (pm[0], pm[1], pm[2], pm[3], pm[4], pm[5] + dy)
    if table_out is not None:
        xh = rec["x_height"] * 100.0 / rec["cap"] if rec["x_height"] else None
        table_out.extend(glyph_table(letters, per_char, origins, xh, pm))
    return _parts_svg([(role, ["monogram"], path)], (0.0, 0.0, 100.0, 100.0), kind="symbol")


# ---------------------------------------------------------------- kept logos (components.logo.mode = keep)

KEPT_DARK_RATIO = 1.25   # a dark file whose aspect ratio differs more than this is another lockup (heuristic)
KEPT_SYMBOL_IDS = re.compile(r"symbol|mark|icon|emblem|glyph|monogram", re.I)
KEPT_WORD_IDS = re.compile(r"word|type|text|name|letter", re.I)
_DARK_MQ = re.compile(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)")


def _aspect(path, text=None):
    """width / height of an SVG (viewBox or width/height) or PNG; None when unknown."""
    try:
        if path.lower().endswith(".png"):
            with open(path, "rb") as fh:
                head = fh.read(24)
            w, h = struct.unpack(">II", head[16:24])
            return w / h if h else None
        if text is None:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        m = re.search(r'viewBox\s*=\s*["\']\s*[-\d.eE+]+[\s,]+[-\d.eE+]+[\s,]+([\d.eE+]+)[\s,]+([\d.eE+]+)', text)
        if m:
            return float(m.group(1)) / float(m.group(2))
        w = re.search(r'<svg[^>]*\swidth\s*=\s*["\']([\d.]+)', text)
        h = re.search(r'<svg[^>]*\sheight\s*=\s*["\']([\d.]+)', text)
        return float(w.group(1)) / float(h.group(1)) if w and h else None
    except (OSError, ValueError, ZeroDivisionError, struct.error):
        return None


def kept_source(identity, set_dir):
    """Absolute path of components.logo.source (relative to the work folder, or the set folder), or None."""
    src = ((identity.get("components") or {}).get("logo") or {}).get("source")
    if not src:
        return None
    if os.path.isabs(src):
        return src if os.path.isfile(src) else None
    work = os.path.dirname(os.path.dirname(os.path.abspath(set_dir)))
    for base in (work, set_dir):
        p = os.path.join(base, src)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def kept_sources(identity, set_dir):
    """The kept logo and its dark-ground version, read only: {src, text (SVG or None), dark_file, dark_text,
    dark_from, dark_skipped}. Dark: logo-dark.svg next to the source or work/site/logo-dark.svg (same aspect),
    else the source's prefers-color-scheme: dark rules, else None (callers derive one colour)."""
    src = kept_source(identity, set_dir)
    if not src:
        return None
    out = {"src": src, "text": None, "dark_file": None, "dark_text": None, "dark_from": None, "dark_skipped": []}
    if src.lower().endswith(".svg"):
        with open(src, encoding="utf-8", errors="replace") as fh:
            out["text"] = fh.read()
    work = os.path.dirname(os.path.dirname(os.path.abspath(set_dir)))
    ratio = _aspect(src, out["text"])
    for cand in (os.path.join(os.path.dirname(src), "logo-dark.svg"), os.path.join(work, "site", "logo-dark.svg")):
        if not os.path.isfile(cand) or os.path.abspath(cand) == src:
            continue
        r = _aspect(cand)
        if ratio and r and max(r, ratio) / min(r, ratio) > KEPT_DARK_RATIO:
            out["dark_skipped"].append(f"{os.path.basename(cand)}: aspect {r:.2f} vs {ratio:.2f} "
                                       "(a different lockup, e.g. symbol only)")
            continue
        out["dark_file"], out["dark_from"] = cand, os.path.basename(cand)
        with open(cand, encoding="utf-8", errors="replace") as fh:
            out["dark_text"] = fh.read()
        break
    if not out["dark_file"] and out["text"] and _DARK_MQ.search(out["text"]):
        out["dark_text"] = out["text"]
        out["dark_from"] = "prefers-color-scheme rules in the source"
    return out


def _css_blocks(css, dark):
    """Flatten a stylesheet into [(selector, {prop: value})]; @media (prefers-color-scheme: dark) blocks are kept
    only when dark=True (after the base rules), other at-rules are dropped."""
    rules, i, n = [], 0, len(css)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    n = len(css)
    tail = []
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if head.startswith("@"):
            if dark and _DARK_MQ.match(head):
                tail.extend(_css_blocks(body, False))
        else:
            decls = {}
            for d in body.split(";"):
                if ":" in d:
                    a, b = d.split(":", 1)
                    decls[a.strip().lower()] = b.replace("!important", "").strip()
            for sel in head.split(","):
                rules.append((sel.strip(), decls))
        i = k
    return rules + tail


def _match(sel, el):
    m = re.fullmatch(r"([A-Za-z][\w-]*)?((?:[.#][\w-]+)*)", sel)
    if not m:
        return False
    tag, rest = m.group(1), m.group(2)
    if tag and _local(el.tag) != tag:
        return False
    classes = set((el.get("class") or "").split())
    for kind, name in re.findall(r"([.#])([\w-]+)", rest):
        if kind == "." and name not in classes:
            return False
        if kind == "#" and el.get("id") != name:
            return False
    return bool(tag or rest)


def _inline_css(svg_text, dark=False):
    """Inline simple <style> rules (tag, .class, #id) into style attributes so the resolver sees the colours."""
    root = ET.fromstring(svg_text)
    css = "".join((el.text or "") for el in root.iter() if _local(el.tag) == "style")
    rules = _css_blocks(css, dark) if css else []
    for parent in list(root.iter()):
        for ch in list(parent):
            if _local(ch.tag) == "style":
                parent.remove(ch)
    if rules:
        for el in root.iter():
            decl = {}
            for sel, d in rules:
                if _match(sel, el):
                    decl.update(d)
            if decl:
                decl.update(_style(el))
                el.set("style", ";".join(f"{k}:{v}" for k, v in decl.items()))
    return ET.tostring(root, encoding="unicode")


def import_kept_svg(svg_text, dark=False):
    """A kept logo SVG -> ([(role 'raw:#hex', ids, path)], symbol_parts or None, notes). Colours stay the
    source's own (CSS classes, style and fill attributes); dark=True applies its prefers-color-scheme: dark rules.
    A full-bleed background rect is dropped. symbol_parts: the parts of elements whose id (or a group id) names
    a symbol/mark/icon, else the left cluster of a wide logo, else None."""
    text = _inline_css(svg_text, dark)
    r = _Resolver({}, default_color="raw:#000000")
    items = r.run(text)
    notes = []
    if r.report["forbidden"]:
        notes.append(f"kept logo has {', '.join(sorted(set(r.report['forbidden'])))}: left out of the variants")
    if r.report["unsupported"] or r.report["gradients"]:
        notes.append("kept logo uses " + ", ".join(sorted(set(r.report["unsupported"] + ["gradient"] * bool(
            r.report["gradients"])))) + ": approximated")
    for it in items:
        if it.color and not it.color.startswith("raw:"):
            it.color = "raw:#000000"
        hx = None
        if it.color:
            try:
                import colorlib
                hx = colorlib.normalize_hex(colorlib.to_hex(colorlib.parse_color(it.color[4:])))
            except Exception:  # noqa: BLE001 - unknown colour keyword: keep black
                hx = "#000000"
        it.color = f"raw:{hx}" if hx else "raw:#000000"
    vb = r.report["viewbox"] or [0, 0, 100, 100]
    k = 100.0 / max(vb[2], vb[3])
    frame = ((100 - vb[2] * k) / 2, (100 - vb[3] * k) / 2, (100 + vb[2] * k) / 2, (100 + vb[3] * k) / 2)
    live = [it for it in items if not _empty(it.path)]
    if len(live) > 1:
        b = _bounds(live[0].path)
        fw, fh = frame[2] - frame[0], frame[3] - frame[1]
        if b and (b[2] - b[0]) >= 0.98 * fw and (b[3] - b[1]) >= 0.98 * fh:
            notes.append("full-bleed background shape dropped")
            live = live[1:]
    parts = _merge_by_color(live)
    if not parts:
        raise LogoError("kept logo has no filled shapes this build can read (text-only or raster inside SVG)")
    sym = [it for it in live if any(KEPT_SYMBOL_IDS.search(t) and not KEPT_WORD_IDS.search(t) for t in it.tags)]
    if not sym:
        boxes = sorted(((_bounds(it.path), it) for it in live if _bounds(it.path)), key=lambda t: t[0][0])
        total = _union_bounds([b for b, _ in boxes])
        width = total[2] - total[0]
        if boxes and width > 2 * (total[3] - total[1]):
            cluster, right = [], boxes[0][0][2]
            for b, it in boxes:
                if cluster and b[0] - right > 0.03 * width:
                    break
                cluster.append(it)
                right = max(right, b[2])
            if len(cluster) < len(boxes) and right - total[0] < 0.45 * width:
                sym = cluster
    return parts, (_merge_by_color(sym) if sym else None), notes


def _contrasting(ground, candidates):
    import colorlib
    cands = [c for c in candidates if c]
    return max(cands, key=lambda c: colorlib.contrast_ratio(c, ground))


def _kept_variants(identity, palette, out_dir, set_dir):
    """variants() for a kept logo: the source is read (never written), outlined as it is, and every version is
    re-coloured from it. master-primary.svg / master-kept-source.svg are byte copies of the source."""
    import colorlib
    info = kept_sources(identity, set_dir)
    if not info:
        raise LogoError("logo is kept but components.logo.source is not a file")
    if not info["text"]:
        raise LogoError(f"kept logo {os.path.basename(info['src'])} is raster: variants need an SVG source")
    os.makedirs(out_dir, exist_ok=True)
    parts, sym_parts, notes = import_kept_svg(info["text"])
    dark_parts = None
    if info["dark_text"]:
        dark_parts, _s, n2 = import_kept_svg(info["dark_text"], dark=True)
        notes += [n for n in n2 if n not in notes]
    modes = (palette or {}).get("modes") or {}
    light, dark = modes.get("light") or {}, modes.get("dark") or {}
    ink_dark = light.get("text") or "#111111"
    ink_light = dark.get("text") or "#ffffff"
    lg = identity.get("logo") or {}
    manifest = {"schema": "brand-identity/logo-build@1", "set": (identity.get("set") or {}).get("id"),
                "kept": True, "source": info["src"], "dark_from": info["dark_from"] or "derived one colour (light)",
                "dark_skipped": info["dark_skipped"], "notes": notes, "type": lg.get("type") or "kept",
                "font": {}, "masters": {}, "versions": {}, "sources": {"kept": {"sha256": _sha(info["text"])}},
                "primary": "kept", "anchors": {}}
    for name, text in (("master-primary.svg", info["text"]), ("master-kept-source.svg", info["text"])):
        _write(os.path.join(out_dir, name), text)
    manifest["masters"] = {"primary": "master-primary.svg", "kept-source": "master-kept-source.svg"}
    if info["dark_text"]:
        dp = info["dark_file"] or os.path.join(out_dir, "kept-dark.svg")
        if not info["dark_file"]:
            _write(dp, _DARK_MQ.sub("@media all", info["dark_text"]))
        manifest["dark"] = dp
    if sym_parts:
        sym_svg = _parts_svg(sym_parts, (0.0, 0.0, 100.0, 100.0), kind="symbol")
        _write(os.path.join(out_dir, "master-symbol.svg"), sym_svg)
        manifest["masters"]["symbol"] = "master-symbol.svg"
    pbox = _union_bounds([_bounds(p) for _r, _i, p in parts])
    view = _pad_view(pbox, 0.12 * (pbox[3] - pbox[1]))

    def version(name, use, view, paint, mode, ground=None, ground_role=None, one_color=False, png_px=None):
        use = _merge_painted(use, paint)
        svg = _parts_svg(use, view, kind=name, ground=ground, colors=paint,
                         extra_attrs=f' data-mode="{mode}" data-kept="1"' +
                         (f' data-ground="{ground_role}"' if ground_role else ""))
        _write(os.path.join(out_dir, f"{name}.svg"), svg)
        w, h = (png_px, png_px) if isinstance(png_px, int) else _png_size(view, DELIVERY_PX)
        write_png(os.path.join(out_dir, f"{name}.png"), w, h,
                  rasterize([(p, paint[r]) for r, _i, p in use], w, h, view, ground,
                            samples=8 if max(w, h) <= 64 else 4))
        manifest["versions"][name] = {
            "svg": f"{name}.svg", "png": f"{name}.png", "size": [w, h], "mode": mode, "ground": ground,
            "ground_role": ground_role, "one_color": one_color, "device": None,
            "parts": [{"role": r, "ids": ids, "hex": paint[r], "area": round(abs(p.area), 2)} for r, ids, p in use]}

    def fit(use, ground, extra=()):
        """The source colours when every part reaches 3:1 on `ground`, else one contrasting colour."""
        paint = {r: r[4:] for r, _i, _p in use}
        if all(colorlib.contrast_ratio(paint[r], ground) >= DEVICE_CONTRAST for r, _i, _p in use):
            return paint, False
        c = _contrasting(ground, list(extra) + [ink_dark, ink_light, "#ffffff", "#000000"])
        return {r: c for r, _i, _p in use}, True

    lbg, dbg = light.get("background") or "#ffffff", dark.get("background") or "#111111"
    version("full-color", parts, view, {r: r[4:] for r, _i, _p in parts}, "light")
    dk = dark_parts or parts
    paint, mono = fit(dk, dbg) if dark_parts else ({r: ink_light for r, _i, _p in parts}, True)
    version("full-color-dark", dk, view, paint, "dark", one_color=mono)
    version("one-color-dark", parts, view, {r: ink_dark for r, _i, _p in parts}, "light", one_color=True)
    version("one-color-light", parts, view, {r: ink_light for r, _i, _p in parts}, "dark", one_color=True)
    grounds = [("light", "background", None), ("dark", "background", None)]
    for role, on in (("primary", "onPrimary"), ("accent", "onAccent")):
        if light.get(role):
            grounds.append(("light", role, on))
    for mode, role, on in grounds:
        g = (modes.get(mode) or {}).get(role)
        if not g:
            continue
        use = dk if mode == "dark" else parts
        on_hex = (modes.get(mode) or {}).get(on) if on else None
        paint, mono = fit(use, g, [on_hex] if on_hex else [])
        version(f"on-{mode}-{role}", use, view, paint, mode, ground=g, ground_role=role, one_color=mono)
    manifest["grounds"] = [{"mode": m, "ground": r, "on": "source colours if >= 3:1, else one colour"}
                           for m, r, _o in grounds]
    icon = sym_parts or parts
    manifest["icon_from"] = "kept symbol parts" if sym_parts else "whole kept logo, fitted"
    ibox = _union_bounds([_bounds(p) for _r, _i, p in icon])
    fview = _square_view(ibox, FAVICON_MARGIN)
    for px in (16, 32, 48):
        paint, _m = fit(icon, lbg)
        version(f"favicon-{px}", icon, fview, paint, "light", png_px=px)
    for px in (16, 32):
        version(f"small-{px}-one-color-dark", icon, fview, {r: ink_dark for r, _i, _p in icon}, "light",
                one_color=True, png_px=px)
        version(f"small-{px}-one-color-light", icon, fview, {r: ink_light for r, _i, _p in icon}, "dark",
                ground=dbg, ground_role="background", one_color=True, png_px=px)
    if sym_parts:
        sb = _union_bounds([_bounds(p) for _r, _i, p in sym_parts])
        version("symbol-only", sym_parts, _pad_view(sb, 0.25 * max(sb[2] - sb[0], sb[3] - sb[1])),
                {r: r[4:] for r, _i, _p in sym_parts}, "light")
    tile = light.get("primary") or lbg
    side = max(ibox[2] - ibox[0], ibox[3] - ibox[1]) / APP_ICON_CONTENT
    cx, cy = (ibox[0] + ibox[2]) / 2, (ibox[1] + ibox[3]) / 2 + APP_ICON_LIFT * side
    paint, _m = fit(icon, tile, [light.get("onPrimary")])
    version("app-icon-512", icon, (cx - side / 2, cy - side / 2, side, side), paint, "light", ground=tile,
            ground_role="primary", one_color=_m, png_px=512)
    manifest["app_icon"] = {"tile": "primary", "source": manifest["icon_from"]}
    _write(os.path.join(out_dir, "manifest.json"), json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def favicon_letter(text):
    """Letter for a wordmark-only favicon: the first letter of the first non-generic word ("Domaine Fauvel" -> "F",
    "The Studio Nine" -> "N"); the first letter of the text when every word is generic."""
    words = [w for w in re.split(r"[\s\-_/·&+]+", text or "") if w]
    for w in words:
        if w.strip(".,'’").lower() not in GENERIC_WORDS:
            letters = [c for c in w if c.isalnum()]
            if letters:
                return letters[0]
    letters = [c for c in (text or "") if c.isalnum()]
    return letters[0] if letters else "?"


def _device_parts(kind, parts, paint, ground, mode, palette, symbol_only):
    """Tile or outline behind the symbol parts that miss DEVICE_CONTRAST on `ground` (logo.device). Returns
    ([(role, ids, path)], {hex}, info) or ([], {}, None) when nothing is needed or possible."""
    import colorlib
    sym = [(r, ids, p) for r, ids, p in parts if symbol_only or not is_wordmark_part(ids)]
    if not sym or not ground:
        return [], {}, None
    weak = [(r, p) for r, _i, p in sym if colorlib.contrast_ratio(paint[r], ground) < DEVICE_CONTRAST]
    if not weak:
        return [], {}, None
    union = _union([p for _r, _i, p in sym])
    box = _bounds(union)
    if not box:
        return [], {}, None
    side = max(box[2] - box[0], box[3] - box[1])
    m = ((palette or {}).get("modes") or {}).get(mode) or {}
    cands = [(r, paint.get(r) or m.get(r)) for r in DEVICE_ROLES if (paint.get(r) or m.get(r))]
    if kind == "tile":
        # the tile must carry every symbol part: maximise the weakest part's contrast on it
        best = max(cands, key=lambda c: min(colorlib.contrast_ratio(paint[r], c[1]) for r, _i, _p in sym))
        pad = DEVICE_TILE_PAD * side
        b = _Builder()
        w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
        rad = DEVICE_TILE_RADIUS * max(w, h)
        _rect(b, box[0] - pad, box[1] - pad, w, h, min(rad, w / 2), min(rad, h / 2))
        path = b.path(close_all=True)
        path = _op(path, union, pathops.PathOp.DIFFERENCE)  # no stacking: the tile is cut under the symbol
    else:
        best = max(cands, key=lambda c: colorlib.contrast_ratio(c[1], ground))
        ring = pathops.Path(union)
        ring.stroke(2 * DEVICE_OUTLINE * side, pathops.LineCap.BUTT_CAP, pathops.LineJoin.ROUND_JOIN, 4)
        ring.convertConicsToQuads(0.02)  # round caps/joins arrive as conics
        path = _op(_union([ring]), union, pathops.PathOp.DIFFERENCE)
    role, hexcol = best
    return [(role, [f"device-{kind}"], path)], {role: hexcol}, {"kind": kind, "role": role, "hex": hexcol}


def variants(identity, palette, out_dir, set_dir=None, font_path=None):
    """Build every logo version of one set into out_dir (normally sets/X/logo/build/): masters (role-tagged),
    full colour (light and dark mode), one colour dark and light, on each main ground (light/dark background,
    primary, accent), symbol only, favicon 16/32/48 (from symbol-small.svg when present), app icon 512 with safe
    area. SVG + PNG for each. Roles resolve in the mode of the ground they sit on. Writes manifest.json and
    optics.json; returns the manifest dict. A kept logo (components.logo.mode keep, or none with a source) is
    re-coloured from its source instead (see _kept_variants); the source file is never written."""
    set_dir = set_dir or os.path.dirname(os.path.dirname(os.path.abspath(out_dir)))
    os.makedirs(out_dir, exist_ok=True)
    comp = (identity.get("components") or {}).get("logo") or {}
    if comp.get("mode") == "keep" or (comp.get("mode") == "none" and comp.get("source")):
        return _kept_variants(identity, palette, out_dir, set_dir)
    lg = identity.get("logo") or {}
    ltype = lg.get("type") or "wordmark"
    langs = ((identity.get("brand") or {}).get("languages") or ["en"])
    lang = langs[0]
    colors = lg.get("colors") or {}
    wm_cfg = lg.get("wordmark") or {}
    role_key = wm_cfg.get("role", "display")
    ty = identity.get("type") or {}
    face = ty.get(role_key) or ty.get("display") or {}
    loc = _face_location(identity, role_key)
    fpath = font_path or font_for(identity, set_dir, role_key, loc)
    features = list(face.get("features") or []) + list(wm_cfg.get("features") or [])
    tracking = wm_cfg.get("tracking", face.get("tracking", 0))
    case = wm_cfg.get("case", face.get("case", "as-is"))
    manifest = {"schema": "brand-identity/logo-build@1", "set": (identity.get("set") or {}).get("id"),
                "type": ltype, "font": {"path": fpath, "role": role_key, "location": loc, "license":
                                        face.get("license"), "family": face.get("family")},
                "masters": {}, "versions": {}, "small": {}, "sources": {}}
    optics = font_optics(fpath, loc)
    _write(os.path.join(out_dir, "optics.json"), json.dumps(optics, indent=2) + "\n")
    manifest["optics"] = optics

    # masters
    resolver = font_resolver(identity, set_dir, font_path)

    sym_rep, small_rep, mono_table = {}, {}, []
    sym_src, symbol = _symbol_of(identity, set_dir, fpath, loc, features, lang, resolver=resolver,
                                 report_out=sym_rep)
    small_src, small = _symbol_of(identity, set_dir, fpath, loc, features, lang, small=True, resolver=resolver,
                                  report_out=small_rep)
    if ltype == "monogram" and symbol is None:
        letters = ((lg.get("monogram") or {}).get("letters") or "")
        symbol = _letters_symbol(apply_case(letters, case, lang), fpath, loc, features, lang,
                                 colors.get("symbol") or "primary", mono_table)
    wordmark = None
    detail = None
    dp = os.path.join(set_dir, "logo", "wordmark-detail.svg")
    if os.path.isfile(dp):
        with open(dp, encoding="utf-8") as fh:
            detail = fh.read()
        manifest["sources"]["wordmark_detail"] = {"sha256": _sha(detail)}
    if ltype in ("wordmark", "symbol+wordmark") or (ltype == "monogram" and wm_cfg.get("text")):
        if not wm_cfg.get("text"):
            raise LogoError("logo.wordmark.text is required for this logo type")
        wm_glyphs = []
        wordmark = wordmark_svg(wm_cfg["text"], fpath, loc, features, tracking, case, lang,
                                color_role=colors.get("wordmark") or "text", detail_svg=detail, glyphs_out=wm_glyphs)
        gp = os.path.join(out_dir, "wordmark-glyphs.json")
        _write(gp, json.dumps({"units": "wordmark: cap height 100, cap top y=0, baseline y=100, x from the pen "
                                        "origin; index = data-glyph", "text": _wordmark_meta(wordmark)["text"],
                               "glyphs": wm_glyphs}, indent=1, ensure_ascii=False) + "\n")
        manifest["glyphs"] = {"wordmark": os.path.basename(gp)}
    if symbol is None and ltype in ("symbol", "symbol+wordmark"):
        raise LogoError(f"logo.type {ltype} needs logo.symbol (a symbol SVG)")
    if sym_src is not None:
        manifest["sources"]["symbol"] = {"sha256": _sha(sym_src)}
    if small_src is not None:
        manifest["sources"]["symbol_small"] = {"sha256": _sha(small_src)}
    lockups = {}
    if symbol is not None and wordmark is not None:
        scale = (lg.get("lockup") or {}).get("symbol_scale")
        for kind in (lg.get("lockups") or ["horizontal"]):
            lockups[kind] = lockup(symbol, wordmark, kind, symbol_scale=scale)
    masters = {}
    if symbol is not None:
        masters["symbol"] = symbol
    if small is not None:
        masters["symbol-small"] = small
    if wordmark is not None:
        masters["wordmark"] = wordmark
    for kind, svg in lockups.items():
        masters[f"lockup-{kind}"] = svg
    if lockups:
        primary_key = f"lockup-{(lg.get('lockups') or ['horizontal'])[0]}"
    elif ltype == "wordmark" or (wordmark is not None and symbol is None):
        primary_key = "wordmark"
    else:
        primary_key = "symbol"
    masters["primary"] = masters[primary_key]
    for name, svg in masters.items():
        p = os.path.join(out_dir, f"master-{name}.svg")
        _write(p, svg)
        manifest["masters"][name] = os.path.basename(p)
    manifest["primary"] = primary_key
    manifest["anchors"] = {k: int(ET.fromstring(v).get("data-anchors") or 0) for k, v in masters.items()
                           if k in ("symbol", "symbol-small")}

    _vb, _pa, pparts = parse_parts(masters["primary"])
    pbox = _union_bounds([_bounds(p) for _r, _i, p in pparts])
    cs = lg.get("clear_space") or {}
    mult = float(cs.get("multiple", 1) or 1)
    if primary_key == "symbol":
        unit = 0.5 * max(pbox[2] - pbox[0], pbox[3] - pbox[1])
    else:
        unit = 100.0 if cs.get("unit", "cap-height") != "symbol-half" else 0.5 * max(
            pbox[2] - pbox[0], pbox[3] - pbox[1])
    pad_view = _pad_view(pbox, unit * mult)
    manifest["clear_space"] = {"unit": cs.get("unit", "cap-height"), "multiple": mult, "units": round(unit * mult, 2)}

    modes = (palette or {}).get("modes") or {}
    light, dark = modes.get("light") or {}, modes.get("dark") or {}
    ink_dark = light.get("text") or "#111111"
    ink_light = dark.get("text") or "#ffffff"
    dark_font, dark_loc = (None, None)
    if wordmark is not None:
        dark_font, dark_loc = _dark_font(identity, set_dir, role_key, fpath, loc)
    if dark_font:
        manifest["dark_grade"] = dark_loc.get("GRAD")
    wm_args = (wm_cfg.get("text", ""), features, tracking, case, lang, colors.get("wordmark") or "text", detail)
    dark_parts = _dark_adjusted(pparts, masters, primary_key, lg, wm_args, dark_font, dark_loc)

    device = (lg.get("device") or "").strip().lower()
    implicit = {"full-color": light.get("background"), "full-color-dark": dark.get("background"),
                "one-color-dark": light.get("background"), "one-color-light": dark.get("background"),
                "symbol-only": light.get("background")}

    def version(name, parts, view, paint, mode, ground_role=None, ground=None, one_color=False, png_px=None):
        dev_info = None
        eff_ground = ground or implicit.get(name)
        parts = _merge_painted(parts, paint)
        if device in ("tile", "outline") and eff_ground and not name.startswith(("favicon", "small-", "app-icon")):
            dparts, dpaint, dev_info = _device_parts(device, parts, paint, eff_ground, mode, palette,
                                                     symbol_only=primary_key == "symbol" or name == "symbol-only")
            if dparts:
                paint = dict(paint)
                for r, hx in dpaint.items():
                    if r in paint and paint[r] != hx:  # role already painted differently: keep parts apart
                        r2 = f"{r}#device"
                        dparts = [(r2 if pr == r else pr, ids, pp) for pr, ids, pp in dparts]
                        paint[r2] = hx
                    else:
                        paint[r] = hx
                parts = dparts + list(parts)
        svg = _parts_svg(parts, view, kind=name, ground=ground, colors=paint,
                         extra_attrs=f' data-mode="{mode}"' + (f' data-ground="{ground_role}"' if ground_role else ""))
        sp = os.path.join(out_dir, f"{name}.svg")
        _write(sp, svg)
        w, h = (png_px, png_px) if isinstance(png_px, int) else _png_size(view, DELIVERY_PX)
        rgba = rasterize([(p, paint[r]) for r, _i, p in parts], w, h, view, ground,
                         samples=8 if max(w, h) <= 64 else 4)
        pp = os.path.join(out_dir, f"{name}.png")
        write_png(pp, w, h, rgba)
        manifest["versions"][name] = {
            "svg": os.path.basename(sp), "png": os.path.basename(pp), "size": [w, h], "mode": mode,
            "ground": ground, "ground_role": ground_role, "one_color": one_color, "device": dev_info,
            "parts": [{"role": r.split("#")[0], "ids": ids, "hex": paint[r], "area": round(abs(p.area), 2),
                       **({"device": ids[0][len("device-"):]} if ids and ids[0].startswith("device-") else {})}
                      for r, ids, p in parts]}

    def ground_safe(name, use, paint, ground, mode, devices=True):
        """Keep the logo readable on its ground. When the largest part (the one the contrast gate judges) is under
        3:1 on `ground` and no tile/outline device carries it, its colour is drawn in that mode's text colour: dark
        ink on a light ground, the reversed ink on a dark ground (a light block on the night ground becomes a dark
        block on paper, a navy wordmark becomes white on the night ground, a one-colour chestnut seal becomes white
        on the dark ground). Smaller weak parts stay as they are (the audit warns). Swaps go to
        manifest.light_swaps / dark_swaps and the audit reports them, so a mark that loses all its colour on a
        ground is visible without blocking the build."""
        import colorlib
        if not ground or not use:
            return paint

        def carried(role, parts):   # favicons draw no device (devices=False)
            return devices and device in ("tile", "outline") and not any(is_wordmark_part(ids) for r, ids, _p in parts
                                                              if r == role)
        role = max(use, key=lambda t: abs(t[2].area))[0]
        if role.startswith("raw:") or role not in paint or carried(role, use):
            return paint
        ratio = colorlib.contrast_ratio(paint[role], ground)
        if ratio >= DEVICE_CONTRAST:
            return paint
        ink = ink_dark if mode == "light" else ink_light
        kept = any(r != role and not r.startswith("raw:") and r in paint and
                   (colorlib.contrast_ratio(paint[r], ground) >= DEVICE_CONTRAST or carried(r, use))
                   for r, _i, _p in use)
        manifest.setdefault(f"{mode}_swaps", []).append(
            {"version": name, "role": role, "from": paint[role], "to": ink, "ground": ground, "ratio": round(ratio, 2),
             "colour_lost": not kept})
        return dict(paint, **{role: ink})

    version("full-color", pparts, pad_view,
            ground_safe("full-color", pparts, _paint(pparts, palette, "light"), light.get("background"), "light"),
            "light")
    version("full-color-dark", pparts, pad_view,
            ground_safe("full-color-dark", pparts, _paint(pparts, palette, "dark"), dark.get("background"), "dark"),
            "dark")
    version("one-color-dark", pparts, pad_view, _paint(pparts, palette, "light", ink_dark), "light", one_color=True)
    version("one-color-light", dark_parts, pad_view, _paint(dark_parts, palette, "dark", ink_light), "dark",
            one_color=True)
    grounds = [("light", "background", None), ("dark", "background", None)]
    for role, on in (("primary", "onPrimary"), ("accent", "onAccent")):
        if light.get(role) and light.get(on):
            grounds.append(("light", role, on))
    for mode, role, on in grounds:
        g = (modes.get(mode) or {}).get(role)
        if not g:
            continue
        use = dark_parts if mode == "dark" else pparts
        if on:
            paint = _paint(use, palette, mode, (modes.get(mode) or {}).get(on))
        else:
            paint = ground_safe(f"on-{mode}-{role}", use, _paint(use, palette, mode), g, mode)
        version(f"on-{mode}-{role}", use, pad_view, paint, mode, ground_role=role, ground=g, one_color=bool(on))
    manifest["grounds"] = [{"mode": m, "ground": r, "on": on or "full colour"} for m, r, on in grounds]

    # symbol only, favicons, app icon
    icon_svg = symbol
    mono_letters = ((lg.get("monogram") or {}).get("letters") or "").strip()
    if icon_svg is None and wordmark is not None:
        if small is not None:
            icon_svg = small
            manifest["icon_from"] = "symbol-small.svg"
        elif mono_letters:
            icon_svg = _letters_symbol(apply_case(mono_letters, case, lang), fpath, loc, features, lang,
                                       colors.get("symbol") or colors.get("wordmark") or "text")
            manifest["icon_from"] = "monogram"
        else:  # fallback: a designed small mark is missing (logo_audit warns)
            first = favicon_letter(_wordmark_meta(wordmark).get("text") or wm_cfg.get("text") or "?")
            icon_svg = _letters_symbol(first, fpath, loc, features, lang, colors.get("wordmark") or "text")
            manifest["icon_from"] = f"fallback: first letter {first!r} of the wordmark"
            manifest["icon_letter"] = first
            manifest["favicon_fallback"] = True
    if icon_svg is not None:
        _v, _a, sparts = parse_parts(icon_svg)
        sbox = _union_bounds([_bounds(p) for _r, _i, p in sparts])
        if symbol is not None:
            version("symbol-only", sparts, _pad_view(sbox, 0.25 * max(sbox[2] - sbox[0], sbox[3] - sbox[1])),
                    ground_safe("symbol-only", sparts, _paint(sparts, palette, "light"), light.get("background"),
                                "light"), "light")
        fav_svg = small or icon_svg
        _v, _a, fparts = parse_parts(fav_svg)
        fbox = _union_bounds([_bounds(p) for _r, _i, p in fparts])
        fview = _square_view(fbox, FAVICON_MARGIN)
        manifest["favicon_source"] = "symbol-small.svg" if small else (
            "symbol" if symbol else ("monogram" if mono_letters else "fallback-letter"))
        for px in (16, 32, 48):
            version(f"favicon-{px}", fparts, fview,
                    ground_safe(f"favicon-{px}", fparts, _paint(fparts, palette, "light"), light.get("background"),
                                "light", devices=False),
                    "light", png_px=px)
        dparts = [(r, ids, _inset(p, DARK_INSET_UNITS * (fview[2] / 100.0))) for r, ids, p in fparts]
        for px in (16, 32):
            version(f"small-{px}-one-color-dark", fparts, fview, _paint(fparts, palette, "light", ink_dark), "light",
                    one_color=True, png_px=px)
            version(f"small-{px}-one-color-light", dparts, fview, _paint(dparts, palette, "dark", ink_light), "dark",
                    ground=dark.get("background"), ground_role="background", one_color=True, png_px=px)
        tile_role, on_role = ("primary", "onPrimary") if light.get("primary") and light.get("onPrimary") else \
            ("background", "text")
        tile = light.get(tile_role) or "#ffffff"
        side = max(sbox[2] - sbox[0], sbox[3] - sbox[1]) / APP_ICON_CONTENT
        cx, cy = (sbox[0] + sbox[2]) / 2, (sbox[1] + sbox[3]) / 2 + APP_ICON_LIFT * side
        aview = (cx - side / 2, cy - side / 2, side, side)
        version("app-icon-512", sparts, aview, _paint(sparts, palette, "light", light.get(on_role) or ink_light),
                "light", ground_role=tile_role, ground=tile, one_color=True, png_px=512)
        manifest["app_icon"] = {"tile": tile_role, "mark": on_role, "content": APP_ICON_CONTENT,
                                "optical_lift": APP_ICON_LIFT, "source": "default"}
    app_src = None
    ap = os.path.join(set_dir, "logo", "app-icon.svg")
    if os.path.isfile(ap):
        with open(ap, encoding="utf-8") as fh:
            app_src = fh.read()
        app_svg = resolve_symbol(app_src, fpath, loc, features, lang, colors.get("symbol") or "primary", resolver)
        _v, _a, aparts = parse_parts(app_svg)
        _write(os.path.join(out_dir, "master-app-icon.svg"), app_svg)
        manifest["masters"]["app-icon"] = "master-app-icon.svg"
        manifest["sources"]["app_icon"] = {"sha256": _sha(app_src)}
        version("app-icon-512", aparts, (0.0, 0.0, 100.0, 100.0), _paint(aparts, palette, "light"), "light",
                png_px=512)
        manifest["app_icon"] = {"source": "app-icon.svg"}
    tables = {}
    for key, rep in (("symbol", sym_rep), ("symbol-small", small_rep)):
        if rep.get("glyph_tables"):
            tables[key] = rep["glyph_tables"]
    if mono_table:
        tables["monogram"] = [{"id": "monogram", "glyphs": mono_table}]
    if tables:
        gp = os.path.join(out_dir, "small-glyphs.json")
        _write(gp, json.dumps({"units": "symbol grid 0..100; index = position in data-glyphs (1-based)",
                               **tables}, indent=1, ensure_ascii=False) + "\n")
        manifest.setdefault("glyphs", {})["small"] = os.path.basename(gp)
    _write(os.path.join(out_dir, "manifest.json"), json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def _dark_font(identity, set_dir, role_key, fpath, loc):
    """(font path, location) for light-on-dark wordmarks: the same face at a negative GRAD when it has that axis
    (DARK_GRAD, clamped to the axis range when known; heuristic, uncalibrated), else (None, None)."""
    axes = font_axes(fpath)
    if "GRAD" not in axes and "GRAD" not in loc:
        return None, None
    dloc = dict(loc)
    if "GRAD" in axes:
        lo, _d, hi = axes["GRAD"]
        dloc["GRAD"] = max(lo, min(hi, DARK_GRAD))
        return fpath, dloc
    dloc["GRAD"] = DARK_GRAD
    try:
        return font_for(identity, set_dir, role_key, dloc), dloc
    except LogoError:
        return None, None


def _dark_adjusted(pparts, masters, primary_key, lg, wm_args, dark_font, dark_loc):
    """Primary parts for light-on-dark use: the wordmark re-outlined at a negative GRAD when the font has that
    axis, the symbol outline inset by DARK_INSET_UNITS of its 100 grid (both heuristic, uncalibrated)."""
    wm_dark = masters.get("wordmark")
    if dark_font and wm_dark is not None:
        text, features, tracking, case, lang, role, detail = wm_args
        wm_dark = wordmark_svg(text, dark_font, dark_loc, features, tracking, case, lang, color_role=role,
                               detail_svg=detail)
    if primary_key == "wordmark":
        return parse_parts(wm_dark)[2]
    if primary_key.startswith("lockup-"):
        scale = (lg.get("lockup") or {}).get("symbol_scale")
        svg = lockup(masters["symbol"], wm_dark, primary_key[len("lockup-"):], symbol_scale=scale)
        root = ET.fromstring(svg)
        sb = [float(x) for x in root.get("data-symbol-box").split()]
        ink = [float(x) for x in ET.fromstring(masters["symbol"]).get("data-ink").split()]
        k = (sb[3] - sb[1]) / max(1e-6, ink[3] - ink[1])
        out = []
        for r, ids, p in parse_parts(svg)[2]:
            out.append((r, ids, p if is_wordmark_part(ids) else _inset(p, DARK_INSET_UNITS * k)))
        return out
    return [(r, ids, _inset(p, DARK_INSET_UNITS)) for r, ids, p in pparts]


# ---------------------------------------------------------------- sets.json -> set folders

_IDENTITY_SKIP = ("schema", "brand", "components", "derived", "audit")


def sync_symbols(work, sets=None, write=True):
    """Sync the model's sets.json draft into the set folders for a logo build.

    Writes only logo/symbol.svg, logo/symbol-small.svg, logo/wordmark-detail.svg and logo/app-icon.svg (when the
    draft has them and they changed; a dropped detail or app icon is removed). Never writes
    identity.json or palette.json: those belong to the pipeline. Returns one record per set:
    {id, set_dir, identity (identity.json merged with the draft's fields, in memory), palette (built in memory
    when the draft's palette is partial), written: [paths], errors: [messages]}. write=False only reads."""
    import identitylib
    path = os.path.join(work, "sets.json")
    try:
        with open(path, encoding="utf-8") as fh:
            draft = json.load(fh)
    except FileNotFoundError:
        raise LogoError(f"{path}: not found (brand.py init creates it)") from None
    except json.JSONDecodeError as e:
        raise LogoError(f"{path}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    if draft.get("schema") != identitylib.SETS_SCHEMA:
        raise LogoError(f"{path}: schema must be {identitylib.SETS_SCHEMA!r}")
    brief = {}
    bp = os.path.join(work, "brief.json")
    if os.path.isfile(bp):
        with open(bp, encoding="utf-8") as fh:
            brief = json.load(fh)
    wanted = {s.strip().upper() for s in sets} if sets else None
    records = []
    for i, entry in enumerate(draft.get("sets") or []):
        ident_in = (entry or {}).get("identity") or {}
        sid = ((ident_in.get("set") or {}).get("id")) or chr(ord("A") + i)
        if wanted and sid not in wanted:
            continue
        set_dir = os.path.join(work, "sets", sid)
        rec = {"id": sid, "set_dir": set_dir, "identity": None, "palette": None, "written": [], "errors": []}
        if write:
            os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        for key, name in (("symbol_svg", "symbol.svg"), ("symbol_small_svg", "symbol-small.svg"),
                          ("wordmark_detail_svg", "wordmark-detail.svg"), ("app_icon_svg", "app-icon.svg")):
            svg = entry.get(key)
            target = os.path.join(set_dir, "logo", name)
            if write and not svg and key in ("wordmark_detail_svg", "app_icon_svg") and os.path.isfile(target):
                os.remove(target)  # the detail is optional: dropping it from the draft drops it from the build
                rec["written"].append(target)
                continue
            if not svg or not write:
                continue
            target = os.path.join(set_dir, "logo", name)
            old = None
            if os.path.isfile(target):
                with open(target, encoding="utf-8") as fh:
                    old = fh.read()
            if old != svg:
                _write(target, svg)
                rec["written"].append(target)
        # kit misuse drawings (logo.misuse[].svg): {"misuse-1": "<svg ...>"} -> logo/misuse-1.svg; sanitised by the kit
        mis = entry.get("misuse_svgs") or {}
        if not isinstance(mis, dict):
            rec["errors"].append("misuse_svgs: an object {\"misuse-1\": \"<svg ...>\"}")
            mis = {}
        for name, svg in mis.items():
            if not re.fullmatch(r"misuse-[A-Za-z0-9_-]{1,40}", str(name)) or not isinstance(svg, str) or not svg.strip():
                rec["errors"].append(f"misuse_svgs.{name}: keys are misuse-<n> and values SVG text")
                continue
            target = os.path.join(set_dir, "logo", f"{name}.svg")
            if not write:
                continue
            old = None
            if os.path.isfile(target):
                with open(target, encoding="utf-8") as fh:
                    old = fh.read()
            if old != svg:
                _write(target, svg)
                rec["written"].append(target)
        ip = os.path.join(set_dir, "identity.json")
        if os.path.isfile(ip):
            with open(ip, encoding="utf-8") as fh:
                base = json.load(fh)
        else:
            base = {"schema": identitylib.SCHEMA,
                    "brand": {"name": brief.get("brand", ""), "tagline": brief.get("tagline", ""),
                              "languages": brief.get("languages") or ["en"], "numbers": brief.get("numbers", False),
                              **({"doc_lang": brief["doc_lang"]} if brief.get("doc_lang") else {})},
                    "components": {c: {"mode": (brief.get("components") or {}).get(c, "new"), "status": "proposed",
                                       "source": None, "sha256": None} for c in identitylib.COMPONENTS},
                    "palette": "palette.json"}
        merged = copy.deepcopy(base)
        for k, v in ident_in.items():
            if k not in _IDENTITY_SKIP:
                merged[k] = copy.deepcopy(v)
        lg = merged.get("logo") or {}
        if entry.get("symbol_svg") and not lg.get("symbol") and isinstance(merged.get("logo"), dict):
            merged["logo"]["symbol"] = "logo/symbol.svg"
        try:
            identitylib.validate_identity(merged)
        except identitylib.IdentityError as e:
            rec["errors"].append(f"identity: {e}")
        rec["identity"] = merged
        rec["palette"], perr = _palette_for(entry.get("palette"), set_dir, merged)
        if perr:
            rec["errors"].append(perr)
        records.append(rec)
    return records


def _palette_for(partial, set_dir, identity):
    """A palette with both modes for colouring the logo: the draft's if built, the set's palette.json when it is
    built from the same seeds, else built in memory with palette_build (nothing written)."""
    disk = None
    pp = os.path.join(set_dir, identity.get("palette") or "palette.json")
    if os.path.isfile(pp):
        try:
            with open(pp, encoding="utf-8") as fh:
                disk = json.load(fh)
        except (OSError, json.JSONDecodeError):
            disk = None
    if partial is None:
        partial = disk
    if not partial:
        return None, "palette: no palette in sets.json or palette.json; colours fall back to placeholders"
    if (partial.get("modes") or {}).get("light") and (partial.get("modes") or {}).get("dark"):
        return partial, None
    opts = identity.get("palette_build") or {}

    def seeds(pal):  # the build lists extended colours in brand[] too (role "extended") and reorders the seeds
        return sorted(b.get("hex", "").lower() for b in pal.get("brand") or [] if b.get("role") != "extended")
    extra = {str(x).split(":", 1)[0].strip().lower() for x in opts.get("extra") or []}
    if disk and (disk.get("modes") or {}).get("light") and seeds(disk) == seeds(partial) and \
            extra <= {x.get("hex", "").lower() for x in disk.get("extended") or []}:
        return disk, None
    try:
        import colorlib
        import palette_build
        part = copy.deepcopy(partial)
        colorlib.validate_palette(part, partial=True)
        built = palette_build.build_palette(
            None, opts.get("accent"), opts.get("strategy") or None, opts.get("neutral_tint") or None,
            opts.get("neutral_chroma") if opts.get("neutral_chroma") is not None else 0.022,
            part.get("name"), part.get("direction"), part, True, opts.get("light_bg"), opts.get("dark_bg"),
            [palette_build.parse_extra(x) for x in opts.get("extra") or []], part.get("feels"))
        return built, None
    except Exception as e:  # noqa: BLE001 - reported as a finding, the logo still builds with placeholders
        return partial, f"palette: could not build the partial palette in memory: {e}"
