#!/usr/bin/env python3
"""Render one identity set as a direction card (1600x1000, PNG @2x) and measure it. docs/architecture.md sections 4.7, 7.1.

Blocks, top to bottom: meta strip (brand · → [A] SET NAME · IDENTITY DIRECTIONS · v0.1 · date · A/3) → name and
mechanism → logo hero in colour + one-colour 64/32/16 rows on light and dark → axes → palette proportion band →
type specimen (brand name + a real sentence per brief language) → one mini application (site header) → checks
row → footer. A `none` component's block collapses (an existing asset is still shown), a `keep` block is tagged
"kept", a set whose audit failed shows FAILED and its first gate message.

The template (templates/card/) is fixed; the set paints it: paper/ink/accent from palette.json, display and
text faces from identity.json via local @font-face (static instances from the font cache).

Probe gates (render_png.render): every face loaded, every role resolves to its face, no text overflow, every
text node meets contrast (4.5:1, large 3:1, weight <= 300 -> 4.5:1, heuristic), logo hero >= logo.min_size.px.
Findings come back to the caller; identity.json is never written here (pipeline/brand.py own it).

Usage:
  python3 scripts/identity_card.py brand-identity/ferrow/sets/A
  python3 scripts/identity_card.py templates/demo/sets/A --no-probe --json
"""
import argparse
import json
import os
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
W, H = 1600, 1000
VERSION = "v0.1"
BANDS = (("primary", 30), ("accent", 12), ("text", 16), ("background", 30), ("surface", 12))  # heuristic shares
BRAND_BAND = 10  # share of a brand colour that no role band shows


# ----------------------------------------------------------------------------- blocks

def axes_html(identity, t=None):
    rows = []
    for key, val in (identity.get("axes") or {}).items():
        lo, hi = identitylib.AXES.get(key, (key, ""))
        if t:
            lo, hi = t.word("axis", lo), t.word("axis", hi)
        pos = (val + 2) / 4 * 100
        ticks = "".join(f'<i style="left:{p}%"></i>' for p in (0, 25, 50, 75, 100))
        rows.append(f'<div class="ax"><span class="l{" on" if val < 0 else ""}">{e(lo)}</span>'
                    f'<span class="track">{ticks}<b style="left:{pos:.0f}%"></b></span>'
                    f'<span class="{"on" if val > 0 else ""}">{e(hi)}</span></div>')
    return "".join(rows)


def _kept(identity, comp, t):
    return f' <span class="kept">{e(t("card.kept"))}</span>' if pl.mode_of(identity, comp) == "keep" else ""


def _mark(svg_or_img, pal, mode="light", mono=None, cls="logo-svg"):
    """Inline a logo. `mono` paints it one colour: data-color groups through the palette roles, and a logo without
    roles (a kept original) through a forced fill so its own colours never leak into a one-colour test."""
    if isinstance(svg_or_img, str) and svg_or_img.lstrip().startswith("<"):
        out = pl.colorize(svg_or_img, pal, mode=mode, mono=mono, cls=cls)
        if mono and "data-color=" not in svg_or_img:
            out = f'<span class="force-mono" style="--mono:{mono}">{out}</span>'
        return out
    if svg_or_img:
        flt = ""
        if mono:  # raster logo: black, or white on dark
            flt = ' style="filter:brightness(0)' + (' invert(1)"' if colorlib.hex_to_oklch(mono)[0] > 0.6 else '"')
        return f'<img src="{e(pl._url(svg_or_img, None))}" alt=""{flt}>'
    return ""


def _device(ident):
    d = ((ident.get("logo") or {}).get("device") or "").strip().lower()
    return d if d in ("tile", "outline") else None


def _device_bg(pal, parts, bg):
    """The tile/outline colour: the candidate that gives the logo parts the most contrast."""
    modes = (pal or {}).get("modes") or {}
    cands = [x for x in ((modes.get("light") or {}).get("background"), (modes.get("dark") or {}).get("background"),
                         "#ffffff", "#111111") if x]
    return max(cands, key=lambda c: min([colorlib.contrast_ratio(p, c) for p in parts] or [21]))


