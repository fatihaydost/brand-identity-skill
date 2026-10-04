#!/usr/bin/env python3
"""Logo audit and the `brand.py logos` command (docs/architecture.md section 4.4, references/logo.md).

audit(identity, palette, build_dir) measures what is broken in one set's logo build; it never scores. Gates are
production rules or measurable breakage; warnings are design checks whose thresholds are our heuristics,
uncalibrated (labelled where they are defined). cli_logos syncs sets.json into the set folders, builds every set's
logo versions with logolib.variants, audits them and renders ONE contact sheet for all sets (primary large,
32 and 16 px, one colour on light and dark), because a broken mark can pass every automated check: look at it.

Usage (through brand.py):
  python3 brand.py logos brand-identity/moonvault
  python3 brand.py logos brand-identity/moonvault --sets A,C --full

Identity fields read besides the contract (all optional):
  logo.defect       one sentence naming the single defect this round fixes; needed when the symbol's anchor count
                    grows between rounds, else logo.detail-creep warns (one named defect per round, <= 2 rounds).
  logo.device       tile | outline: draw a tile/contour where a symbol part misses 3:1 on a ground.
  logo.colors_why   why the logo needs more than 3 colours.
  logo.font_permission   written permission for a non-OFL face.
  logo.lockup.symbol_scale   symbol height in cap heights in the horizontal lockup (default 1.25).
  logo.ideas        4-6 one-sentence ideas considered for the set; shown small under the contact sheet only.
sets.json entries may add symbol_small_svg, wordmark_detail_svg and app_icon_svg (see logolib).

Findings use identitylib.finding (component "logo"). Standard library + logolib's dependencies.
"""
import json
import math
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if __name__ == "__main__":
    import pydeps  # noqa: E402
    pydeps.ensure()  # missing packages: re-run in uv's cached environment (or the fallback venv)

import colorlib  # noqa: E402
import identitylib  # noqa: E402
import logolib  # noqa: E402
from identitylib import finding  # noqa: E402

__all__ = ["audit", "cli_logos", "contact_sheet", "thin_features", "build_set", "SHEET_NAME"]

SHEET_NAME = "logo-sheet.png"
STDOUT_CAP = 1200

# Heuristic, uncalibrated thresholds (docs/architecture.md section 0). Each is named where it is used.
THIN_K = 1.0                 # brief section 6 check 7: thinnest stroke/gap >= k px at the minimum size; start 1.0
THIN_CELLS = 10              # raster cells per target pixel for the erosion test
THIN_AREA_PX2 = 0.5          # a lost/closed region of at least this many target px^2 counts as a feature
MAX_COLOURS = 3              # brief section 6 check 8
MIN_SEGMENT_DL = 0.10        # brief section 6 check 9: OKLCH L difference between colour segments, to calibrate
NEAR_EQUAL_REL = 0.04        # brief section 6 check 11: radii/weights within 4% but not equal, to calibrate
NEAR_EQUAL_DEG = 2.0         # brief section 6 check 11: angles within 2 degrees but not equal, to calibrate
EQUAL_DEG = 0.05             # below this, angles are equal (coordinate rounding), not "almost equal"
EQUAL_UNITS = 0.1            # below this (100-unit grid), radii/weights are equal (rounding)
BAND_TOL_CAPS = 0.05         # brief section 6 check 14: symbol centre off the cap band centre, in cap heights
APP_SAFE = 10.0              # app icon: content inside 10..90 of the 100 grid (safe area, heuristic, uncalibrated)
ADJACENT_UNITS = 2.0         # colour segments closer than this (100 grid) count as adjacent
TILE_COVER = 0.7             # cliche: radiused tile covering >= 70% of the canvas, to calibrate
SPARK_RATIO = 0.7            # cliche: 8-vertex star whose inner/outer radius ratio is below this
HEX_TOL = 0.06               # cliche: regular hexagon side/radius spread
ICON_STROKE = (100 / 12) * 0.65, (100 / 12) * 1.35   # cliche: outline icon stroke ~ 1/12 of the size
CONTRAST_MIN = 3.0           # WCAG 2.2 SC 1.4.11 non-text contrast (a practice gate for logos)
OFL_LIKE = ("ofl", "apache", "ufl", "cc0", "public domain", "mit")


def _f(id_, sev, msg, measured=None, threshold=None, fix=None, roles=None):
    return finding(id_, "logo", sev, msg, measured, threshold, fix, roles)


# ---------------------------------------------------------------- morphology (thin strokes and clogging gaps)

def _hshift(row, r, mask, dilate):
    out = row
    for d in range(1, r + 1):
        if dilate:
            out |= ((row << d) | (row >> d))
        else:
            out &= (row << d) & (row >> d)
    return out & mask


def _morph(rows, r, mask, dilate):
    n = len(rows)
    spans = [(dy, int(math.floor(math.sqrt(max(0.0, r * r - dy * dy))))) for dy in range(-int(r), int(r) + 1)]
    out = []
    for y in range(n):
        acc = 0 if dilate else mask
        for dy, hw in spans:
            yy = y + dy
            row = rows[yy] if 0 <= yy < n else 0
            h = _hshift(row, hw, mask, dilate)
            if dilate:
                acc |= h
            else:
                acc &= h
        out.append(acc)
    return out


def _components(rows, n):
    pts = set()
    for y, row in enumerate(rows):
        x = 0
        while row:
            if row & 1:
                pts.add((x, y))
            row >>= 1
            x += 1
    sizes = []
    while pts:
        stack = [pts.pop()]
        size = 0
        while stack:
            x, y = stack.pop()
            size += 1
            for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if q in pts:
                    pts.remove(q)
                    stack.append(q)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def thin_features(path, px, view=(0.0, 0.0, 100.0, 100.0), k=THIN_K):
    """Erosion test: render `path` (view mapped onto px pixels) and open it with a disk k px wide (strokes thinner
    than that vanish), then close it (gaps narrower than that fill in). Returns
    {"stroke_px2": largest lost region, "gap_px2": largest clogged region} in target px^2.
    Heuristic, uncalibrated (THIN_K, THIN_CELLS, THIN_AREA_PX2)."""
    n = int(px * THIN_CELLS)
    rows = logolib.bitmap(path, n, view)
    mask = (1 << n) - 1
    r = k * THIN_CELLS / 2 - 0.5
    opened = _morph(_morph(rows, r, mask, False), r, mask, True)
    lost = [a & ~b & mask for a, b in zip(rows, opened)]
    closed = _morph(_morph(rows, r, mask, True), r, mask, False)
    gained = [b & ~a & mask for a, b in zip(rows, closed)]
    cell = THIN_CELLS * THIN_CELLS
    ls = _components(lost, n)
    gs = _components(gained, n)
    return {"stroke_px2": round(ls[0] / cell, 2) if ls else 0.0, "gap_px2": round(gs[0] / cell, 2) if gs else 0.0}


