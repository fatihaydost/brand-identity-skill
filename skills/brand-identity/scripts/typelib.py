"""Type helpers for the brand-identity skill: get a font file, read facts from it, map languages, case text.

  resolve_font  family (+ pinned axis location) -> path of a static font file in the cache. Google Fonts families are
                downloaded from GitHub raw google/fonts (fallback: the Google Fonts CSS2 API); a variable font is
                instanced with every fvar axis pinned and overlaps removed (fontTools.varLib.instancer). Instances
                are OFL Modified Versions: they live only in caches and are never written to user outputs as fonts.
  font_info     names, metrics, axes, features and cmap size of a font file.
  lang_id       brief language code -> gflanguages id through its default script ("tr" -> "tr_Latn"); the
                language data ships with the skill (assets/languages.json.gz, exported from gflanguages).
  case_text     locale-aware upper/lower casing (Turkish/Azerbaijani i -> İ, ı -> I).

Cache: ${XDG_CACHE_HOME:-~/.cache}/brand-identity/fonts/{google/<dir>/, instances/}. Network is used only to fetch
Google Fonts files and METADATA.pb (docs/architecture.md section 4.0). See docs/architecture.md sections 4.3 and 11.

Public API (names are a contract): resolve_font, font_info, lang_id, case_text, bcp47, cache_root, font_cache,
google_family, google_file, load_catalog, catalog_entry, parse_textproto, languages, scripts, FontError.
Needs fontTools (+ skia-pathops for overlap removal); each is imported where it is used so the error names the
missing package.
"""
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

__all__ = [
    "FontError", "resolve_font", "font_info", "lang_id", "case_text", "bcp47", "languages", "scripts", "cache_root",
    "font_cache", "google_family", "google_file", "load_catalog", "catalog_entry", "parse_textproto", "default_dir",
    "tabular_figures",
    "LICENSES", "GOOGLE_LICENSE_DIRS",
]

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(os.path.dirname(HERE), "assets", "fonts", "catalog.json")
RAW = "https://raw.githubusercontent.com/google/fonts/main"
CSS2 = "https://fonts.googleapis.com/css2"
USER_AGENT = "brand-identity-skill/0.2 (+https://github.com/google/fonts)"
TIMEOUT = 30
# google/fonts licence directories -> SPDX-style ids used in identity.json and the catalogue.
GOOGLE_LICENSE_DIRS = {"ofl": "OFL-1.1", "apache": "Apache-2.0", "ufl": "UFL-1.0"}
METADATA_LICENSES = {"OFL": "OFL-1.1", "APACHE2": "Apache-2.0", "UFL": "UFL-1.0"}
LICENSES = ("OFL-1.1", "Apache-2.0", "UFL-1.0", "ITF-FFL-2.0", "commercial", "unknown")
INSTANCE_VERSION = "1"  # bump when instancing settings change so stale cache entries are not reused
TABULAR_TOLERANCE = 0.01  # figure advances within 1% of the em count as tabular
TURKIC = {"tr", "az"}   # Unicode SpecialCasing.txt language-sensitive dotted/dotless i
# Default script where gflanguages' speaker counts would pick another one (Unicode CLDR likelySubtags).
DEFAULT_SCRIPT = {"az": "Latn", "sr": "Cyrl", "pa": "Guru", "so": "Latn", "mn": "Cyrl", "bs": "Latn", "uz": "Latn",
                  "bm": "Latn", "kk": "Cyrl", "ky": "Cyrl", "tg": "Cyrl", "tk": "Latn", "ha": "Latn", "ku": "Latn",
                  "zh": "Hans", "yue": "Hant", "ug": "Arab", "sd": "Arab", "ks": "Arab", "shi": "Tfng"}


class FontError(RuntimeError):
    """A font could not be found, fetched or instanced. The message says what to do next."""


def _log(msg):
    print(msg, file=sys.stderr)


def _fonttools():
    try:
        import fontTools  # noqa: F401
    except ImportError:
        raise FontError("fontTools is not installed; run `python3 brand.py check` for the install line") from None


# ---------------------------------------------------------------- cache paths