def ground_mark(info, logos, gt, svg=None, height=None):
    """The logo painted for a card ground (identity.card.ground). Prefers the logolib version made for that
    ground (device tile/outline included). Otherwise: full colour in the ground's mode when every part keeps 3:1,
    full colour on a tile/outline when logo.device asks for one, else one colour in the ground's ink."""
    ident, pal = info["identity"], info["palette"]
    primary = logos.get("primary") or logos.get("image")
    svg = svg or primary
    st = f' style="height:{height:.0f}px"' if height else ""
    if svg == primary:
        ver = pl.logo_version(info["dir"], gt)
        if ver:
            return f'<span class="mk"{st}>{pl.colorize(ver, None, default=None)}</span>', "logolib"
    if not (isinstance(svg, str) and svg.lstrip().startswith("<")):  # raster (kept PNG)
        mono = None if gt["mode"] == "light" else gt["ink"]
        return f'<span class="mk"{st}>{_mark(svg, pal, mono=mono)}</span>', "raster"
    roles = pl.logo_colors(svg)
    parts = [h for h in (pl.role_hex(pal, r, gt["mode"]) for r in roles) if h]
    if not roles:  # a kept logo without palette roles: as supplied on light, one colour elsewhere
        if gt["name"] == "light":
            return f'<span class="mk"{st}>{_mark(svg, pal)}</span>', "as-is"
        return f'<span class="mk"{st}>{_mark(svg, pal, mono=gt["ink"])}</span>', "one colour"
    if gt.get("ref") and not _device(ident):
        # a role / brand-id ground without a logolib version: one colour in the ground's ink (role colours in
        # either mode were chosen for the page grounds, not for this one)
        return f'<span class="mk"{st}>{_mark(svg, pal, mono=gt["ink"])}</span>', "one colour"
    if all(colorlib.contrast_ratio(p, gt["bg"]) >= 3 for p in parts):
        return f'<span class="mk"{st}>{_mark(svg, pal, mode=gt["mode"])}</span>', "full colour"
    dev = _device(ident)
    if dev:
        dbg = _device_bg(pal, parts, gt["bg"])
        inner = f'<span class="mk" style="height:{(height or 60) * 0.72:.0f}px">{_mark(svg, pal, mode=gt["mode"])}</span>'
        return (f'<span class="dev {dev}" style="--dev-bg:{dbg};height:{height or 60:.0f}px;padding:0 '
                f'{(height or 60) * 0.16:.0f}px">{inner}</span>', f"full colour on {dev}")
    return f'<span class="mk"{st}>{_mark(svg, pal, mono=gt["ink"])}</span>', "one colour"


