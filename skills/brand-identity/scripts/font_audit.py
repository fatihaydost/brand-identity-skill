"""Font audit and catalogue search: the checks of docs/architecture.md section 4.3 on one family, and `brand.py fonts`.

Gates (measurably broken or irreversible):
  * coverage: 100% of each brief language's precomposed BASE exemplar characters (gflanguages; entries holding
    combining marks are skipped) are in the font's cmap.
  * licence: known and covering the uses (logo outline, web, app, PDF embed). Commercial and ITF FFL fonts get a
    note, not a gate; an unreadable licence or a restricted-embedding fsType for app/PDF use is a gate.
  * tabular figures (`tnum`, or default figures of equal width) when the brand shows numbers.
Warnings: auxiliary + punctuation exemplars missing; a text face with < 2 usable weights; I/l/1 raster similarity
(IoU > 0.9, heuristic, uncalibrated); high stroke contrast for the text role (heuristic, uncalibrated).
Info: GF Latin Core codepoints missing; estimated web weight (WOFF2).

Measurements are our own (fontTools + a small scanline rasteriser below); tools/build_font_catalog.py reuses them.
Public API: audit(family_or_path, langs, numbers=False, uses=("web","logo"), role=None, license=None,
location=None) -> list[finding]; cli_fonts(args) for `brand.py fonts search|audit`.
"""
import base64
import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import identitylib  # noqa: E402
import typelib  # noqa: E402
from typelib import FontError  # noqa: E402

__all__ = ["audit", "cli_fonts", "measure_font", "coverage", "confusable_iou", "stroke_contrast", "woff2_kb",
           "gf_latin_core", "families_covering", "search", "USES", "IOU_WARN", "CONTRAST_WARN"]

SKILL = os.path.dirname(HERE)
GF_LATIN_CORE = os.path.join(SKILL, "assets", "fonts", "gf-latin-core.txt")
AI_DEFAULTS = os.path.join(SKILL, "assets", "ai-defaults.json")
TAGS_URL = "https://raw.githubusercontent.com/google/fonts/main/tags/all/families.csv"
USES = ("logo", "web", "app", "pdf")
# Heuristic, uncalibrated: I/l IoU measured 0.79-0.94 on six families at 48 px during development.
IOU_WARN = 0.9
IOU_PPEM = 48
# Heuristic, uncalibrated: thin/thick stroke of "o" (1.0 = monoline). Didones sit far below; set from the
# catalogue's text-face distribution (tools/build_font_catalog.py prints it).
CONTRAST_WARN = 0.3
STDOUT_LIMIT = 1200
FIGURES = {"default": "tabular by default", "tnum": "tabular via tnum feature",
           "default-approx": "tabular by default (within 1% of em)",
           "tnum-approx": "tabular via tnum feature (within 1% of em)", None: "proportional only"}
WOFF2_TIMEOUT = 20  # seconds; the web-weight estimate is information only
# Base exemplars that orthography treats as optional: a missing one is a warning, not a gate.
#   U+1E9E LATIN CAPITAL LETTER SHARP S (German): official since 2017, but the Council for German Orthography
#   allows "SS" as the capital form, and uppercasing produces "SS" by default; a font without it still writes
#   correct German.
OPTIONAL_BASE = {"\u1E9E"}
FREE_LICENSES = ("OFL-1.1", "Apache-2.0", "UFL-1.0")
LICENSE_NOTES = {
    "commercial": "Commercial · licences per weight: desktop / web (pageviews) / app · trial OK for this preview "
                  "only · the client buys the licence",
    "ITF-FFL-2.0": "Fontshare ITF FFL: free, but no subsetting/conversion and files can't be handed to clients; "
                   "each party downloads from Fontshare",
}


# ---------------------------------------------------------------- outlines and raster (no FreeType needed)

def _flatten(glyphset, glyph_name, steps=8):
    """Glyph outline as closed polygons [(x, y), ...] in font units (curves flattened)."""
    from fontTools.pens.basePen import BasePen

    class Pen(BasePen):
        def __init__(self, gs):
            super().__init__(gs)
            self.polys, self.cur = [], None

        def _moveTo(self, p):
            self.cur = [p]
            self.polys.append(self.cur)

        def _lineTo(self, p):
            self.cur.append(p)

        def _curveToOne(self, p1, p2, p3):
            x0, y0 = self._getCurrentPoint()
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                self.cur.append((u ** 3 * x0 + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                                 u ** 3 * y0 + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))

        def _qCurveToOne(self, p1, p2):
            x0, y0 = self._getCurrentPoint()
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                self.cur.append((u * u * x0 + 2 * u * t * p1[0] + t * t * p2[0],
                                 u * u * y0 + 2 * u * t * p1[1] + t * t * p2[1]))

        def _closePath(self):
            self.cur = None

        def _endPath(self):
            self.cur = None

    pen = Pen(glyphset)
    glyphset[glyph_name].draw(pen)
    return [p for p in pen.polys if len(p) > 2]


def _edges(polys):
    out = []
    for poly in polys:
        for i, (x0, y0) in enumerate(poly):
            x1, y1 = poly[(i + 1) % len(poly)]
            if y0 != y1:
                out.append((x0, y0, x1, y1))
    return out


