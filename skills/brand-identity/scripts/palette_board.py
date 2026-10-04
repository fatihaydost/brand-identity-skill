#!/usr/bin/env python3
"""Build the presentation board for palette directions: one calm image a client can read.

Reads the preview folder written by site_preview.py (OUT/preview.json) and lays out one card per palette:
the palette as stacked colour bands, the site (or mock page) screenshot, the direction's name and one-line idea,
and one plain verdict line. Below the cards a short table says, in plain words, how each direction feels, whether
it is easy to read, whether it works for colour-blind readers, how it prints (when a print ground was given) and
the one thing to watch out for. No jargon on the board: the full technical audit (contrast and colour-vision
tables) is written next to it as audit.md.

Usage:
  python3 scripts/palette_board.py preview/ --png
  python3 scripts/palette_board.py preview/ --png --recommend B --brief "Small-batch hazelnut farm, Trabzon"
  python3 scripts/palette_board.py preview/ --json               # the plain-language verdicts as JSON

The board shows the theme(s) site_preview.py captured (--theme light|dark|both): with both, each card shows the
light and the dark screenshot. Writes board.html, board.png (with --png) and audit.md.
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import colorlib  # noqa: E402
import palette_audit  # noqa: E402

e = html.escape
PAD, GAP = 56, 24
BANDS = (("background", 32), ("surface", 16), ("primary", 26), ("accent", 14), ("text", 12))
BAND_H = 150

# ----------------------------------------------------------------------------- plain words

PLAIN = {  # role -> how a client would say it
    "background": "the page background", "surface": "cards", "surfaceAlt": "panels", "border": "dividers",
    "text": "body text", "textMuted": "secondary text", "primary": "the main colour", "onPrimary": "text on buttons",
    "accent": "the accent", "onAccent": "text on accent labels", "link": "links", "focus": "the keyboard focus outline",
    "success": "success messages", "warning": "warnings", "danger": "error messages", "info": "info messages",
}
FAMILY_NAMES = [  # (hue start, base, dark name, light name)
    (10, "red", "burgundy", "pink"), (45, "orange", "rust", "peach"), (75, "gold", "olive", "sand"),
    (115, "green", "forest green", "mint"), (165, "teal", "deep teal", "aqua"), (215, "blue", "navy", "sky blue"),
    (270, "violet", "indigo", "lavender"), (310, "magenta", "plum", "pink"), (350, "rose", "wine", "pink"),
]


def colour_name(hx):
    """A plain colour word for a hex ("white", "navy", "gold"), good enough for a sentence."""
    try:
        L, C, h = colorlib.hex_to_oklch(hx)
    except Exception:
        return "this colour"
    if C < 0.025:
        return "white" if L > 0.94 else "light grey" if L > 0.75 else "grey" if L > 0.45 else \
            "charcoal" if L > 0.25 else "black"
    name = FAMILY_NAMES[-1]
    for row in FAMILY_NAMES:
        if h >= row[0]:
            name = row
    if h < FAMILY_NAMES[0][0]:
        name = FAMILY_NAMES[-1]
    base, dark, light = name[1], name[2], name[3]
    if base == "gold" and C > 0.14 and L > 0.8:
        base = "yellow"
    if base == "orange" and C < 0.12:
        base = "copper"
    if base == "gold" and C < 0.07 and L >= 0.6:
        return "sand"
    if base == "blue" and C < 0.07 and L >= 0.42:
        return "slate blue"
    return dark if L < 0.42 else light if (L > 0.86 and C < 0.1) else base


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def _hexes(f):
    return re.findall(r"#[0-9a-fA-F]{6}", f.get("message", ""))


def plain_sentence(f):
    """Translate one audit finding into one sentence a client understands; None = not worth a client's time."""
    fid = str(f.get("id", ""))
    hx = _hexes(f)
    m = re.match(r"contrast\.(light|dark)\.(\w+)-on-(\w+)", fid)
    if m:
        fg, bg = m.group(2), m.group(3)
        fgn = colour_name(hx[0]) if hx else ""
        bgn = colour_name(hx[1]) if len(hx) > 1 else ""
        if fg == "onPrimary":
            return f"{_cap(fgn)} text on the main buttons is hard to read — use a darker shade of the button colour."
        if fg == "onAccent":
            return f"Text on the {bgn} accent labels is hard to read — swap the text colour."
        if fg == "link" and bg == "text":
            return "Links look like body text — underline them."
        if fg == "link":
            return f"The {fgn} links are hard to read on the page — use a darker shade."
        if fg == "text":
            return "Body text is too faint on the page — darken it."
        if fg == "textMuted":
            return "Secondary text is too faint — darken it a step."
        if fg == "accent":
            return f"The {fgn} accent is faint against the page — use it with dark text or an outline."
        if fg == "primary":
            return f"The {fgn} buttons blend into the page — use a deeper shade."
        if fg == "focus":
            return "The keyboard focus outline is hard to see."
        if fg == "border":
            return None
        return f"{_cap(PLAIN.get(fg, fg))} is hard to read on {PLAIN.get(bg, bg)}."
    m = re.match(r"cvd\.(light|dark)\.\w+\.(\w+)-(\w+)", fid)
    if m:
        a, b = m.group(2), m.group(3)
        if {a, b} == {"link", "text"}:
            return "Links look like body text for some colour-blind readers — underline them."
        if {a, b} == {"success", "danger"}:
            return "Success and error colours look alike for some colour-blind readers — add icons or words."
        return "The main and accent colours look alike for some colour-blind readers — don't rely on colour alone."
    m = re.match(r"ground\.([\w-]+)\.", fid)
    if m:
        name = colour_name(hx[0]) if hx else "this colour"
        g = m.group(1)
        return f"{_cap(name)} disappears on {g} — print it on a white label or use a darker {name}."
    if fid.startswith("extended."):
        if ".distinct." in fid and len(hx) > 1:
            return (f"The extra {colour_name(hx[0])} and the {colour_name(hx[1])} look alike — make one lighter or "
                    "darker, or label the variants.")
        if ".ui." in fid and hx:
            return f"The extra {colour_name(hx[0])} is faint on the page — use it as a fill with text, not a thin line."
        if ".on." in fid and len(hx) > 1:
            return f"Text on the extra {colour_name(hx[1])} is hard to read — use it for large labels only."
        return "One of the extra colours needs another look — see audit.md."
    if fid.startswith("distinct."):
        return f"{_cap(colour_name(hx[0]))} and {colour_name(hx[1])} are too alike to tell apart." if len(hx) > 1 else \
            "Two brand colours are too alike to tell apart."
    if fid.startswith("isoluminant."):
        return "Two colours are equally light, so where they meet the edge shimmers — make one darker."
    if fid == "structure.lightness-range":
        return "The brand colours are all similarly light or dark — the palette may look flat."
    if fid.startswith("structure.mid-lightness"):
        return f"The {colour_name(hx[0]) if hx else 'mid-tone'} is a middle tone: small text on it reads poorly in white or black."
    if fid == "structure.hue-count":
        return "More than three hues — the palette may feel busy."
    if fid == "dark.surface-too-light":
        return "The dark mode background is too light to feel like a dark mode."
    if fid == "dark.brand-derivative":
        return "In dark mode the main colour needs a lighter shade to stay readable."
    return None