def logo_block(info, logos, t, html_dir, gt=None, tr=None):
    ident, pal = info["identity"], info["palette"]
    tr = tr or pl.strings(info)
    gt = gt or pl.ground_theme(pal, t, "light")
    mode = pl.mode_of(ident, "logo")
    primary = logos.get("primary") or logos.get("image")
    if not primary:  # none + no asset: the name set in the display face takes the hero
        cap = tr("card.logo_none") if mode == "none" else tr("card.logo_not_built")
        return (f'<div class="typehero" data-block="logo"><div class="big" data-role="display" data-fit>'
                f'{e(ident["brand"]["name"])}</div><span class="cap">{e(cap)}</span></div>')
    asp = pl.aspect(primary) if isinstance(primary, str) and primary.lstrip().startswith("<") else 3.0
    # hero box ~ 848 x 296: the logo fills at most 62% of the width and 44% of the height
    h = min(130.0, 0.62 * 848 / asp, 0.44 * 296)
    min_px = ((ident.get("logo") or {}).get("min_size") or {}).get("px", 24)
    mark, how = ground_mark(info, logos, gt)
    ground_word = (tr("card.ground_light") if gt["name"] == "light" else tr("card.ground_dark") if gt["name"] == "dark"
                   else tr("card.ground_on", ground=_ground_name(pal, gt["name"], tr)))
    hero = (f'<div class="logo-hero" style="{pl.ground_css(gt)}"><div class="lh" data-logo="hero" '
            f'data-min="{min_px}" style="height:{h:.0f}px">{mark}</div>'
            f'<span class="cap">{e(tr("card.primary"))} · {e(ground_word)}{_kept(ident, "logo", tr)}</span>'
            f'<span class="cap r">{e(_logo_kind(ident, logos, tr))}</span></div>')
    # the one-colour row tests the mark people will see small: the symbol/monogram, or for a wordmark-only
    # logo the wordmark itself (a favicon added later must not hide how the name holds up)
    small = logos.get("small")
    if (ident.get("logo") or {}).get("type") == "wordmark":
        small = logos.get("wordmark") or primary
    test = small or primary
    test_asp = pl.aspect(test) if isinstance(test, str) and test.lstrip().startswith("<") else 1.0
    dev = _device(ident)
    sizes = test_sizes(test_asp * (1.0 if not dev else 1.0 / 0.72 + 0.32 / test_asp))
    ink, ondark = t["ink"], t["onDark"]

    def one(sz, colour, row_bg):
        if dev == "tile":  # a tile in the one colour, the mark knocked out of it
            return (f'<div class="m dev tile" style="--dev-bg:{colour};height:{sz}px;padding:0 {sz * 0.16:.0f}px">'
                    f'<span class="mk" style="height:{sz * 0.72:.1f}px;display:flex">{_mark(test, pal, mono=row_bg)}'
                    f'</span></div>')
        if dev == "outline":
            return (f'<div class="m dev outline" style="--dev-bg:{colour};height:{sz}px;padding:0 {sz * 0.16:.0f}px">'
                    f'<span class="mk" style="height:{sz * 0.72:.1f}px;display:flex">{_mark(test, pal, mono=colour)}'
                    f'</span></div>')
        return f'<div class="m" style="height:{sz}px">{_mark(test, pal, mono=colour)}</div>'

    def row(kind, colour, row_bg):
        figs = "".join(f'<figure>{one(s, colour, row_bg)}<figcaption>{s}</figcaption></figure>' for s in sizes)
        word = tr("card.on_light") if kind == "light" else tr("card.on_dark")
        return f'<div class="t {kind}"><span class="lab">{e(tr("card.one_colour"))}<br>{e(word)}</span>{figs}</div>'
    return (f'<div class="logo-zone" data-block="logo">{hero}'
            f'<div class="tests">{row("light", ink, t["panel"])}{row("dark", ondark, t["dark"])}</div></div>')


# one-colour strip: 848 px wide minus padding (38), label (92), gaps (4 x 22) and captions (3 x 26) -> marks
TEST_ROW_PX = 0.92 * (848 - 38 - 92 - 88 - 78)
TEST_SIZES = ((64, 32, 16), (48, 32, 16), (48, 24, 16), (40, 24, 16), (32, 24, 16), (32, 16), (24, 16), (16,))


def test_sizes(aspect, row_px=TEST_ROW_PX):
    """Heights (px) for the one-colour test row: 64/32/16 when it fits, else the largest smaller ladder whose
    marks fit the row at their real aspect ratio. Labels print these real sizes."""
    for sizes in TEST_SIZES:
        if aspect * sum(sizes) <= row_px:
            return sizes
    return (max(8, int(row_px / aspect)),)


def _ground_name(pal, ref, t):
    """A card ground (identity.card.ground) as a label: a brand id shows its colour name, a role its role word."""
    for b in (pal or {}).get("brand") or []:
        if b.get("id") == ref and b.get("name"):
            return b["name"]
    return pl.role_word(ref, t)


def _logo_kind(ident, logos, t):
    lg = ident.get("logo") or {}
    if lg.get("type"):
        return t.word("logo_type", lg["type"], lg["type"].replace("+", " + "))
    return t("card.existing_logo") if logos else ""


