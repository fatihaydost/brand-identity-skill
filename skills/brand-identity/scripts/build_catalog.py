#!/usr/bin/env python3
"""Build the brand palette library: classifications + measured website palettes -> catalog, stats, gallery.

Maintainer tool. Reads, inside assets/library/:
  classifications.json   hand/agent labels per brand (source of truth for brand, url, country, industry,
                         positioning, strategy_note, exemplary, optional role_overrides / role_note)
and the raw measurements written by `python3 scripts/site_palette.py competitors <url...> --out DIR`
(DIR/competitors.json). Writes:
  catalog.json   one record per brand: the labels + measured roles, brand colours, hues, lightness structure
  stats.json     per-industry hue-family distributions, n_hues, dark-ground share, country mix
  gallery.html   self-contained swatch strips grouped by industry (no external requests)

Measured hex values are approximations of the public websites on the measurement day, not official brand
specifications (see TRADEMARKS.md). Roles come from site_palette.py and are proposals; corrections from visual
review live in classifications.json as `role_overrides` + `role_note`.

Usage:
  python3 scripts/build_catalog.py --raw harvest/                  # (re)measure: merge every competitors.json found
  python3 scripts/build_catalog.py --raw a/competitors.json --raw b/competitors.json
  python3 scripts/build_catalog.py                                 # relabel only: reuse measurements in catalog.json
  python3 scripts/build_catalog.py --check                         # validate schema and consistency, exit 1 on errors
  python3 scripts/build_catalog.py --list-industries               # the fixed industry vocabulary

Measuring new brands (read-only, polite):
  python3 scripts/site_palette.py competitors https://a.com https://b.com --out harvest/fintech --concurrency 3
  then add one classifications.json object per brand and run with --raw harvest/.
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, OrderedDict
from urllib.parse import urlparse

sys.dont_write_bytecode = True  # keep the skill folder clean (no __pycache__)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import colorlib  # noqa: E402

LIBRARY_DIR = os.path.normpath(os.path.join(HERE, "..", "assets", "library"))
SCHEMA = "brand-identity/library@1"

# Fixed industry vocabulary (also documented in references/library-guide.md).
INDUSTRIES = OrderedDict([
    ("banking", "retail and commercial banks"),
    ("fintech", "neobanks, payments, investing and spend-management apps"),
    ("insurance", "insurers (consumer and commercial)"),
    ("telecom", "mobile, broadband and fixed-line operators"),
    ("airline", "passenger airlines, full-service and low-cost"),
    ("ecommerce", "online marketplaces and pure-play online shops"),
    ("retail", "supermarkets, discounters, department, electronics and home stores"),
    ("fashion", "apparel, sportswear and footwear brands"),
    ("luxury", "luxury fashion, leather goods, jewellery and watches"),
    ("beauty", "cosmetics, skincare and beauty retail"),
    ("food-beverage", "packaged food, drinks and FMCG food brands"),
    ("restaurant-delivery", "quick-service restaurants, coffee chains and food delivery"),
    ("saas", "B2B and productivity software, developer platforms"),
    ("agency", "design, branding and advertising agencies"),
    ("education", "universities and online learning"),
    ("healthcare", "hospitals, pharma, pharmacies and digital health"),
    ("automotive", "car makers and commercial vehicles"),
    ("energy", "oil and gas, fuel retail, utilities and renewables"),
    ("travel-hospitality", "hotels, online travel agencies and hospitality tech"),
    ("media-entertainment", "streaming, news and publishing"),
    ("professional-services", "consulting, audit and law firms"),
])
FAMILIES = ("red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "brown", "neutral")
CHROMATIC_FAMILIES = FAMILIES[:-1]
PRICES = ("budget", "mid", "premium", "luxury")
NEUTRAL_C = 0.045    # OKLCH chroma below this is a neutral (white, grey, slate, black)
BRAND_C = 0.06       # chroma a colour needs to count as a brand hue (same threshold as site_engine.js)
DARK_GROUND_L = 0.40  # page background OKLCH L below this = dark ground
HUE_MERGE = 30       # hues closer than this (degrees) count as one hue
RARE_SHARE = 0.15    # a chromatic family present in fewer than this share of brands is "rare"
CLASS_KEYS = ("brand", "url", "country", "industry", "positioning", "strategy_note", "exemplary")
OPTIONAL_CLASS_KEYS = ("role_overrides", "role_note")
MEASURED_KEYS = ("roles", "role_confidence", "brand_colors", "hues", "n_hues", "lightness_range", "dark_ground",
                 "measured_at")
HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
# Browser default link colours (unvisited, visited): an unstyled <a>, never a brand decision.
UA_DEFAULTS = ("#0000ee", "#551a8b")
ISO2_RE = re.compile(r"^[A-Z]{2}$")


# ----------------------------------------------------------------------------- colour helpers

def oklch(hex_):
    return colorlib.hex_to_oklch(hex_)


def family_of_angle(h):
    """Chromatic family of an OKLCH hue angle (degrees), ignoring lightness and chroma."""
    h %= 360
    if h < 5 or h >= 335:
        return "pink"
    if h < 40:
        return "red"
    if h < 75:
        return "orange"
    if h < 115:
        return "yellow"
    if h < 170:
        return "green"
    if h < 225:
        return "teal"
    if h < 290:
        return "blue"
    return "purple"


def hue_family(hex_):
    """Hue family of a colour: one of FAMILIES. Low chroma -> neutral; dark low-chroma warm hues -> brown;
    very dark pinks (wine, bordeaux) -> red; very light reds (blush, rose) -> pink."""
    L, C, h = oklch(hex_)
    if C < NEUTRAL_C:
        return "neutral"
    fam = family_of_angle(h)
    if 30 <= h < 105 and L <= 0.62 and C < 0.13:
        return "brown"
    if fam == "pink" and L < 0.42:
        return "red"
    if fam == "red" and L > 0.8:
        return "pink"  # pastel reds (blush, rose) read as pink
    return fam


def merge_hues(colors):
    """OKLCH hue angles of the chromatic colours, merged when closer than HUE_MERGE degrees (first one wins)."""
    hues = []
    for c in colors:
        L, C, h = oklch(c)
        if C < BRAND_C:
            continue
        if any(min(abs(h - x) % 360, 360 - abs(h - x) % 360) < HUE_MERGE for x in hues):
            continue
        hues.append(round(h))
    return hues


def host_of(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def slug(s):
    s = s.lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("é", "e"), ("è", "e"),
                 ("ø", "o"), ("&", "and"), ("+", "plus")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "brand"


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 3)


# ----------------------------------------------------------------------------- validation

def validate_classifications(rows):
    """Return a list of error strings for classifications.json (empty = valid)."""
    errs = []
    if not isinstance(rows, list):
        return ["classifications.json: top level must be a list of objects"]
    seen_brand, seen_host = {}, {}
    for i, r in enumerate(rows):
        where = f"classifications[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{where}: must be an object")
            continue
        where = f"classifications[{i}] ({r.get('brand', '?')})"
        for k in CLASS_KEYS:
            if k not in r:
                errs.append(f"{where}: missing '{k}'")
        extra = set(r) - set(CLASS_KEYS) - set(OPTIONAL_CLASS_KEYS)
        if extra:
            errs.append(f"{where}: unknown keys {sorted(extra)}")
        if not isinstance(r.get("brand"), str) or not r.get("brand", "").strip():
            errs.append(f"{where}: 'brand' must be a non-empty string")
        url = r.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url or ""):
            errs.append(f"{where}: 'url' must be an http(s) URL")
        else:
            h = host_of(url)
            if h in seen_host:
                errs.append(f"{where}: url host {h} duplicates classifications[{seen_host[h]}]")
            seen_host[h] = i
        b = (r.get("brand") or "").lower()
        if b in seen_brand:
            errs.append(f"{where}: duplicate brand (also classifications[{seen_brand[b]}])")
        seen_brand[b] = i
        if not isinstance(r.get("country"), str) or not ISO2_RE.match(r.get("country") or ""):
            errs.append(f"{where}: 'country' must be an ISO 3166-1 alpha-2 code like 'TR'")
        if r.get("industry") not in INDUSTRIES:
            errs.append(f"{where}: industry {r.get('industry')!r} not in the vocabulary ({', '.join(INDUSTRIES)})")
        pos = r.get("positioning")
        if not isinstance(pos, dict):
            errs.append(f"{where}: 'positioning' must be an object {{price, tone}}")
        else:
            if pos.get("price") not in PRICES:
                errs.append(f"{where}: positioning.price must be one of {', '.join(PRICES)}")
            tone = pos.get("tone")
            if not (isinstance(tone, list) and 2 <= len(tone) <= 3 and all(isinstance(t, str) and t for t in tone)):
                errs.append(f"{where}: positioning.tone must be a list of 2-3 adjectives")
            if set(pos) - {"price", "tone"}:
                errs.append(f"{where}: positioning has unknown keys {sorted(set(pos) - {'price', 'tone'})}")
        note = r.get("strategy_note")
        if not isinstance(note, str) or len(note.strip()) < 10:
            errs.append(f"{where}: 'strategy_note' must be one sentence")
        if not isinstance(r.get("exemplary"), bool):
            errs.append(f"{where}: 'exemplary' must be true or false")
        ov = r.get("role_overrides")
        if ov is not None:
            if not isinstance(ov, dict):
                errs.append(f"{where}: 'role_overrides' must be an object role -> #rrggbb")
            else:
                for k, v in ov.items():
                    if k not in colorlib.ROLE_KEYS:
                        errs.append(f"{where}: role_overrides.{k} is not a role key")
                    if not (isinstance(v, str) and HEX_RE.match(v)):
                        errs.append(f"{where}: role_overrides.{k} must be a lowercase #rrggbb hex")
        if "role_note" in r and not isinstance(r["role_note"], str):
            errs.append(f"{where}: 'role_note' must be a string")
    return errs


def validate_catalog(records, classes):
    """Consistency of catalog.json with classifications.json and with its own measured fields."""
    errs = []
    by_host = {host_of(c["url"]): c for c in classes if isinstance(c, dict) and isinstance(c.get("url"), str)}
    cat_hosts = set()
    for r in records:
        where = f"catalog ({r.get('brand', '?')})"
        h = host_of(r.get("url", ""))
        cat_hosts.add(h)
        if h not in by_host:
            errs.append(f"{where}: not in classifications.json (stale record; rebuild)")
        else:
            c = by_host[h]
            for k in CLASS_KEYS:
                if c.get(k) != r.get(k):
                    errs.append(f"{where}: '{k}' differs from classifications.json (rebuild)")
                    break
        for k in MEASURED_KEYS:
            if k not in r:
                errs.append(f"{where}: missing measured field '{k}'")
        roles = r.get("roles") or {}
        for k in ("background", "primary"):
            if k not in roles:
                errs.append(f"{where}: roles.{k} missing")
        for k, v in roles.items():
            if not (isinstance(v, str) and HEX_RE.match(v)):
                errs.append(f"{where}: roles.{k} = {v!r} is not #rrggbb")
        for v in r.get("brand_colors") or []:
            if not (isinstance(v, str) and HEX_RE.match(v)):
                errs.append(f"{where}: brand_colors has {v!r}")
        if roles.get("primary") and r.get("brand_colors") and r["brand_colors"][0] != roles["primary"]:
            errs.append(f"{where}: brand_colors[0] must be roles.primary")
        if r.get("n_hues") != len(r.get("hues") or []):
            errs.append(f"{where}: n_hues != len(hues)")
        if roles.get("background") and r.get("dark_ground") != (oklch(roles["background"])[0] < DARK_GROUND_L):
            errs.append(f"{where}: dark_ground disagrees with roles.background")
        if r.get("primary_family") not in FAMILIES:
            errs.append(f"{where}: primary_family {r.get('primary_family')!r} invalid")
    for h, c in by_host.items():
        if h not in cat_hosts:
            errs.append(f"classification {c.get('brand')} ({h}) has no measurement in catalog.json (measure it, "
                        f"pass --raw, or remove it)")
    return errs


# ----------------------------------------------------------------------------- measurement -> record

def load_raw(paths):
    """host -> (site dict, measuredAt) from competitors.json files or directories containing them."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                files.extend(os.path.join(root, n) for n in names if n == "competitors.json")
        elif os.path.isfile(p):
            files.append(p)
        else:
            raise SystemExit(f"error: --raw {p}: no such file or directory")
    out = {}
    for f in sorted(files):
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("schema") != "brand-identity/competitors@1":
            print(f"warning: {f}: schema {doc.get('schema')!r}, expected brand-identity/competitors@1", file=sys.stderr)
        for s in doc.get("sites", []):
            if s.get("error") or not s.get("roles"):
                continue
            h = host_of(s.get("host") and "https://" + s["host"] or s.get("url", ""))
            out[h] = (s, doc.get("measuredAt"))
    return out