def cache_root():
    """${XDG_CACHE_HOME:-~/.cache}/brand-identity (created on demand)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "brand-identity"


def font_cache():
    """Font downloads and instances: <cache_root>/fonts."""
    return cache_root() / "fonts"


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _fetch(url, what):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise FontError(f"{what}: HTTP {e.code} from {url}") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise FontError(f"{what}: network error ({reason}) fetching {url}") from None


# ---------------------------------------------------------------- METADATA.pb (protobuf text format)

_TOKEN = re.compile(r'\s*(?:(#[^\n]*)|("(?:[^"\\]|\\.)*")|(\{)|(\})|(:)|([^\s{}:"#]+))')


def _unquote(tok):
    body = tok[1:-1]
    # protobuf text escapes: \" \\ \n \t and octal bytes (UTF-8 encoded)
    raw = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8)]),
                 body.encode("utf-8").replace(b'\\"', b'"').replace(b"\\n", b"\n").replace(b"\\t", b"\t"))
    return raw.replace(b"\\\\", b"\\").decode("utf-8", errors="replace")


def parse_textproto(text):
    """Parse protobuf text format (google/fonts METADATA.pb) into {key: [values]}; nested messages are dicts.

    Every key maps to a list because protobuf fields may repeat. Scalars stay strings except plain numbers.
    """
    pos, stack = 0, [{}]
    key = None
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"METADATA.pb: cannot parse near {text[pos:pos + 40]!r}")
        pos = m.end()
        comment, string, lbrace, rbrace, colon, word = m.groups()
        if comment or colon:
            continue
        if lbrace:
            child = {}
            stack[-1].setdefault(key, []).append(child)
            stack.append(child)
            key = None
        elif rbrace:
            stack.pop()
            if not stack:
                raise ValueError("METADATA.pb: unbalanced '}'")
        elif key is None:
            key = word if word is not None else _unquote(string)
        else:
            values = stack[-1].setdefault(key, [])
            if string is not None:
                val = _unquote(string)
                # adjacent string literals concatenate: "a" "b" == "ab"
                while True:
                    m2 = _TOKEN.match(text, pos)
                    if not (m2 and m2.group(2) is not None):
                        break
                    val += _unquote(m2.group(2))
                    pos = m2.end()
            else:
                val = word
                if re.fullmatch(r"-?\d+", val):
                    val = int(val)
                elif re.fullmatch(r"-?\d*\.\d+(?:[eE]-?\d+)?|-?\d+\.\d*", val):
                    val = float(val)
            values.append(val)
            key = None
    return stack[0]


def _one(d, key, default=None):
    v = d.get(key)
    return v[0] if v else default


def _family_meta(pb, directory):
    """The fields of a parsed METADATA.pb that the skill uses."""
    fonts = [{"filename": _one(f, "filename"), "style": _one(f, "style", "normal"), "weight": _one(f, "weight", 400)}
             for f in pb.get("fonts", [])]
    lic = METADATA_LICENSES.get(_one(pb, "license", ""), None) or GOOGLE_LICENSE_DIRS.get(directory.split("/")[0])
    return {
        "family": _one(pb, "name"), "dir": directory, "license": lic,
        "category": [c for c in pb.get("category", [])], "classifications": pb.get("classifications", []),
        "stroke": _one(pb, "stroke"), "designer": _one(pb, "designer", ""), "date_added": _one(pb, "date_added"),
        "subsets": [s for s in pb.get("subsets", []) if s != "menu"],
        "axes": {_one(a, "tag"): [_one(a, "min_value"), _one(a, "max_value")] for a in pb.get("axes", [])},
        "fonts": fonts,
    }


# ---------------------------------------------------------------- catalogue

_CATALOG = None


def load_catalog(path=None):
    """assets/fonts/catalog.json as a dict (cached); {} with a stderr note when it is missing."""
    global _CATALOG
    if path is None and _CATALOG is not None:
        return _CATALOG
    p = path or CATALOG
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        _log(f"note: font catalogue not found at {p}; run tools/build_font_catalog.py")
        data = {"families": []}
    if path is None:
        _CATALOG = data
    return data


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def catalog_entry(family, catalog=None):
    """The catalogue record of a family (name match ignores case, spaces and punctuation), or None."""
    cat = catalog if catalog is not None else load_catalog()
    key = _norm(family)
    for rec in cat.get("families", []):
        if _norm(rec["family"]) == key:
            return rec
    return None


# ---------------------------------------------------------------- Google Fonts download

def default_dir(family, license):
    """google/fonts directory a family normally lives in: <ofl|apache|ufl>/<lowercase alphanumerics>."""
    lic_dir = {v: k for k, v in GOOGLE_LICENSE_DIRS.items()}.get(license, "ofl")
    return f"{lic_dir}/{_norm(family)}"


def _google_dir(family):
    """google/fonts directory candidates for a family: the catalogue's, else ofl|apache|ufl/<slug>."""
    rec = catalog_entry(family)
    if rec and rec.get("source") == "google":
        return [rec.get("dir") or default_dir(rec["family"], rec.get("license"))]
    slug = _norm(family)
    return [f"{lic}/{slug}" for lic in GOOGLE_LICENSE_DIRS]