def palette_block(info, t, tr=None, gt=None):
    """The palette strip in the card ground's mode (a dark card shows the dark roles), plus every core brand colour
    that no role band already shows (a brand's night ground or a third colour is part of the identity)."""
    ident, pal = info["identity"], info["palette"]
    tr = tr or pl.strings(info)
    if not pal:
        return ""
    mode = "dark" if (gt or {}).get("mode") == "dark" else "light"
    lt = pal["modes"][mode]
    segs = []
    for role, w in BANDS:
        hx = lt.get(role)
        if not hx or any(colorlib.delta_e_ok(hx, s[1]) < 0.03 for s in segs):
            continue
        segs.append((role, hx, w))
    core = [b for b in pal.get("brand") or [] if b.get("role") != "extended" and b.get("hex")]
    shown = lambda hx: any(colorlib.delta_e_ok(hx, s[1]) < 0.03 or  # noqa: E731
                           ((pl.brand_colour(pal, s[0], s[1]) or {}).get("hex") or "") == hx.lower() for s in segs)
    # brand[0] / brand[1] are the primary / accent: their role band already stands for them, even where the
    # band shows this mode's UI step (a derived accent's dark step) rather than the brand hex
    by_role = {"primary": core[0]["hex"].lower() if core else None,
               "accent": core[1]["hex"].lower() if len(core) > 1 else None}
    banded = {by_role[s[0]] for s in segs if s[0] in by_role and by_role[s[0]]}
    for b in core:
        if b["hex"].lower() not in banded and not shown(b["hex"]):
            segs.append((None, b["hex"].lower(), BRAND_BAND))
    total = sum(w for *_x, w in segs) or 1
    cells = []
    for role, hx, w in segs:
        bc = pl.brand_colour(pal, role, hx) if role else None
        same = next((b for b in core if b["hex"].lower() == hx.lower()), None)
        name = ((bc or {}).get("name") or (same or {}).get("name") or
                (pl.role_word(role, tr) if role else (same or {}).get("id", "")))
        show = bc["hex"] if bc else hx  # a locked brand colour shows its own value, not the UI step
        note = ""
        if bc and bc["role_hex"] != bc["hex"]:
            note = f'<span class="bh">UI {e(bc["role_hex"])}</span>'
        fg = pl.ink_on(show)
        cells.append(f'<div style="flex:{w / total:.3f};background:{show};color:{fg}">'
                     f'<span class="bn">{e(name)}</span><span class="bh">{e(show)}</span>{note}</div>')
    feels = " · ".join(pal.get("feels") or [])
    return (f'<section class="band-wrap" data-block="palette"><div class="band">{"".join(cells)}</div>'
            f'<div class="band-cap"><span>{e(tr("card.palette_cap", name=pal.get("name") or ""))}'
            f'{_kept(ident, "palette", tr)}</span><span>{e(feels)}</span></div></section>')


def type_block(info, faces, only=False, tr=None):
    ident = info["identity"]
    tr = tr or pl.strings(info)
    ty = ident.get("type") or {}
    if pl.mode_of(ident, "type") == "none" and not ty:
        return ""
    disp, text = ty.get("display") or ty.get("text"), ty.get("text") or ty.get("display")
    if not disp:
        return ""
    brand = ident["brand"]

    def desc(face):
        ws = " · ".join(str(w) for w in face.get("weights") or [])
        trk = face.get("tracking") or 0
        extra = f" · {tr('card.tracking')} {trk:+d}" if trk else ""
        return f'{e(face["family"])} · {ws}{extra}'
    copy = pl.brand_copy(brand, tr)
    sents = "".join(f'<div class="sent"><span class="lg">{e(code)}</span><span lang="{e(code)}">{e(s)}</span></div>'
                    for code, s in pl.sample_sentences(brand.get("languages"), copy["sentences"])[:2] if s)
    mono = ty.get("mono")
    nums = ""
    if brand.get("numbers"):
        lg0 = pl.lang_of(brand)
        nums = (f'<div class="nums">{e(tr("card.figures"))}&ensp;<b lang="{e(lg0)}">0123456789&ensp;{pl.format_number(12.4, lg0)}'
                f'&ensp;{pl.format_number(3815.07, lg0)}</b></div>')
    line = brand.get("tagline") or brand["name"]
    pairing = tr.word("pairing", ty.get("pairing_mode") or "", (ty.get("pairing_mode") or "").replace("-", " "))
    return (f'<div class="spec{" only" if only else ""}" data-block="type">'
            f'<div style="min-width:0"><div class="aa" data-role="display" data-fit>Aa</div>'
            f'<span class="lab aa-cap">{e(tr("card.type"))} · {e(pairing)}{_kept(ident, "type", tr)}</span></div>'
            f'<div class="rows"><div class="roles{" r3" if ty.get("mono") else ""}">'
            f'<div><span class="lab">{e(tr("type_role.display"))}</span><div class="v">{desc(disp)}</div></div>'
            f'<div><span class="lab">{e(tr("type_role.text"))}</span><div class="v">{desc(text)}</div></div>'
            + (f'<div><span class="lab">{e(tr("type_role.mono"))}</span><div class="v">{desc(mono)}</div></div>'
               if mono else "")
            + f'</div><div class="disp" data-role="display" data-fit lang="{e(copy["lang"])}">{e(line)}</div>'
            f'<div data-role="text">{sents}</div>{nums}</div></div>')