def measured_from_site(site, measured_at):
    """Measured fields (before overrides) from one site entry of competitors.json."""
    roles = {k: v.lower() for k, v in (site.get("roles") or {}).items()
             if k in colorlib.ROLE_KEYS and isinstance(v, str) and HEX_RE.match(v.lower())}
    for k in ("primary", "accent"):
        if roles.get(k) in UA_DEFAULTS:
            del roles[k]
    conf = {k: round(float(v), 2) for k, v in (site.get("confidence") or {}).items() if k in roles}
    top = [{"hex": t["hex"].lower(), "area": t.get("areaShare", 0), "score": t.get("score", 0)}
           for t in site.get("topColors", []) if isinstance(t.get("hex"), str) and HEX_RE.match(t["hex"].lower())]
    lr = site.get("lightnessRange")
    return {"roles": roles, "role_confidence": conf, "top_colors": top,
            "lightness_range": lr, "measured_at": measured_at}


def derive(rec):
    """Fill brand_colors, hues, n_hues, families, lightness and dark_ground from roles + top_colors."""
    roles = rec["roles"]
    cols = []

    def add(c):
        if not c or c in cols:
            return
        if any(colorlib.delta_e_ok(c, x) < 0.05 for x in cols):
            return
        cols.append(c)
    add(roles.get("primary"))
    add(roles.get("accent"))
    for t in rec.get("top_colors", []):
        if len(cols) >= 5:
            break
        if oklch(t["hex"])[1] >= BRAND_C and t["hex"] not in UA_DEFAULTS:
            add(t["hex"])
    rec["brand_colors"] = cols
    rec["hues"] = merge_hues(cols)
    rec["n_hues"] = len(rec["hues"])
    rec["primary_family"] = hue_family(roles["primary"]) if roles.get("primary") else "neutral"
    rec["accent_family"] = hue_family(roles["accent"]) if roles.get("accent") else None
    rec["families"] = sorted({hue_family(c) for c in cols}, key=FAMILIES.index)
    bgL = oklch(roles["background"])[0] if roles.get("background") else 1.0
    rec["dark_ground"] = bgL < DARK_GROUND_L
    pL = oklch(roles["primary"])[0] if roles.get("primary") else None
    rec["primary_lightness"] = round(pL, 3) if pL is not None else None
    lr = rec.get("lightness_range")
    if not (isinstance(lr, list) and len(lr) == 2):
        Ls = [oklch(c)[0] for c in list(roles.values()) + [t["hex"] for t in rec.get("top_colors", [])]]
        lr = [round(min(Ls), 2), round(max(Ls), 2)] if Ls else None
    rec["lightness_range"] = lr
    rec["lightness_structure"] = lightness_structure(rec["dark_ground"], pL)
    rec["primary_contrast"] = (round(colorlib.contrast_ratio(roles["primary"], roles["background"]), 2)
                               if roles.get("primary") and roles.get("background") else None)
    return rec