def _spans(edges, y):
    """Ink intervals [(x_start, x_end)] of a horizontal line at y under the non-zero winding rule."""
    xs = []
    for x0, y0, x1, y1 in edges:
        if (y0 <= y < y1) or (y1 <= y < y0):
            xs.append((x0 + (y - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1))
    xs.sort()
    spans, wind, start = [], 0, None
    for x, d in xs:
        before = wind
        wind += d
        if before == 0 and wind != 0:
            start = x
        elif before != 0 and wind == 0:
            spans.append((start, x))
    return spans


def _raster(polys, scale, x_off, y_top, width, height, sub=4):
    """Anti-aliased coverage bitmap (rows of floats 0..1): exact horizontal coverage, `sub` samples vertically."""
    edges = _edges(polys)
    rows = []
    for py in range(height):
        row = [0.0] * width
        for s in range(sub):
            y = y_top - (py + (s + 0.5) / sub) / scale
            for a, b in _spans(edges, y):
                a = a * scale + x_off
                b = b * scale + x_off
                a, b = max(a, 0.0), min(b, float(width))
                if b <= a:
                    continue
                ia, ib = int(a), int(b)
                if ia == ib:
                    row[ia] += (b - a) / sub
                    continue
                row[ia] += (ia + 1 - a) / sub
                for i in range(ia + 1, min(ib, width)):
                    row[i] += 1.0 / sub
                if ib < width:
                    row[ib] += (b - ib) / sub
        rows.append(row)
    return rows


def _glyphset(font, location=None):
    if location and "fvar" in font:
        axes = {a.axisTag: (a.minValue, a.maxValue) for a in font["fvar"].axes}
        loc = {t: min(max(float(v), axes[t][0]), axes[t][1]) for t, v in location.items() if t in axes}
        return font.getGlyphSet(location=loc)
    return font.getGlyphSet()


def _text_location(font, location=None):
    """Where text-size measurements are taken: wght 400 and opsz 14 when those axes exist, else defaults."""
    if "fvar" not in font:
        return None
    loc = {}
    for a in font["fvar"].axes:
        if a.axisTag == "wght":
            loc["wght"] = 400
        elif a.axisTag == "opsz":
            loc["opsz"] = 14
    loc.update(location or {})
    return loc


def confusable_iou(font, location=None, ppem=IOU_PPEM, pairs=(("I", "l"), ("I", "1"), ("l", "1"))):
    """Soft IoU of glyph pairs rasterised at `ppem`, baseline-aligned, best of +-3 px horizontal shifts.

    1.0 = identical pixels. Heuristic, uncalibrated (see IOU_WARN). Returns {"I/l": 0.86, ...}; a pair with a
    missing glyph is left out.
    """
    cmap = font.getBestCmap() or {}
    gs = _glyphset(font, location)
    upm = font["head"].unitsPerEm
    scale = ppem / upm
    os2 = font.get("OS/2")
    asc = getattr(os2, "sTypoAscender", None) or font["hhea"].ascent
    desc = getattr(os2, "sTypoDescender", None) or font["hhea"].descent
    height = int(math.ceil((asc - desc) * scale)) + 2
    width = ppem * 2
    cache = {}

    def bitmap(ch):
        if ch not in cache:
            gn = cmap.get(ord(ch))
            if gn is None:
                cache[ch] = None
            else:
                polys = _flatten(gs, gn)
                xs = [x for p in polys for x, _ in p] or [0]
                center = (min(xs) + max(xs)) / 2 * scale
                cache[ch] = _raster(polys, scale, width / 2 - center, asc + scale ** -1, width, height)
        return cache[ch]

    out = {}
    for a, b in pairs:
        A, B = bitmap(a), bitmap(b)
        if A is None or B is None:
            continue
        best = 0.0
        for dx in range(-3, 4):
            inter = union = 0.0
            for ra, rb in zip(A, B):
                for i in range(width):
                    j = i - dx
                    va = ra[i]
                    vb = rb[j] if 0 <= j < width else 0.0
                    if va or vb:
                        inter += min(va, vb)
                        union += max(va, vb)
            if union:
                best = max(best, inter / union)
        out[f"{a}/{b}"] = round(best, 3)
    return out


def stroke_contrast(font, location=None):
    """Thin/thick stroke ratio of "o" (1.0 = monoline, Didones ~0.2): vertical ink runs through the bowl's centre
    column (thin, top/bottom) over horizontal runs through its middle row (thick, sides). Heuristic, uncalibrated."""
    cmap = font.getBestCmap() or {}
    gn = cmap.get(ord("o")) or cmap.get(ord("O"))
    if gn is None:
        return None
    polys = _flatten(_glyphset(font, location), gn, steps=24)
    pts = [pt for p in polys for pt in p]
    if not pts:
        return None
    xmin, xmax = min(x for x, _ in pts), max(x for x, _ in pts)
    ymin, ymax = min(y for _, y in pts), max(y for _, y in pts)
    thick = [b - a for a, b in _spans(_edges(polys), (ymin + ymax) / 2)]
    # vertical runs: swap axes and reuse the horizontal span finder
    swapped = [[(y, x) for x, y in p] for p in polys]
    thin = [b - a for a, b in _spans(_edges(swapped), (xmin + xmax) / 2)]
    if not thick or not thin or max(thick) <= 0:
        return None
    return round(min(thin) / max(thick), 3)


def round_aspect(font, location=None):
    """Width/height of the "o" and "O" bounding boxes (1.0 = as wide as tall): {"o_aspect", "O_aspect"}.
    Geometric sans faces draw near-circular bowls; a proxy for class search, heuristic, uncalibrated."""
    from fontTools.pens.boundsPen import BoundsPen
    cmap = font.getBestCmap() or {}
    gs = _glyphset(font, location)
    out = {}
    for ch, key in (("o", "o_aspect"), ("O", "O_aspect")):
        gn = cmap.get(ord(ch))
        if gn is None:
            continue
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        if bp.bounds and bp.bounds[3] > bp.bounds[1]:
            x0, y0, x1, y1 = bp.bounds
            out[key] = round((x1 - x0) / (y1 - y0), 3)
    return out


def x_metrics(font):
    """x-height/cap-height, x-height/UPM and width (mean a-z advance / x-height), measured from glyphs."""
    cmap = font.getBestCmap() or {}
    gs = font.getGlyphSet()
    from fontTools.pens.boundsPen import BoundsPen

    def top(ch):
        gn = cmap.get(ord(ch))
        if gn is None:
            return None
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        return bp.bounds[3] if bp.bounds else None

    upm = font["head"].unitsPerEm
    xh, ch = top("x"), top("H")
    adv = [font["hmtx"][cmap[ord(c)]][0] for c in "abcdefghijklmnopqrstuvwxyz" if ord(c) in cmap]
    return {"x_cap": round(xh / ch, 3) if xh and ch else None, "x_upm": round(xh / upm, 3) if xh else None,
            "width": round(sum(adv) / len(adv) / xh, 3) if adv and xh else None}


def woff2_kb(path, codepoints, timeout=WOFF2_TIMEOUT):
    """Estimated WOFF2 size (KB) of a font subset to `codepoints` with all layout features kept.

    The work runs in a daemon thread; after `timeout` seconds (None = no limit) TimeoutError is raised on every
    platform and the thread is abandoned (it ends with the process). Reason: when HarfBuzz cannot repack a subset
    GPOS, fontTools' own overflow resolution can run for many minutes, and the size is information only."""
    import logging
    import threading
    from fontTools import subset
    from fontTools.ttLib import TTFont
    quiet = [logging.getLogger(n) for n in ("fontTools.subset", "fontTools.ttLib", "fontTools.ttLib.tables")]
    levels = [lg.level for lg in quiet]
    for lg in quiet:
        lg.setLevel(logging.ERROR)  # "FFTM NOT subset" and similar chatter is not a finding
    box = {}

    def work():
        try:
            box["kb"] = _woff2_kb(path, codepoints, subset, TTFont)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            box["error"] = e

    worker = threading.Thread(target=work, name="woff2-estimate", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"WOFF2 estimate took longer than {timeout} s")
    for lg, lv in zip(quiet, levels):
        lg.setLevel(lv)
    if "error" in box:
        raise box["error"]
    return box["kb"]


def _woff2_kb(path, codepoints, subset, TTFont):
    font = TTFont(str(path))
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.flavor = "woff2"
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=codepoints)
    sub.subset(font)
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    return round(len(buf.getvalue()) / 1024)