HEADER_PX = 28


def header_logo(ident, logos):
    """The site-header logo: a wordmark-only identity never shows a symbol next to its name; otherwise the
    horizontal lockup. Returns (svg, height_px) with height >= logo.min_size.px."""
    lg = ident.get("logo") or {}
    if lg.get("type") == "wordmark":
        mark = logos.get("wordmark") or logos.get("master") or logos.get("primary")
    elif lg.get("type") == "monogram":
        mark = logos.get("horizontal") or logos.get("primary")
    else:
        mark = logos.get("horizontal") or logos.get("primary")
    mark = mark or logos.get("image")
    min_px = (lg.get("min_size") or {}).get("px", 0) or 0
    return mark, max(HEADER_PX, int(min_px))


def app_block(info, logos, only=False, gt=None, tr=None):
    """Mock site header on the card ground. A mock is an illustration: what still overflows after fitting (links
    dropped, logo down to its minimum, call to action moved below the headline) is a warn (data-mock)."""
    ident, pal = info["identity"], info["palette"]
    gt = gt or pl.ground_theme(pal, pl.theme(pal), "light")
    tr = tr or pl.strings(info)
    brand = ident["brand"]
    copy = pl.brand_copy(brand, tr)
    mark, h = header_logo(ident, logos)
    min_px = ((ident.get("logo") or {}).get("min_size") or {}).get("px", 0) or 0
    if mark:
        inner, _how = ground_mark(info, logos, gt, svg=mark)
        lg = (f'<span class="lg" data-logo="site-header" data-min="{max(min_px, 12)}" style="height:{h}px">'
              f'{inner}</span>')
    else:
        lg = f'<span class="lg"><span class="word" data-role="display">{e(brand["name"])}</span></span>'
    nav = "".join(f"<a>{e(x)}</a>" for x in copy["nav"][:3])
    sub = (f'<p class="sub" data-role="text" data-shrink-mock>{e(copy["sentence"])}</p>'
           if copy["sentence"] else "")
    head = brand.get("tagline") or brand["name"]
    cta = e(copy["cta"])
    return (f'<div class="app{" only" if only else ""}" data-block="app"><span class="lab">{e(tr("card.in_use"))}</span>'
            f'<div class="frame" data-mock lang="{e(copy["lang"])}" style="--nav-h:{max(50, h + 20)}px;'
            f'{pl.ground_css(gt)}">'
            f'<nav data-nav>{lg}{nav}<a class="btn" data-probe-box>{cta}</a></nav>'
            f'<div class="hero-s" data-fit-box><div class="hl" data-role="display" data-shrink-mock>{e(head)}</div>'
            f'<div class="rule"></div>{sub}<a class="cta2" data-probe-box>{cta}</a></div></div></div>')


def notes_block(info, tr=None):
    tr = tr or pl.strings(info)
    st = info["identity"]["set"]
    return (f'<div class="notes" data-block="notes"><span class="lab">{e(tr("card.differs"))}</span>'
            f'<p>{e(st.get("differs_by") or "")}</p></div>')


CHECK_GROUPS = (("type", "check.type"), ("logo", "check.logo"), ("palette", "check.palette"), ("card", "check.card"))