def lightness_structure(dark_ground, primary_L):
    ground = "dark ground" if dark_ground else "light ground"
    if primary_L is None:
        return ground
    tier = "dark" if primary_L < 0.45 else "light" if primary_L > 0.75 else "mid"
    return f"{ground}, {tier} primary"


def build_record(cls, measured):
    rec = OrderedDict((k, cls[k]) for k in CLASS_KEYS)
    rec["slug"] = cls["industry"] + "--" + slug(cls["brand"])
    roles = dict(measured["roles"])
    conf = dict(measured["role_confidence"])
    over = cls.get("role_overrides") or {}
    for k, v in over.items():
        roles[k] = v
        conf[k] = 1.0
    rec["roles"] = OrderedDict((k, roles[k]) for k in colorlib.ROLE_KEYS if k in roles)
    rec["role_confidence"] = OrderedDict((k, conf[k]) for k in colorlib.ROLE_KEYS if k in conf)
    rec["roles_overridden"] = sorted(over)
    if cls.get("role_note"):
        rec["role_note"] = cls["role_note"]
    rec["top_colors"] = measured.get("top_colors", [])
    rec["lightness_range"] = measured.get("lightness_range")
    rec["measured_at"] = measured.get("measured_at")
    derive(rec)
    return rec


def measured_from_record(r):
    """Reuse an existing catalog record's measurement (relabel-only rebuild); undo previous overrides."""
    return {"roles": r.get("measured_roles") or r.get("roles", {}),
            "role_confidence": r.get("measured_confidence") or r.get("role_confidence", {}),
            "top_colors": r.get("top_colors", []), "lightness_range": r.get("lightness_range"),
            "measured_at": r.get("measured_at")}


