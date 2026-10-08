#!/usr/bin/env python3
"""Brand guidelines kit for the chosen set. docs/architecture.md sections 4.7, 7.3.

Pages (1920x1080, reference frame: meta strip `→ [ n.m ] SECTION`, `BRAND GUIDELINES · VERSION · date · page`):
  1 Cover · 2 Brand idea · 3 Logo (primary, construction, clear space) · 4 Variations + minimum sizes (px, mm) ·
  5 Logo on colour (2x2) + 4-6 misuses (logo.misuse) · 6 Colours (proportion bands, 80/50/20 tints, HEX/RGB/OKLCH, Lab D50, ask
  the printer for CMYK/Pantone) · 7 Typography (roles, weight columns, giant "Aa", scale, features, licence,
  embed code) · 8 Applications (site header, business card, social avatar).
  --full adds grid, iconography, imagery, dark mode and accessibility pages. Pages of `none` components are
  omitted (a `none` logo with an existing asset is still documented).
Outputs in <work>/kit/<SET>/ (cleaned on every build): pages/NN-*.png, <brand>-guidelines.pdf, logo/ (logolib.variants when installed),
tokens/ (export_tokens.py + fonts.css), fonts.md (sources, licences, links; never font files).
Gates: every face loaded and used by its role, no text overflow, text contrast, logo >= min size, PDF fonts
embedded (FontFile2/3, no Type3), expected page count, no external requests in the HTML.

Usage:
  python3 scripts/kit_build.py brand-identity/ferrow/sets/A
  python3 scripts/kit_build.py brand-identity/ferrow/sets/A --full
"""
import argparse
import html as _html
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    import pydeps  # noqa: E402
    pydeps.ensure()  # missing packages: re-run in uv's cached environment (or the fallback venv)

import colorlib  # noqa: E402
import identitylib  # noqa: E402
import presentlib as pl  # noqa: E402
import render_png  # noqa: E402

e = pl.e
W, H = 1920, 1080
VERSION = "1.0"
WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold",
                700: "Bold", 800: "ExtraBold", 900: "Black"}