# Google Fonts' "latin" and "latin-ext" unicode-range sets (as served by the CSS2 API), used for size estimates.
LATIN = (list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) +
         [0x131, 0x152, 0x153, 0x2BB, 0x2BC, 0x2C6, 0x2DA, 0x2DC, 0x304, 0x308, 0x329, 0x2000, 0x2001, 0x2002,
          0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x200B, 0x2013, 0x2014, 0x2018, 0x2019,
          0x201A, 0x201C, 0x201D, 0x201E, 0x2022, 0x2026, 0x2039, 0x203A, 0x2044, 0x2074, 0x20AC, 0x2122, 0x2191,
          0x2193, 0x2212, 0x2215, 0xFEFF, 0xFFFD])
LATIN_EXT = sorted(set(LATIN) | set(range(0x100, 0x250)) | set(range(0x1E00, 0x1F00)) | {0x259, 0x2C60, 0x2C7F})


# ---------------------------------------------------------------- coverage

def _tokens(chars):
    """Exemplar string -> list of precomposed tokens; entries holding combining marks are skipped."""
    out = []
    for tok in (chars or "").split():
        tok = unicodedata.normalize("NFC", tok.strip("{}"))
        if not tok or any(unicodedata.category(c).startswith("M") for c in tok):
            continue
        out.append(tok)
    return out


def coverage(cmap_codepoints, lid):
    """Coverage of one gflanguages id: {"base": [missing...], "base_total": n, "extended": [missing aux/punct]}."""
    lang = typelib.languages()[lid]
    have = set(cmap_codepoints)
    ex = lang.exemplar_chars
    tokens = _tokens(ex.base)
    base = [t for t in tokens if t not in OPTIONAL_BASE]
    optional = [t for t in tokens if t in OPTIONAL_BASE]
    miss_base = [t for t in base if not all(ord(c) in have for c in t)]
    extra = _tokens(ex.auxiliary) + _tokens(ex.punctuation)
    miss_ext = [t for t in extra if not all(ord(c) in have for c in t)]
    return {"base": miss_base, "base_total": len(base), "extended": miss_ext, "extended_total": len(extra),
            "optional": [t for t in optional if not all(ord(c) in have for c in t)],
            "script": lid.split("_", 1)[1] if "_" in lid else ""}


_CORE = None