# ----------------------------------------------------------------------------- statistics

def summarize(records):
    """Aggregate view of a set of catalog records (used for stats.json and search_library.py --summary)."""
    n = len(records)
    if not n:
        return {"n": 0}
    prim = Counter(r["primary_family"] for r in records)
    pres = Counter(f for r in records for f in r.get("families", []))
    chroma_hues = sorted({h for r in records for h in r.get("hues", [])})
    structures = Counter(r.get("lightness_structure") for r in records)
    pLs = [r["primary_lightness"] for r in records if r.get("primary_lightness") is not None]
    pcs = [r["primary_contrast"] for r in records if r.get("primary_contrast")]
    unused = [f for f in CHROMATIC_FAMILIES if pres.get(f, 0) == 0]
    rare = [f for f in CHROMATIC_FAMILIES if 0 < pres.get(f, 0) / n < RARE_SHARE and n >= 5]
    return OrderedDict([
        ("n", n),
        ("primary_family", OrderedDict((f, round(c / n, 3)) for f, c in prim.most_common())),
        ("family_presence", OrderedDict((f, round(c / n, 3)) for f, c in pres.most_common())),
        ("mean_n_hues", round(sum(r["n_hues"] for r in records) / n, 2)),
        ("dark_ground_share", round(sum(1 for r in records if r["dark_ground"]) / n, 3)),
        ("lightness_structures", OrderedDict(structures.most_common())),
        ("primary_lightness", {"p25": pct(pLs, .25), "median": pct(pLs, .5), "p75": pct(pLs, .75)}),
        ("primary_contrast", {"p25": pct(pcs, .25), "median": pct(pcs, .5), "p75": pct(pcs, .75),
                              "below_3": round(sum(1 for x in pcs if x < 3) / len(pcs), 3) if pcs else None}),
        ("countries", OrderedDict(Counter(r["country"] for r in records).most_common())),
        ("unused_families", unused),
        ("rare_families", rare),
        ("hue_gaps", hue_gaps(chroma_hues)),
        ("exemplary", [r["brand"] for r in records if r.get("exemplary")]),
    ])