ALPHA = "".join(c + c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
DIGITS = "0123456789"


# --- helpers ---

class Kit:
    """Everything the page builders need about the set."""

    def __init__(self, set_dir):
        self.info = pl.load_set(set_dir)
        self.ident = self.info["identity"]
        self.pal = self.info["palette"]
        self.t = pl.theme(self.pal)
        self.logos = pl.logo_svgs(self.info)
        self.brand = self.ident["brand"]
        self.faces = pl.resolve_faces(self.ident, self.info["dir"])
        self.ty = self.ident.get("type") or {}
        lg = self.ident.get("logo") or {}
        self.min_px = (lg.get("min_size") or {}).get("px", 24)
        self.min_mm = (lg.get("min_size") or {}).get("mm", 8)
        self.clear = lg.get("clear_space") or {"unit": "cap-height", "multiple": 1, "inferred": True}
        self.tr = pl.strings(self.info)  # tool text in the document language (i18nlib)
        self.findings = []  # page builders add kit findings here (misuse page)

    def has(self, comp):
        if comp == "logo":
            return bool(self.logos.get("primary") or self.logos.get("image"))
        if comp == "type":
            return pl.mode_of(self.ident, "type") != "none" and bool(self.ty.get("display") or self.ty.get("text"))
        if comp == "palette":
            return self.pal is not None
        return True

    def mark(self, key="primary", mono=None, mode="light", height=None, attrs="", bg=None, probe=True, one=False):
        """A logo box. Full colour on `bg` uses logolib's on-* version for that ground when there is one; `mono`
        forces one colour (also on kept logos without palette roles). The probe measures every box
        (data-logo-ground) unless probe=False (the misuse examples)."""
        import identity_card
        svg = self.logos.get(key) or (self.logos.get("primary") if key != "small" else None)
        if not svg:
            return ""
        inner = None
        if bg and not mono and svg == self.logos.get("primary"):
            ver = pl.logo_version_for_bg(self.info["dir"], bg)
            if ver:
                inner = pl.colorize(ver, None)
        if inner is None:
            inner = identity_card._mark(svg, self.pal, mode=mode, mono=mono)
        st = f' style="height:{height:.1f}px"' if height else ""
        pa = (" data-logo-ground" + (" data-one-colour" if (mono or one) else "")) if probe else ""
        return f'<div class="mk"{st}{attrs}{pa}>{inner}</div>'

    def aspect(self, key="primary"):
        svg = self.logos.get(key) or self.logos.get("primary")
        return pl.aspect(svg) if isinstance(svg, str) and svg.lstrip().startswith("<") else 3.0

    def logo_parts(self, key="primary", mode="light"):
        svg = self.logos.get(key) or self.logos.get("primary") or ""
        return [h for h in (pl.role_hex(self.pal, r, mode) for r in pl.logo_colors(svg)) if h]


ICON_FULL_PX = 64  # from this size up a mark box shows the full symbol; the small cut is for 16-48 px


def icon_key(logos, px):
    """Which logo master a square mark box of `px` shows: the small cut below ICON_FULL_PX, else the full symbol
    (a monogram, then the small mark of a wordmark-only set, then the primary logo when there is no symbol)."""
    if px < ICON_FULL_PX and logos.get("small"):
        return "small"
    for key in ("symbol", "monogram", "small"):
        if logos.get(key):
            return key
    return "primary"


def app_icon_svg(set_dir):
    """The built app icon (logo/build/app-icon-512.svg) when the set drew its own (app_icon_svg), else None."""
    bd = os.path.join(set_dir, "logo", "build")
    if not os.path.isfile(os.path.join(bd, "master-app-icon.svg")):
        return None
    try:
        with open(os.path.join(bd, "app-icon-512.svg"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def site_domain(work):
    """The brand's own web address from brief.json ('site'), without scheme and www; None without a site."""
    try:
        with open(os.path.join(work or "", "brief.json"), encoding="utf-8") as fh:
            site = (json.load(fh) or {}).get("site")
    except (OSError, ValueError):
        return None
    if not isinstance(site, str) or not site.strip():
        return None
    from urllib.parse import urlparse
    u = site.strip()
    host = urlparse(u if "://" in u else "https://" + u).hostname or ""
    host = host[4:] if host.startswith("www.") else host
    return host if "." in host else None


def axes_html(ident, t):
    rows = []
    for key, val in (ident.get("axes") or {}).items():
        lo, hi = identitylib.AXES.get(key, (key, ""))
        lo, hi = t.word("axis", lo), t.word("axis", hi)
        ticks = "".join(f'<i style="left:{p}%"></i>' for p in (0, 25, 50, 75, 100))
        rows.append(f'<div class="ax"><span class="l{" on" if val < 0 else ""}">{e(lo)}</span><span class="track">'
                    f'{ticks}<b style="left:{(val + 2) / 4 * 100:.0f}%"></b></span>'
                    f'<span class="{"on" if val > 0 else ""}">{e(hi)}</span></div>')
    return f'<div class="axes">{"".join(rows)}</div>' if rows else ""


def head(title, lede=""):
    return (f'<div class="head g12"><p class="lede">{lede}</p><h1 class="title" data-role="display" data-fit>'
            f'{e(title)}</h1></div>')


# ----------------------------------------------------------------------------- pages

def page_cover(k):
    asp = k.aspect("primary")
    h = min(190.0, 1000 / asp)
    on = k.t["onPrimary"]
    tr = k.tr
    d = pl.today_long(t=tr)
    sector = k.brand.get("sector") or ""
    content = (f'<div class="hero">{k.mark("primary", mono=on, height=h, attrs=f" data-logo=cover data-min={k.min_px}")}'
               f'</div>' if k.has("logo") else '<div class="hero"></div>')
    content += (f'<div class="bottom"><h1 class="disp" data-role="display">{e(tr("kit.guide"))}</h1>'
                f'<div class="v">{e(tr("kit.version", v=VERSION))}<br>{e(d)}<br>{e(sector)}</div></div>')
    return {"name": "cover", "cls": "cover", "section": tr("kit.sec.cover"), "subs": [tr("kit.sec.cover")],
            "content": content}


def page_idea(k):
    st = k.ident["set"]
    tr = k.tr
    pal = k.pal or {}
    feels = "".join(f"<span>{e(f)}</span>" for f in pal.get("feels") or [])
    bl = e(pl.lang_of(k.brand))
    lede = " — ".join(x for x in (f'<span lang="{bl}">{e(k.brand["tagline"])}</span>' if k.brand.get("tagline")
                                  else "", e(k.brand.get("sector") or "")) if x)
    side = (f'<div class="side"><span class="lab">{e(tr("kit.idea.sits"))}</span>{axes_html(k.ident, tr)}'
            + (f'<span class="lab" style="margin-top:26px">{e(tr("kit.idea.feels"))}</span><div class="feels">{feels}'
               '</div>' if feels else "")
            + '</div>')
    concept = ((k.ident.get("logo") or {}).get("concept") or "")
    body = (f'<div class="body g12 idea">{side}<div style="grid-column:5/13;display:flex;flex-direction:column">'
            f'<span class="lab">{e(tr("kit.idea.idea"))}</span><p class="mech disp" data-role="display" style="margin-top:14px;'
            f'text-transform:none;letter-spacing:0">{e(st.get("mechanism") or "")}</p>'
            f'<div class="g12" style="grid-template-columns:repeat(8,1fr);margin-top:auto">'
            f'<div style="grid-column:1/5"><span class="lab">{e(tr("card.move"))}</span>'
            f'<p class="p" style="margin-top:10px">{e(st.get("expression_move") or "")}</p></div>'
            f'<div style="grid-column:5/9"><span class="lab">{e(tr("card.differs"))}</span>'
            f'<p class="p" style="margin-top:10px">{e(st.get("differs_by") or "")}</p></div></div>'
            + (f'<div style="margin-top:28px"><span class="lab">{e(tr("kit.idea.mark"))}</span><p class="p" style="margin-top:10px;'
               f'max-width:900px">{e(concept)}</p></div>' if concept else "")
            + '</div></div>')
    return {"name": "idea", "section": tr("kit.sec.idea"),
            "subs": [tr("kit.sec.idea"), tr("kit.sec.personality"), tr("kit.sec.position")],
            "content": head(tr("kit.head.idea"), lede) + body}


def _clear_space(k, svg, h_px):
    """Clear-space distance in px for a logo rendered h_px tall, plus guide lines (cap line, baseline)."""
    vb = pl.viewbox(svg) or [0, 0, 100, 100]
    met = pl.wordmark_metrics(svg)
    mult = float(k.clear.get("multiple") or 1)
    unit = k.clear.get("unit") or "cap-height"
    guides, inferred = [], bool(k.clear.get("inferred"))
    if met:
        cap, base = met
        s = h_px / vb[3]
        cap_px = cap * s
        guides = [(k.tr("kit.logo.cap_height"), (base - cap - vb[1]) * s), (k.tr("kit.logo.baseline"), (base - vb[1]) * s)]
        x = {"cap-height": cap_px, "x-height": 0.7 * cap_px, "symbol-half": h_px / 2}.get(unit, cap_px) * mult
        if unit == "x-height":
            inferred = True
    else:
        units = ((pl.logo_manifest(k.info["dir"]).get("clear_space") or {}).get("units"))
        if units:  # logolib measured it in the master's viewBox units
            x = float(units) * h_px / vb[3]
        else:
            x, inferred = 0.25 * h_px * mult, True
    return x, guides, unit, mult, inferred


def page_logo(k):
    svg = k.logos.get("primary")
    asp = k.aspect("primary")
    h = min(220.0, 640 / asp, 300)
    x, guides, unit, mult, inferred = _clear_space(k, svg if isinstance(svg, str) else "", h)
    w = h * asp
    lines = "".join(f'<div class="gl" style="left:{-x:.1f}px;width:{w + 2 * x:.1f}px;top:{y:.1f}px">'
                    f'<span>{e(n)}</span></div>' for n, y in guides)
    xm = (f'<div class="xm" style="left:{-x / 2:.1f}px;top:{h / 2 - 8:.1f}px">x</div>'
          f'<div class="xm" style="left:{w + x / 2:.1f}px;top:{h / 2 - 8:.1f}px">x</div>'
          f'<div class="xm" style="left:{w / 2:.1f}px;top:{-x / 2 - 8:.1f}px">x</div>'
          f'<div class="xm" style="left:{w / 2:.1f}px;top:{h + x / 2 - 8:.1f}px">x</div>')
    cs = (f'<div class="cs" style="width:{w:.1f}px;height:{h:.1f}px">'
          f'<div class="zone" style="left:{-x:.1f}px;top:{-x:.1f}px;width:{w + 2 * x:.1f}px;height:{h + 2 * x:.1f}px">'
          f'</div><div class="inner" style="left:0;top:0;width:{w:.1f}px;height:{h:.1f}px"></div>'
          f'{k.mark("primary", height=h, attrs=f" data-logo=primary data-min={k.min_px}")}{lines}{xm}</div>')
    tr = k.tr
    unit_word = tr.word("kit.unit", unit)
    ltype = (k.ident.get("logo") or {}).get("type") or ""
    kind = tr.word("logo_type", ltype, ltype.replace("+", " + ")) if ltype else tr("kit.logo.logo_word")
    kept = pl.mode_of(k.ident, "logo") == "keep"
    side = (f'<div class="side"><p class="p">{e(tr("kit.logo.primary_p", kind=kind))}'
            f'{(" " + e(tr("kit.logo.kept_p"))) if kept else ""}</p>'
            f'<div><span class="lab">{e(tr("kit.sec.clear"))}</span><p class="p" style="margin-top:8px">'
            f'{e(tr("kit.logo.clear_p", mult=f"{mult:g}", unit=unit_word))}'
            f'{(" " + e(tr("kit.logo.inferred"))) if inferred else ""}</p></div>'
            f'<ul class="rule-list"><li>{e(tr("kit.logo.rule1"))}</li>'
            f'<li>{e(tr("kit.logo.rule2"))}</li><li>{e(tr("kit.logo.rule3"))}</li></ul></div>')
    body = f'<div class="body g12 logo1">{side}<div class="work">{cs}</div></div>'
    return {"name": "logo", "section": tr("kit.sec.primary"),
            "subs": [tr("kit.sec.primary"), tr("kit.sec.clear"), tr("kit.sec.construction")],
            "content": head(tr("kit.head.logo"), "") + body}


def page_variations(k):
    tiles = []
    tr = k.tr

    def tile(title, text, key=None, mono=None, bg=None, extra=""):
        asp = k.aspect(key or "primary")
        h = min(150.0, 300 / asp)
        style = f' style="background:{bg};box-shadow:none"' if bg else ""
        _vn, ve = pl.version_entry_for_bg(k.info["dir"], bg) if bg and (key or "primary") == "primary" else (None, None)
        if ve:  # logolib's file for this ground
            mk = k.mark("primary", height=h, bg=bg, one=bool(ve.get("one_color")))
        else:
            mk = k.mark(key or "primary", mono=mono, height=h, bg=bg)
        tiles.append(f'<div><div class="tile"{style}>{mk}{extra}</div>'
                     f'<h3>{e(title)}</h3><p>{e(text)}</p></div>')
    lg = k.logos
    if lg.get("horizontal"):
        tile(tr("kit.var.primary"), tr("kit.var.primary_p"), "horizontal")
    if lg.get("stacked"):
        tile(tr("kit.var.stacked"), tr("kit.var.stacked_p"), "stacked")
    if lg.get("symbol"):
        tile(tr("kit.var.symbol"), tr("kit.var.symbol_p"), "symbol")
    if lg.get("small"):
        small = k.logos["small"]
        on = k.t["onPrimary"]
        fav = "".join(f'<div style="width:{s * 1.6:.0f}px;height:{s * 1.6:.0f}px;background:{k.t["primary"]};'
                      f'border-radius:{s * .3:.0f}px;display:flex;align-items:center;justify-content:center;'
                      f'margin:0 8px">{pl.colorize(small, k.pal, mono=on).replace("<svg", f"<svg style=height:{s}px;width:auto", 1)}'
                      f'</div>' for s in (48, 32, 16))
        tiles.append(f'<div><div class="tile">{fav}</div><h3>{e(tr("kit.var.favicon"))}</h3>'
                     f'<p>{e(tr("kit.var.favicon_p"))}</p></div>')
    if len(tiles) < 4:
        tile(tr("kit.var.one"), tr("kit.var.one_p"), "primary", mono=k.t["ink"])
    if len(tiles) < 4:
        tile(tr("kit.var.reversed"), tr("kit.var.reversed_p"), "primary", mono=k.t["onDark"], bg=k.t["dark"])
    if len(tiles) < 4:
        tile(tr("kit.var.on_primary"), tr("kit.var.on_primary_p"), "primary", mono=k.t["onPrimary"],
             bg=k.t["primary"])
    mins = []
    asp = k.aspect("primary")
    mins.append(f'<figure>{k.mark("primary", height=k.min_px, attrs=f" data-logo=minimum data-min={k.min_px}")}'
                f'<figcaption><b>{k.min_px} px</b> · {e(tr("kit.var.min_screen"))}</figcaption></figure>')
    mins.append(f'<figure><div class="mk" style="height:{k.min_mm}mm">'
                f'{pl.colorize(k.logos.get("primary"), k.pal) if isinstance(k.logos.get("primary"), str) else ""}</div>'
                f'<figcaption><b>{k.min_mm} mm</b> · {e(tr("kit.var.min_print"))}</figcaption></figure>')
    if lg.get("small"):
        for s in (32, 16):
            mins.append(f'<figure>{k.mark("small", height=s)}<figcaption><b>{s} px</b> · '
                        f'{e(tr("kit.var.min_symbol"))}</figcaption></figure>')
    del asp
    side = (f'<div class="side"><p class="p">{e(tr("kit.var.side_small" if lg.get("small") else "kit.var.side_nosmall", px=k.min_px))}'
            '</p></div>')
    body = (f'<div class="body g12">{side}<div class="vars">{"".join(tiles[:4])}</div>'
            f'<div class="mins">{"".join(mins)}</div></div>')
    return {"name": "logo-variations", "section": tr("kit.sec.variations"),
            "subs": [tr("kit.sec.variations"), tr("kit.sec.min")], "content": head(tr("kit.head.logo"), "") + body}


def _best_version(k, bg):
    parts = k.logo_parts("primary")
    if parts and all(colorlib.contrast_ratio(p, bg) >= 3 for p in parts):
        return None, min(colorlib.contrast_ratio(p, bg) for p in parts)
    cands = [k.t["ink"], k.t["onDark"], "#ffffff", "#111111", k.t["onPrimary"]]
    mono = max(cands, key=lambda c: colorlib.contrast_ratio(c, bg))
    return mono, colorlib.contrast_ratio(mono, bg)


def page_on_colour(k):
    tr = k.tr
    grounds = [(tr("kit.onc.paper"), k.t["panel"]), (tr("kit.onc.primary"), k.t["primary"]),
               (tr("kit.onc.ink"), k.t["dark"]), (tr("kit.onc.accent"), k.t["accent"])]
    if colorlib.delta_e_ok(k.t["accent"], k.t["primary"]) < 0.05:
        grounds[3] = (tr("kit.onc.ground"), k.t["paper"])
    sq = []
    asp = k.aspect("primary")
    h = min(84.0, 330 / asp)
    for name, bg in grounds:
        fg = pl.ink_on(bg)
        vname, ventry = pl.version_entry_for_bg(k.info["dir"], bg)
        if ventry:  # logolib painted the logo for this ground: use that file as delivered
            one = bool(ventry.get("one_color"))
            hexes = [p_.get("hex") for p_ in ventry.get("parts") or [] if p_.get("hex")]
            cr = min([colorlib.contrast_ratio(x, bg) for x in hexes] or [21])
            word = tr("kit.onc.one") if one else tr("kit.onc.full")
            mk = k.mark("primary", height=h, bg=bg, one=one)
        else:
            mono, cr = _best_version(k, bg)
            word = tr("kit.onc.full") if mono is None else tr("kit.onc.one")
            mk = k.mark("primary", mono=mono, height=h, bg=bg)
        sq.append(f'<div class="sq" style="background:{bg};color:{fg}">{mk}'
                  f'<span class="lab" style="color:{fg}">{e(name)} · {e(word)} · {tr.ratio(cr)}</span></div>')
    tiles = misuse_tiles(k, asp)
    body = (f'<div class="body g12"><div class="onc">{"".join(sq)}</div>'
            f'<div class="mis" data-n="{len(tiles)}">{"".join(tiles)}</div></div>')
    return {"name": "logo-on-colour", "section": tr("kit.sec.on_colour"),
            "subs": [tr("kit.sec.on_colour"), tr("kit.sec.misuse")],
            "content": head(tr("kit.head.logo"), e(tr("kit.onc.lede"))) + body}


# --- misuse (docs/architecture.md section 7.3): logo.misuse = 4-6 items, generic ones drawn from the real logo,
# brand-specific ones drawn by the model as their own SVG (same viewBox logic as the logo, sanitised here)

MISUSE_SVG_TAGS = ("svg", "g", "path", "circle", "ellipse", "rect", "polygon", "polyline", "line", "title", "desc")
MISUSE_SVG_MAX_BYTES = 512 * 1024   # a drawn misuse is a few shapes; anything larger is not a logo drawing
MISUSE_SVG_MAX_ELEMENTS = 5000


def misuse_svg(path, set_dir=None):
    """A model-drawn misuse SVG -> (inline SVG text, None) or (None, reason). Allowed: filled shapes and groups
    (data-color for palette roles, raw fills allowed: a misuse may be off-palette). Rejected: any other element
    (<text>, <image>, <script>, <style>, <use>, <foreignObject>, filter ...), event attributes, href and url()
    references, data-op (draw the final shapes; nothing is resolved here), a missing or empty viewBox, a file that
    resolves outside the set folder (symlinks included), more than 512 KB or 5000 elements."""
    import xml.etree.ElementTree as ET
    if set_dir:
        real, root_dir = os.path.realpath(path), os.path.realpath(set_dir)
        try:
            inside = os.path.commonpath([real, root_dir]) == root_dir
        except ValueError:  # Windows: another drive
            inside = False
        if not inside:
            return None, "resolves outside the set folder"
    try:
        if os.path.getsize(path) > MISUSE_SVG_MAX_BYTES:
            return None, f"larger than {MISUSE_SVG_MAX_BYTES // 1024} KB"
    except OSError as exc:
        return None, f"cannot read {os.path.basename(path)}: {exc.strerror or exc}"
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, f"cannot read {os.path.basename(path)}: {exc.strerror or exc}"
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        return None, "DOCTYPE/ENTITY declarations are not allowed"
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return None, f"not valid XML: {exc}"

    def local(tag):
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
    if local(root.tag) != "svg":
        return None, "the root element must be <svg>"
    vb = pl.viewbox(f'viewBox="{root.get("viewBox") or ""}"')
    if not vb or len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        return None, "needs a viewBox 'x y width height' with a positive size (use the logo's own)"
    if sum(1 for _ in root.iter()) > MISUSE_SVG_MAX_ELEMENTS:
        return None, f"more than {MISUSE_SVG_MAX_ELEMENTS} elements"
    for el in root.iter():
        tag = local(el.tag)
        if tag not in MISUSE_SVG_TAGS:
            return None, f"<{tag}> is not allowed (filled shapes only, no text, images, scripts or filters)"
        for name, val in el.attrib.items():
            n = local(name).lower()
            if n.startswith("on") or n == "href" or "url(" in str(val).lower() or n == "filter":
                return None, f"attribute {n}={val[:40]!r} on <{tag}> is not allowed (no scripts or references)"
            if n == "data-op":
                return None, "data-op is not resolved in misuse drawings; draw the final shapes"
            if n == "style" and re.search(r"url\(|filter|@import|expression", str(val), re.I):
                return None, f"style on <{tag}> is not allowed to reference anything"
    for parent in list(root.iter()):
        for ch in list(parent):
            if local(ch.tag) in ("title", "desc"):
                parent.remove(ch)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode"), None


def _misuse_items(k):
    """logo.misuse as a list of dicts; the six generic items when the field is absent (backward compatible)."""
    items = (k.ident.get("logo") or {}).get("misuse")
    if not items:
        return [{"do": d} for d in identitylib.MISUSE_DEFAULT]
    return [dict(x) for x in items if isinstance(x, dict)][:6]


def misuse_tiles(k, asp):
    tr = k.tr
    off = colorlib.hex_to_oklch(k.t["primary"])
    wrong = colorlib.oklch_to_hex(0.62, max(0.13, off[1]), (off[2] + 150) % 360)
    low_bg = pl.tint(k.t["primary"], 45)
    mh = min(56.0, 150 / asp)
    tiles, specific = [], 0
    for item in _misuse_items(k):
        bg, cap = None, None
        if item.get("svg"):
            svg, err = misuse_svg(os.path.join(k.info["dir"], item["svg"]), k.info["dir"])
            if err:
                k.findings.append(identitylib.finding(
                    "kit.misuse.svg", "kit", "gate", f"logo.misuse {item['svg']}: {err}", measured=item["svg"],
                    suggested_fix="redraw it as filled shapes on the logo's viewBox (copy logo/build/master-*.svg "
                                  "and change it); no text, images, scripts or references"))
                continue
            specific += 1
            vb = pl.viewbox(svg) or [0, 0, 100, 100]
            h = min(120.0, 210 / (vb[2] / vb[3]))  # sized by its own shape: a symbol-only drawing is not tiny
            inner = (f'<div style="display:flex"><div class="mk" style="height:{h:.1f}px">'
                     f'{identity_card_mark(svg, k)}</div></div>')
            cap = item.get("label") or ""
        else:
            do = item.get("do")
            if do not in identitylib.MISUSE_GENERIC:
                continue
            cap = tr(f"kit.misuse.{do}")
            mark = None
            if do == "stretch":
                style = "transform:scaleX(1.45)"
            elif do == "rotate":
                style = "transform:rotate(-14deg)"
            elif do == "effects":
                style = "filter:drop-shadow(5px 7px 5px rgba(0,0,0,.45))"
            else:
                style = ""
            if do == "recolour":
                mark = k.mark("primary", mono=wrong, height=mh, probe=False)
            elif do == "low-contrast":
                bg = low_bg
            elif do == "crowd":
                ch = min(mh * 0.8, 170 / asp)  # mark + text bars fit the tile
                bars = "".join(f'<i style="width:{ch * f:.0f}px;height:{max(5.0, ch * .14):.1f}px"></i>'
                               for f in (1.9, 2.4, 1.4))
                mark = (f'<div style="display:flex;align-items:center;gap:3px">'
                        f'{k.mark("primary", height=ch, probe=False)}<span class="crowd">{bars}</span></div>')
            elif do == "rearrange":
                sym, wm = k.logos.get("symbol"), k.logos.get("wordmark")
                if not (isinstance(sym, str) and isinstance(wm, str)):
                    k.findings.append(identitylib.finding(
                        "kit.misuse.rearrange", "kit", "warn", "logo.misuse rearrange needs a symbol and a wordmark "
                        "(symbol+wordmark logo); left out", suggested_fix="use another item for this logo"))
                    continue
                wh = mh * 0.62
                mark = (f'<div style="display:flex;align-items:flex-end;gap:{wh * .35:.0f}px">'
                        f'{k.mark("wordmark", height=wh, probe=False)}{k.mark("symbol", height=wh * 1.9, probe=False)}'
                        f'</div>')
            mark = mark or k.mark("primary", height=mh, probe=False)
            cls = "outl" if do == "outline" else ""
            inner = f'<div class="{cls}" style="display:flex;{style}">{mark}</div>'
        bgs = f' style="background:{bg}"' if bg else ""
        tiles.append(f'<div class="t"><div class="box"{bgs}>{inner}<span class="x">{pl.CROSS}</span></div>'
                     f'<div class="c"><b>{e(cap)}</b></div></div>')
    made = pl.mode_of(k.ident, "logo") in ("new", "refresh")
    if made and not specific:
        k.findings.append(identitylib.finding(
            "kit.misuse.generic", "kit", "warn", "the misuse page shows only generic items; a new or refreshed logo "
            "usually has 1-2 misuses of its own (filling a stencil gap, moving the symbol, the detailed version at "
            "small size)", measured=0, threshold=1,
            suggested_fix="add logo.misuse items {\"svg\": \"logo/misuse-1.svg\", \"label\": \"...\"} next to 3-4 "
                          "{\"do\": \"stretch\"} items in sets.json (misuse_svgs), `build WORK --sets X`, then kit"))
    return tiles


def identity_card_mark(svg, k):
    import identity_card
    return identity_card._mark(svg, k.pal)


def _vals(hx, pal):
    r, g, b = (round(c * 255) for c in colorlib.parse_color(hx))
    L, C, hh = colorlib.hex_to_oklch(hx)
    lab = None
    for br in (pal or {}).get("brand") or []:
        if br.get("hex", "").lower() == hx.lower():
            lab = ((br.get("print") or {}).get("lab_d50"))
    lab = lab or [round(v, 1) for v in colorlib.hex_to_lab(hx, "D50")]
    return [("HEX", hx.upper()), ("RGB", f"{r} {g} {b}"), ("OKLCH", f"{L:.2f} {C:.3f} {hh:.0f}"),
            ("Lab D50", f"{lab[0]:.1f} {lab[1]:.1f} {lab[2]:.1f}")]


COLOUR_ROWS_FIT = 6   # full colour rows (112 px) that fill the 672 px band area; more turn extended rows thin


def page_colours(k):
    lt = k.pal["modes"]["light"]
    tr = k.tr
    rows, seen = [], []
    spec = [("primary", 32), ("accent", 13), ("text", 18), ("background", 20), ("surface", 9)]
    for role, w in spec:
        word = tr(f"kit.col.use.{role}")
        hx = lt.get(role)
        if not hx or any(colorlib.delta_e_ok(hx, s) < 0.02 for s in seen):
            continue
        seen.append(hx)
        bc = pl.brand_colour(k.pal, role, hx)
        if bc:  # a locked brand colour shows its own value; the UI step is noted when it differs
            if bc["role_hex"] != bc["hex"]:
                word += f" · UI {bc['role_hex'].upper()}"
            hx = bc["hex"]
        rows.append(((bc or {}).get("name") or pl.role_word(role, tr), word, hx, w))
    core = len(rows)
    for x in (k.pal.get("extended") or [])[:3]:
        rows.append((x.get("name") or x["id"], tr("kit.col.supporting"), x["hex"], 8))
    compact = len(rows) > COLOUR_ROWS_FIT
    cells = []
    for i, (name, word, hx, w) in enumerate(rows):
        fg = pl.ink_on(hx)
        thin = compact and i >= core
        vals = "".join(f"<span>{e(a)}</span><span>{e(b)}</span>" for a, b in _vals(hx, k.pal)[:1 if thin else None])
        if colorlib.hex_to_oklch(hx)[0] > 0.93:  # tints of a near-white ground are not distinguishable
            tints = '<div class="tn" style="grid-column:span 3;background:var(--paper)"></div>'
        else:
            tints = "".join(f'<div class="tn" style="background:{pl.tint(hx, p)};color:{pl.ink_on(pl.tint(hx, p))}">'
                            f'{p}%</div>' for p in (80, 50, 20))
        line = "" if colorlib.contrast_ratio(hx, k.t["paper"]) > 1.15 else ";box-shadow:inset 0 0 0 1px var(--line)"
        size = "flex:1 1 0;min-height:0" if thin else "flex:0 0 112px" if compact else f"flex:{w} 1 0;min-height:112px"
        cells.append(f'<div class="row{" thin" if thin else ""}" style="{size}"><div class="main" '
                     f'style="background:{hx};color:{fg}{line}"><div><div class="nm">{e(name)}</div>'
                     f'<div class="rl">{e(word)}</div></div><div class="vals">{vals}</div></div>{tints}</div>')
    side = (f'<div class="side"><p class="p">{e(tr("kit.col.side"))}</p>'
            f'<div><span class="lab">{e(tr("kit.col.print"))}</span><p class="small" style="margin-top:8px">'
            f'{e(tr("kit.col.print_p"))}</p></div>'
            f'<div><span class="lab">{e(tr("kit.sec.tints"))}</span><p class="small" style="margin-top:8px">'
            f'{e(tr("kit.col.tints_p"))}</p></div></div>')
    body = f'<div class="body g12 colours">{side}<div class="bands">{"".join(cells)}</div></div>'
    return {"name": "colours", "section": tr("kit.sec.colours"),
            "subs": [tr("kit.sec.colours"), tr("kit.sec.proportions"), tr("kit.sec.tints")],
            "content": head(tr("kit.head.colours"), e(k.pal.get("direction") or "")) + body}


def _gf_link(fams):
    q = "&".join(f"family={f.replace(' ', '+')}:wght@{';'.join(str(w) for w in sorted(ws))}" for f, ws in fams.items())
    return f'<link href="https://fonts.googleapis.com/css2?{q}&display=swap" rel="stylesheet">'


def page_type(k):
    ty = k.ty
    tr = k.tr
    disp, text = ty.get("display") or ty.get("text"), ty.get("text") or ty.get("display")
    cols, seen = [], set()
    for role, face in (("display", disp), ("text", text), ("mono", ty.get("mono"))):
        if not face:
            continue
        for w in face.get("weights") or [400]:
            if (face["family"], w) in seen:
                continue
            seen.add((face["family"], w))
            cols.append((role, face, w))
    first = {}
    for c in cols:
        first.setdefault(c[0], c)
    more = [c for c in cols if c is not first[c[0]]][:max(0, 4 - len(first))]
    cols = [c for c in cols if c is first[c[0]] or c in more]  # every role keeps a column: each face gets embedded
    lang, sample = next(((c, x) for c, x in pl.sample_sentences(k.brand.get("languages"),
                                                             pl.brand_copy(k.brand, tr)["sentences"]) if x),
                        ("en", ""))
    wc = []
    for role, face, w in cols:
        fam = f"font-family:'{face['family']}',{face.get('fallback') or 'sans-serif'};font-weight:{w}"
        alpha = "\u200b".join(ALPHA[i:i + 2] for i in range(0, len(ALPHA), 2))
        wc.append(f'<div class="wcol" data-role="{role}" style="{fam}"><div class="wn">{e(face["family"])}<br>'
                  f'{e(WEIGHT_NAMES.get(w, str(w)))} {w}</div><div class="alpha">{alpha}<br>{DIGITS}</div>'
                  f'<p class="smp" lang="{e(lang)}">{e(sample)}</p>'
                  f'<div class="aa" data-fit>Aa</div></div>')
    trk = (disp.get("tracking") or 0) / 1000
    scale = [("H1", 64, 1.05, trk, "display"), ("H2", 40, 1.1, trk, "display"), ("H3", 28, 1.2, 0, "text"),
             (tr("kit.type.body"), 18, 1.5, 0, "text"), (tr("kit.type.small"), 14, 1.45, 0.01, "text")]
    srows = "".join(f'<tr><td>{e(n)}</td><td>{s}px</td><td>{lh:g}</td><td>{t:+.3f}em</td>'
                    f'<td>{e(tr(f"type_role.{r}"))}</td></tr>' for n, s, lh, t, r in scale)
    feats = sorted({f for face in (disp, text) for f in (face.get("features") or [])})
    lic = "; ".join(sorted({f"{f['family']} · {f.get('license', '?')} · {f.get('source', '?')}"
                            for f in (disp, text, ty.get("mono")) if f}))
    mono = ty.get("mono")
    gfam = {}
    for face in (disp, text, mono):
        if face and face.get("source") == "google":
            gfam.setdefault(face["family"], set()).update(face.get("weights") or [400])
    code = _gf_link(gfam) if gfam else f"/* {tr('kit.type.self_host')} */"
    code += (f"\n:root {{ --font-display: \"{disp['family']}\", {disp.get('fallback') or 'sans-serif'};"
             f" --font-text: \"{text['family']}\", {text.get('fallback') or 'sans-serif'};"
             + (f" --font-mono: \"{mono['family']}\", {mono.get('fallback') or 'monospace'};" if mono else "")
             + " }")
    def case(face):
        return e(tr.word("case", face.get("case", "as-is")))
    roles = (f'<table><tr><th>{e(tr("kit.type.role"))}</th><th>{e(tr("kit.type.family"))}</th>'
             f'<th>{e(tr("kit.type.weights"))}</th><th>{e(tr("kit.type.case"))}</th></tr>'
             f'<tr><td>{e(tr("type_role.display"))}</td><td>{e(disp["family"])}</td>'
             f'<td>{" ".join(map(str, disp.get("weights") or []))}</td><td>{case(disp)}</td></tr>'
             f'<tr><td>{e(tr("type_role.text"))}</td><td>{e(text["family"])}</td>'
             f'<td>{" ".join(map(str, text.get("weights") or []))}</td><td>{case(text)}</td></tr>'
             + (f'<tr><td>{e(tr("type_role.mono"))}</td><td>{e(ty["mono"]["family"])}</td>'
                f'<td>{" ".join(map(str, ty["mono"].get("weights") or []))}</td><td>{case(ty["mono"])}</td></tr>'
                if ty.get("mono") else "") + '</table>')
    side = (f'<div class="side">{roles}<table><tr><th>{e(tr("kit.type.level"))}</th><th>{e(tr("kit.type.size"))}</th>'
            f'<th>{e(tr("kit.type.line"))}</th><th>{e(tr("kit.type.tracking"))}</th>'
            f'<th>{e(tr("kit.type.role"))}</th></tr>{srows}</table>'
            f'<p class="small"><b>{e(tr("kit.type.features"))}</b> {e(", ".join(feats) or tr("kit.type.defaults"))}. '
            f'<b>{e(tr("kit.type.licence"))}</b> {e(lic)}.</p>'
            f'<pre data-probe-skip>{e(code)}</pre></div>')
    body = (f'<div class="body g12 typo">{side}<div class="wcols" style="--wn:{len(wc)};grid-template-rows:1fr">'
            f'{"".join(wc)}</div></div>')
    pm = ty.get("pairing_mode") or ""
    pairing = tr.word("pairing", pm, pm.replace("-", " "))
    return {"name": "typography", "section": tr("kit.sec.fonts"),
            "subs": [tr("kit.sec.fonts"), tr("kit.sec.hierarchy"), tr("kit.sec.usage")],
            "content": head(tr("kit.head.type"), e(tr("kit.type.lede", display=disp["family"], text=text["family"],
                                                      pairing=pairing))) + body}


def page_applications(k):
    import identity_card
    brand = k.brand
    tr = k.tr
    copy = pl.brand_copy(brand, tr)
    mark, hpx = identity_card.header_logo(k.ident, k.logos)
    hpx = max(34, hpx)
    if k.has("logo") and isinstance(mark, str) and mark.lstrip().startswith("<"):
        lg = (f'<div class="mk" style="height:{hpx}px" data-logo=site data-min={k.min_px}>'
              f'{pl.colorize(mark, k.pal)}</div>')
    elif k.has("logo"):
        lg = k.mark("primary", height=hpx, attrs=f" data-logo=site data-min={k.min_px}")
    else:
        lg = f'<span class="disp" data-role="display" style="font-size:28px">{e(brand["name"])}</span>'
    on = k.t["onPrimary"]
    sym = icon_key(k.logos, 72)
    blk_mark = k.mark(sym, mono=on, height=min(72.0, 260 / k.aspect(sym))) if k.has("logo") else ""
    nav = "".join(f"<a>{e(x)}</a>" for x in copy["nav"][:3])
    sub = f'<p class="sub" data-role="text">{e(copy["sentence"])}</p>' if copy["sentence"] else ""
    site = (f'<div class="site" lang="{e(copy["lang"])}" style="--nav-h:{max(76, hpx + 36)}px"><nav data-nav>'
            f'<span class="lg">{lg}</span>{nav}<a class="btn" data-probe-box>{e(copy["cta"])}</a></nav><div class="hs"><div>'
            f'<div class="hl disp" data-role="display" data-fit>{e(brand.get("tagline") or brand["name"])}</div>{sub}'
            f'<div class="cta"><span class="a">{e(copy["cta"])}</span></div></div>'
            f'<div class="blk">{blk_mark}<div class="acc"></div></div>'
            f'</div></div>')
    asp = k.aspect("primary")
    # business card ~274 x 177 px on the page: the logo fills <= 75% of the width, <= 30% of the height
    front = k.mark("primary", mono=on, height=min(0.30 * 177, 0.75 * 274 / asp)) if k.has("logo") else ""
    back_mark = k.mark("primary", height=min(16.0, 0.6 * 240 / asp)) if k.has("logo") else ""
    # the back carries no invented contact data: name and title are labelled samples, the address line is the
    # brand's own site when the brief has one, else a reserved example.com address
    contact = site_domain(k.info.get("work")) or tr("kit.app.email")
    bc = (f'<div class="bc"><div><div class="card front">{front}</div><div class="cap">{e(tr("kit.app.bc_front"))}'
          f'</div></div><div><div class="card back" data-sample><b>{e(tr("kit.app.name"))}</b>'
          f'<span>{e(tr("kit.app.job"))}</span><span style="margin-top:6px">{e(contact)}</span>{back_mark}</div>'
          f'<div class="cap">{e(tr("kit.app.bc_back"))} · {e(tr("kit.app.sample"))}</div></div></div>')
    # avatar and app icon are 150 px: the full symbol (the small cut is drawn for 16-48 px); the mark fills
    # <= 56% of the height and <= 66% of the width (a wordmark gets smaller)
    av = icon_key(k.logos, 150)
    av_h = min(0.56 * 150, 0.66 * 150 / k.aspect(av))
    av_mark = k.mark(av, mono=on, height=av_h) if k.has("logo") else ""
    app = app_icon_svg(k.info["dir"])
    if app:  # the set's own app icon (app_icon_svg: its tile is part of the drawing) shown as built
        av_dark = f'<div class="mk" style="height:150px">{pl.colorize(app, None)}</div>'
        icon_cap = tr("kit.app.icon_own")
    else:
        av_dark = k.mark(av, mono=k.t["onDark"], height=av_h) if k.has("logo") else ""
        icon_cap = tr("kit.app.icon")
    soc = (f'<div class="soc"><div><div class="av">{av_mark}</div><div class="cap">{e(tr("kit.app.avatar"))}</div>'
           f'</div><div><div class="av sq{" app" if app else ""}">{av_dark}</div><div class="cap">{e(icon_cap)}</div>'
           f'</div></div>')
    body = f'<div class="body g12"><div class="apps">{site}{bc}{soc}</div></div>'
    return {"name": "applications", "section": tr("kit.sec.apps"),
            "subs": [tr("kit.sec.apps"), tr("kit.sec.site"), tr("kit.sec.print"), tr("kit.sec.social")],
            "content": head(tr("kit.head.apps"), "") + body}


# --- full kit extras

def page_grid(k):
    tr = k.tr
    cols = "".join("<i></i>" for _ in range(12))
    body = (f'<div class="body g12 gridp"><div class="demo"><div class="cols">{cols}</div><div class="txt">'
            f'<div style="grid-column:1/7"><span class="lab">{e(tr("kit.grid.lab"))}</span>'
            f'<div class="disp" data-role="display" lang="{e(pl.lang_of(k.brand))}" style="font-size:64px;line-height:1.02;margin-top:24px">'
            f'{e(k.brand.get("tagline") or k.brand["name"])}</div></div><div style="grid-column:8/12">'
            f'<p class="p" data-role="text" style="margin-top:44px">{e(tr("kit.grid.p"))}</p></div>'
            f'</div></div></div>')
    return {"name": "grid", "section": tr("kit.sec.grid"), "subs": [tr("kit.sec.grid"), tr("kit.sec.layout")],
            "content": head(tr("kit.head.grid"), "") + body}


ICONS = ["M4 11l8-7 8 7v9h-5v-6h-6v6H4z", "M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.5 15.5L20 20",
         "M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 20c1.5-4 4.5-6 8-6s6.5 2 8 6", "M4 6h16v14H4zM4 10h16M8 3v5M16 3v5",
         "M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21zM12 7.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4z",
         "M4 12h15M14 6l6 6-6 6"]


def page_icons(k):
    icons = "".join(f'<div style="height:150px;background:var(--panel);box-shadow:inset 0 0 0 1px var(--line);'
                    f'display:flex;align-items:center;justify-content:center"><svg viewBox="0 0 24 24" '
                    f'style="width:64px;height:64px" fill="none" stroke="{k.t["ink"]}" stroke-width="1.75" '
                    f'stroke-linecap="round" stroke-linejoin="round"><path d="{d}"/></svg></div>' for d in ICONS)
    side = ('<div class="side"><ul class="rule-list">'
            + "".join(f"<li>{e(k.tr(f'kit.icons.r{i}'))}</li>" for i in range(1, 5)) + '</ul></div>')
    body = (f'<div class="body g12">{side}<div style="grid-column:5/13;display:grid;grid-template-columns:'
            f'repeat(6,1fr);gap:16px;align-content:start">{icons}</div></div>')
    return {"name": "iconography", "section": k.tr("kit.sec.icons"), "subs": [k.tr("kit.sec.icons")],
            "content": head(k.tr("kit.head.icons"), "") + body}


def page_imagery(k):
    p, a, d = k.t["primary"], k.t["accent"], k.t["dark"]
    blocks = "".join(f'<div style="background:linear-gradient(160deg,{c1},{c2});height:100%" data-bg-ok></div>'
                     for c1, c2 in ((p, d), (pl.tint(p, 40), p), (d, a)))
    side = ('<div class="side"><ul class="rule-list">'
            + "".join(f"<li>{e(k.tr(f'kit.imagery.r{i}'))}</li>" for i in range(1, 5)) + '</ul></div>')
    body = (f'<div class="body g12">{side}<div style="grid-column:5/13;display:grid;grid-template-columns:repeat(3,1fr);'
            f'gap:24px;height:100%">{blocks}</div></div>')
    return {"name": "imagery", "section": k.tr("kit.sec.imagery"),
            "subs": [k.tr("kit.sec.imagery"), k.tr("kit.sec.colour_treatment")],
            "content": head(k.tr("kit.head.imagery"), "") + body}


def page_dark(k):
    dk = k.pal["modes"].get("dark") or {}
    bg = dk.get("background", k.t["dark"])
    mono, _cr = _best_version(k, bg)
    asp = k.aspect("primary")
    tr = k.tr
    copy = pl.brand_copy(k.brand, tr)
    ver = tr("kit.dark.full") if mono is None else tr("kit.dark.one")
    body = (f'<div class="body g12"><div class="side"><p class="p">{e(tr("kit.dark.p", bg=bg.upper(), version=ver))}'
            f'</p></div>'
            f'<div style="grid-column:5/13;background:{bg};box-shadow:inset 0 0 0 1px {dk.get("border", "#333")};'
            f'display:flex;flex-direction:column;justify-content:center;padding:56px;gap:28px;height:100%">'
            f'{k.mark("primary", mono=mono, mode="dark", height=min(90.0, 600 / asp))}'
            f'<div lang="{e(copy["lang"])}" style="display:contents"><div class="disp" data-role="display" '
            f'style="font-size:48px;color:{dk.get("text", "#fff")}">'
            f'{e(k.brand.get("tagline") or k.brand["name"])}</div><p class="p" style="color:{dk.get("textMuted", "#ccc")};'
            f'max-width:640px">{e(copy["sentence"] or "")}</p>'
            f'<span style="align-self:flex-start;background:{dk.get("primary")};color:{dk.get("onPrimary")};'
            f'padding:12px 20px;border-radius:4px;font-weight:600">{e(copy["cta"])}</span></div></div></div>')
    return {"name": "dark-mode", "section": tr("kit.sec.dark"), "subs": [tr("kit.sec.dark")],
            "content": head(tr("kit.head.dark"), "") + body}


def page_access(k):
    tiles = []
    for mode in ("light", "dark"):
        m = k.pal["modes"].get(mode) or {}
        for fg_r, bg_r in (("text", "background"), ("textMuted", "background"), ("onPrimary", "primary"),
                           ("onAccent", "accent")):
            fg, bg = m.get(fg_r), m.get(bg_r)
            if not fg or not bg:
                continue
            cr = colorlib.contrast_ratio(fg, bg)
            tiles.append(f'<div><div class="pr" style="background:{bg};color:{fg}"><span class="big disp" '
                         f'data-role="display">Aa</span></div><div class="small" style="margin-top:6px">'
                         f'{e(k.tr("kit.access.pair", mode=k.tr(f"kit.mode.{mode}"), fg=pl.role_word(fg_r, k.tr), bg=pl.role_word(bg_r, k.tr)))}'
                         f' · <b>{k.tr.ratio(cr)}</b></div></div>')
    side = f'<div class="side"><p class="p">{e(k.tr("kit.access.p"))}</p></div>'
    body = f'<div class="body g12">{side}<div class="pairs">{"".join(tiles)}</div></div>'
    return {"name": "accessibility", "section": k.tr("kit.sec.access"),
            "subs": [k.tr("kit.sec.access"), k.tr("kit.sec.pairs")], "content": head(k.tr("kit.head.access"), "") + body}


# ----------------------------------------------------------------------------- assembly

def plan(k, full=False):
    """[(group, builder)] in order; pages of `none` components are left out."""
    pages = [("cover", page_cover), ("idea", page_idea)]
    if k.has("logo"):
        pages += [("logo", page_logo), ("logo", page_variations), ("logo", page_on_colour)]
    if k.has("palette"):
        pages += [("colour", page_colours)]
    if k.has("type"):
        pages += [("type", page_type)]
    pages += [("apps", page_applications)]
    if full:
        pages += [("more", page_grid)]
        if k.has("palette"):
            pages += [("more", page_dark), ("more", page_access)]
        pages += [("more", page_icons), ("more", page_imagery)]
    return pages


def build_html(k, full=False, html_dir=None, date_str=None):
    shell = pl.read_template("kit", "kit.html")
    tpl = pl.read_template("kit", "page.html")
    group_no, sub_no, last = 0, 0, None
    out = []
    tr = k.tr
    d = date_str or pl.today_long(t=tr)
    for i, (group, fn) in enumerate(plan(k, full)):
        p = fn(k)
        if group == "cover":
            num = "0.0"
        else:
            if group != last:
                group_no, sub_no = group_no + 1, 0
            sub_no += 1
            num = f"{group_no}.{sub_no}"
        last = group
        subs = "".join(f'<li class="{"on" if j == 0 else ""}">{e(s)}</li>' for j, s in enumerate(p["subs"]))
        out.append(pl.fill(tpl, {
            "CLASS": p.get("cls", ""), "NAME": p["name"], "BRAND": e(k.brand["name"]), "ARROW": pl.ARROW,
            "NUM": num, "SUBS": subs, "VERSION": VERSION, "DATE": e(d), "PAGE_NO": f"{i + 1:02d}",
            "CONTENT": p["content"], "FOOT_LEFT": e(tr("kit.rights")), "T_GUIDE": e(tr("kit.guide")),
            "T_VERSION": e(tr("kit.version", v=VERSION)),
            "FOOT_MID": e(" · ".join(x for x in (k.brand["name"], k.brand.get("sector")) if x)),
            "YEAR": d[-4:], "BLANG": e(pl.lang_of(k.brand))}))
    css = pl.read_template("kit", "kit.css") + "\n.outl svg *{fill:none!important;stroke:var(--ink);stroke-width:1.5px;" \
                                                  "vector-effect:non-scaling-stroke}\n"
    langs = k.brand.get("languages") or ["en"]
    fit = pl.read_template("card", "card.html")
    script = fit[fit.index("<script>"):fit.index("</script>") + 9]
    return pl.fill(shell, {"LANG": e(langs[0]), "BRAND": e(k.brand["name"]), "DOC_LANG": e(tr.html_lang),
                           "T_GUIDE": e(tr("kit.guide")),
                           "FONT_FACES": pl.font_face_css(k.faces, html_dir), "THEME": pl.theme_css(k.t),
                           "ROLE_VARS": pl.role_vars(k.ident), "CSS": css,
                           "PAGES": "\n".join(out) + script}), len(out)


_EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//""", re.I)


def fonts_md(k):
    tr = k.tr
    lines = [f"# {k.brand['name']} — {tr('kit.fonts.title')}", "", tr("kit.fonts.intro"), ""]
    for role, face in pl.type_faces(k.ident):
        src = face.get("source")
        link = (f"https://fonts.google.com/specimen/{face['family'].replace(' ', '+')}" if src == "google" else
                f"https://www.fontshare.com/fonts/{pl.slug(face['family'])}" if src == "fontshare" else
                tr("kit.fonts.own_copy"))
        loc = ", ".join(f"{a} {v:g}" for a, v in (face.get("location") or {}).items()) or tr("kit.fonts.static")
        lines += [f"## {tr(f'type_role.{role}')}: {face['family']}", "",
                  f"- {tr('kit.fonts.source')}: {src} — {link}", f"- {tr('kit.type.licence')}: {face.get('license')}",
                  f"- {tr('kit.type.weights')}: {', '.join(map(str, face.get('weights') or []))}; "
                  f"{tr('kit.fonts.axes')}: {loc}",
                  f"- {tr('kit.type.tracking')}: {face.get('tracking', 0)}/1000 em; {tr('kit.type.case')}: "
                  f"{tr.word('case', face.get('case', 'as-is'))}",
                  f"- {tr('kit.type.features')}: {', '.join(face.get('features') or []) or tr('kit.type.defaults')}",
                  f"- {tr('kit.fonts.fallback')}: {face.get('fallback')}", ""]
    if any(f.get("source") == "commercial" for _r, f in pl.type_faces(k.ident)):
        lines += [tr("kit.fonts.commercial"), ""]
    return "\n".join(lines)


def fonts_css(k):
    out = [":root {"]
    for role, face in pl.type_faces(k.ident):
        out.append(f'  --font-{role}: "{face["family"]}", {face.get("fallback") or "sans-serif"};')
        out.append(f"  --font-{role}-weight: {(face.get('weights') or [400])[0]};")
        out.append(f"  --font-{role}-tracking: {(face.get('tracking') or 0) / 1000:.3f}em;")
        loc = ", ".join(f'"{a}" {v:g}' for a, v in (face.get("location") or {}).items() if a != "wght")
        if loc:
            out.append(f"  --font-{role}-variation: {loc};")
    out.append("}")
    return "\n".join(out) + "\n"


def _logo_files(k, kit):
    if importlib.util.find_spec("logolib") is None:
        return "skipped: logolib not installed"
    try:
        lib = importlib.import_module("logolib")
        res = lib.variants(k.ident, k.pal, os.path.join(kit, "logo"), set_dir=k.info["dir"])
        return f"{len(res) if hasattr(res, '__len__') else 'ok'} variant(s)"
    except Exception as exc:  # noqa: BLE001 - logo files are optional for the kit pages
        return f"skipped: logolib.variants failed: {exc}"


def _tokens(k, kit):
    tdir = os.path.join(kit, "tokens")
    os.makedirs(tdir, exist_ok=True)
    written = []
    if k.pal:
        pfile = os.path.join(k.info["dir"], k.ident.get("palette") or "palette.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "export_tokens.py"), pfile, "--out", tdir, "--json"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode == 0:
            try:
                written += json.loads(r.stdout or "[]")
            except ValueError:
                written += [x for x in r.stdout.split() if x]
        else:
            written.append(f"export_tokens failed: {r.stderr.strip()[:200]}")
        sw = os.path.join(tdir, "swatches.html")
        if os.path.isfile(sw):  # the swatch sheet is for people: rewrite it in the document language
            import export_tokens
            with open(sw, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(export_tokens.to_swatches(colorlib.load_palette(pfile), k.tr))
    if pl.type_faces(k.ident):
        p = os.path.join(tdir, "fonts.css")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(fonts_css(k))
        written.append(p)
    return written


def build(set_dir, full=False, scale=1.0, date_str=None):
    """Build the kit for one set. Returns {pages, pdf, fonts_md, tokens, logo, findings, passed}."""
    k = Kit(set_dir)
    work = k.info["work"]
    kit = os.path.join(work, "kit", k.ident["set"]["id"])  # one folder per set: no files leak between kits
    if os.path.isdir(kit):
        shutil.rmtree(kit)  # a rebuild starts clean (logolib variants, tokens, pages are all regenerated)
    pages_dir = os.path.join(kit, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    html_path = os.path.join(work, ".cache", f"kit-{k.ident['set']['id']}.html")
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    html, n = build_html(k, full, os.path.dirname(html_path), date_str)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    pdf = os.path.join(kit, f"{pl.slug(k.brand['name'])}-guidelines.pdf")
    res = render_png.render(html_path, pages_dir, W, H, pdf=pdf, pages=True, probe=True, scale=scale)
    roles = {}
    for f in k.faces:
        roles.setdefault(f["role"], f["family"])
    findings = render_png.probe_findings(res, pl.expected_fonts(k.faces), roles, k.min_px, component="kit")
    findings += render_png.pdf_font_findings(res["pdf_fonts"], {f["family"] for f in k.faces if f["path"]})
    findings += k.findings
    if len(res.get("pages") or []) != n:
        findings.append(identitylib.finding("kit-page-count", "kit", "gate",
                                            f"{len(res.get('pages') or [])} page PNGs for {n} pages",
                                            measured=len(res.get("pages") or []), threshold=n))
    ext = _EXTERNAL.findall(html)
    if ext:
        findings.append(identitylib.finding("kit-external-request", "kit", "gate",
                                            f"kit HTML requests {len(ext)} external resource(s)", measured=ext[:3],
                                            threshold=0))
    for f in k.faces:
        if not f["path"]:
            findings.append(identitylib.finding("font-file-missing", "kit", "gate",
                                                f"no font file for {f['family']} {f['weight']}", measured=f["note"]))
    fmd = os.path.join(kit, "fonts.md")
    with open(fmd, "w", encoding="utf-8") as fh:
        fh.write(fonts_md(k))
    tokens = _tokens(k, kit)
    findings += k.tr.findings("kit")  # after every page, fonts.md and swatches.html asked for their text
    logo = _logo_files(k, kit)
    gates = [f for f in findings if f["severity"] == "gate"]
    return {"kit": kit, "html": html_path, "pages": res.get("pages") or [], "pdf": res.get("pdf"), "pdf_fonts": res.get("pdf_fonts"),
            "fonts_md": fmd, "tokens": tokens, "logo": logo, "findings": findings, "passed": not gates}


def cli_kit(args):
    """brand.py kit SET_DIR [--full] handler. Prints pages + PDF path + gates (<= 1.2 KB)."""
    identitylib.utf8_console()
    try:
        r = build(args.set_dir, full=getattr(args, "full", False))
    except (identitylib.IdentityError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    gates = [f for f in r["findings"] if f["severity"] == "gate"]
    print(f"kit: {r['kit']} ({len(r['pages'])} pages)")
    print(f"pdf: {r['pdf']}")
    print(f"fonts: {r['fonts_md']}  tokens: {len(r['tokens'])} file(s)  logo: {r['logo']}")
    if gates:
        for g in gates[:5]:
            print(f"✘ {g['id']}: {g['message']}"[:220])
    else:
        print("gates: ✔ fonts loaded + embedded, no overflow, text contrast, logo sizes")
    for w in [f for f in r["findings"] if f["severity"] == "warn" and str(f["id"]).startswith(("i18n.", "kit.misuse"))]:
        print(f"warn {w['id']}: {w['message']}"[:220] + (f" → {w['suggested_fix']}"[:200] if w.get("suggested_fix") else ""))
    if getattr(args, "json", False):
        print(json.dumps(r, default=str)[:4000])
    return 0 if not gates else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set_dir")
    ap.add_argument("--full", action="store_true", help="add grid, iconography, imagery, dark mode, accessibility")
    ap.add_argument("--json", action="store_true")
    return cli_kit(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