def plain_short(f):
    """A 2-5 word label for the same finding (table cells and the card summary)."""
    fid = str(f.get("id", ""))
    hx = _hexes(f)
    m = re.match(r"contrast\.(light|dark)\.(\w+)-on-(\w+)", fid)
    if m:
        fg, bg = m.group(2), m.group(3)
        return {"onPrimary": "button text hard to read", "onAccent": "accent label text hard to read",
                "text": "body text too faint", "textMuted": "secondary text too faint",
                "accent": f"faint {colour_name(hx[0]) if hx else ''} accent".replace("  ", " "),
                "primary": "buttons blend into the page", "focus": "focus outline hard to see",
                }.get(fg, "links blend into text" if (fg, bg) == ("link", "text") else f"{PLAIN.get(fg, fg)} hard to read")
    m = re.match(r"cvd\.(light|dark)\.\w+\.(\w+)-(\w+)", fid)
    if m:
        pair = {m.group(2), m.group(3)}
        return "links blend into text" if pair == {"link", "text"} else \
            "success and error look alike" if pair == {"success", "danger"} else "main and accent look alike"
    m = re.match(r"ground\.([\w-]+)\.", fid)
    if m:
        return f"{colour_name(hx[0]) if hx else 'a colour'} fades on {m.group(1)}"
    return None


def _mode_of(f):
    m = re.search(r"\.(light|dark)\.", f".{f.get('id', '')}.")
    if m:
        return m.group(1)
    for r in f.get("roles") or []:
        if r.startswith(("light.", "dark.")):
            return r.split(".", 1)[0]
    return None