def hue_gaps(hues, min_gap=40, k=3):
    """Largest empty arcs of the hue wheel between measured brand hues: [{from, to, width, families}]."""
    if not hues:
        return [{"from": 0, "to": 360, "width": 360, "families": list(CHROMATIC_FAMILIES[:-1])}]
    hs = sorted(h % 360 for h in hues)
    gaps = []
    for i, a in enumerate(hs):
        b = hs[(i + 1) % len(hs)] + (360 if i == len(hs) - 1 else 0)
        if b - a >= min_gap:
            fams = []
            for d in range(int(a) + 5, int(b) - 4, 5):
                f = family_of_angle(d % 360)
                if f not in fams:
                    fams.append(f)
            gaps.append({"from": int(a), "to": int(b) % 360, "width": int(b - a), "families": fams})
    gaps.sort(key=lambda g: -g["width"])
    return gaps[:k]


def build_stats(records):
    by_ind = OrderedDict()
    for ind in INDUSTRIES:
        rows = [r for r in records if r["industry"] == ind]
        if rows:
            by_ind[ind] = summarize(rows)
    overall = summarize(records)
    return OrderedDict([
        ("schema", SCHEMA),
        ("count", len(records)),
        ("industries", len(by_ind)),
        ("turkish_brands", sum(1 for r in records if r["country"] == "TR")),
        ("families", list(FAMILIES)),
        ("method", "Measured from rendered public websites with site_palette.py competitors; hue families from "
                   "OKLCH (neutral C < 0.045). Approximations, not official brand specifications."),
        ("overall", overall),
        ("by_industry", by_ind),
    ])