def google_family(family):
    """METADATA.pb facts of a Google Fonts family (downloaded once into the cache). Raises FontError if unknown."""
    rec = catalog_entry(family)
    if rec and rec.get("source") not in (None, "google"):
        raise FontError(f"{rec['family']} is a {rec['source']} family ({rec.get('license')}); it is not downloaded "
                        "or rendered by the skill. Link it with a licence note, or pass the user's own font file")
    errors = []
    for directory in _google_dir(family):
        local = font_cache() / "google" / directory / "METADATA.pb"
        if not local.is_file():
            try:
                data = _fetch(f"{RAW}/{directory}/METADATA.pb", f"Google Fonts metadata for {family!r}")
            except FontError as e:
                errors.append(str(e))
                continue
            _atomic_write(local, data)
        meta = _family_meta(parse_textproto(local.read_text(encoding="utf-8")), directory)
        if meta["family"] and _norm(meta["family"]) == _norm(family):
            return meta
        errors.append(f"{directory} holds {meta['family']!r}, not {family!r}")
    if all("HTTP 404" in e or "holds" in e for e in errors):
        raise FontError(f"{family!r} is not a Google Fonts family (checked {', '.join(_google_dir(family))}). "
                        "Check the spelling with `brand.py fonts search QUERY`, or pass a font file")
    raise FontError(errors[-1] + " (offline? fonts already used once are served from the cache)")


def google_file(meta, filename):
    """Download one font file of a family into the cache; returns its Path."""
    local = font_cache() / "google" / meta["dir"] / filename
    if not local.is_file():
        url = f"{RAW}/{meta['dir']}/{urllib.parse.quote(filename)}"
        _atomic_write(local, _fetch(url, f"{meta['family']} font file"))
    return local


def _pick_file(meta, location):
    """Choose the family file that serves a location: the variable file of the style, else the closest weight."""
    italic = float(location.get("ital", 0) or 0) >= 0.5
    style = "italic" if italic else "normal"
    fonts = [f for f in meta["fonts"] if f["style"] == style] or meta["fonts"]
    if not fonts:
        raise FontError(f"{meta['family']}: METADATA.pb lists no font files")
    variable = [f for f in fonts if "[" in (f["filename"] or "")]
    if variable:
        return variable[0]["filename"]
    want = float(location.get("wght", 400))
    best = min(fonts, key=lambda f: (abs(f["weight"] - want), f["weight"]))
    if "wght" in location and best["weight"] != want:
        _log(f"note: {meta['family']} has no {_num(want)} {style} file; using {best['weight']} "
             f"(available: {', '.join(str(f['weight']) for f in fonts)})")
    return best["filename"]


def _css2_fallback(family, location):
    """Static TTF for an exact axis tuple from the Google Fonts CSS2 API (used when GitHub raw is unreachable)."""
    loc = {k: v for k, v in location.items() if k != "ital"}
    italic = float(location.get("ital", 0) or 0) >= 0.5
    if italic:
        loc["ital"] = 1
    # CSS2 wants axis tags sorted: lowercase tags alphabetically first, then uppercase ones.
    tags = sorted(loc, key=lambda t: (t[0].isupper(), t))
    fam = family.replace(" ", "+")
    query = f"{fam}:{','.join(tags)}@{','.join(_num(loc[t]) for t in tags)}" if tags else fam
    css = _fetch(f"{CSS2}?family={query}", f"Google Fonts CSS2 for {family!r}").decode("utf-8", "replace")
    urls = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
    if not urls:
        raise FontError(f"Google Fonts CSS2 returned no TTF for {family!r} at {loc}")
    key = hashlib.sha256(urls[0].encode()).hexdigest()[:16]
    local = font_cache() / "google" / "css2" / _norm(family) / f"{key}.ttf"
    if not local.is_file():
        _atomic_write(local, _fetch(urls[0], f"{family} font file"))
    _log(f"note: {family}: GitHub unreachable, using a Google Fonts CSS2 static instance at {loc or 'defaults'}")
    return local