def gf_latin_core():
    """GF Latin Core codepoints from assets/fonts/gf-latin-core.txt (vendored from google/glyphsets)."""
    global _CORE
    if _CORE is None:
        cps = []
        with open(GF_LATIN_CORE, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*0x([0-9A-Fa-f]{4,6})\b", line)
                if m:
                    cps.append(int(m.group(1), 16))
        _CORE = cps
    return _CORE


def _bits(rec):
    b = (rec.get("coverage") or {}).get("langs")
    return base64.b64decode(b) if b else b""


SCRIPT_SUBSETS = {"Hans": "chinese-simplified", "Hant": "chinese-traditional", "Jpan": "japanese", "Kore": "korean",
                  "Latn": "latin-ext"}


def _script_subset(lid):
    script = lid.split("_", 1)[1] if "_" in lid else ""
    if script in SCRIPT_SUBSETS:
        return SCRIPT_SUBSETS[script]
    name = typelib.scripts().get(script)
    return name.lower().replace(" ", "-") if name else None


def covers(rec, lid, index):
    """True/False whether a catalogue record covers lid: the measured bitset, or (unmeasured records) the script's
    Google subset. None when the catalogue cannot tell (not indexed and unmeasured, or not a Google record)."""
    if rec.get("source") != "google":
        script, sub = (rec.get("script") or "").lower(), _script_subset(lid)
        return False if script and sub and script not in sub else None
    if rec.get("measured", True) and lid in index:
        k = index.index(lid)
        bits = _bits(rec)
        return bool(len(bits) > k // 8 and bits[k // 8] >> (7 - k % 8) & 1)
    if rec.get("measured", True) is False:
        sub = _script_subset(lid)
        return sub in (rec.get("subsets") or []) if sub else None
    return None


def families_covering(lid, catalog=None, category=None, n=3, stroke=None, exclude=(), skip_defaults=False):
    """Up to n catalogue families (by popularity) whose base coverage includes lid; None if lid is not indexed in
    the catalogue (then the catalogue cannot answer). skip_defaults drops AI-default families; stroke ("slab", ...)
    is preferred when enough families share it."""
    cat = catalog if catalog is not None else typelib.load_catalog()
    index = cat.get("lang_index") or []
    if lid not in index:
        return None
    defaults = _ai_default_fonts() if skip_defaults else set()
    skip = {typelib._norm(x) for x in exclude}
    hits = []
    for rec in cat.get("families", []):
        if category and rec.get("category") != category:
            continue
        if typelib._norm(rec["family"]) in skip:
            continue
        if skip_defaults and (rec.get("ai_default") or typelib._norm(rec["family"]) in defaults):
            continue
        if covers(rec, lid, index):
            hits.append(rec)
    hits.sort(key=lambda r: (r.get("popularity") or 10 ** 6, r["family"]))
    if stroke:
        same = [r for r in hits if r.get("stroke") == stroke]
        if len(same) >= n:
            hits = same
    return [r["family"] for r in hits[:n]]


# ---------------------------------------------------------------- licence

def detect_license(info):
    """Licence id from a font file's name table (IDs 13/14): OFL-1.1 | Apache-2.0 | UFL-1.0 | ITF-FFL-2.0 | None."""
    text = " ".join(x for x in (info["names"].get("license"), info["names"].get("license_url")) if x).lower()
    if re.search(r"open font licen[cs]e|scripts\.sil\.org/ofl|openfontlicense\.org|\bofl\b", text):
        return "OFL-1.1"
    if re.search(r"apache licen[cs]e|apache\.org/licenses", text):
        return "Apache-2.0"
    if "ubuntu font licen" in text:
        return "UFL-1.0"
    if re.search(r"fontshare|itf free font licen[cs]e|\bffl\b", text):
        return "ITF-FFL-2.0"
    return None


def canon_license(text):
    """Licence spelling -> id: "OFL", "SIL OFL 1.1", "ofl-1.1" -> OFL-1.1; "Apache 2" -> Apache-2.0; else as given."""
    t = re.sub(r"[\s_]+", "-", (text or "").strip().lower())
    if re.fullmatch(r"(sil-)?(ofl|open-font-licen[cs]e)(-1\.1|-v?1\.1)?", t):
        return "OFL-1.1"
    if re.fullmatch(r"apache(-licen[cs]e)?(-2(\.0)?)?", t):
        return "Apache-2.0"
    if re.fullmatch(r"ufl(-1\.0)?|ubuntu-font-licen[cs]e", t):
        return "UFL-1.0"
    if re.fullmatch(r"(itf-)?ffl(-2\.0)?|itf-free-font-licen[cs]e", t):
        return "ITF-FFL-2.0"
    return text


def _sibling_license(path, family):
    """Licence id from a licence text next to a user font (OFL.txt, OFL-<Family>.txt, LICENSE...), or None.

    A file naming another family is ignored; with several candidates the one naming this family wins."""
    texts = []
    for q in Path(path).parent.iterdir():
        if q.is_file() and re.match(r"(ofl|license|licence|copying)", q.name, re.I) and q.stat().st_size < 200_000:
            try:
                texts.append((q.name, q.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    key = typelib._norm(family)
    named = [t for n, t in texts if key and key in typelib._norm(n)]
    unnamed = [t for n, t in texts if not re.search(r"[-_ ]", Path(n).stem)]
    for text in named or unnamed:
        lic = detect_license({"names": {"license": text[:4000], "license_url": None}})
        if lic:
            return lic
    return None


# ---------------------------------------------------------------- measuring one font (audit + catalogue)

def measure_font(path, iou=True, contrast=True, location=None):
    """Our fontTools measurements of one file (shared with tools/build_font_catalog.py)."""
    from fontTools.ttLib import TTFont
    font = TTFont(str(path), fontNumber=0)
    cmap = font.getBestCmap() or {}
    loc = _text_location(font, location)
    out = {"metrics": x_metrics(font), "cmap": set(cmap)}
    out["metrics"]["contrast"] = stroke_contrast(font, loc) if contrast else None
    out["metrics"].update(round_aspect(font, loc))
    out["iou"] = confusable_iou(font, loc) if iou else {}
    font.close()
    return out


def _weights_of_files(paths):
    from fontTools.ttLib import TTFont
    out = set()
    for p in paths:
        try:
            f = TTFont(str(p), lazy=True)
            if "fvar" in f:
                ax = {a.axisTag: (a.minValue, a.maxValue) for a in f["fvar"].axes}
                if "wght" in ax:
                    out.update({ax["wght"][0], ax["wght"][1]})
            os2 = f.get("OS/2")
            if os2 and not (os2.fsSelection & 1):
                out.add(os2.usWeightClass)
            f.close()
        except Exception:  # noqa: BLE001 - a sibling that is not a font is simply ignored
            continue
    return sorted(out)


def _usable_weights(weights, axes):
    """Usable text weights: a wght axis spanning 400-700 counts as two; static: a regular (300-500) plus a bold
    (>= 600) upright file."""
    if "wght" in axes:
        lo, hi = axes["wght"][0], axes["wght"][1]
        return 2 if lo <= 400 and hi >= 700 else (1 if hi - lo < 200 else 2)
    reg = any(300 <= w <= 500 for w in weights)
    bold = any(w >= 600 for w in weights)
    return int(reg) + int(bold) if weights else 1


def _target(family_or_path):
    """Find what to audit: a user file, a Google family (downloaded) or a catalogue-only family (Fontshare)."""
    p = Path(str(family_or_path)).expanduser()
    if p.is_file():
        info = typelib.font_info(p)
        fam = info["names"]["family"] or p.stem
        sibs = [q for q in p.parent.iterdir() if q.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2")]
        same = []
        from fontTools.ttLib import TTFont
        for q in sibs:
            try:
                f = TTFont(str(q), lazy=True)
                names = {f["name"].getBestFamilyName(), f["name"].getDebugName(16)}
                f.close()
            except Exception:  # noqa: BLE001
                continue
            if fam in names:
                same.append(q)
        axes = {a["tag"]: [a["min"], a["max"]] for a in info["axes"]}
        lic = detect_license(info) or _sibling_license(p, fam)
        return {"kind": "user", "family": fam, "path": p, "info": info, "license": lic,
                "weights": _weights_of_files(same or [p]), "axes": axes, "source": "user"}
    rec = typelib.catalog_entry(str(family_or_path))
    if rec and rec.get("source") == "fontshare":
        return {"kind": "catalog", "family": rec["family"], "rec": rec, "license": rec.get("license"),
                "source": "fontshare", "weights": rec.get("weights") or [], "axes": rec.get("axes") or {}}
    meta = typelib.google_family(str(family_or_path))
    path = typelib.google_file(meta, typelib._pick_file(meta, {}))
    weights = sorted({f["weight"] for f in meta["fonts"] if f["style"] == "normal"})
    return {"kind": "google", "family": meta["family"], "path": path, "info": typelib.font_info(path),
            "license": meta["license"], "weights": weights, "axes": meta["axes"], "source": "google", "meta": meta}


def _fmt_chars(tokens, limit=12):
    s = " ".join(tokens[:limit])
    return s + (f" (+{len(tokens) - limit})" if len(tokens) > limit else "")


def _audit(family_or_path, langs, numbers=False, uses=("web", "logo"), role=None, license=None, location=None):
    F = identitylib.finding
    bad = [u for u in uses if u not in USES]
    if bad:
        raise ValueError(f"unknown use(s) {bad}; use {', '.join(USES)}")
    lids = [typelib.lang_id(code) for code in (langs or ["en"])]
    t = _target(family_or_path)
    fam = t["family"]
    findings = []
    facts = {"family": fam, "source": t["source"], "license": license or t.get("license"), "langs": lids,
             "file": str(t.get("path") or ""), "axes": t.get("axes") or {}, "weights": t.get("weights") or []}

    # licence (gate when unknown or not covering the uses; a note for commercial / FFL)
    detected = t.get("license")
    facts["license_detected"] = detected
    if license and detected and canon_license(license) != canon_license(detected):
        where = "the Google Fonts catalogue" if t["source"] in ("google", "fontshare") else "the font file"
        findings.append(F("type.license-mismatch", "type", "warn",
                          f"{fam}: declared licence {license!r} differs from {detected} found in {where}",
                          measured=detected, threshold=license,
                          suggested_fix="check the licence; the declared one is used for the checks below"))
    lic = canon_license(license) if license else detected
    if not lic:
        findings.append(F("type.license", "type", "gate",
                          f"{fam}: licence unknown (no OFL/Apache/UFL/FFL text in the font's name table)",
                          measured=None, threshold="known licence covering " + ", ".join(uses),
                          suggested_fix="confirm the licence covers logo outline, web, app and PDF embedding, then "
                                        "pass it (identity type.<role>.license), or pick a Google Fonts family"))
    elif lic in LICENSE_NOTES or lic not in FREE_LICENSES:
        note = LICENSE_NOTES.get(lic, LICENSE_NOTES["commercial"])
        findings.append(F("type.license", "type", "info", f"{fam}: {note}", measured=lic, threshold=None,
                          suggested_fix="show this note on the card; offer a rendered Google Fonts alternative"))
    fs_type = (t.get("info") or {}).get("metrics", {}).get("fs_type", 0) or 0
    if fs_type & 0x0002 and any(u in uses for u in ("app", "pdf")):
        findings.append(F("type.license.embedding", "type", "gate",
                          f"{fam}: fsType marks the font 'restricted licence embedding'; it cannot be embedded in "
                          "PDFs or apps", measured=f"fsType=0x{fs_type:04x}", threshold="embedding allowed",
                          suggested_fix="ask the foundry for an embedding licence or choose another family"))

    if t["kind"] == "catalog":
        findings.append(F("type.coverage", "type", "warn",
                          f"{fam} is a {t['source']} family: files are not downloaded, coverage not measured",
                          suggested_fix=f"download it from {t['source']} and run `brand.py fonts audit <file> "
                                        f"--langs {','.join(langs or ['en'])}`"))
        if numbers:
            findings.append(F("type.tnum", "type", "warn", f"{fam}: tabular figures not measured",
                              suggested_fix="audit the downloaded file with --numbers"))
        return findings, facts

    info = t["info"]
    m = measure_font(t["path"], location=location)
    cps = m["cmap"]
    facts.update({"variable": info["variable"], "metrics": m["metrics"], "iou": m["iou"], "tnum": info["tnum"],
                  "tabular": info["tabular"],
                  "features": info["features"], "ss_cv": info["ss_cv"], "locl": info["locl"]})

    # coverage gate + warn per language
    for lid in lids:
        cov = coverage(cps, lid)
        name = typelib.languages()[lid].name
        if cov["base_total"] == 0:
            findings.append(F(f"type.coverage.{lid}", "type", "warn",
                              f"{name} ({lid}) has no base exemplar data in gflanguages; coverage not checked"))
            continue
        if cov["base"]:
            rec = typelib.catalog_entry(fam) or {}
            alts = families_covering(lid, category=rec.get("category"), stroke=rec.get("stroke"), exclude=[fam],
                                     skip_defaults=True)
            fix = (f"pick a {rec.get('category', 'family')} that covers {lid}, e.g. {', '.join(alts)}"
                   if alts else
                   (f"no family in the catalogue covers {lid}; use a script-specific family (e.g. Noto) for it"
                    if alts is not None else f"the catalogue does not index {lid}; audit candidates directly"))
            findings.append(F(f"type.coverage.{lid}", "type", "gate",
                              f"{fam} lacks {name} base letters: {_fmt_chars(cov['base'])}",
                              measured=f"{cov['base_total'] - len(cov['base'])}/{cov['base_total']}",
                              threshold="100% base exemplars", suggested_fix=fix))
        if cov["optional"]:
            findings.append(F(f"type.coverage.{lid}.optional", "type", "warn",
                              f"{fam} lacks optional {name} letters: {_fmt_chars(cov['optional'])}",
                              measured=f"{len(cov['optional'])} missing", threshold="optional base letters",
                              suggested_fix="fine if all-caps text may use the conventional fallback (e.g. SS for "
                                            "ẞ); else another family"))
        if cov["extended"]:
            findings.append(F(f"type.coverage.{lid}.extended", "type", "info",
                              f"{fam} lacks {name} auxiliary/punctuation characters: {_fmt_chars(cov['extended'])}",
                              measured=f"{cov['extended_total'] - len(cov['extended'])}/{cov['extended_total']}",
                              threshold="100% auxiliary + punctuation",
                              suggested_fix="fine if the brand never writes them (loanwords, names); else another "
                                            "family"))
    if any(lid.endswith("_Latn") for lid in lids):
        core = gf_latin_core()
        missing = [c for c in core if c not in cps]
        if missing:
            findings.append(F("type.coverage.gf-latin-core", "type", "info",
                              f"{fam} misses {len(missing)}/{len(core)} GF Latin Core codepoints (reach beyond the "
                              "brief's languages)", measured=f"{len(core) - len(missing)}/{len(core)}",
                              threshold="GF Latin Core",
                              suggested_fix="matters only if the brand may add more Latin-script languages"))

    # tabular figures
    if numbers and not info["tabular"]:
        why = ("its `tnum` feature leaves the digits more than 1% of the em apart" if info["tnum"]
               else "no `tnum` feature")
        findings.append(F("type.tnum", "type", "gate", f"{fam} has no tabular figures (proportional default digits, "
                          f"{why}); numbers in tables and prices will jitter", measured="proportional",
                          threshold="tnum or tabular default figures",
                          suggested_fix="pick a family with `tnum` (fonts search --numbers) or a mono/data face for "
                                        "figures"))

    text_role = role in (None, "text")
    as_text = "" if role == "text" else " as a text face"
    usable = _usable_weights(t["weights"], t["axes"])
    if text_role and usable < 2:
        wdesc = (f"wght {t['axes']['wght'][0]:g}-{t['axes']['wght'][1]:g}" if "wght" in t["axes"]
                 else ", ".join(str(w) for w in t["weights"]) or "1 weight")
        findings.append(F("type.weights", "type", "warn",
                          f"{fam} has < 2 usable weights{as_text} ({wdesc}); hierarchy will need faux bold",
                          measured=usable, threshold="regular (300-500) + bold (>= 600), or wght 400-700",
                          suggested_fix="use it for display only, or pick a family with 400 and 600/700"))

    iou = m["iou"]
    if iou and max(iou.values()) > IOU_WARN:
        worst = max(iou, key=iou.get)
        ss = info["ss_cv"]
        fix = ("try " + ", ".join(f"{k} {v!r}" if v else k for k, v in list(ss.items())[:4])
               if ss else "no ss/cv alternates; choose another face for small text and data")
        findings.append(F("type.confusables", "type", "info" if role == "display" else "warn",
                          f"{fam}: {worst} look alike at small sizes (IoU {iou[worst]:.2f} at {IOU_PPEM} px)",
                          measured=iou[worst], threshold=f"{IOU_WARN} (heuristic, uncalibrated)",
                          suggested_fix=fix))

    con = m["metrics"].get("contrast")
    if text_role and con is not None and con < CONTRAST_WARN:
        findings.append(F("type.contrast", "type", "warn",
                          f"{fam}: high stroke contrast (thin/thick {con:.2f}){as_text}; hairlines break up at "
                          "small sizes", measured=con, threshold=f">= {CONTRAST_WARN} (heuristic, uncalibrated)",
                          suggested_fix="keep it for display sizes; pair a lower-contrast text face"))

    # web weight (info)
    need = set(range(0x20, 0x7F))
    for lid in lids:
        ex = typelib.languages()[lid].exemplar_chars
        for tok in _tokens(ex.base) + _tokens(ex.auxiliary) + _tokens(ex.punctuation):
            need.update(ord(c) for c in tok)
    try:
        kb = woff2_kb(t["path"], sorted(need & set(cps)))
        what = "one variable file, all weights" if info["variable"] else "per weight file"
        findings.append(F("type.web-weight", "type", "info",
                          f"{fam}: ~{kb} KB WOFF2 for the brief's characters ({what}; estimate)", measured=kb,
                          threshold=None, suggested_fix=None))
        facts["woff2_kb"] = kb
    except TimeoutError as e:
        findings.append(F("type.web-weight", "type", "info", f"{fam}: web weight skipped: timeout ({e})",
                          measured=None, threshold=None, suggested_fix=None))
    except Exception as e:  # noqa: BLE001 - size is information only
        print(f"note: web weight estimate skipped ({e})", file=sys.stderr)
    return findings, facts


def audit(family_or_path, langs, numbers=False, uses=("web", "logo"), role=None, license=None, location=None):
    """Audit one family (Google Fonts name, catalogue name or font file path) -> list of findings (docs/architecture.md section 3.5).

    langs: brief language codes ("en", "tr"); numbers: the product shows figures (tnum gate); uses: subset of
    logo|web|app|pdf the licence must cover. Extra keywords: role "display"|"text" (None = treat as text),
    license (declared licence id, e.g. "commercial"), location (measure at this axis location).
    Raises FontError when the family/file cannot be found, ValueError on bad arguments.
    """
    return _audit(family_or_path, langs, numbers, uses, role, license, location)[0]


# ---------------------------------------------------------------- catalogue search

def _cat(*names):
    return lambda r: r.get("category") in names


def _m(r, key, default=None):
    return (r.get("metrics") or {}).get(key, default)


def _geometric(r):
    # heuristic, uncalibrated: near-circular o and O (geometric sans measured 0.95-1.03, grotesques 0.84-0.94)
    return r.get("category") == "sans-serif" and _m(r, "o_aspect", 0) >= 0.95 and _m(r, "O_aspect", 0) >= 0.93


def _serif_contrast(lo, hi):
    def pred(r):
        c = _m(r, "contrast")
        return r.get("category") == "serif" and r.get("stroke") != "slab" and c is not None and lo <= c < hi
    return pred


# Class names for `fonts search --class`. tags: Google's human-assessed class tags (score >= 50; fetched at
# runtime into the cache). proxy: our measurements, used when the tags cannot be fetched (heuristic,
# uncalibrated; noted in the output). Grotesque vs humanist sans is not separable from our measurements.
CLASS_DEFS = {
    "sans": {"aliases": ("sans-serif",), "proxy": _cat("sans-serif")},
    "serif": {"aliases": (), "proxy": _cat("serif")},
    "slab": {"aliases": ("slab-serif", "egyptian", "clarendon"), "tags": ("/Slab/",),
             "proxy": lambda r: r.get("stroke") == "slab"},
    "mono": {"aliases": ("monospace", "monospaced"), "proxy": _cat("monospace")},
    "display": {"aliases": (), "proxy": _cat("display")},
    "handwriting": {"aliases": ("script", "handwritten", "hand"), "proxy": _cat("handwriting")},
    "geometric": {"aliases": ("geometric-sans",), "tags": ("/Sans/Geometric",), "proxy": _geometric},
    "grotesque": {"aliases": ("grotesk", "grotesque-sans", "grot"), "tags": ("/Sans/Grotesque", "/Sans/Neo Grotesque"),
                  "proxy": lambda r: r.get("category") == "sans-serif" and not _geometric(r),
                  "proxy_note": "proxy = sans that is not geometric (grotesque and humanist not separable)"},
    "neo-grotesque": {"aliases": ("neo-grotesk", "neogrotesque", "neogrotesk", "swiss"),
                      "tags": ("/Sans/Neo Grotesque",),
                      "proxy": lambda r: r.get("category") == "sans-serif" and not _geometric(r),
                      "proxy_note": "proxy = sans that is not geometric (grotesque and humanist not separable)"},
    "humanist": {"aliases": ("humanist-sans",), "tags": ("/Sans/Humanist",),
                 "proxy": lambda r: r.get("category") == "sans-serif" and not _geometric(r),
                 "proxy_note": "proxy = sans that is not geometric (grotesque and humanist not separable)"},
    "rounded": {"aliases": (), "tags": ("/Sans/Rounded",), "proxy": None},
    "didone": {"aliases": ("modern", "bodoni", "high-contrast", "fat-face"),
               "tags": ("/Serif/Didone", "/Serif/Modern", "/Serif/Fat Face", "/Serif/Scotch"),
               "proxy": _serif_contrast(0, 0.3)},
    "transitional": {"aliases": ("baskerville",), "tags": ("/Serif/Transitional",),
                     "proxy": _serif_contrast(0.3, 0.6),
                     "proxy_note": "proxy = serif with contrast 0.3-0.6 (transitional and old-style not separable)"},
    "old-style": {"aliases": ("oldstyle", "garalde", "venetian", "humanist-serif", "garamond"),
                  "tags": ("/Serif/Old Style Garalde", "/Serif/Humanist Venetian"),
                  "proxy": _serif_contrast(0.3, 0.6),
                  "proxy_note": "proxy = serif with contrast 0.3-0.6 (transitional and old-style not separable)"},
    "condensed": {"aliases": ("narrow", "compressed"), "proxy": lambda r: 0 < _m(r, "width", 1) < 0.9},
    "wide": {"aliases": ("extended", "expanded"), "proxy": lambda r: _m(r, "width", 0) > 1.15},
}
CLASS_NAMES = ", ".join(CLASS_DEFS)


def class_def(name):
    """(canonical name, definition) for a class name or alias; ValueError listing the valid classes."""
    k = re.sub(r"[\s_]+", "-", (name or "").strip().lower())
    for canon, d in CLASS_DEFS.items():
        if k == canon or k in d["aliases"]:
            return canon, d
    raise ValueError(f"unknown class {name!r}; valid classes: {CLASS_NAMES} (aliases such as grotesk, "
                     "neo-grotesk, garalde, script, extended also work)")


def _ai_default_fonts():
    try:
        with open(AI_DEFAULTS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    entries = data.get("entries", data) if isinstance(data, dict) else data
    out = set()
    for e in entries if isinstance(entries, list) else []:
        for f in ((e or {}).get("match") or {}).get("fonts", []) or []:
            out.add(typelib._norm(f))
    return out


def _google_tags():
    """Google's human-assessed tag scores {family: {"/Expressive/Calm": 81, ...}}, fetched at runtime into the cache
    (their licence is unconfirmed, so they are never committed to the repository)."""
    path = typelib.cache_root() / "gf-tags" / "families.csv"
    if not path.is_file():
        typelib._atomic_write(path, typelib._fetch(TAGS_URL, "Google Fonts tags (for --mood)"))
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 4 and row[2].startswith("/"):
                try:
                    score = float(row[3])
                except ValueError:
                    continue
                fam = out.setdefault(row[0], {})
                fam[row[2]] = max(score, fam.get(row[2], 0))
    return out


def search(query=None, klass=None, mood=None, exclude_defaults=False, langs=("en",), numbers=False, catalog=None):
    """Filter the catalogue; returns (rows, notes). Rows are catalogue records, most popular first (or by mood)."""
    cat = catalog if catalog is not None else typelib.load_catalog()
    fams = list(cat.get("families", []))
    notes = []
    rank = {}
    if query:
        q = query.lower().strip()
        qn = typelib._norm(q)
        for r in fams:
            name = r["family"].lower()
            if typelib._norm(name) == qn:
                rank[r["family"]] = 0                                   # exact name
            elif re.search(r"(^|[\s-])" + re.escape(q), name):
                rank[r["family"]] = 1                                   # a word starts with the query
            elif q in name:
                rank[r["family"]] = 2                                   # substring (Archivo for "chivo")
            elif any(q in d.lower() for d in r.get("designers", [])):
                rank[r["family"]] = 3                                   # designer
        fams = [r for r in fams if r["family"] in rank]
    tags = None
    if klass:
        canon, d = class_def(klass)
        tag_keys = d.get("tags")
        if tag_keys:
            try:
                tags = _google_tags()
            except FontError as e:
                notes.append(f"Google class tags unavailable ({str(e)[:60]}...)")
        if tag_keys and tags:
            fams = [r for r in fams if any(t.startswith(k) and s >= 50 for t, s in tags.get(r["family"], {}).items()
                                           for k in tag_keys)]
            notes.append(f"class {canon}: Google's human-assessed class tags (>= 50)")
        elif d.get("proxy"):
            fams = [r for r in fams if d["proxy"](r)]
            if tag_keys or canon in ("condensed", "wide"):
                notes.append(f"class {canon}: measured proxy, heuristic"
                             + (f"; {d['proxy_note']}" if d.get("proxy_note") else ""))
        else:
            raise FontError(f"class {canon} needs Google's class tags, which could not be fetched; retry online")
    score = None
    if mood:
        tags = tags or _google_tags()
        key = f"/expressive/{mood.lower()}"

        def score(r):
            return max((s for t, s in tags.get(r["family"], {}).items() if t.lower() == key), default=0)
        fams = [r for r in fams if score(r) >= 50]
        notes.append(f"mood {mood!r}: Google's expressive tag scores, human assessed (limited evidence)")
    if exclude_defaults:
        defaults = _ai_default_fonts()
        fams = [r for r in fams if not r.get("ai_default") and typelib._norm(r["family"]) not in defaults]
    index = cat.get("lang_index") or []
    for code in langs or []:
        lid = typelib.lang_id(code)
        if lid not in index:
            notes.append(f"{lid} not indexed in the catalogue; audit candidates with fonts audit")
            continue
        # non-Google records (Fontshare) are kept: their coverage is unknown, the row says "not rendered"
        fams = [r for r in fams if covers(r, lid, index) is not False]
    if numbers:
        fams = [r for r in fams if (r.get("features") or {}).get("tnum")]
    if score:
        fams.sort(key=lambda r: (rank.get(r["family"], 0), -score(r), r.get("popularity") or 10 ** 6))
    else:
        fams.sort(key=lambda r: (rank.get(r["family"], 0), r.get("popularity") or 10 ** 6, r["family"]))
    return fams, notes


def _row(r):
    axes = r.get("axes") or {}
    if "wght" in axes:
        w = f"wght {axes['wght'][0]:g}-{axes['wght'][1]:g}"
    else:
        ws = r.get("weights") or []
        w = f"{len(ws)} wt" if ws else "-"
    extra = [t for t in axes if t != "wght"]
    met = r.get("metrics") or {}
    bits = [r["family"], r.get("category", "?"), w + (f" +{','.join(extra)}" if extra else "")]
    if met.get("x_cap"):
        bits.append(f"x/H {met['x_cap']:.2f}")
    if met.get("contrast") is not None:
        bits.append(f"contrast {met['contrast']:.2f}")
    feats = r.get("features") or {}
    if feats.get("tnum"):
        bits.append("tnum")
    if r.get("source") != "google":
        bits.append(f"{r.get('source')} {r.get('license')} (not rendered)")
    if r.get("ai_default"):
        bits.append("AI-default")
    if r.get("popularity"):
        bits.append(f"#{r['popularity']}")
    return " · ".join(bits)


# ---------------------------------------------------------------- CLI (brand.py fonts ...)

def _clip(lines, limit=STDOUT_LIMIT):
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line.encode("utf-8")) + 1 > limit - 60:
            out.append(f"... {len(lines) - i} more line(s); use --full or --json")
            break
        out.append(line)
        size += len(line.encode("utf-8")) + 1
    return out


def _cli_search(args):
    if (args.klass or "").lower() in ("list", "help", "?"):
        print("classes: " + "; ".join(f"{k} ({', '.join(d['aliases'])})" if d["aliases"] else k
                                      for k, d in CLASS_DEFS.items()))
        return 0
    langs = [x.strip() for x in (args.langs or "en").split(",") if x.strip()]
    rows, notes = search(args.query, args.klass, args.mood, args.exclude_defaults, langs, args.numbers)
    if args.json:
        print(json.dumps({"count": len(rows), "notes": notes, "families": rows if args.full else rows[:12]},
                         ensure_ascii=False))
        return 0
    limit = None if args.full else 12
    order = "best match, then most popular" if args.query else "most popular first"
    head = f"{len(rows)} families" + (f" (showing 12, {order})" if limit and len(rows) > 12 else "")
    lines = [head] + [_row(r) for r in rows[:limit]] + [f"note: {n}" for n in notes]
    print("\n".join(lines if args.full else _clip(lines)))
    return 0 if rows else 1


def _cli_audit(args):
    if not args.query:
        print("error: fonts audit needs a FAMILY or a font file path", file=sys.stderr)
        return 2
    langs = [x.strip() for x in (args.langs or "en").split(",") if x.strip()]
    findings, facts = _audit(args.query, langs, numbers=args.numbers)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in identitylib.SEVERITIES}
    if args.json:
        print(json.dumps({"schema": identitylib.AUDIT_SCHEMA, "passed": counts["gate"] == 0, "counts": counts,
                          "facts": facts, "findings": findings}, ensure_ascii=False, default=str))
        return 1 if counts["gate"] else 0
    axes = facts.get("axes") or {}
    shape = ("variable " + ",".join(axes)) if facts.get("variable") else (
        "static " + ",".join(str(w) for w in facts.get("weights", [])) if facts.get("weights") else "static")
    lines = [f"{facts['family']} · {facts['source']} · {facts.get('license') or 'licence ?'} · {shape}"]
    met = facts.get("metrics") or {}
    if met:
        iou = facts.get("iou") or {}
        lines.append(f"x/H {met.get('x_cap')} · contrast {met.get('contrast')} · I/l/1 IoU "
                     f"{max(iou.values()) if iou else '-'} · figures {FIGURES[facts.get('tabular')]} · "
                     f"langs {','.join(facts['langs'])}")
    order = {"gate": 0, "warn": 1, "info": 2}
    for f in sorted(findings, key=lambda f: order[f["severity"]]):
        line = f"{f['severity'].upper():4}  {f['id']}: {f['message']}"
        if f.get("suggested_fix") and (args.full or f["severity"] == "gate"):
            line += f"  -> {f['suggested_fix']}"
        lines.append(line)
    lines.append(f"result: {'FAIL' if counts['gate'] else 'PASS'} ({counts['gate']} gate, {counts['warn']} warn, "
                 f"{counts['info']} info)")
    if args.full:
        lines.append(f"file: {facts.get('file')}")
    print("\n".join(lines if args.full else _clip(lines[:-1]) + lines[-1:]))
    return 1 if counts["gate"] else 0


def cli_fonts(args):
    """`brand.py fonts search|audit` handler; returns the exit code."""
    identitylib.utf8_console()
    try:
        if args.action == "search":
            return _cli_search(args)
        return _cli_audit(args)
    except (FontError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