# ----------------------------------------------------------------------------- gallery

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


SHORT = {"background": "ground", "surface": "surface", "text": "text", "primary": "primary", "accent": "accent",
         "link": "link"}


def render_gallery(records, stats):
    parts = []
    nav = []
    for ind, desc in INDUSTRIES.items():
        rows = [r for r in records if r["industry"] == ind]
        if not rows:
            continue
        s = stats["by_industry"][ind]
        nav.append(f'<a href="#{ind}">{esc(ind)} <span>{len(rows)}</span></a>')
        fam_bar = "".join(
            f'<i class="fam f-{f}" style="flex:{share}" title="{f} {round(share * 100)}%"></i>'
            for f, share in s["primary_family"].items())
        cards = []
        for r in sorted(rows, key=lambda r: (not r.get("exemplary"), r["brand"].lower())):
            roles = r["roles"]
            strip = []
            for k in ("background", "surface", "text", "primary", "accent", "link"):
                if k in roles:
                    low = r["role_confidence"].get(k, 1) < 0.5 and k not in r.get("roles_overridden", [])
                    strip.append(f'<div class="sw"><i style="background:{roles[k]}"></i>'
                                 f'<b>{SHORT.get(k, k)}{"?" if low else ""}</b><code>{roles[k]}</code></div>')
            extra = [c for c in r["brand_colors"] if c not in roles.values()]
            chips = "".join(f'<span class="chip" style="background:{c}" title="{c}"></span><code>{c}</code>'
                            for c in extra)
            star = '<span class="star" title="exemplary">★</span> ' if r.get("exemplary") else ""
            note = f'<p class="note">{esc(r["strategy_note"])}</p>'
            rn = f'<p class="note rn">Role note: {esc(r["role_note"])}</p>' if r.get("role_note") else ""
            cards.append(
                f'<article id="{esc(r["slug"])}"><header><h3>{star}{esc(r["brand"])}</h3>'
                f'<span class="meta">{esc(r["country"])} · {esc(r["positioning"]["price"])} · '
                f'{esc(", ".join(r["positioning"]["tone"]))} · {esc(r["primary_family"])}'
                f'{" · dark ground" if r["dark_ground"] else ""}</span></header>'
                f'<div class="strip">{"".join(strip)}</div>'
                f'{"<div class=chips>" + chips + "</div>" if extra else ""}{note}{rn}'
                f'<p class="meta host">{esc(host_of(r["url"]))} · measured {esc((r.get("measured_at") or "")[:10])}</p>'
                f'</article>')
        top = ", ".join(f"{f} {round(v * 100)}%" for f, v in list(s["primary_family"].items())[:3])
        parts.append(
            f'<section id="{ind}"><div class="sechead"><h2>{esc(ind)}</h2><span class="meta">{esc(desc)} · '
            f'{len(rows)} brands · primary: {esc(top)} · dark ground {round(s["dark_ground_share"] * 100)}%</span>'
            f'<div class="fambar">{fam_bar}</div></div><div class="grid">{"".join(cards)}</div></section>')
    tr = stats["turkish_brands"]
    return GALLERY_TEMPLATE.replace("__NAV__", "".join(nav)).replace("__BODY__", "".join(parts)).replace(
        "__COUNT__", f'{stats["count"]} brands · {stats["industries"]} industries · {tr} Turkish')