def _clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def checks_html(identity, card_findings, failed_msg, t=None):
    import i18nlib
    t = t or i18nlib.for_brand(identity.get("brand"))
    audit = identity.get("audit") or {}
    findings = pl.shown_findings(identity, card_findings)
    items = []
    for comp, key in CHECK_GROUPS:
        word = t(key)
        if comp != "card" and pl.mode_of(identity, comp) == "none":
            continue
        if comp != "card" and pl.mode_of(identity, comp) == "keep":
            items.append(f'<span class="ck na">{pl.DASH}{e(word)} · {e(t("card.kept"))}</span>')
            continue
        gates = [f for f in findings if f.get("component") == comp and f.get("severity") == "gate"]
        audited = comp == "card" or audit
        if gates:
            items.append(f'<span class="ck bad">{pl.CROSS}{e(word)} · {e(t.n("card.gates", len(gates)))}</span>')
        elif audited:
            items.append(f'<span class="ck">{pl.CHECK}{e(word)}</span>')
        else:
            items.append(f'<span class="ck na">{pl.DASH}{e(word)} · {e(t("card.not_audited"))}</span>')
    warns = [f for f in findings if f.get("severity") == "warn"]
    flags = ""
    if failed_msg:
        flags = f'<span class="flags">{e(t("card.failed_up"))} — {e(_clip(failed_msg, 90))}</span>'
    elif warns:
        defaults = sum(1 for f in warns if str(f.get("id", "")).startswith("default."))
        head = t.n("card.warnings", len(warns))
        if defaults:
            head += " · " + t.n("card.ai_flags", defaults)
        first = next((f for f in warns if not str(f.get("id", "")).startswith("default.")), warns[0])
        # English messages are for the agent: another document language gets the catalogue's short sentence
        flags = f'<span class="flags">{e(head)}: {e(_clip(t.finding(first), 60))}</span>'
    elif audit:
        flags = f'<span class="flags">{e(t("card.no_warnings"))}</span>'
    else:
        flags = f'<span class="flags">{e(t("card.audits_in_build"))}</span>'
    return "".join(items) + flags


def first_gate_finding(identity, card=None):
    """First gate finding of the set: component audits, plus the card probe when given (a fresh probe replaces
    the audit's stored card findings)."""
    for f in pl.shown_findings(identity, card):
        if f.get("severity") == "gate":
            return f
    return None


def first_gate(identity, card=None):
    """First gate message of the set (English, for the agent), or None."""
    f = first_gate_finding(identity, card)
    return (f.get("message") or f.get("id")) if f else None


# ----------------------------------------------------------------------------- render

def build_html(info, faces, html_dir, card_findings=None, version=VERSION, date_str=None, tr=None):
    ident, pal = info["identity"], info["palette"]
    tr = tr or pl.strings(info)
    t = pl.theme(pal)
    gt = pl.ground_theme(pal, t, (ident.get("card") or {}).get("ground"))
    logos = pl.logo_svgs(info)
    st = ident["set"]
    total = pl.sets_total(info["dir"])
    gate = first_gate_finding(ident, card_findings if card_findings is not None else [])
    failed = tr.finding(gate) if gate else None  # English message only in an English document
    has_type = bool(type_block(info, faces, tr=tr))
    has_pal = pal is not None
    classes = []
    if not has_pal:
        classes.append("no-palette")
    if not (logos.get("primary") or logos.get("image")):
        classes.append("no-logo")
    if failed:
        classes.append("is-failed")
    sub = tr("card.direction", id=st["id"], total=total) + (f" · {tr('card.recommended')}" if st.get("recommended")
                                                            else "")
    lower_type = type_block(info, faces, only=False, tr=tr) if has_type else notes_block(info, tr)  # type `none`
    langs = ident["brand"].get("languages") or ["en"]
    css = pl.read_template("card", "card.css")
    values = {
        "LANG": e(langs[0].split("-")[0]), "DOC_LANG": e(tr.html_lang), "T_TITLE": e(tr("card.title")),
        "T_DOC": e(tr("card.doc")), "T_MOVE": e(tr("card.move")), "T_DIFFERS": e(tr("card.differs")), "BRAND": e(ident["brand"]["name"]), "SET_LABEL": e(st["id"]),
        "FONT_FACES": pl.font_face_css(faces, html_dir), "THEME": pl.theme_css(t), "ROLE_VARS": pl.role_vars(ident),
        "FRAME_FONT": pl.frame_font_css(), "FONT_UI": pl.FRAME_FAMILY,
        "CSS": css, "CLASSES": " ".join(classes), "ARROW": pl.ARROW, "SET_ID": e(st["id"]),
        "SET_NAME": e(st.get("name") or f"Set {st['id']}"), "SET_NAME_UP": e((st.get("name") or "").upper()),
        "SET_SUB": e(sub), "VERSION": version, "DATE": e(date_str or pl.today(t=tr)),
        "FAILED_CHIP": f'<span class="chip-failed">{e(tr("card.failed"))}</span>' if failed else "",
        "SET_TOTAL": total, "MECHANISM": e(st.get("mechanism") or ""), "MOVE": e(st.get("expression_move") or ""),
        "DIFFERS": e(st.get("differs_by") or "—"),
        "AXES": axes_html(ident, tr), "LOGO_BLOCK": logo_block(info, logos, t, html_dir, gt, tr),
        "PALETTE_BLOCK": palette_block(info, t, tr, gt), "TYPE_BLOCK": lower_type,
        "APP_BLOCK": app_block(info, logos, gt=gt, tr=tr),
        "CHECKS": checks_html(ident, card_findings if card_findings is not None else [], failed, tr),
        "FOOT_LEFT": e(tr("card.draft")),
        "FOOT_MID": e(_clip(" · ".join(x for x in (ident["brand"]["name"], ident["brand"].get("sector")) if x), 78)),
        "FOOT_RIGHT": f"© {(date_str or pl.today(t=tr))[-4:]}",
    }
    html = pl.fill(pl.read_template("card", "card.html"), values)
    if failed:
        html = html.replace('<section class="checks">', '<section class="checks failed">', 1)
    return html


