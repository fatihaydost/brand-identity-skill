#!/usr/bin/env python3
"""Build skills/brand-identity/assets/fonts/catalog.json (developer tool; the skill never runs it).

Sources, all fetched at build time:
  * google/fonts on GitHub: the repository tree (one API call), every family's METADATA.pb (name, category, designer,
    licence, axes, subsets, files) and one font file per family, which we measure with fontTools through
    scripts/font_audit.py: x/cap ratio, width, stroke contrast, I/l/1 raster similarity, tnum, locl, ss/cv names,
    base-exemplar coverage per language (gflanguages), GF Latin Core gaps, WOFF2 size estimates.
  * fonts.google.com/metadata/fonts (undocumented endpoint): popularity rank only. Tolerated when absent.
  * api.fontshare.com/v2/fonts: Fontshare families as link-only records (render: false). Optional.
  * skills/brand-identity/assets/ai-defaults.json, when present: the ai_default flag.
Only computed fields and factual metadata are written. Google's tags/*.csv scores are NOT read here; the skill
fetches them at runtime into the user's cache (their licence is unconfirmed).

Coverage is stored as a bitset over `lang_index` (gflanguages ids of living languages with >= 1M speakers and base
exemplars), base64 encoded. Display and handwriting families carry a narrower field set (no legibility, WOFF2, ss/cv
or locl). Families whose measured file is larger than --max-mb are listed unmeasured (coverage from their Google
subsets only, at search time).

Usage:
  .venv/bin/python tools/build_font_catalog.py                     # full build (~2 GB download cache, ~10-20 min)
  .venv/bin/python tools/build_font_catalog.py --families "Inter,Fraunces" --out /tmp/cat.json
  .venv/bin/python tools/build_font_catalog.py --no-fontshare --workers 8
Cache: ${XDG_CACHE_HOME:-~/.cache}/brand-identity/catalog-build (reused between runs; --refresh re-downloads the
tree, metadata and popularity).
"""
import argparse
import base64
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True

import font_audit as fa  # noqa: E402
import typelib  # noqa: E402

OUT = os.path.join(ROOT, "skills", "brand-identity", "assets", "fonts", "catalog.json")
AI_DEFAULTS = os.path.join(ROOT, "skills", "brand-identity", "assets", "ai-defaults.json")
TREE_URL = "https://api.github.com/repos/google/fonts/git/trees/main?recursive=1"
COMMIT_URL = "https://api.github.com/repos/google/fonts/commits/main"
GF_META_URL = "https://fonts.google.com/metadata/fonts"
FONTSHARE_URL = "https://api.fontshare.com/v2/fonts"
SCHEMA = "brand-identity/font-catalog@1"
CATEGORIES = {"SANS_SERIF": "sans-serif", "SERIF": "serif", "DISPLAY": "display", "HANDWRITING": "handwriting",
              "MONOSPACE": "monospace"}
STROKES = {"SANS_SERIF": "sans", "SERIF": "serif", "SLAB_SERIF": "slab", "Sans Serif": "sans", "Serif": "serif",
           "Slab Serif": "slab"}
NARROW = ("display", "handwriting")
MIN_POPULATION = 1_000_000


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def cached_fetch(url, path, what, refresh=False):
    if refresh or not os.path.isfile(path):
        typelib._atomic_write(path, typelib._fetch(url, what))
    with open(path, "rb") as fh:
        return fh.read()


def lang_index():
    langs = typelib.languages()
    ids = sorted(k for k, v in langs.items()
                 if (v.population or 0) >= MIN_POPULATION and not v.historical and fa._tokens(v.exemplar_chars.base)
                 and k.count("_") == 1)
    need = {k: {ord(c) for t in fa._tokens(langs[k].exemplar_chars.base) for c in t} for k in ids}
    return ids, need