# ---------------------------------------------------------------- cliche detectors (references/logo.md section 7)

def _radii(verts):
    cx = sum(p[0] for p in verts) / len(verts)
    cy = sum(p[1] for p in verts) / len(verts)
    return [math.dist(p, (cx, cy)) for p in verts]


def _spread(vals):
    m = sum(vals) / len(vals)
    return (max(vals) - min(vals)) / m if m else 1.0


def cliches(report):
    """Structure flags from an inspect_symbol report: [(tag, evidence)]. Thresholds heuristic, uncalibrated."""
    hits = []
    for el in report.get("elements", []):
        v = el.get("vertices") or []
        if len(v) > 2 and v[0] == v[-1]:
            v = v[:-1]
        if len(v) == 8:
            r = _radii(v)
            a, b = r[0::2], r[1::2]
            inner, outer = (a, b) if sum(a) < sum(b) else (b, a)
            if _spread(inner) < 0.15 and _spread(outer) < 0.15 and \
                    sum(inner) / len(inner) < SPARK_RATIO * sum(outer) / len(outer):
                hits.append(("spark", f"{el['id']}: 8-vertex star, alternating radii"))
        if len(v) == 6:
            sides = [math.dist(v[i], v[(i + 1) % 6]) for i in range(6)]
            if _spread(sides) < HEX_TOL and _spread(_radii(v)) < HEX_TOL:
                hits.append(("hexagon", f"{el['id']}: regular hexagon"))
    vb = report.get("viewbox") or [0, 0, 100, 100]
    scale = 100.0 / max(vb[2], vb[3])
    for el in report.get("elements", []):
        rect = el.get("rect")
        if rect and rect[4] > 0 and rect[2] * rect[3] * scale * scale >= TILE_COVER * 100 * 100 and \
                report.get("glyphs"):
            hits.append(("initial-in-rounded-square", f"{el['id']}: radiused tile + glyph {report['glyphs'][0]}"))
    strokes = report.get("strokes", [])
    filled = [e for e in report.get("elements", []) if e["id"] not in {s["id"] for s in strokes}]
    if strokes and not filled and not report.get("glyphs") and all(not s["fill"] for s in strokes):
        widths = [s["width"] for s in strokes]
        if all(ICON_STROKE[0] <= w <= ICON_STROKE[1] for w in widths) and any(s["cap"] == "round" for s in strokes):
            hits.append(("outline-icon", f"stroke-only, stroke {widths[0]:.1f}/100, round caps"))
    if report.get("gradients"):
        hits.append(("gradient-orb", f"gradient fill ({report['gradients'][0]})"))
    centres = {}
    for _eid, (angle, cx, cy) in report.get("rotations", []):
        centres.setdefault((round(cx, 1), round(cy, 1)), set()).add(round(angle % 360, 1))
    for (cx, cy), angles in centres.items():
        n = len(angles)
        if n >= 5:
            a = sorted(angles)
            gaps = [(a[(i + 1) % n] - a[i]) % 360 for i in range(n)]
            if max(gaps) - min(gaps) <= 3.0:
                hits.append(("radial-swirl", f"{n} rotated copies around ({cx:g}, {cy:g})"))
    return hits


def _default_entry(identity, tag):
    for item in identity.get("defaults_used") or []:
        iid = str(item.get("id", ""))
        if iid == tag or tag in iid:
            return item
    return None


# ---------------------------------------------------------------- the audit