def _num(v):
    return str(int(v)) if float(v).is_integer() else f"{float(v):g}"


# ---------------------------------------------------------------- instancing

def _axes(font):
    return {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes} if "fvar" in font else {}


def _full_location(path, axes, location):
    """Pin every fvar axis: given values (validated), defaults for the rest. Raises FontError with the valid axes."""
    valid = ", ".join(f"{t} {_num(a)}..{_num(b)}" for t, (a, _, b) in axes.items())
    full = {t: d for t, (_, d, _) in axes.items()}
    for tag, value in (location or {}).items():
        if tag == "ital" and tag not in axes:
            continue  # file selector (italic file), not an axis of this file
        if tag not in axes:
            raise FontError(f"location axis {tag!r} is not in {Path(path).name}; its axes: {valid}")
        lo, _, hi = axes[tag]
        if not lo <= float(value) <= hi:
            raise FontError(f"location {tag}={_num(value)} is outside {Path(path).name}'s range {_num(lo)}..{_num(hi)}")
        full[tag] = float(value)
    return full


def _instance(path, location):
    """Static instance of a variable font file, cached by source hash + full location."""
    _fonttools()
    from fontTools.ttLib import TTFont
    font = TTFont(str(path), lazy=True)
    axes = _axes(font)
    font.close()
    if not axes:
        unknown = [t for t in (location or {}) if t not in ("ital", "wght")]
        if unknown:
            _log(f"note: {Path(path).name} is static; location axes {unknown} ignored")
        return Path(path)
    full = _full_location(path, axes, location)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    loc_key = json.dumps({k: full[k] for k in sorted(full)}, separators=(",", ":"))
    key = hashlib.sha256(f"{INSTANCE_VERSION}|{digest}|{loc_key}".encode()).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", Path(path).stem.split("[")[0]) or "font"
    out = font_cache() / "instances" / f"{stem}-{key}.ttf"
    if out.is_file():
        return out
    try:
        import pathops  # noqa: F401
    except ImportError:
        raise FontError("skia-pathops is not installed (needed to remove overlaps when instancing); "
                        "run `python3 brand.py check`") from None
    from fontTools.varLib import instancer
    vf = TTFont(str(path))
    try:
        try:
            inst = instancer.instantiateVariableFont(vf, full, overlap=instancer.OverlapMode.REMOVE,
                                                     updateFontNames=True)
        except Exception:  # noqa: BLE001 - names need a STAT table; the outlines do not
            vf = TTFont(str(path))
            inst = instancer.instantiateVariableFont(vf, full, overlap=instancer.OverlapMode.REMOVE)
    except Exception as e:  # noqa: BLE001
        raise FontError(f"instancing {Path(path).name} at {full} failed: {e}") from None
    inst.flavor = None
    import io
    buf = io.BytesIO()
    inst.save(buf)
    _atomic_write(out, buf.getvalue())
    return out


def resolve_font(family, location=None, source=None, file=None):
    """Return a static font file for a family at a pinned location, downloading and instancing as needed.

    family    family name ("Fraunces"); used for the download unless `file` is given.
    location  {"wght": 600, "opsz": 144, ...}; every fvar axis of the file ends up pinned (missing ones at their
              default). "ital": 1 selects the italic file of a Google family. Unknown axes or out-of-range values
              raise FontError naming the valid ranges.
    source    google (default) | user | fontshare | commercial. Fontshare and commercial families are never
              downloaded (licence); pass `file` with the user's own copy instead.
    file      a user-supplied font file (ttf/otf/woff/woff2); used as given, instanced if variable.
    """
    location = dict(location or {})
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            raise FontError(f"font file not found: {p}")
        return _instance(p, location)
    src = (source or "google").lower()
    if src in ("fontshare", "commercial", "user"):
        why = {"fontshare": "Fontshare (ITF FFL) forbids conversion and redistribution, so the skill does not "
                            "download or render it",
               "commercial": "commercial fonts are not downloadable",
               "user": "a user font needs its file"}[src]
        raise FontError(f"{family}: {why}; pass file=<path to the user's licensed copy>, or pick a Google Fonts "
                        "alternative")
    if src != "google":
        raise FontError(f"unknown font source {source!r}; use google, user, fontshare or commercial")
    try:
        meta = google_family(family)
        path = google_file(meta, _pick_file(meta, location))
    except FontError as e:
        if "network error" not in str(e):
            raise
        path = _css2_fallback(family, location)
    return _instance(path, location)


# ---------------------------------------------------------------- font facts

def _features(font):
    tags = set()
    for t in ("GSUB", "GPOS"):
        if t in font and font[t].table.FeatureList:
            tags.update(fr.FeatureTag for fr in font[t].table.FeatureList.FeatureRecord)
    return sorted(tags)


def _feature_names(font):
    """{ssNN|cvNN: UI name} from the GSUB FeatureParams name ids (empty string when unnamed)."""
    out = {}
    if "GSUB" not in font or not font["GSUB"].table.FeatureList:
        return out
    name = font["name"]
    for fr in font["GSUB"].table.FeatureList.FeatureRecord:
        tag = fr.FeatureTag
        if not re.fullmatch(r"ss\d\d|cv\d\d", tag) or tag in out:
            continue
        label = ""
        params = getattr(fr.Feature, "FeatureParams", None)
        nid = getattr(params, "UINameID", None) or getattr(params, "FeatUILabelNameID", None)
        if nid:
            label = name.getDebugName(nid) or ""
        out[tag] = label
    return dict(sorted(out.items()))


def _locl_langs(font):
    out = set()
    if "GSUB" not in font or not font["GSUB"].table.ScriptList:
        return []
    fl = font["GSUB"].table.FeatureList.FeatureRecord
    for sr in font["GSUB"].table.ScriptList.ScriptRecord:
        for lsr in sr.Script.LangSysRecord:
            if any(fl[i].FeatureTag == "locl" for i in lsr.LangSys.FeatureIndex):
                out.add(lsr.LangSysTag.strip())
    return sorted(out)


def _feature_lookups(table, tag):
    lookups = set()
    if table.FeatureList:
        for fr in table.FeatureList.FeatureRecord:
            if fr.FeatureTag == tag:
                lookups.update(fr.Feature.LookupListIndex)
    return [table.LookupList.Lookup[i] for i in sorted(lookups)] if table.LookupList else []


def _subtables(lookup):
    for st in lookup.SubTable:
        yield getattr(st, "ExtSubTable", st)  # unwrap extension lookups (GSUB 7 / GPOS 9)


def tabular_figures(font):
    """How the font sets figures 0-9 at equal width: "default" (the default digits already share one advance, as
    in monospaced faces), "tnum" (applying the `tnum` feature makes them equal), or None (proportional only).
    "default-approx" / "tnum-approx": the advances differ by at most 1% of the em (font defects such as one
    tabular figure 5/1000 em off, which do not show), counted as tabular.

    `tnum` is applied for real: GSUB single substitutions and GPOS single-adjustment advances; a `tnum` feature
    that leaves the digits further apart does not count."""
    cmap = font.getBestCmap() or {}
    if "hmtx" not in font or not all(ord(d) in cmap for d in "0123456789"):
        return None
    hmtx = font["hmtx"]
    tol = font["head"].unitsPerEm * TABULAR_TOLERANCE

    def equal(adv):
        spread = max(adv) - min(adv)
        return "" if spread == 0 else ("-approx" if spread <= tol else None)

    glyphs = [cmap[ord(d)] for d in "0123456789"]
    eq = equal([hmtx[g][0] for g in glyphs])
    if eq is not None:
        return "default" + eq
    found = False
    if "GSUB" in font:
        for lk in _feature_lookups(font["GSUB"].table, "tnum"):
            for st in _subtables(lk):
                mapping = getattr(st, "mapping", None)
                if mapping:
                    found = True
                    glyphs = [mapping.get(g, g) for g in glyphs]
    adv = [hmtx[g][0] for g in glyphs]
    if "GPOS" in font:
        for lk in _feature_lookups(font["GPOS"].table, "tnum"):
            for st in _subtables(lk):
                if getattr(st, "Format", None) is None or not hasattr(st, "Coverage"):
                    continue
                if lk.LookupType not in (1, 9):
                    continue
                cov = st.Coverage.glyphs
                for i, g in enumerate(glyphs):
                    if g not in cov:
                        continue
                    vr = st.Value if st.Format == 1 else st.Value[cov.index(g)]
                    found = True
                    adv[i] += getattr(vr, "XAdvance", 0) or 0
    eq = equal(adv)
    return "tnum" + eq if found and eq is not None else None


def _glyph_top(font, ch, glyphset=None):
    gn = font.getBestCmap().get(ord(ch))
    if gn is None:
        return None
    from fontTools.pens.boundsPen import BoundsPen
    gs = glyphset or font.getGlyphSet()
    bp = BoundsPen(gs)
    gs[gn].draw(bp)
    return bp.bounds[3] if bp.bounds else None


def font_info(path):
    """Facts about one font file: names, metrics, axes, features and cmap size (a plain JSON-able dict)."""
    _fonttools()
    from fontTools.ttLib import TTFont
    p = Path(path)
    try:
        font = TTFont(str(p), fontNumber=0)
    except Exception as e:  # noqa: BLE001
        raise FontError(f"{p}: not a readable font file ({e})") from None
    name, os2, head = font["name"], font.get("OS/2"), font["head"]
    upm = head.unitsPerEm
    cmap = font.getBestCmap() or {}
    xh = getattr(os2, "sxHeight", 0) if os2 and os2.version >= 2 else 0
    ch = getattr(os2, "sCapHeight", 0) if os2 and os2.version >= 2 else 0
    xh = xh or _glyph_top(font, "x") or 0
    ch = ch or _glyph_top(font, "H") or 0
    feats = _features(font)
    digits = [font["hmtx"][cmap[ord(d)]][0] for d in "0123456789" if ord(d) in cmap] if "hmtx" in font else []
    info = {
        "file": str(p), "format": "woff2" if font.flavor == "woff2" else ("woff" if font.flavor == "woff" else
                                                                         ("otf" if "CFF " in font or "CFF2" in font
                                                                          else "ttf")),
        "names": {
            "family": name.getBestFamilyName(), "subfamily": name.getBestSubFamilyName(),
            "full": name.getBestFullName(), "postscript": name.getDebugName(6),
            "version": name.getDebugName(5), "designer": name.getDebugName(9), "manufacturer": name.getDebugName(8),
            "license": name.getDebugName(13), "license_url": name.getDebugName(14),
        },
        "metrics": {
            "upm": upm, "ascender": getattr(os2, "sTypoAscender", None), "descender": getattr(os2, "sTypoDescender", None),
            "line_gap": getattr(os2, "sTypoLineGap", None), "x_height": xh, "cap_height": ch,
            "x_over_cap": round(xh / ch, 3) if ch else None, "x_over_upm": round(xh / upm, 3) if upm else None,
            "weight_class": getattr(os2, "usWeightClass", None), "width_class": getattr(os2, "usWidthClass", None),
            "italic": bool(head.macStyle & 2) or bool(getattr(os2, "fsSelection", 0) & 1),
            "fs_type": getattr(os2, "fsType", 0),
        },
        "axes": [{"tag": a.axisTag, "min": a.minValue, "default": a.defaultValue, "max": a.maxValue,
                  "name": name.getDebugName(a.axisNameID)} for a in font["fvar"].axes] if "fvar" in font else [],
        "named_instances": len(font["fvar"].instances) if "fvar" in font else 0,
        "variable": "fvar" in font,
        "features": feats, "ss_cv": _feature_names(font), "locl": _locl_langs(font),
        "tnum": "tnum" in feats, "kern": "kern" in feats or ("kern" in font),
        "tabular_default": bool(digits) and len(digits) == 10 and len(set(digits)) == 1,
        "tabular": tabular_figures(font),
        "cmap_size": len(cmap), "glyphs": len(font.getGlyphOrder()),
    }
    font.close()
    return info


# ---------------------------------------------------------------- languages and casing

_LANGS = None
_SCRIPTS = None
LANGUAGES = os.path.join(os.path.dirname(HERE), "assets", "languages.json.gz")


class Exemplars:
    """exemplar_chars of a language: space-separated exemplar strings ('' when the data has none)."""
    __slots__ = ("base", "auxiliary", "punctuation")

    def __init__(self, base="", auxiliary="", punctuation=""):
        self.base, self.auxiliary, self.punctuation = base, auxiliary, punctuation


class Language:
    """One language record (the fields of gflanguages' LanguageProto the skill reads)."""
    __slots__ = ("id", "name", "population", "historical", "exemplar_chars")

    def __init__(self, lid, name, population, historical, base, auxiliary, punctuation):
        self.id, self.name, self.population, self.historical = lid, name, population, bool(historical)
        self.exemplar_chars = Exemplars(base, auxiliary, punctuation)

    def __repr__(self):
        return f"Language({self.id!r}, {self.name!r})"


def _language_data():
    """assets/languages.json.gz: exported from gflanguages by tools/export_languages.py (Apache-2.0; exemplars
    adapted from Unicode CLDR)."""
    import gzip
    try:
        with gzip.open(LANGUAGES, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise FontError(f"language data {LANGUAGES} is missing or unreadable ({e}); reinstall the skill") from None


def languages():
    """Language records keyed by gflanguages id ("tr_Latn"), in gflanguages' order, from the bundled data."""
    global _LANGS, _SCRIPTS
    if _LANGS is None:
        doc = _language_data()
        _LANGS = {lid: Language(lid, *row) for lid, row in doc["languages"].items()}
        _SCRIPTS = dict(doc.get("scripts") or {})
    return _LANGS


def scripts():
    """Script code -> name ("Latn" -> "Latin"), from the bundled data."""
    languages()
    return _SCRIPTS


def lang_id(code):
    """Brief language code -> gflanguages id via the language's default script.

    "tr" -> "tr_Latn", "tr-TR" -> "tr_Latn", "sr" -> the most spoken script ("sr_Cyrl"), "sr-Latn" -> "sr_Latn",
    "tr_Latn" -> unchanged. Raises ValueError for an unknown language.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("empty language code")
    langs = languages()
    c = code.strip().replace("-", "_")
    if c in langs:
        return c
    parts = c.split("_")
    base = parts[0].lower()
    script = next((p.title() for p in parts[1:] if len(p) == 4 and p.isalpha()), None)
    if script and f"{base}_{script}" in langs:
        return f"{base}_{script}"
    if base in DEFAULT_SCRIPT and f"{base}_{DEFAULT_SCRIPT[base]}" in langs:
        return f"{base}_{DEFAULT_SCRIPT[base]}"
    cands = [k for k in langs if k.split("_")[0] == base and k.count("_") == 1]
    if not cands:
        raise ValueError(f"unknown language code {code!r}; use ISO 639 codes such as en, tr, de, ar, sr-Latn")
    # Living languages with exemplar data first, then the most speakers; a tie goes to the first id alphabetically
    # (gflanguages' own record order follows the file system's listing, so it differs between machines).
    return max(sorted(cands), key=lambda k: (not langs[k].historical, bool(langs[k].exemplar_chars.base),
                                             langs[k].population or 0))


def bcp47(code):
    """BCP 47 tag for HTML lang / HarfBuzz buffer language: "tr" -> "tr", "sr_Latn" -> "sr-Latn" (script only when
    it is not the language's default)."""
    lid = lang_id(code)
    base, script = lid.split("_", 1)
    return base if lang_id(base) == lid else f"{base}-{script}"


def case_text(text, case="as-is", lang="en"):
    """Locale-aware casing for wordmarks and specimens. case: as-is | upper | lower.

    Turkish and Azerbaijani map i <-> İ and ı <-> I (Unicode SpecialCasing); other languages use Python's full
    Unicode case mapping (ß -> SS, final sigma). `lang` is the first brief language.
    """
    if case in (None, "as-is"):
        return text
    base = (lang or "en").replace("-", "_").split("_")[0].lower()
    if case == "upper":
        if base in TURKIC:
            text = text.replace("i", "İ")
        return text.upper()
    if case == "lower":
        if base in TURKIC:
            text = text.replace("I", "ı").replace("İ", "i")
        return unicodedata.normalize("NFC", text.lower())
    raise ValueError(f"case must be as-is, upper or lower, not {case!r}")