def card_findings(result, faces, ident, tr=None):
    roles = {}
    for f in faces:
        roles.setdefault(f["role"], f["family"])
    min_px = ((ident.get("logo") or {}).get("min_size") or {}).get("px")
    out = render_png.probe_findings(result, pl.expected_fonts(faces), roles, min_px, component="card")
    for f in faces:
        if not f["path"]:
            out.append(identitylib.finding("font-file-missing", "card", "gate",
                                           f"no font file for {f['family']} {f['weight']} ({f['role']})",
                                           measured=f.get("note"), threshold="a static font file",
                                           suggested_fix="run `brand.py fonts audit` / install typelib"))
        elif f.get("note"):
            out.append(identitylib.finding("font-note", "card", "info", f["note"]))
    if tr is not None:
        out += tr.findings("card")
    return out


def render(set_dir, probe=True, out=None, scale=2.0, date_str=None):
    """Write <set>/card.html and card.png. Returns {html, png, findings, passed, probe, fonts}."""
    info = pl.load_set(set_dir)
    faces = pl.resolve_faces(info["identity"], info["dir"])
    html_path = os.path.join(info["dir"], "card.html")
    png = out or os.path.join(info["dir"], "card.png")
    tr = pl.strings(info)

    def write(findings):
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(build_html(info, faces, os.path.dirname(html_path), findings, date_str=date_str, tr=tr))

    write(None)
    result = render_png.render(html_path, png, W, H, probe=probe, scale=scale)
    findings = card_findings(result, faces, info["identity"], tr) if probe else tr.findings("card")
    if any(f["severity"] == "gate" for f in findings):  # second pass so the card shows its own failure
        write(findings)
        result = render_png.render(html_path, png, W, H, probe=probe, scale=scale)
    gates = [f for f in findings if f["severity"] == "gate"]
    if probe:  # remembered for the board when the card is not re-rendered
        cache = os.path.join(info["work"], ".cache", "cards")
        os.makedirs(cache, exist_ok=True)
        with open(os.path.join(cache, f"{info['identity']['set']['id']}.json"), "w", encoding="utf-8") as fh:
            json.dump(findings, fh, indent=1)
    return {"html": html_path, "png": png, "findings": findings, "passed": not gates and not first_gate(info["identity"], findings),
            "probe": {k: result.get(k) for k in ("fonts", "roles", "overflow", "logo_px")} if probe else None,
            "fonts": [{k: f[k] for k in ("role", "family", "weight", "path", "how")} for f in faces]}


def main(argv=None):
    pl_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set_dir")
    ap.add_argument("--no-probe", action="store_true", help="skip the measurements (faster)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    try:
        r = render(a.set_dir, probe=not a.no_probe)
    except (identitylib.IdentityError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(r, indent=1, default=str)[:20000])
    else:
        gates = [f for f in r["findings"] if f["severity"] == "gate"]
        print(r["png"])
        print(f"card gates: {'✔' if not gates else '✘ ' + gates[0]['message']}")
    return 0 if r["passed"] else 1


def pl_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