def verdicts(audit, themes, has_ground):
    """Plain-language cells for one direction: readability, colour vision, print, watch out, card line."""
    findings = [f for f in (audit or {}).get("findings") or [] if f.get("severity") in ("gate", "warn")]
    findings = [f for f in findings if _mode_of(f) in (None, *themes)]

    def pick(pred):
        out = []
        for f in sorted(findings, key=lambda f: 0 if f.get("severity") == "gate" else 1):
            if not pred(f):
                continue
            s = plain_sentence(f)
            if s is None:
                continue
            if _mode_of(f) == "dark" and len(themes) > 1:
                s = "Dark mode: " + s[0].lower() + s[1:]
            if s not in out:
                out.append(s)
        return out

    def first(prefix):
        for f in sorted(findings, key=lambda f: 0 if f.get("severity") == "gate" else 1):
            if f["id"].startswith(prefix) and plain_sentence(f):
                short = plain_short(f) or plain_sentence(f)
                if _mode_of(f) == "dark" and len(themes) > 1:
                    short = "dark mode: " + short
                return f, short
        return None, None

    rf, rs = first("contrast.")
    cf, cs = first("cvd.")
    pf, ps = first("ground.")
    risks = pick(lambda f: f.get("severity") == "gate") + pick(lambda f: f["id"].startswith("ground.")) + \
        pick(lambda f: f["id"].startswith("contrast.")) + pick(lambda f: f["id"].startswith("cvd.")) + \
        pick(lambda f: not f["id"].startswith(("contrast.", "cvd.", "ground.")))
    cells = {"read": (rf is None, _cap(rs) if rs else "Clear"),
             "cvd": (cf is None, _cap(cs) if cs else "Yes, key colours stay distinct")}
    parts = ["clear to read" if rf is None else rs, "colour-blind safe" if cf is None else f"{cs} for colour-blind readers"]
    if has_ground:
        cells["print"] = (pf is None, _cap(ps) if ps else "Holds up")
        parts.append("prints well" if pf is None else ps)
    cells["watch"] = risks[0] if risks else "Nothing major."
    cells["watch2"] = None
    cells["card"] = _cap(" · ".join(parts)) + "."
    return cells


OPPOSITES = [{"muted", "vivid"}, {"muted", "lively"}, {"cool", "warm"}, {"light", "deep"}, {"soft", "vivid"},
             {"restrained", "vivid"}, {"restrained", "lively"}, {"calm", "lively"}, {"moody", "light"}, {"clean", "moody"}]


def feels_like(pal, roles):
    """2-4 words: the palette's own `feels` list first; otherwise at most 3 words guessed from its colours, never two
    opposites (muted/lively, cool/warm, light/deep, soft/vivid …)."""
    for key in ("feels", "mood", "adjectives"):
        v = (pal or {}).get(key)
        if isinstance(v, list) and v:
            return [str(x) for x in v[:4]]
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in re.split(r"[,·]", v) if x.strip()][:4]
    cand = []  # in priority order
    p, a, bg = roles.get("primary"), roles.get("accent"), roles.get("background")
    try:
        if p:
            L, C, h = colorlib.hex_to_oklch(p)
            cand.append("restrained" if C < 0.04 else "muted" if C < 0.09 else "vivid" if C > 0.16 else "")
            if C >= 0.04:
                cand.append("warm" if (h < 100 or h > 340) else "cool" if 180 <= h <= 300 else "fresh")
            cand.append("deep" if L < 0.42 else "light" if L > 0.75 else "")
        if bg:
            L, C, h = colorlib.hex_to_oklch(bg)
            cand.append("moody" if L < 0.3 else "soft" if C > 0.01 and (h < 110 or h > 330) else "clean")
        if p and a and p.lower() != a.lower():
            hp, ha = colorlib.hex_to_oklch(p)[2], colorlib.hex_to_oklch(a)[2]
            d = abs(hp - ha) % 360
            cand.append("lively" if min(d, 360 - d) > 90 else "harmonious")
    except Exception:
        pass
    out = []
    for w in cand:
        if w and w not in out and not any({w, o} in OPPOSITES for o in out):
            out.append(w)
    return out[:3]


def extended_cell(pal, audit):
    """(ok, text) for the extra-colours column, or None when the palette has no extended colours. Findings may name
    an extended colour by its ext id (ext-2) or by the brand entry that carries it (brand-4, role "extended")."""
    ext = (pal or {}).get("extended") or []
    if not ext:
        return None
    names = {x.get("id"): x.get("name") or x.get("id") for x in ext}
    ext_ids = set(names)
    for b in (pal or {}).get("brand") or []:
        if b.get("role") == "extended":
            ext_ids.add(b.get("id"))
        names.setdefault(b.get("id"), b.get("name") or b.get("id"))
    other = {"Primary": "the main colour", "Accent": "the accent", "primary": "the main colour",
             "accent": "the accent", "link": "the links", "text": "the text", "background": "the page"}
    found = []
    for f in (audit or {}).get("findings") or []:
        fid = str(f.get("id", ""))
        if f.get("severity") not in ("gate", "warn") or not fid.startswith(("extended.", "distinct.")):
            continue
        ids = [r.split(".", 1)[-1] if r.startswith(("light.", "dark.")) else r for r in f.get("roles") or []]
        exts = [r for r in ids if r in ext_ids]
        if not exts:
            continue
        rest = [r for r in ids if r not in ext_ids]
        dark = ".dark." in fid
        if len(exts) >= 2:
            found.append((0, f"{names[exts[0]]} and {names[exts[1]]} look alike"))
        elif rest:
            found.append((1, f"{names[exts[0]]} is close to {other.get(rest[0], names.get(rest[0], rest[0]))}"))
        elif ".ui." in fid:
            found.append((2, f"{names[exts[0]]} is faint on the {'dark ' if dark else ''}page"))
        elif ".on." in fid or "contrast" in fid:
            found.append((3, f"Text on {names[exts[0]]} is hard to read{' in dark mode' if dark else ''}"))
        else:
            found.append((4, f"{names[exts[0]]} needs another look (see audit.md)"))
    if found:
        found.sort(key=lambda x: x[0])
        return False, found[0][1]
    return True, f"{len(ext)} — easy to tell apart"