def bitset(flags):
    out = bytearray((len(flags) + 7) // 8)
    for i, ok in enumerate(flags):
        if ok:
            out[i // 8] |= 1 << (7 - i % 8)
    return base64.b64encode(bytes(out)).decode("ascii")


# ---------------------------------------------------------------- per-family measurement (runs in a worker)

_IDX = None


def _init_worker():
    global _IDX
    _IDX = lang_index()


def measure(job):
    """job = (family, path, category) -> measured fields, or {"error": ...}."""
    family, path, category = job
    try:
        from fontTools.ttLib import TTFont
        narrow = category in NARROW
        info = typelib.font_info(path)
        m = fa.measure_font(path, iou=not narrow)
        cps = m["cmap"]
        ids, need = _IDX
        flags = [need[k] <= cps for k in ids]
        core = fa.gf_latin_core()
        font = TTFont(path, lazy=True)
        axes = {a.axisTag: [round(a.minValue, 2), round(a.maxValue, 2), round(a.defaultValue, 2)]
                for a in font["fvar"].axes} if "fvar" in font else {}
        font.close()
        rec = {
            "axes": {k: [_n(x) for x in v] for k, v in axes.items()},
            "metrics": {k: (v if k == "x_cap" else round(v, 2)) for k, v in m["metrics"].items() if v is not None},
            "features": {"tnum": info["tabular"] is not None},  # tabular figures: by default or via tnum
            "coverage": {"langs": bitset(flags), "n": sum(flags),
                         "core_missing": sum(1 for c in core if c not in cps)},
        }
        if not narrow:
            if info["locl"]:
                rec["features"]["locl"] = info["locl"]
            if info["ss_cv"]:
                rec["features"]["ss_cv"] = {k: (v or "")[:28] for k, v in list(info["ss_cv"].items())[:12]}
            rec["legibility"] = {k: round(v, 2) for k, v in m["iou"].items()}
            try:
                rec["woff2_kb"] = {"latin": fa.woff2_kb(path, [c for c in fa.LATIN if c in cps], timeout=60),
                                   "latin_ext": fa.woff2_kb(path, [c for c in fa.LATIN_EXT if c in cps], timeout=60)}
            except TimeoutError:
                pass  # GPOS repacking pathology; size left out rather than stalling the build
        return family, rec
    except Exception as e:  # noqa: BLE001 - one broken family must not stop the build
        return family, {"error": f"{type(e).__name__}: {e}"}


def _n(x):
    return int(x) if float(x).is_integer() else x


# ---------------------------------------------------------------- build

def google_records(args, cache):
    tree = json.loads(cached_fetch(TREE_URL, os.path.join(cache, "tree.json"), "google/fonts tree", args.refresh))
    if tree.get("truncated"):
        log("warning: GitHub tree listing is truncated; some families may be missing")
    sizes = {e["path"]: e.get("size", 0) for e in tree["tree"] if e["type"] == "blob"}
    dirs = sorted(p.rsplit("/", 1)[0] for p in sizes
                  if p.endswith("/METADATA.pb") and p.split("/")[0] in typelib.GOOGLE_LICENSE_DIRS
                  and p.count("/") == 2)
    want = {typelib._norm(f) for f in args.families.split(",")} if args.families else None
    if want:
        dirs = [d for d in dirs if d.split("/")[1] in want]
    log(f"{len(dirs)} family directories")

    def get_meta(d):
        path = os.path.join(cache, "gf", d, "METADATA.pb")
        raw = cached_fetch(f"{typelib.RAW}/{d}/METADATA.pb", path, f"{d} METADATA.pb", args.refresh)
        return typelib._family_meta(typelib.parse_textproto(raw.decode("utf-8")), d)

    metas = []
    with cf.ThreadPoolExecutor(16) as ex:
        for d, res in zip(dirs, ex.map(lambda d: _safe(get_meta, d), dirs)):
            if isinstance(res, Exception):
                log(f"skip {d}: {res}")
            elif res["family"] and res["fonts"]:
                metas.append(res)
    if want:
        metas = [m for m in metas if typelib._norm(m["family"]) in want]

    jobs, unmeasured = [], []

    def get_file(meta):
        fname = typelib._pick_file(meta, {})
        rel = f"{meta['dir']}/{fname}"
        if sizes.get(rel, 0) > args.max_mb * 1e6:
            return meta, None
        path = os.path.join(cache, "gf", meta["dir"], fname)
        cached_fetch(f"{typelib.RAW}/{meta['dir']}/{urllib.parse.quote(fname)}", path, rel)
        return meta, path

    t0 = time.time()
    with cf.ThreadPoolExecutor(16) as ex:
        for res in ex.map(lambda m: _safe(get_file, m), metas):
            if isinstance(res, Exception):
                log(f"download failed: {res}")
                continue
            meta, path = res
            if path is None:
                unmeasured.append(meta)
            else:
                jobs.append((meta, path))
    log(f"files ready: {len(jobs)} to measure, {len(unmeasured)} over {args.max_mb} MB ({time.time() - t0:.0f}s)")
    return jobs, unmeasured


def _safe(fn, x):
    try:
        return fn(x)
    except Exception as e:  # noqa: BLE001
        return e


def base_record(meta, popularity):
    cat = CATEGORIES.get((meta["category"] or ["SANS_SERIF"])[0], (meta["category"] or ["?"])[0].lower())
    rec = {"family": meta["family"], "source": "google", "license": meta["license"], "category": cat}
    if meta["dir"] != typelib.default_dir(meta["family"], meta["license"]):
        rec["dir"] = meta["dir"]
    stroke = STROKES.get(meta.get("stroke") or "") or popularity.get("_stroke", {}).get(meta["family"])
    if stroke:
        rec["stroke"] = stroke
    cls = [c.lower() for c in meta.get("classifications") or []]
    if cls:
        rec["class"] = cls
    designers = [d.strip() for d in re.split(r",| and ", meta.get("designer") or "") if d.strip()]
    if designers:
        rec["designers"] = designers[:3]
    if meta.get("date_added"):
        rec["added"] = meta["date_added"]
    rec["popularity"] = popularity.get(meta["family"])
    static = sorted({f["weight"] for f in meta["fonts"] if f["style"] == "normal" and "[" not in f["filename"]})
    if static and not any("[" in f["filename"] for f in meta["fonts"]):
        rec["weights"] = static
    if any(f["style"] == "italic" for f in meta["fonts"]):
        rec["italic"] = True
    rec["subsets"] = meta["subsets"]
    return rec


def load_popularity(args, cache):
    try:
        raw = cached_fetch(GF_META_URL, os.path.join(cache, "gf-metadata.json"), "Google Fonts metadata",
                           args.refresh).decode("utf-8")
        data = json.loads(raw[raw.index("{"):])
    except Exception as e:  # noqa: BLE001 - popularity is optional
        log(f"note: popularity unavailable ({e}); catalogue written without it")
        return {}
    pop = {f["family"]: f.get("popularity") for f in data.get("familyMetadataList", [])}
    pop["_stroke"] = {f["family"]: STROKES.get(f.get("stroke") or "") for f in data.get("familyMetadataList", [])
                      if STROKES.get(f.get("stroke") or "")}
    return pop


def fontshare_records(args, cache, google_names):
    try:
        data = json.loads(cached_fetch(FONTSHARE_URL, os.path.join(cache, "fontshare.json"), "Fontshare API",
                                       args.refresh))
    except Exception as e:  # noqa: BLE001 - optional source
        log(f"note: Fontshare unavailable ({e}); skipped")
        return []
    cats = {"sans": "sans-serif", "serif": "serif", "slab": "serif", "display": "display", "script": "handwriting",
            "handwritten": "handwriting"}
    out = []
    for f in data.get("fonts", []):
        if typelib._norm(f["name"]) in google_names:
            continue
        first = (f.get("category") or "").split(",")[0].strip().lower()
        styles = f.get("styles") or []
        weights = sorted({s["weight"]["number"] for s in styles if not s.get("is_italic") and s.get("weight")})
        rec = {"family": f["name"], "source": "fontshare",
               "license": "ITF-FFL-2.0" if f.get("license_type") == "itf_ffl" else "OFL-1.1", "render": False,
               "category": cats.get(first, first or "?"), "url": f"https://www.fontshare.com/fonts/{f['slug']}"}
        if f.get("script"):
            rec["script"] = f["script"]
        if first == "slab" or "slab" in (f.get("category") or "").lower():
            rec["stroke"] = "slab"
        designers = [d["name"] for d in f.get("designers") or [] if d.get("name")]
        if designers:
            rec["designers"] = designers[:3]
        rec["popularity"] = None
        if weights:
            rec["weights"] = weights
        if any(s.get("is_italic") for s in styles):
            rec["italic"] = True
        props = next((s.get("properties") for s in styles if s.get("default")), None) or {}
        if props.get("x_height") and props.get("cap_height"):
            rec["metrics"] = {"x_cap": round(props["x_height"] / props["cap_height"], 3)}
        out.append(rec)
    return out


def ai_default_names():
    try:
        with open(AI_DEFAULTS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    entries = data.get("entries", data) if isinstance(data, dict) else data
    return {typelib._norm(n) for e in entries if isinstance(e, dict)
            for n in ((e.get("match") or {}).get("fonts") or [])}


def write_catalog(path, header, families):
    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in families]
    head = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    text = head[:-1] + ',"families":[\n' + ",\n".join(lines) + "\n]}\n"
    json.loads(text)  # sanity
    typelib._atomic_write(path, text.encode("utf-8"))
    return len(text.encode("utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("Usage:")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cache", default=str(typelib.cache_root() / "catalog-build"))
    ap.add_argument("--families", help="comma list; build only these (for testing)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--max-mb", type=float, default=6.0, help="skip measuring font files larger than this")
    ap.add_argument("--no-fontshare", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    os.makedirs(args.cache, exist_ok=True)

    popularity = load_popularity(args, cache=args.cache)
    jobs, unmeasured = google_records(args, args.cache)
    ids, _ = lang_index()
    measured = {}
    with cf.ProcessPoolExecutor(args.workers, initializer=_init_worker) as ex:
        futs = [ex.submit(measure, (m["family"], p, base_record(m, popularity)["category"])) for m, p in jobs]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            fam, rec = fut.result()
            measured[fam] = rec
            if i % 200 == 0:
                log(f"measured {i}/{len(futs)} ({time.time() - t0:.0f}s)")
    defaults = ai_default_names()
    families, errors = [], []
    for meta, _ in jobs:
        rec = base_record(meta, popularity)
        m = measured.get(meta["family"], {"error": "not measured"})
        if "error" in m:
            errors.append(f"{meta['family']}: {m['error']}")
            rec["measured"] = False
        else:
            rec.update(m)
            if not rec["axes"]:
                del rec["axes"]
        rec["ai_default"] = typelib._norm(rec["family"]) in defaults
        families.append(rec)
    for meta in unmeasured:
        rec = base_record(meta, popularity)
        rec["axes"] = {t: [_n(a), _n(b)] for t, (a, b) in meta["axes"].items()}
        if not rec["axes"]:
            del rec["axes"]
        rec["measured"] = False
        rec["ai_default"] = typelib._norm(rec["family"]) in defaults
        families.append(rec)
    google_names = {typelib._norm(r["family"]) for r in families}
    if not args.no_fontshare and not args.families:
        for rec in fontshare_records(args, args.cache, google_names):
            rec["ai_default"] = typelib._norm(rec["family"]) in defaults
            families.append(rec)
    families.sort(key=lambda r: (r["family"].lower(), r["source"]))
    commit = None
    try:
        commit = json.loads(typelib._fetch(COMMIT_URL, "google/fonts commit"))["sha"]
    except Exception:  # noqa: BLE001 - provenance only
        pass
    header = {
        "schema": SCHEMA, "built": datetime.date.today().isoformat(),
        "sources": {"google_fonts": {"repo": "https://github.com/google/fonts", "commit": commit},
                    "popularity": GF_META_URL if popularity else None,
                    "fontshare": FONTSHARE_URL if any(r["source"] == "fontshare" for r in families) else None,
                    "gflanguages": typelib._language_data()["source"]["version"]},
        "notes": "Measured by tools/build_font_catalog.py with scripts/font_audit.py. metrics: x_cap = x-height / "
                 "cap height, x_upm = x-height / UPM, width = mean a-z advance / x-height, contrast = thin/thick "
                 "stroke of 'o' at wght 400/opsz 14 (heuristic). legibility = I/l/1 soft IoU at 48 px "
                 "(heuristic, uncalibrated). woff2_kb = estimate for Google's latin / latin-ext ranges. "
                 "coverage.langs = base64 bitset over lang_index (100% base exemplars); core_missing = GF Latin "
                 "Core codepoints absent. measured=false: file too large or unreadable; use subsets.",
        "lang_index": ids,
    }
    size = write_catalog(args.out, header, families)
    n_google = sum(1 for r in families if r["source"] == "google")
    log(f"wrote {args.out}: {size / 1e6:.2f} MB, {len(families)} families ({n_google} google, "
        f"{len(families) - n_google} fontshare), {len(errors)} errors, {time.time() - t0:.0f}s")
    for e in errors[:20]:
        log(f"  error: {e}")
    # contrast distribution of text faces, to calibrate font_audit.CONTRAST_WARN
    con = sorted(r["metrics"]["contrast"] for r in families if r.get("category") in ("sans-serif", "serif")
                 and (r.get("metrics") or {}).get("contrast") is not None)
    if con:
        q = lambda p: con[min(len(con) - 1, int(p * len(con)))]  # noqa: E731
        log(f"text-face contrast quantiles p5 {q(.05)} p10 {q(.10)} p25 {q(.25)} p50 {q(.5)} (n={len(con)})")
    iou = sorted(max(r["legibility"].values()) for r in families if r.get("legibility"))
    if iou:
        q = lambda p: iou[min(len(iou) - 1, int(p * len(iou)))]  # noqa: E731
        log(f"max I/l/1 IoU quantiles p50 {q(.5)} p75 {q(.75)} p90 {q(.9)} p95 {q(.95)} (n={len(iou)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