GALLERY_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brand Palette Library</title>
<style>
:root{--bg:#f5f4f1;--card:#ffffff;--ink:#1c1c1a;--muted:#686660;--line:#e2e0da;--accent:#1f5f7a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--card:#1f1f1d;--ink:#ecebe7;
--muted:#a3a19a;--line:#34332f;--accent:#7cc0dc}}
:root[data-theme="dark"]{--bg:#151514;--card:#1f1f1d;--ink:#ecebe7;--muted:#a3a19a;--line:#34332f;--accent:#7cc0dc}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.top{padding:24px 16px 8px;max-width:1400px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.lede{color:var(--muted);margin:0 0 12px;max-width:80ch}
nav{position:sticky;top:0;z-index:3;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 16px;
display:flex;gap:6px;flex-wrap:wrap}
nav a{color:var(--ink);text-decoration:none;border:1px solid var(--line);border-radius:99px;padding:2px 10px;font-size:12px}
nav a span{color:var(--muted)}
nav a:hover{border-color:var(--accent)}
main{max-width:1400px;margin:0 auto;padding:0 16px 48px}
section{padding-top:28px;scroll-margin-top:48px}
.sechead h2{font-size:18px;margin:0}
.meta{color:var(--muted);font-size:12px}
.fambar{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:8px 0 14px;max-width:520px}
.fam{display:block}
.f-red{background:#d6312b}.f-orange{background:#ee7a1c}.f-yellow{background:#f2c230}.f-green{background:#2f9e4f}
.f-teal{background:#1a9c9c}.f-blue{background:#2c5fd0}.f-purple{background:#7a46c9}.f-pink{background:#e0458f}
.f-brown{background:#8a5a34}.f-neutral{background:#77756f}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
article{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;scroll-margin-top:56px;min-width:0}
article:target{outline:2px solid var(--accent)}
article header{display:flex;flex-direction:column;gap:2px;margin-bottom:8px}
h3{font-size:15px;margin:0}
.star{color:#c99a12}
.strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:4px}
.sw{min-width:0;display:flex;flex-direction:column;gap:2px}
.sw i{display:block;height:44px;border-radius:5px;border:1px solid var(--line)}
.sw b{font-size:10px;font-weight:600;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sw code{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chips{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;margin-top:8px}
.chip{width:14px;height:14px;border-radius:3px;display:inline-block;border:1px solid var(--line)}
.chips code{font-size:11px;color:var(--muted)}
.note{margin:8px 0 0;font-size:12.5px}
.rn{color:var(--muted)}
.host{margin:6px 0 0}
footer{max-width:1400px;margin:0 auto;padding:16px;color:var(--muted);font-size:12px}
@media (max-width:480px){.grid{grid-template-columns:1fr}}
</style></head><body>
<div class="top"><h1>Brand Palette Library</h1>
<p class="lede">__COUNT__. Website palettes measured from rendered public pages: approximations, not official brand
specifications. Role labels are measured proposals; “?” marks a low-confidence role. All names and colour marks
belong to their owners; this is reference material for reading a category, never for copying an identity.</p></div>
<nav>__NAV__</nav>
<main>__BODY__</main>
<footer>Built by scripts/build_catalog.py. Query it with scripts/search_library.py --industry X --summary.</footer>
</body></html>
"""


# ----------------------------------------------------------------------------- main

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data, compact=False):
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(data, fh, ensure_ascii=False, indent=None, separators=(",", ":"))
        else:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main(argv=None):
    colorlib.setup_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", action="append", default=[],
                    help="competitors.json file or a directory searched for them (repeatable)")
    ap.add_argument("--library", default=LIBRARY_DIR, help="library folder (default: assets/library)")
    ap.add_argument("--check", action="store_true", help="validate classifications + catalog; exit 1 on errors")
    ap.add_argument("--list-industries", action="store_true", help="print the industry vocabulary")
    a = ap.parse_args(argv)

    if a.list_industries:
        for k, v in INDUSTRIES.items():
            print(f"{k:22s} {v}")
        return 0
    cls_path = os.path.join(a.library, "classifications.json")
    cat_path = os.path.join(a.library, "catalog.json")
    classes = read_json(cls_path)
    if classes is None:
        print(f"error: {cls_path} not found", file=sys.stderr)
        return 1
    errs = validate_classifications(classes)
    if a.check:
        catalog = read_json(cat_path, [])
        errs += validate_catalog(catalog, classes)
        stats = read_json(os.path.join(a.library, "stats.json"), {})
        if catalog and stats.get("count") != len(catalog):
            errs.append("stats.json count differs from catalog.json (rebuild)")
        if not os.path.exists(os.path.join(a.library, "gallery.html")):
            errs.append("gallery.html missing (rebuild)")
        for e in errs:
            print("  ! " + e)
        n_tr = sum(1 for c in classes if isinstance(c, dict) and c.get("country") == "TR")
        inds = Counter(c.get("industry") for c in classes if isinstance(c, dict))
        print(f"{len(classes)} classifications, {len(catalog)} catalog records, {len(inds)} industries, "
              f"{n_tr} Turkish; {len(errs)} problem(s)")
        return 1 if errs else 0
    if errs:
        for e in errs:
            print("  ! " + e, file=sys.stderr)
        print(f"error: classifications.json has {len(errs)} problem(s); fix them first", file=sys.stderr)
        return 1

    raw = load_raw(a.raw) if a.raw else {}
    previous = {host_of(r["url"]): r for r in read_json(cat_path, [])}
    records, missing = [], []
    for c in classes:
        h = host_of(c["url"])
        if h in raw:
            m = measured_from_site(*raw[h])
        elif h in previous:
            m = measured_from_record(previous[h])
        else:
            missing.append(f"{c['brand']} ({h})")
            continue
        if not m["roles"].get("primary") and not (c.get("role_overrides") or {}).get("primary"):
            missing.append(f"{c['brand']} ({h}): no primary measured; add role_overrides.primary")
            continue
        rec = build_record(c, m)
        rec["measured_roles"] = m["roles"]
        rec["measured_confidence"] = m["role_confidence"]
        records.append(rec)

    # category fit: does the brand's primary family belong to its industry's common families?
    for r in records:
        peers = [x for x in records if x["industry"] == r["industry"] and x is not r]
        share = sum(1 for x in peers if x["primary_family"] == r["primary_family"]) / len(peers) if peers else 0
        r["category_fit"] = "conform" if share >= 0.2 else "differentiate"
        r["primary_family_share"] = round(share, 3)

    records.sort(key=lambda r: (list(INDUSTRIES).index(r["industry"]), r["brand"].lower()))
    stats = build_stats(records)
    with open(cat_path, "w", encoding="utf-8") as fh:  # one record per line: small, diff-friendly
        fh.write("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records)
                 + "\n]\n")
    write_json(os.path.join(a.library, "stats.json"), stats)
    with open(os.path.join(a.library, "gallery.html"), "w", encoding="utf-8") as fh:
        fh.write(render_gallery(records, stats))
    print(f"catalog: {len(records)} records, {stats['industries']} industries, {stats['turkish_brands']} Turkish "
          f"-> {cat_path}")
    print(f"stats   -> {os.path.join(a.library, 'stats.json')}")
    print(f"gallery -> {os.path.join(a.library, 'gallery.html')}")
    low = [r["brand"] for r in records if r["role_confidence"].get("primary", 1) < 0.5 and not r.get("role_note")
           and "primary" not in r["roles_overridden"]]
    if low:
        print(f"review: {len(low)} low-confidence primaries without role_note: {', '.join(low[:20])}"
              f"{' ...' if len(low) > 20 else ''}", file=sys.stderr)
    if missing:
        print(f"warning: {len(missing)} classification(s) without measurement:", file=sys.stderr)
        for m in missing:
            print("  - " + m, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