def ext_strip_html(pal, theme):
    ext = (pal or {}).get("extended") or []
    if not ext:
        return ""
    boxes = []
    for x in ext[:5]:
        hx = ((x.get("dark") or {}).get("hex") if theme == "dark" else None) or x.get("hex")
        if not hx:
            continue
        boxes.append(f'<div><i style="background:{hx}"></i><b>{e(x.get("name") or x.get("id") or "")}</b>'
                     f'<code>{e(hx)}</code></div>')
    return f'<div class="ext">{"".join(boxes)}</div>' if boxes else ""


# ----------------------------------------------------------------------------- data

def load_palette(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def column_audit(col, grounds):
    """Audit the column's colours (partial: only what is there) with the preview's print grounds."""
    pal = load_palette(col.get("palette"))
    if pal is None:
        roles = col.get("roles") or {}
        brand = []
        for k in ("primary", "accent"):
            hx = (roles.get("light") or {}).get(k)
            if hx and all(b["hex"] != hx for b in brand):
                brand.append({"id": f"brand-{len(brand) + 1}", "hex": hx, "source": "measured"})
        pal = {"schema": colorlib.SCHEMA_ID, "name": col.get("name") or "current", "brand": brand,
               "modes": {m: r for m, r in roles.items() if r}}
    try:
        return pal, palette_audit.audit_palette(pal, partial=True, grounds=grounds or None)
    except Exception as exc:  # a broken palette must not break the board
        return pal, {"findings": [], "error": str(exc)}


def ink(bg):
    return colorlib.best_text_on(bg, ("#ffffff", "#141414"))


def font_faces():
    """Reuse the OFL variable font embedded in the mock templates, if present."""
    tdir = os.path.normpath(os.path.join(HERE, "..", "templates", "mock-sites"))
    for name in ("landing.html", "app.html"):
        p = os.path.join(tdir, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                faces = re.findall(r"@font-face\s*\{[^}]*\}", fh.read())
            if faces:
                return "\n".join(faces)
    return ""


def bands_html(roles):
    rows = []
    for k, w in BANDS:
        hx = roles.get(k)
        if not hx or (k == "surface" and colorlib.hex_to_oklch(hx)[0] > 0.97):
            continue  # a white card surface reads as the card itself, not as a band
        if any(hx.lower() == r[0].lower() or colorlib.delta_e_ok(hx, r[0]) < 0.04 for r in rows):
            continue  # same colour twice (primary = accent, surface = background) shows once
        rows.append((hx, w))
    total = sum(w for _, w in rows) or 1
    return '<div class="bands">' + "".join(
        f'<div style="background:{hx};color:{ink(hx)};height:{BAND_H * w / total:.1f}px"><span>{e(hx)}</span></div>'
        for hx, w in rows) + "</div>"


def strip_html(roles):
    out = []
    for k, _ in BANDS:
        hx = roles.get(k)
        if k != "surface" and hx and all(colorlib.delta_e_ok(hx, o) >= 0.04 for o in out):
            out.append(hx)
    return '<div class="strip">' + "".join(f'<i style="background:{h}"></i>' for h in out) + "</div>"


CVD_COLS = (("primary", "Main"), ("accent", "Accent"), ("link", "Link"), ("text", "Text"), ("success", "Success"),
            ("danger", "Error"))
CVD_VIEWS = ((None, "Typical", ""), ("protan", "Red-blind", "protanopia"), ("deutan", "Green-blind", "deuteranopia"),
             ("tritan", "Blue-blind", "tritanopia"))
PAIR_WORD = dict(CVD_COLS)


def pick_mobile(tshots):
    """The phone shot for a card: the page's own, or, when the main scene is print (product), the landing page's."""
    if not tshots:
        return None
    if tshots.get("template") == "product":
        for x in tshots.get("extra") or []:
            if x.get("label") == "landing":
                return x.get("mobile")
        return None
    return tshots.get("mobile")


def shots_row(desk, mob, label, avail):
    """Desktop and, beside it, the phone at the same height; nothing overlaps."""
    lab = f'<div class="tl">{e(label)}</div>' if label else ""
    if not mob:
        w = avail
        return (f'{lab}<div class="shots"><div class="d" style="width:{w}px;height:{w * 0.625:.0f}px">'
                f'{_img(desk)}</div></div>')
    gap = 10
    w = (avail - gap) / (1 + 0.625 * 390 / 844)
    h = w * 0.625
    mw = h * 390 / 844
    return (f'{lab}<div class="shots"><div class="d" style="width:{w:.0f}px;height:{h:.0f}px">{_img(desk)}</div>'
            f'<div class="m" style="width:{mw:.0f}px;height:{h:.0f}px">{_img(mob)}</div></div>')


def _img(src):
    return f'<img src="{e(src)}" alt="">' if src else '<div class="missing">no screenshot</div>'


def in_use_html(roles, head):
    g = lambda k, d: roles.get(k) or d  # noqa: E731
    bg, tx = g("background", "#ffffff"), g("text", "#111111")
    mu, pr, ln = g("textMuted", tx), g("primary", tx), g("link", g("primary", tx))
    op = g("onPrimary", ink(pr))
    ac = roles.get("accent")
    oa = g("onAccent", ink(ac) if ac else "#ffffff")
    badge = f'<span class="badge" style="background:{ac};color:{oa}">New</span>' if ac else ""
    return (f'<div class="use" style="background:{bg};color:{tx}"><div class="ut"><b>{e(head)}</b>{badge}</div>'
            f'<p style="color:{mu}">Body text with a <u style="color:{ln}">link</u>.</p>'
            f'<span class="btn" style="background:{pr};color:{op}">Get started</span></div>')


def status_html(roles):
    chips = [(k, n) for k, n in (("success", "Success"), ("warning", "Warning"), ("danger", "Error"), ("info", "Info"))
             if roles.get(k)]
    if not chips:
        return ""
    return '<div class="chips">' + "".join(
        f'<div><i style="background:{roles[k]}"></i><b>{n}</b><code>{e(roles[k])}</code></div>' for k, n in chips) + "</div>"


def readability_html(roles_by, themes, audit=None):
    """Body text, buttons, links: the ratio(s) on the page, and a mark exactly as the audit judges them (warn or gate
    findings for that pair in the shown themes), so board and audit.md agree."""
    bad = {}
    for f in (audit or {}).get("findings") or []:
        m = re.match(r"contrast\.(light|dark)\.(\w+)-on-(\w+)", str(f.get("id", "")))
        if m and m.group(1) in themes and f.get("severity") in ("gate", "warn"):
            bad[(m.group(2), m.group(3))] = True
    rows = []
    # (label, measured pair, extra pairs judged with it, reason shown when an extra pair fails)
    spec = (("Body text", ("text", "background"), (), ""),
            ("Buttons", ("onPrimary", "primary"), (("primary", "background"), ), "blend into the page"),
            ("Links", ("link", "background"), (("link", "text"), ), "look like body text"))
    for label, (fg, bg), extra, extra_why in spec:
        if not any((roles_by.get(t) or {}).get(fg) and (roles_by.get(t) or {}).get(bg) for t in themes):
            continue
        show, why = (fg, bg), ""
        ok = not bad.get((fg, bg))
        if not ok and label == "Buttons":
            why = "label hard to read"
        failing = [x for x in extra if bad.get(x)]
        if ok and failing:
            ok, why = False, extra_why
            if label == "Buttons":
                show = failing[0]  # show the ratio that is the problem: fill against the page
        ratios = []
        for t in themes:
            r = roles_by.get(t) or {}
            if r.get(show[0]) and r.get(show[1]):
                ratios.append(f"{colorlib.contrast_ratio(r[show[0]], r[show[1]]):.1f}:1")
        why_html = f' <small class="why">{why}</small>' if why else ""
        mark = '<span class="ok">✓</span>' if ok else '<span class="no">!</span>'
        rows.append(f'<div class="rr"><span>{label}{why_html}</span><small>{" · ".join(ratios)}</small>{mark}</div>')
    return "".join(rows)


def cvd_html(pal, roles_by, themes):
    """Same simulation, pairs and threshold as palette_audit.py; a row is ! when any theme's pair collapses."""
    roles = roles_by.get(themes[0]) or {}
    cols = [(k, n) for k, n in CVD_COLS if roles.get(k)]
    ext = [x for x in ((pal or {}).get("extended") or [])[:5] if x.get("hex")]
    head = "".join(f"<em>{n}</em>" for _, n in cols) + ('<em class="x">Extra</em>' if ext else "")
    out = [f'<div class="cv hd"><span></span><span class="cells">{head}</span><span></span></div>']
    for kind, name, sub in CVD_VIEWS:
        sim = (lambda h: colorlib.simulate_cvd(h, kind)) if kind else (lambda h: h)
        cells = "".join(f'<i style="background:{sim(roles[k])}"></i>' for k, _ in cols)
        if ext:
            cells += '<i class="x">' + "".join(f'<i style="background:{sim(x["hex"])}"></i>' for x in ext) + "</i>"
        clash = None
        for t in themes:
            r = roles_by.get(t) or {}
            for a, b in palette_audit.CVD_PAIRS:
                if not r.get(a) or not r.get(b) or r[a].lower() == r[b].lower():
                    continue
                d = colorlib.delta_e2000(sim(r[a]), sim(r[b]))
                if d < palette_audit.CVD_DE and clash is None:
                    clash = f"{PAIR_WORD.get(a, a)} ≈ {PAIR_WORD.get(b, b)}"
        mark = '<span class="ok">✓</span>' if clash is None else '<span class="no">!</span>'
        out.append(f'<div class="cv"><span class="nm">{name}{f"<small>{sub}</small>" if sub else ""}</span>'
                   f'<span class="cells">{cells}</span>{mark}'
                   + (f'<span class="clash">{e(clash)}</span>' if clash else "") + '</div>')
    return "".join(out)


CSS = """
:root{--paper:#f7f6f3;--ink:#161616;--mut:#6b6b6b;--line:#e7e5e0}
*{box-sizing:border-box}html,body{margin:0;background:var(--paper)}
body{color:var(--ink);font:15px/1.5 'BI Instrument Sans',ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{padding:52px __PAD__px 48px}
h1{margin:0;font-size:38px;line-height:1.1;letter-spacing:-.02em;font-weight:680}
.brief{margin:8px 0 34px;color:var(--mut);font-size:17px}
.cards{display:grid;grid-template-columns:repeat(__N__,__CW__px);column-gap:__GAP__px;grid-template-rows:repeat(8,auto)}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden;grid-row:span 8;display:grid;grid-template-rows:subgrid;grid-template-columns:minmax(0,1fr);row-gap:0}
.card>*{min-width:0}
.sec{padding:0 20px}.sec.top{padding-top:18px}
.sec h4{margin:22px 0 8px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);font-weight:600}
.shots{display:flex;gap:10px;margin-bottom:12px}
.shots .d img,.shots .m img,.shots .missing{display:block;width:100%;height:100%;object-fit:cover;object-position:top;border-radius:7px;border:1px solid var(--line)}
.shots .missing{display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:12px;background:#faf9f7}
.use{border-radius:9px;padding:10px 12px 12px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.07)}
.use .ut{display:flex;align-items:center;gap:6px;white-space:nowrap}.use b{font-size:13.5px;overflow:hidden;text-overflow:ellipsis}
.use p{margin:3px 0 8px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.use .btn{display:inline-block}
.uses{display:flex;gap:8px}.uses .use{flex:1}
.badge{font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:999px;vertical-align:1px}
.btn{font-size:12px;font-weight:600;padding:6px 12px;border-radius:999px;white-space:nowrap}
.chips{display:flex;gap:8px}.chips div{flex:1;min-width:0}
.chips i,.ext i{display:block;height:20px;border-radius:5px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.07)}
.chips b{display:block;font-size:12px;font-weight:600;margin-top:5px}.chips code{font-size:11px;color:var(--mut);font-family:inherit}
.rr{display:grid;grid-template-columns:1fr auto 16px;gap:12px;align-items:baseline;padding:4px 0;font-size:14px}
.rr small{color:var(--mut);font-size:12px;text-align:right}.rr small.why{color:#a85a00;margin-left:6px}
.cv{display:grid;grid-template-columns:84px 1fr 16px;column-gap:10px;align-items:center;margin:5px 0}
.cv .clash{grid-column:2/4;font-size:11.5px;color:#a85a00;margin-top:2px}
.cv .nm{font-size:13px;line-height:1.15}.cv .nm small{display:block;color:var(--mut);font-size:10.5px}
.cv .cells{display:flex;height:18px;border-radius:4px;overflow:hidden}
.cv .cells>i{flex:1}.cv .cells>i.x{display:flex;flex:1.6;margin-left:4px;border-left:2px solid #fff}.cv .cells>i.x i{flex:1}
.cv.hd .cells{height:auto;border-radius:0}.cv.hd em{flex:1;min-width:0;font-style:normal;font-size:10px;color:var(--mut);text-align:center;white-space:nowrap;overflow:hidden}
.cv.hd em.x{flex:1.6;margin-left:4px}
.cv .ok,.cv .no{font-size:13px}
.bottom{padding-bottom:22px}
.card.rec{border:1.5px solid var(--ink)}
.bands{border-bottom:1px solid var(--line)}
.bands div{display:flex;align-items:center;padding:0 14px;font-size:11px;letter-spacing:.03em;min-height:22px}
.bands span{opacity:.8}

.tl{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 5px}
h2{margin:4px 0 0;font-size:22px;line-height:1.2;font-weight:680;letter-spacing:-.01em}
.rec-tag{margin:4px 0 0;font-size:12px;letter-spacing:.16em;font-weight:650}
.idea{margin:8px 0 0;color:#3a3a3a}
.ext{display:flex;gap:6px;margin:0 0 14px}
.tl{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 5px}
.ext div{flex:1;min-width:0}
.ext i{display:block;height:26px;border-radius:5px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.07)}
.ext b{display:block;font-size:12px;font-weight:600;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ext code{font-size:11px;color:var(--mut);font-family:inherit}
.table{margin-top:28px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:6px 24px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);font-weight:600;padding:14px 12px 10px}
td{vertical-align:top;padding:14px 12px;border-top:1px solid var(--line);font-size:14.5px}
td.dir{white-space:nowrap;font-weight:650}
.strip{display:flex;width:140px;height:22px;border-radius:5px;overflow:hidden;margin-bottom:6px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.08)}
.strip i{flex:1}
.ok,.no{font-weight:700;margin-right:6px}.ok{color:#1d7a4c}.no{color:#a85a00}
.note{margin:18px 0 0;color:var(--mut);font-size:13px}
"""


def build(out_dir, html_path=None, png=False, scale=1.0, title=None, brief=None, recommend=None):
    """Write board.html (+ board.png) and audit.md. Returns (html_path, png_path_or_None)."""
    mpath = os.path.join(out_dir, "preview.json")
    if not os.path.isfile(mpath):
        raise FileNotFoundError(f"{mpath} not found: run site_preview.py first")
    with open(mpath, encoding="utf-8") as fh:
        man = json.load(fh)
    cols = man.get("columns") or []
    if not cols:
        raise ValueError(f"{mpath} has no columns")
    themes = man.get("themes") or [man.get("mode", "light")]
    grounds = man.get("grounds") or []
    html_path = html_path or os.path.join(out_dir, "board.html")
    html_dir = os.path.dirname(os.path.abspath(html_path))

    def rel(p):
        if not p:
            return None
        full = p if os.path.isabs(p) else os.path.join(out_dir, p)
        return os.path.relpath(full, html_dir).replace(os.sep, "/") if os.path.isfile(full) else None

    n = len(cols)
    cw = int(max(400, min(560, (1840 - 2 * PAD - (n - 1) * GAP) / n)))
    brand = man.get("brandName") or (man.get("title") or "").split(" | ")[0].split(" – ")[0].strip() or "Brand"
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    rec = (recommend or man.get("recommend") or "").strip().lower()
    cards, rows, md = [], [], [f"# Technical checks: {man.get('brandName') or man.get('title') or 'palette directions'}", "",
                               "Full palette_audit.py output per column (partial audit: only the colours each column "
                               "has). The board shows these in plain words.", ""]
    any_ext = any((load_palette(c.get("palette")) or {}).get("extended") for c in cols)
    for c in cols:
        is_cur = c.get("kind") == "current"
        letter = "" if is_cur else next(letters)
        label = "Current" if is_cur else f"{letter}\u2002·\u2002{c.get('name') or ''}"
        is_rec = bool(c.get("recommended")) or (not is_cur and rec in (letter.lower(), str(c.get("name", "")).lower(),
                                                                       str(c.get("slug", "")).lower()) and rec != "")
        roles_by = c.get("roles") or {}
        roles = roles_by.get(themes[0]) or roles_by.get("light") or {}
        pal, audit = column_audit(c, grounds)
        v = verdicts(audit, themes, bool(grounds))
        shots = c.get("shots") or {}
        avail = cw - 40
        imgs = "".join(shots_row(rel((shots.get(t) or {}).get("desktop")), rel(pick_mobile(shots.get(t))),
                                 t if len(themes) > 1 else "", avail) for t in themes)
        idea = c.get("direction") or ("Today's site, as measured." if is_cur else "")
        head_txt = brand if len(brand) <= 14 else brand.split()[0]
        uses = "".join(in_use_html(roles_by.get(t) or {}, head_txt) for t in themes)
        status = status_html(roles)
        sections = [
            bands_html(roles),
            f'<div class="sec top">{ext_strip_html(pal, themes[0])}</div>',
            f'<div class="sec">{imgs}</div>',
            f'<div class="sec"><h2>{e(label)}</h2>' + ('<div class="rec-tag">RECOMMENDED</div>' if is_rec else "")
            + (f'<p class="idea">{e(idea)}</p>' if idea else "") + "</div>",
            f'<div class="sec"><h4>In use</h4><div class="uses">{uses}</div></div>',
            f'<div class="sec">' + (f'<h4>Status colours</h4>{status}' if status else "") + "</div>",
            f'<div class="sec"><h4>Readability</h4>{readability_html(roles_by, themes, audit)}</div>',
            f'<div class="sec bottom"><h4>Colour vision</h4>{cvd_html(pal, roles_by, themes)}</div>',
        ]
        cards.append(f'<article class="card{" rec" if is_rec else ""}">' + "".join(sections) + "</article>")
        mark = lambda ok: '<span class="ok">✓</span>' if ok else '<span class="no">!</span>'  # noqa: E731
        cells = [f'<td class="dir">{strip_html(roles)}{e(label)}</td>',
                 f'<td>{e(", ".join(feels_like(pal, roles)) or "—")}</td>',
                 f'<td>{mark(v["read"][0])}{e(v["read"][1])}</td>',
                 f'<td>{mark(v["cvd"][0])}{e(v["cvd"][1])}</td>']
        if any_ext:
            xc = extended_cell(pal, audit)
            cells.append(f'<td>{mark(xc[0])}{e(xc[1])}</td>' if xc else "<td>—</td>")
        if grounds:
            cells.append(f'<td>{mark(v["print"][0])}{e(v["print"][1])}</td>')
        watch = v["watch"]
        cells.append(f'<td>{e(watch)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
        md += [f"## {label}", ""]
        if c.get("direction"):
            md += [c["direction"], ""]
        if audit.get("error"):
            md += [f"Audit failed: {audit['error']}", ""]
        else:
            body = palette_audit.markdown(pal, audit).split("\n", 1)[1] if "\n" in palette_audit.markdown(pal, audit) else ""
            md += [re.sub(r"^## ", "### ", body.strip(), flags=re.M), ""]
    head = ["Direction", "Feels like", "Easy to read", "Colour-blind friendly"] + (["Extra colours"] if any_ext else []) + (
        [f"Print ({', '.join(g['name'] for g in grounds)})"] if grounds else []) + ["Watch out"]
    width = 2 * PAD + n * cw + (n - 1) * GAP
    ttl = title or f"{brand} — colour directions"
    sub = brief or man.get("brief") or (man.get("tagline") if man.get("source") == "mock" else man.get("url")) or ""
    css = (CSS.replace("__PAD__", str(PAD)).replace("__N__", str(n)).replace("__CW__", str(cw))
           .replace("__GAP__", str(GAP)))
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(ttl)}</title>'
           f'<meta name="viewport" content="width={width}"><style>{font_faces()}{css}html,body{{width:{width}px}}</style>'
           f'</head><body><div class="wrap"><h1>{e(ttl)}</h1><p class="brief">{e(sub)}</p>'
           f'<div class="cards">{"".join(cards)}</div>'
           f'<div class="table"><table><thead><tr>{"".join(f"<th>{e(h)}</th>" for h in head)}</tr></thead>'
           f'<tbody>{"".join(rows)}</tbody></table></div>'
           f'<p class="note">Technical checks: audit.md · {datetime.now().strftime("%d %b %Y")}</p></div></body></html>')
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    with open(os.path.join(html_dir, "audit.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md).rstrip() + "\n")
    png_path = None
    if png:
        import render_png
        png_path = os.path.splitext(html_path)[0] + ".png"
        render_png.screenshot_html(html_path, png_path, width=width, height=900, scale=scale, full_page=True)
    return html_path, png_path


def main(argv=None):
    colorlib.setup_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="preview folder written by site_preview.py (contains preview.json)")
    ap.add_argument("-o", "--html", help="board HTML path (default OUT_DIR/board.html)")
    ap.add_argument("--png", action="store_true", help="also render board.png next to the HTML")
    ap.add_argument("--scale", type=float, default=1.0, help="PNG device scale factor (default 1)")
    ap.add_argument("--title", help="title (default '<Brand> — colour directions')")
    ap.add_argument("--brief", help="one-line brief under the title")
    ap.add_argument("--recommend", help="letter, name or slug of the recommended direction (framed on the board)")
    ap.add_argument("--json", action="store_true", help="print the plain-language verdicts per column as JSON")
    a = ap.parse_args(argv)
    try:
        html_path, png_path = build(a.out_dir, a.html, a.png, a.scale, a.title, a.brief, a.recommend)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:  # rendering
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.json:
        with open(os.path.join(a.out_dir, "preview.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        themes = man.get("themes") or [man.get("mode", "light")]
        out = []
        for c in man["columns"]:
            pal, audit = column_audit(c, man.get("grounds") or [])
            roles = (c.get("roles") or {}).get(themes[0]) or {}
            out.append({"name": c.get("name"), "kind": c.get("kind"), "feels": feels_like(pal, roles),
                        **verdicts(audit, themes, bool(man.get("grounds")))})
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(png_path or html_path)
    print(f"wrote {html_path}, audit.md" + (f" and {png_path}" if png_path else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