def _load(build_dir, name):
    p = os.path.join(build_dir, name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _near_equal(pairs, rel=None, deg=None):
    out = []
    vals = [(eid, v) for eid, v in pairs if v > 0]
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            (ea, a), (eb, b) = vals[i], vals[j]
            if rel is not None:
                d = abs(a - b) / max(a, b)
                if abs(a - b) >= EQUAL_UNITS and d <= rel:
                    out.append((ea, eb, a, b))
            else:
                d = abs(a - b) % 180.0
                d = min(d, 180.0 - d)
                if EQUAL_DEG <= d <= deg:
                    out.append((ea, eb, a, b))
    return out


def _adjacent(a, b):
    """Two filled paths touch or nearly touch (within ADJACENT_UNITS)."""
    grow = logolib.pathops.Path(a)
    grow.stroke(2 * ADJACENT_UNITS, logolib.pathops.LineCap.BUTT_CAP, logolib.pathops.LineJoin.MITER_JOIN, 4)
    grow.convertConicsToQuads(0.02)
    hit = logolib._op(logolib._union([grow, a]), b, logolib.pathops.PathOp.INTERSECTION)
    return not logolib._empty(hit)


def _ground_like(hexcol, palette):
    if not hexcol:
        return False
    modes = (palette or {}).get("modes") or {}
    for mode in ("light", "dark"):
        for role in logolib.GROUND_ROLES:
            g = (modes.get(mode) or {}).get(role)
            if g and colorlib.contrast_ratio(hexcol, g) < 1.1:
                return True
    return hexcol.lower() in ("#ffffff", "#fff")


def audit(identity, palette, build_dir):
    """Audit one set's logo build (build_dir = sets/X/logo/build, written by logolib.variants).
    Returns a list of findings (component "logo")."""
    out = []
    lg = identity.get("logo") or {}
    set_dir = os.path.dirname(os.path.dirname(os.path.abspath(build_dir)))
    raw = _load(build_dir, "manifest.json")
    if raw is None:
        return [_f("logo.build-missing", "gate", f"no logo build in {build_dir}", fix="run brand.py logos WORK")]
    man = json.loads(raw)
    font = man.get("font") or {}
    modes = (palette or {}).get("modes") or {}
    light, dark = modes.get("light") or {}, modes.get("dark") or {}
    lang = ((identity.get("brand") or {}).get("languages") or ["en"])[0]
    default_role = (lg.get("colors") or {}).get("symbol") or "primary"

    # sources: what the model wrote
    reports = {}
    for key, rel in (("symbol", lg.get("symbol")), ("symbol-small", "logo/symbol-small.svg")):
        if not rel:
            continue
        p = rel if os.path.isabs(rel) else os.path.join(set_dir, rel)
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        try:
            reports[key] = logolib.inspect_symbol(src, font.get("path"), font.get("location"), None, lang,
                                                  default_role, logolib.font_resolver(identity, set_dir))
        except logolib.LogoError as e:
            out.append(_f("logo.symbol-invalid", "gate", f"{key}: {e}", fix="fix the symbol SVG"))
            continue
        # strings the resolver ignores: check the raw text too
        reports[key]["raw_text"] = src

    # 1. forbidden elements and viewBox (gate, production rule)
    for key, rep in reports.items():
        bad = sorted(set(rep["forbidden"]))
        if re.search(r"<\s*(?:\w+:)?text[\s>/]", rep["raw_text"]) and "text" not in bad:
            bad.append("text")
        if bad:
            tags = ", ".join(f"<{b}>" for b in bad)
            out.append(_f("logo.forbidden-element", "gate", f"{key}.svg contains {tags}",
                          measured=bad, threshold="none",
                          fix="no live text, images or filters: letters come from the font as outlines "
                              "(data-glyphs), effects are not part of a mark"))
        if not rep["has_viewbox"]:
            out.append(_f("logo.viewbox", "gate", f"{key}.svg has no viewBox", threshold="viewBox present",
                          fix='add viewBox="0 0 100 100" and draw on that grid'))
        if rep["unsupported"]:
            out.append(_f("logo.unsupported-element", "gate",
                          f"{key}.svg uses {', '.join(sorted(set(rep['unsupported'])))}, which the build ignores",
                          measured=sorted(set(rep["unsupported"])),
                          fix="use circle/rect/polygon/path with data-op booleans instead"))
        for err in rep["errors"]:
            out.append(_f("logo.boolean", "gate", f"{key}.svg: {err}"))
        if rep["missing_ids"]:
            out.append(_f("logo.ids", "info", f"{key}.svg: {rep['missing_ids']} element(s) without an id",
                          fix="give every primitive a semantic id so edits change one element per round"))
        if rep["opacity"]:
            out.append(_f("logo.opacity", "warn", f"{key}.svg: opacity on {', '.join(rep['opacity'][:3])} is ignored",
                          fix="flat colours only; use a palette role instead of transparency"))

    detail = None
    dp = os.path.join(set_dir, "logo", "wordmark-detail.svg")
    if os.path.isfile(dp):
        with open(dp, encoding="utf-8") as fh:
            detail = fh.read()
        bad = sorted(set(re.findall(r"<\s*(text|image|foreignObject|filter)[\s>/]", detail)))
        if bad:
            out.append(_f("logo.forbidden-element", "gate", f"wordmark-detail.svg contains "
                          f"{', '.join(f'<{b}>' for b in bad)}", measured=bad, threshold="none"))

    # 1b/2. generated files: no forbidden elements, no strokes after resolve (gate)
    for name in sorted(os.listdir(build_dir)):
        if not name.endswith(".svg"):
            continue
        text = _load(build_dir, name) or ""
        if re.search(r"<(?:text|image|foreignObject|filter)[\s>/]", text) or "viewBox" not in text:
            out.append(_f("logo.forbidden-element", "gate", f"{name}: live text/image/filter or no viewBox"))
        if re.search(r'\sstroke(?:-width)?="(?!none)', text):
            out.append(_f("logo.stroke", "gate", f"{name} still has strokes after resolve",
                          fix="strokes must be outlined (logolib.resolve_symbol does this)"))

    # 3. every fill maps to a palette role or brand id (gate)
    for key, rep in reports.items():
        for eid, val in rep["raw_fills"]:
            out.append(_f("logo.color-role", "gate", f"{key}.svg: {eid} uses raw colour {val}",
                          measured=val, threshold="palette role or brand id",
                          fix='use data-color="primary" (or another role/brand id) and no fill colour'))
        if rep["gradients"]:
            out.append(_f("logo.color-role", "gate", f"{key}.svg: gradient fill is not a palette role",
                          fix="flat master in palette roles; a gradient can be an application, not the mark"))
    roles_seen = set()
    for v in (man.get("versions") or {}).values():
        if v.get("one_color"):
            continue
        for part in v.get("parts", []):
            roles_seen.add(part["role"])
    for role in sorted(r for r in roles_seen if r and not r.startswith("raw:")):
        if logolib.resolve_color(palette, role, "light") is None:
            out.append(_f("logo.color-role", "gate", f"colour {role!r} is not a palette role or brand id",
                          measured=role, threshold=", ".join(colorlib.ROLE_KEYS[:8]) + ", brand-N",
                          fix="use a role key from palette.json modes or a brand[].id", roles=[role]))

    # 4. one-colour versions exist; no false holes (gate)
    versions = man.get("versions") or {}
    for name in ("one-color-dark", "one-color-light"):
        if name not in versions or not os.path.isfile(os.path.join(build_dir, versions[name]["svg"])):
            out.append(_f("logo.one-color", "gate", f"{name} version missing", fix="rerun brand.py logos"))
    for key, rep in reports.items():
        for ov in rep["overlaps"]:
            col = ov["upper_color"]
            hexcol = logolib._raw_hex(col) if col and col.startswith("raw:") else \
                logolib.resolve_color(palette, col, "light")
            if (col in logolib.GROUND_ROLES) or _ground_like(hexcol, palette):
                out.append(_f("logo.false-hole", "gate",
                              f"{key}.svg: {ov['upper']} is a ground-coloured shape over {ov['lower']} (a fake hole: "
                              "it shows as a patch on any other ground and vanishes in one colour)",
                              measured=f"{ov['area']} units^2 overlap", threshold="real holes only",
                              fix=f'make it a real hole: data-op="subtract" on {ov["upper"]}'))
        if rep["fill_rule_ambiguous"]:
            out.append(_f("logo.fill-rule", "warn",
                          f"{key}.svg: {', '.join(rep['fill_rule_ambiguous'][:3])} fills differently under non-zero "
                          "and even-odd (a hole that depends on the fill rule)",
                          threshold="no difference", fix='declare fill-rule="evenodd" or cut with data-op="subtract"'))

    # 5. 16 and 32 px renders exist (gate; cli_logos puts them on the contact sheet)
    for px in (16, 32):
        v = versions.get(f"favicon-{px}")
        if not v or not os.path.isfile(os.path.join(build_dir, v["png"])):
            out.append(_f("logo.small-renders", "gate", f"{px} px render missing", fix="rerun brand.py logos"))

    # 6. contrast >= 3:1 on every ground used, or a tile/outline device declared AND drawn in that version
    device = (lg.get("device") or "").lower()
    implicit = {"full-color": light.get("background"), "full-color-dark": dark.get("background"),
                "one-color-dark": light.get("background"), "one-color-light": dark.get("background"),
                "symbol-only": light.get("background")}
    for name, v in versions.items():
        ground = v.get("ground") or implicit.get(name)
        if not ground or name.startswith("app-icon"):
            continue
        parts = [p for p in v.get("parts", []) if p.get("hex") and not p.get("device")]
        if not parts:
            continue
        dev = v.get("device") or None
        drawn = bool(dev) and f'id="device-{dev["kind"]}"' in (_load(build_dir, v["svg"]) or "")
        main = max(parts, key=lambda p: p.get("area", 0))
        for p in parts:
            ratio = colorlib.contrast_ratio(p["hex"], ground)
            if ratio >= CONTRAST_MIN:
                continue
            is_main = p is main
            covered = drawn and not logolib.is_wordmark_part(p.get("ids"))
            if covered and dev["kind"] == "tile":
                covered = colorlib.contrast_ratio(p["hex"], dev["hex"]) >= CONTRAST_MIN
            elif covered:
                covered = colorlib.contrast_ratio(dev["hex"], ground) >= CONTRAST_MIN
            if covered:
                out.append(_f("logo.contrast", "info", f"{name}: {p['role']} {p['hex']} on {ground} is {ratio:.2f}:1; "
                              f"carried by a {dev['kind']} in {dev['role']} {dev['hex']}",
                              measured=round(ratio, 2), roles=[p["role"]]))
                continue
            sev = "gate" if is_main else "warn"
            note = ""
            if device in ("tile", "outline"):
                note = f" (logo.device {device} declared but no {device} carries it in {v['svg']})"
            out.append(_f("logo.contrast", sev,
                          f"{name}: {p['role']} {p['hex']} on {ground} is {ratio:.2f}:1"
                          + (" (largest part)" if is_main else "") + note,
                          measured=round(ratio, 2), threshold=f">= {CONTRAST_MIN}:1 (WCAG 2.2 SC 1.4.11, practice)",
                          fix="use a one-colour version on this ground, or declare logo.device tile|outline",
                          roles=[p["role"]]))

    for mode in ("light", "dark"):
        for sw in man.get(f"{mode}_swaps") or []:
            out.append(_f("logo.light-swap" if mode == "light" else "logo.dark-swap", "info",
                          f"{sw['version']}: {sw['role']} {sw['from']} is {sw['ratio']:.2f}:1 on {sw['ground']}; "
                          f"drawn in the {mode} text colour {sw['to']} (a colour stays only where it reads)",
                          measured=sw["ratio"], roles=[sw["role"]]))

    # 7. thinnest stroke/gap at the minimum size and at 16 px for the favicon source (warn: k is uncalibrated)
    min_px = int(((lg.get("min_size") or {}).get("px")) or 24)
    sym = _load(build_dir, "master-symbol.svg")
    small = _load(build_dir, "master-symbol-small.svg")
    if sym:
        path = logolib._union([p for _r, _i, p in logolib.parse_parts(sym)[2]])
        tf = thin_features(path, min_px)
        bad = tf["stroke_px2"] >= THIN_AREA_PX2 or tf["gap_px2"] >= THIN_AREA_PX2
        if bad:
            out.append(_f("logo.thin-feature", "info" if small else "warn",
                          f"symbol at {min_px} px (min size): strokes/gaps thinner than {THIN_K:g} px "
                          f"(lost {tf['stroke_px2']} px², clogged {tf['gap_px2']} px²)"
                          + ("; symbol-small.svg covers small sizes" if small else ""),
                          measured=tf, threshold=f">= {THIN_K:g} px = {THIN_K * 100 / min_px:.1f} units "
                          "(heuristic, uncalibrated)",
                          fix="add symbol_small_svg for small sizes, or raise logo.min_size.px (or thicken the "
                              "thin part / widen the gap)"))
    fav_src = small or sym
    if fav_src:
        parts = logolib.parse_parts(fav_src)[2]
        path = logolib._union([p for _r, _i, p in parts])
        box = logolib._bounds(path)
        if box:
            view = logolib._square_view(box, logolib.FAVICON_MARGIN)
            tf = thin_features(path, 16, view)
            if tf["stroke_px2"] >= THIN_AREA_PX2 or tf["gap_px2"] >= THIN_AREA_PX2:
                out.append(_f("logo.small-size", "warn",
                              f"{'symbol-small' if small else 'symbol'} at 16 px: features narrower than 1 px "
                              f"(lost {tf['stroke_px2']} px², clogged {tf['gap_px2']} px²)",
                              measured=tf, threshold="1 px (raster erosion test, heuristic, uncalibrated)",
                              fix="add or simplify symbol-small.svg: fewer, bolder parts"))

    # small mark: a wordmark-only set needs a designed mark for favicons (warn); the first letter is a fallback
    if man.get("favicon_fallback"):
        out.append(_f("logo.small-mark-missing", "warn",
                      "a wordmark-only set needs a designed small mark (symbol_small_svg or monogram) for 16-48 px; "
                      f"favicon defaulted to the first letter {man.get('icon_letter', '')!r} (fallback)",
                      measured=man.get("favicon_source"), threshold="symbol_small_svg or logo.monogram",
                      fix="set symbol_small_svg or logo.monogram"))

    # 8. colour count (warn)
    full = versions.get("full-color") or {}
    hexes = sorted({p["hex"] for p in full.get("parts", [])})
    if len(hexes) > MAX_COLOURS and not lg.get("colors_why"):
        out.append(_f("logo.colors", "warn", f"{len(hexes)} colours in the logo", measured=len(hexes),
                      threshold=f"<= {MAX_COLOURS} (heuristic, uncalibrated)", fix="fewer colours or logo.colors_why"))
    # 9. lightness difference between adjacent colour segments of the symbol (warn); the wordmark is not a segment
    sym_master = _load(build_dir, "master-symbol.svg")
    if sym_master:
        sparts = []
        for role, ids, path in logolib.parse_parts(sym_master)[2]:
            hx = logolib.resolve_color(palette, role, "light") or logolib._raw_hex(role)
            if hx:
                sparts.append((role, hx, path))
        for i in range(len(sparts)):
            for j in range(i + 1, len(sparts)):
                (ra, ha, pa), (rb, hb, pb) = sparts[i], sparts[j]
                if ha == hb or not _adjacent(pa, pb):
                    continue
                dl = abs(colorlib.hex_to_oklch(ha)[0] - colorlib.hex_to_oklch(hb)[0])
                if dl < MIN_SEGMENT_DL:
                    out.append(_f("logo.segment-lightness", "warn",
                                  f"symbol parts {ra} {ha} and {rb} {hb} touch and differ by OKLCH L {dl:.2f}: "
                                  "separated by hue alone", measured=round(dl, 3),
                                  threshold=f">= {MIN_SEGMENT_DL} (to calibrate)", roles=[ra, rb],
                                  fix="separate adjacent colour segments in lightness, not hue alone"))
    # role names resolve to the palette's UI tones; brand ids give the exact brand hex (info)
    brand = [(b.get("id"), (b.get("hex") or "").lower()) for b in (palette or {}).get("brand") or [] if b.get("hex")]
    seed_of = {"primary": 0, "accent": 1}   # palette_build seeds: brand[0] -> primary, brand[1] -> accent
    seen = {sw["role"] for sw in man.get("light_swaps") or [] if sw.get("version") == "full-color"}  # drawn in ink
    for p in full.get("parts", []):
        role, hx = p["role"], (p.get("hex") or "").lower()
        if role in seen or role not in seed_of or not hx or not brand:
            continue
        seen.add(role)
        if hx in {h for _i, h in brand}:
            continue
        i = seed_of[role]
        bid, bhex = brand[i] if i < len(brand) else min(brand, key=lambda b: colorlib.delta_e_ok(hx, b[1]))
        out.append(_f("logo.role-tone", "info", f"{role} resolves to {hx} (UI tone); use {bid} for {bhex}",
                      measured=hx, threshold=bhex, roles=[role],
                      fix=f'data-color="{bid}" (or logo.colors) for the exact brand colour'))
    # app icon from app_icon_svg: content inside the safe area (warn)
    app = _load(build_dir, "master-app-icon.svg")
    if app:
        content = []
        tile_seen = False
        for role, ids, path in logolib.parse_parts(app)[2]:
            b = logolib._bounds(path)
            if not b:
                continue
            if not tile_seen and not content and b[2] - b[0] >= 90 and b[3] - b[1] >= 90:
                tile_seen = True   # the first full-bleed part is the tile, not content
                continue
            content.append(b)
        if content:
            box = logolib._union_bounds(content)
            margin = min(box[0], box[1], 100 - box[2], 100 - box[3])
            if margin < APP_SAFE:
                out.append(_f("logo.app-icon-safe-area", "warn",
                              f"app icon content reaches {margin:.1f} units from the edge; platforms mask and "
                              "round the corners", measured=round(margin, 1),
                              threshold=f">= {APP_SAFE:g} of 100 (heuristic, uncalibrated)",
                              fix="keep the mark inside the central 80% of the 512 square"))
    # 10. anchor growth between rounds without a named defect (warn)
    hist_raw = _load(build_dir, "history.json")
    if hist_raw:
        hist = json.loads(hist_raw)
        if len(hist) >= 2 and hist[-1]["anchors"] > hist[-2]["anchors"] and not hist[-1].get("defect"):
            out.append(_f("logo.detail-creep", "warn",
                          f"symbol anchors grew {hist[-2]['anchors']} -> {hist[-1]['anchors']} without a named defect",
                          measured=hist[-1]["anchors"], threshold="relative",
                          fix="name the one defect this round fixes in logo.defect, or remove the addition"))
    # 11. near-equal angles, radii, weights in the symbol (warn; glyph outlines are skipped)
    rep = reports.get("symbol")
    if rep:
        near = (_near_equal(rep["radii"], rel=NEAR_EQUAL_REL) + _near_equal(rep["weights"], rel=NEAR_EQUAL_REL)
                + _near_equal(rep["angles"], deg=NEAR_EQUAL_DEG))
        if near:
            ex = "; ".join(f"{a} {va:.2f} vs {b} {vb:.2f}" for a, b, va, vb in near[:3])
            out.append(_f("logo.near-equal", "warn", f"almost-equal values in the symbol: {ex}",
                          measured=len(near), threshold=f"equal, or > {NEAR_EQUAL_REL:.0%} / {NEAR_EQUAL_DEG:g}° "
                          "apart (tolerance to calibrate)", fix="make repeated parts share one exact value"))
        # 12. category cliches (warn; gate if declared in defaults_used without a why)
        for tag, evidence in cliches(rep):
            entry = _default_entry(identity, tag)
            if entry is None:
                out.append(_f("logo.cliche", "warn", f"{tag}: {evidence}", measured=tag,
                              threshold="detector heuristic, uncalibrated",
                              fix="who on the shelf uses it, what second reading is this brand's? Justify in "
                                  "defaults_used[].why or change the idea"))
            elif not str(entry.get("why") or "").strip():
                out.append(_f("logo.cliche", "gate", f"{tag}: listed in defaults_used without a why", measured=tag))
            else:
                out.append(_f("logo.cliche", "info", f"{tag}: justified in defaults_used", measured=tag))

    # 13. wordmark font licence (gate)
    has_text = bool(_load(build_dir, "master-wordmark.svg")) or rep is not None and rep.get("glyphs")
    if has_text or man.get("type") == "monogram":
        lic = str(font.get("license") or "").strip()
        if not lic:
            out.append(_f("logo.font-license", "gate", f"no licence recorded for {font.get('family')!r}",
                          fix="set type.<role>.license (OFL-1.1 permits logo use)"))
        elif not any(k in lic.lower() for k in OFL_LIKE) and not lg.get("font_permission"):
            out.append(_f("logo.font-license", "gate", f"{font.get('family')!r} is {lic}: outlining it into a logo "
                          "needs written permission", measured=lic, threshold="OFL/Apache/UFL or permission",
                          fix="record the permission in logo.font_permission or choose an OFL face"))

    # 14. symbol on the cap-height band in lockups (warn)
    for name in ("master-lockup-horizontal.svg",):
        text = _load(build_dir, name)
        if not text:
            continue
        m_band = re.search(r'data-band="([^"]+)"', text)
        m_box = re.search(r'data-symbol-box="([^"]+)"', text)
        m_cap = re.search(r'data-cap-height="([^"]+)"', text)
        if m_band and m_box and m_cap:
            top, bottom = (float(x) for x in m_band.group(1).split())
            sb = [float(x) for x in m_box.group(1).split()]
            cap = float(m_cap.group(1))
            off = abs((sb[1] + sb[3]) / 2 - (top + bottom) / 2) / cap
            if off > BAND_TOL_CAPS:
                out.append(_f("logo.cap-band", "warn", f"symbol centre {off:.2f} cap heights off the cap-height band",
                              measured=round(off, 3), threshold=f"<= {BAND_TOL_CAPS} (to calibrate)",
                              fix="align the symbol to the cap-height band (logolib.lockup)"))
    return out


# ---------------------------------------------------------------- build one set (used by cli_logos and pipeline)

def kept_logo(identity, palette, set_dir):
    """A kept logo (components.logo.mode = keep): logolib.variants re-colours every version from the source (which
    is never written); this measures the source's own colours on the light and dark grounds and returns
    (manifest, findings). The contact sheet shows the source as it is, labelled kept."""
    out = []
    info = logolib.kept_sources(identity, set_dir)
    if not info:
        return None, [_f("logo.kept-missing", "warn", "logo is kept but components.logo.source is not a file",
                         fix="set components.logo.source to the logo file (relative to the work folder)")]
    build_dir = os.path.join(set_dir, "logo", "build")
    os.makedirs(build_dir, exist_ok=True)
    try:
        man = logolib.variants(identity, palette, build_dir, set_dir=set_dir)
    except logolib.LogoError as e:  # e.g. a raster source: show it, derive nothing
        out.append(_f("logo.kept-variants", "warn", f"kept logo shown as is; no versions built: {e}"))
        man = {"schema": "brand-identity/logo-build@1", "kept": True, "source": info["src"], "versions": {},
               "dark_from": info["dark_from"] or "source as is", "dark_skipped": info["dark_skipped"]}
    for note in man.get("notes", []):
        out.append(_f("logo.kept-note", "info", note))
    if not man.get("dark"):
        man["dark"] = info["dark_file"] or (os.path.join(build_dir, "one-color-light.svg")
                                            if "one-color-light" in man["versions"] else info["src"])
        if not info["dark_text"] and "one-color-light" in man["versions"]:
            man["dark_from"] = "derived one-colour white"
    modes = (palette or {}).get("modes") or {}
    lbg = (modes.get("light") or {}).get("background") or "#ffffff"
    dbg = (modes.get("dark") or {}).get("background") or "#111111"
    # contrast of the source's own colours (warn: a kept logo is a constraint, not something to fix here)
    measured = {}
    for label, key, ground in (("light", "full-color", lbg), ("dark", "full-color-dark", dbg)):
        v = man["versions"].get(key)
        if not v:
            continue
        cols = list(dict.fromkeys(p["hex"] for p in v["parts"]))
        best = max(colorlib.contrast_ratio(c, ground) for c in cols)
        measured[f"kept-{label}"] = {"ground": ground, "colours": cols, "best_contrast": round(best, 2)}
        if best < CONTRAST_MIN:
            out.append(_f("logo.kept-contrast", "warn",
                          f"kept logo on {label} ground {ground}: best colour reaches {best:.2f}:1",
                          measured=round(best, 2), threshold=f">= {CONTRAST_MIN}:1 (WCAG 2.2 SC 1.4.11)",
                          fix="use the logo's other version on this ground or a tile behind it"))
    man["measured"] = measured
    out.append(_f("logo.kept", "info", f"kept logo shown as is ({os.path.basename(info['src'])}); "
                  f"dark: {man.get('dark_from')}"))
    with open(os.path.join(build_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return man, out


def build_set(rec):
    """Build and audit one synced set record (from logolib.sync_symbols). Returns (manifest or None, findings)."""
    findings = [_f("logo.sync", "warn", e) for e in rec.get("errors", [])]
    identity, palette = rec["identity"], rec["palette"]
    build_dir = os.path.join(rec["set_dir"], "logo", "build")
    mode = (((identity.get("components") or {}).get("logo") or {}).get("mode")) or "new"
    if mode == "keep":
        man, kf = kept_logo(identity, palette, rec["set_dir"])
        return man, findings + kf
    if mode == "none":
        return None, findings + [_f("logo.skipped", "info", "logo component is 'none': not built")]
    if not identity.get("logo"):
        return None, findings + [_f("logo.missing", "gate", "identity.logo is empty",
                                    fix="write the logo block in sets.json")]
    prev = None
    mp = os.path.join(build_dir, "manifest.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8") as fh:
                prev = json.load(fh)
        except (OSError, json.JSONDecodeError):
            prev = None
    try:
        manifest = logolib.variants(identity, palette, build_dir, set_dir=rec["set_dir"])
    except logolib.LogoError as e:
        return None, findings + [_f("logo.build", "gate", str(e))]
    except Exception as e:  # noqa: BLE001 - one broken set must not stop the others
        return None, findings + [_f("logo.build", "gate", f"{type(e).__name__}: {e}")]
    _history(build_dir, manifest, prev, identity)
    findings += audit(identity, palette, build_dir)
    with open(os.path.join(build_dir, "audit.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": identitylib.AUDIT_SCHEMA, "findings": findings}, fh, indent=2, ensure_ascii=False,
                  default=str)
        fh.write("\n")
    return manifest, findings


def _history(build_dir, manifest, prev, identity):
    sha = ((manifest.get("sources") or {}).get("symbol") or {}).get("sha256")
    if not sha:
        return
    p = os.path.join(build_dir, "history.json")
    hist = []
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                hist = json.load(fh)
        except (OSError, json.JSONDecodeError):
            hist = []
    if hist and hist[-1].get("sha256") == sha:
        return
    hist.append({"sha256": sha, "anchors": (manifest.get("anchors") or {}).get("symbol", 0),
                 "defect": ((identity.get("logo") or {}).get("defect") or "")})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(hist[-10:], fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- contact sheet

def _inline(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return re.sub(r"^<\?xml[^>]*>", "", text)


def _rel(path, start):
    """Relative path for the sheet's HTML; an absolute file URL when they sit on different Windows drives."""
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return "file:///" + os.path.abspath(path).replace(os.sep, "/").lstrip("/")


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def contact_sheet(records, out_png, cache_dir):
    """One PNG for all sets: per set the primary large (full colour), one colour on light and dark, the primary at
    its minimum size, and the 32 and 16 px renders (1x and 4x, light and dark). <= 1600 px long edge.
    Returns (png path, [missing small renders])."""
    html, width, height, missing = sheet_html(records, cache_dir)
    html_path = os.path.join(cache_dir, "logo-sheet.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    import render_png
    render_png.screenshot_html(html_path, out_png, width=width, height=height, full_page=False)
    return out_png, missing


def sheet_html(records, cache_dir):
    """The contact sheet's HTML (labels in the document language) -> (html, width, height, missing renders)."""
    import i18nlib
    os.makedirs(cache_dir, exist_ok=True)
    rows = []
    missing = []
    brand = next(((r.get("identity") or {}).get("brand") for r in records if (r.get("identity") or {}).get("brand")),
                 None) or {}
    t = i18nlib.for_brand(brand, os.path.dirname(os.path.abspath(cache_dir)))
    for rec in records:
        ident = rec["identity"] or {}
        st = ident.get("set") or {}
        lg = ident.get("logo") or {}
        man = rec.get("manifest")
        bd = os.path.join(rec["set_dir"], "logo", "build")
        face = ((ident.get("type") or {}).get("display") or {})
        loc = (man or {}).get("font", {}).get("location") or {}
        ltype = lg.get("type") or ""
        title = (f"<b>{_esc(rec['id'])}</b> {_esc(st.get('name') or '')}"
                 f"<span>{_esc(t.word('logo_type', ltype, ltype))} · {_esc(face.get('family') or '')} "
                 f"{_esc(' '.join(f'{k} {v:g}' for k, v in loc.items()))}</span>")
        gates = [f for f in rec.get("findings", []) if f["severity"] == "gate"]
        status = (f'<i class="bad">{_esc(t.n("card.gates", len(gates)))}</i>' if gates
                  else f'<i class="ok">{_esc(t("sheet.gates_ok"))}</i>')
        if not man:
            msg = t.finding(gates[0]) if gates else t("board.not_built")
            rows.append(f'<section><h2>{title}{status}</h2><div class="fail">{_esc(msg)}</div></section>')
            continue
        if man.get("kept"):
            lbg_k = ((rec.get("palette") or {}).get("modes") or {}).get("light", {}).get("background", "#ffffff")
            dbg_k = ((rec.get("palette") or {}).get("modes") or {}).get("dark", {}).get("background", "#111111")
            light_src, dark_src = _rel(man["source"], cache_dir), _rel(man["dark"], cache_dir)
            kimg = lambda path, h: (f'<img src="{_esc(path)}" style="height:{h}px;width:100%;'  # noqa: E731
                                    f'max-width:100%;object-fit:contain">')
            ksmall = lambda path, h: f'<img src="{_esc(path)}" style="height:{h}px;width:auto">'  # noqa: E731
            smalls = lambda path: "".join(  # noqa: E731
                f'<figure>{ksmall(path, px)}<figcaption>{px}</figcaption></figure>' for px in (32, 16))
            rows.append(f"""<section><h2>{title}<i class="kept">{_esc(t("card.kept"))}</i></h2>
<div class="grid">
  <div class="hero" style="background:{lbg_k}">{kimg(light_src, 150)}</div>
  <div class="col">
    <div class="mono" style="background:{lbg_k}">{kimg(light_src, 70)}</div>
    <div class="mono" style="background:{dbg_k}">{kimg(dark_src, 70)}</div>
  </div>
  <div class="col small">
    <div class="strip" style="background:{lbg_k}">{smalls(light_src)}</div>
    <div class="strip" style="background:{dbg_k}">{smalls(dark_src)}</div>
    <div class="min" style="background:{lbg_k}"><em>{_esc(t("sheet.dark_from", how=man.get("dark_from", "")))}</em></div>
  </div>
</div></section>""")
            continue
        v = man["versions"]

        def svg(name):
            p = os.path.join(bd, v[name]["svg"]) if name in v else None
            return _inline(p) if p and os.path.isfile(p) else ""

        def img(name, px, zoom=1, ground=None):
            if name not in v:
                missing.append(f"{rec['id']}:{name}")
                return f'<div class="miss">{_esc(t("sheet.missing"))}</div>'
            rel = os.path.relpath(os.path.join(bd, v[name]["png"]), cache_dir)
            bg = f' style="background:{ground}"' if ground else ""
            return (f'<figure{bg}><img src="{_esc(rel)}" width="{px * zoom}" height="{px * zoom}"'
                    f'{" class=px" if zoom > 1 else ""}><figcaption>{px}{"×" + str(zoom) if zoom > 1 else ""}'
                    f'</figcaption></figure>')

        modes = (rec.get("palette") or {}).get("modes") or {}
        lbg = (modes.get("light") or {}).get("background", "#ffffff")
        dbg = (modes.get("dark") or {}).get("background", "#111111")
        min_px = int(((lg.get("min_size") or {}).get("px")) or 24)
        zooms = ((32, 1), (32, 2), (16, 1), (16, 4))
        small_light = "".join(img(f"favicon-{px}", px, z) for px, z in zooms)
        if man.get("favicon_fallback"):
            small_light += f'<b class="fb">{_esc(t("sheet.fallback_letter", letter=man.get("icon_letter", "")))}</b>'
        small_mono = "".join(img(f"small-{px}-one-color-dark", px, z) for px, z in zooms)
        small_dark = "".join(img(f"small-{px}-one-color-light", px, z, dbg) for px, z in zooms)
        rows.append(f"""<section><h2>{title}{status}</h2>
<div class="grid">
  <div class="hero" style="background:{lbg}">{svg('full-color')}</div>
  <div class="col">
    <div class="mono" style="background:{lbg}">{svg('one-color-dark')}</div>
    <div class="mono" style="background:{dbg}">{svg('one-color-light')}</div>
  </div>
  <div class="col small">
    <div class="strip" style="background:{lbg}">{small_light}</div>
    <div class="strip" style="background:{lbg}">{small_mono}</div>
    <div class="strip" style="background:{dbg}">{small_dark}</div>
    <div class="min" style="background:{lbg}"><div style="height:{min_px}px">{svg('one-color-dark')}</div>
      <div style="height:{min_px}px;background:{dbg}">{svg('one-color-light')}</div><em>{_esc(t("sheet.min", px=min_px))}</em></div>
  </div>
</div></section>""")
    n = max(1, len(records))
    ideas = []
    for rec in records:
        lst = ((rec.get("identity") or {}).get("logo") or {}).get("ideas")
        if isinstance(lst, list) and lst:
            items = " · ".join(f"{i + 1}) {_esc(str(x))}" for i, x in enumerate(lst[:6]))
            ideas.append(f"<p><b>{_esc(t('sheet.ideas', id=rec['id']))}</b> {items}</p>")
    foot = f'<footer>{"".join(ideas)}</footer>' if ideas else ""
    foot_h = (12 + 30 * len(ideas)) if ideas else 0
    row_h = 340
    height = 24 + n * row_h + foot_h
    width = 1600
    if height > 1600:  # keep the long edge <= 1600 px
        row_h = (1600 - 24 - foot_h) // n
        height = 24 + n * row_h + foot_h
    html = f"""<!doctype html><html lang="{_esc(t.html_lang)}"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{width}px;background:#e9e9e6;font:12px/1.3 system-ui,sans-serif;color:#222;padding:12px 16px}}
section{{height:{row_h}px;padding:6px 0;border-bottom:1px solid #c9c9c4;display:flex;flex-direction:column}}
h2{{font-size:13px;font-weight:500;margin-bottom:6px;display:flex;gap:10px;align-items:baseline}}
h2 span{{color:#666}} h2 i{{font-style:normal;margin-left:auto;padding:1px 6px;border-radius:3px}}
.ok{{background:#d7ead9}} .bad{{background:#f3c9c4}} .kept{{background:#dde3f0}}
.grid{{display:grid;grid-template-columns:600px 400px 1fr;gap:8px;flex:1;min-height:0}}
.hero,.mono{{display:flex;align-items:center;justify-content:center;padding:14px;min-height:0;overflow:hidden}}
.hero svg{{max-width:100%;max-height:100%;width:auto;height:100%}}
.mono svg{{max-width:100%;height:100%}}
.col{{display:flex;flex-direction:column;gap:8px;min-height:0}} .col>div{{flex:1;min-height:0}}
.small>.strip{{flex:0 0 auto;height:84px}} .small>.min{{flex:1}}
.strip{{display:flex;align-items:center;gap:22px;padding:2px 12px}}
figure{{display:flex;flex-direction:column;align-items:center;gap:2px;padding:2px}}
figcaption{{font-size:9px;color:#888}} img.px{{image-rendering:pixelated}}
.min{{display:flex;align-items:center;gap:16px;padding:4px 10px}} .min div{{display:flex;padding:0 6px}}
.min svg{{height:100%;width:auto}} .min em{{font-size:10px;color:#888}}
.fail{{background:#f3c9c4;padding:12px;flex:1}} .miss{{color:#b00}}
.fb{{font-size:11px;font-weight:600;color:#9a3412;background:#fde7d3;padding:2px 6px;border-radius:3px}}
footer{{padding:8px 0 0;font-size:10px;line-height:1.35;color:#555}} footer p{{height:30px;overflow:hidden}}
</style></head><body>{''.join(rows)}{foot}</body></html>"""
    return html, width, height, missing


# ---------------------------------------------------------------- CLI

def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def cli_logos(args):
    """brand.py logos WORK [--sets A,B] [--full]: sync, build, audit, one contact sheet. Prints the sheet path and
    gate lines (<= 1.2 KB); details go to sets/X/logo/build/audit.json. Exit 1 on any gate."""
    identitylib.utf8_console()
    work = os.path.abspath(args.work)
    sets = [s for s in (getattr(args, "sets", None) or "").split(",") if s.strip()] or None
    try:
        records = logolib.sync_symbols(work, sets)
    except logolib.LogoError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not records:
        print("error: no sets to build (check sets.json and --sets)", file=sys.stderr)
        return 2
    for rec in records:
        rec["manifest"], rec["findings"] = build_set(rec)
    sheet_records = records
    if sets:  # the sheet always shows every set; the others from their last build
        built = {r["id"]: r for r in records}
        sheet_records = []
        for rec in logolib.sync_symbols(work, None, write=False):
            if rec["id"] in built:
                sheet_records.append(built[rec["id"]])
                continue
            bd = os.path.join(rec["set_dir"], "logo", "build")
            rec["manifest"] = _json(os.path.join(bd, "manifest.json"))
            rec["findings"] = (_json(os.path.join(bd, "audit.json")) or {}).get("findings", [])
            sheet_records.append(rec)
    sheet = os.path.join(work, SHEET_NAME)
    lines = []
    failed = False
    try:
        _p, missing = contact_sheet(sheet_records, sheet, os.path.join(work, ".cache"))
        lines.append(f"sheet: {sheet}")
        for m in missing:
            failed = True
            lines.append(f"GATE sheet: {m} render missing from the contact sheet")
    except Exception as e:  # noqa: BLE001 - the sheet is required; say why it failed
        failed = True
        lines.append(f"GATE sheet: contact sheet not rendered: {e}")
    full = getattr(args, "full", False)
    for rec in records:
        fs = rec["findings"]
        g = [f for f in fs if f["severity"] == "gate"]
        w = [f for f in fs if f["severity"] == "warn"]
        failed = failed or bool(g)
        name = ((rec["identity"] or {}).get("set") or {}).get("name") or ""
        lines.append(f"{rec['id']} {name}: {'FAIL' if g else 'ok'} · {len(g)} gate · {len(w)} warn")
        for f in (fs if full else g):
            lines.append(f"  {f['severity'].upper()} {f['id']}: {f['message']}"[:160])
        if not full:
            for f in [f for f in fs if f["id"] == "logo.role-tone"][:2]:
                lines.append(f"  info: {f['message']}"[:150])
        if not full and w:
            sync = [f for f in w if f["id"] == "logo.sync"]
            if sync:
                more = f" (+{len(sync) - 1} more)" if len(sync) > 1 else ""
                lines.append(f"  warn sync: {sync[0]['message']}"[:150] + more)
            rest = sorted({f["id"].split(".", 1)[1] for f in w if f["id"] != "logo.sync"})
            if rest:
                lines.append("  warn: " + ", ".join(rest))
    if any((r.get("manifest") or {}).get("glyphs") for r in records):
        lines.insert(1, "glyphs: sets/X/logo/build/wordmark-glyphs.json (+ small-glyphs.json): letter x ranges, "
                        "stems; index = data-glyph")
    text = "\n".join(lines)
    if not full and len(text.encode("utf-8")) > STDOUT_CAP:
        text = text.encode("utf-8")[:STDOUT_CAP - 60].decode("utf-8", "ignore").rsplit("\n", 1)[0]
        text += "\n... more in sets/X/logo/build/audit.json (--full)"
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="build, audit and contact-sheet every set's logo")
    ap.add_argument("work")
    ap.add_argument("--sets")
    ap.add_argument("--full", action="store_true")
    sys.exit(cli_logos(ap.parse_args()))
