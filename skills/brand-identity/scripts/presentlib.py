"""Shared helpers for the presentation layer (identity_card, identity_board, kit_build). docs/architecture.md sections 4.7, 7.

- load_set(set_dir): identity.json + palette.json (+ component modes) for one set.
- theme(palette): the tokens that paint the fixed templates (paper, panel, ink, muted, line, primary, accent, dark).
- resolve_faces(identity, set_dir): one static font file per role and weight. Order: typelib.resolve_font (when
  installed), the face's `file`, type/fonts.json, then a family-name search in the work folder's .cache/fonts,
  the XDG cache and (development only) tests/fixtures/fonts. Variable files are pinned to a static instance in
  <work>/.cache/fonts (fontTools instancer) so PDFs embed real fonts, never Type3.
- logo_svgs / colorize / mono: the set's logo files inlined and painted from palette roles.
Font instances live only in caches; no font file is ever written next to user outputs.
"""
import hashlib
import html
import importlib
import importlib.util
import json
import os
import re
import sys
from datetime import date
from urllib.parse import quote

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import colorlib  # noqa: E402
import identitylib  # noqa: E402

SKILL = os.path.dirname(HERE)
TEMPLATES = os.path.join(SKILL, "templates")
DEV_FONT_DIR = os.path.normpath(os.path.join(SKILL, "..", "..", "tests", "fixtures", "fonts"))
e = html.escape

ARROW = ('<svg class="ico-arrow" viewBox="0 0 28 10" aria-hidden="true"><path d="M0 5h26M21 1l5 4-5 4" '
         'fill="none" stroke="currentColor" stroke-width="1.3"/></svg>')
CHECK = ('<svg class="ico" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6.4l2.6 2.6L10 3.4" fill="none" '
         'stroke="currentColor" stroke-width="1.6"/></svg>')
CROSS = ('<svg class="ico" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" fill="none" '
         'stroke="currentColor" stroke-width="1.6"/></svg>')
DASH = ('<svg class="ico" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 6h6" fill="none" '
        'stroke="currentColor" stroke-width="1.6"/></svg>')

# A real sentence per language for type specimens (diacritics included where the language has them).
SAMPLES = {
    "en": "We answer every message within one working day, and we say so when we can't.",
    "tr": "Her mesaja bir iş günü içinde yanıt veriyoruz; yapamadığımızda bunu açıkça söylüyoruz.",
    "de": "Wir beantworten jede Anfrage innerhalb eines Werktags – und sagen ehrlich, wenn es länger dauert.",
    "fr": "Nous répondons à chaque message sous un jour ouvré, et nous le disons quand ce n'est pas possible.",
    "es": "Respondemos a cada mensaje en un día hábil y avisamos cuando no podemos hacerlo.",
    "it": "Rispondiamo a ogni messaggio entro un giorno lavorativo, e lo diciamo quando non è possibile.",
    "pt": "Respondemos a cada mensagem em um dia útil e avisamos quando não é possível.",
    "nl": "We beantwoorden elk bericht binnen één werkdag, en zeggen het eerlijk als dat niet lukt.",
    "pl": "Odpowiadamy na każdą wiadomość w ciągu jednego dnia roboczego, a jeśli się nie da — mówimy o tym.",
    "sv": "Vi svarar på varje meddelande inom en arbetsdag och säger till när det inte går.",
    "cs": "Na každou zprávu odpovídáme do jednoho pracovního dne, a když to nejde, řekneme to.",
    "ro": "Răspundem fiecărui mesaj într-o zi lucrătoare și spunem deschis când nu se poate.",
    "hu": "Minden üzenetre egy munkanapon belül válaszolunk, és szólunk, ha ez nem sikerül.",
    "da": "Vi svarer på hver besked inden for én arbejdsdag og siger til, når det ikke kan lade sig gøre.",
    "fi": "Vastaamme jokaiseen viestiin yhden työpäivän kuluessa ja kerromme, jos se ei onnistu.",
    "el": "Απαντάμε σε κάθε μήνυμα μέσα σε μία εργάσιμη ημέρα και το λέμε όταν δεν γίνεται.",
    "ru": "Мы отвечаем на каждое сообщение в течение рабочего дня и честно говорим, если не успеваем.",
    "uk": "Ми відповідаємо на кожне повідомлення протягом робочого дня і чесно кажемо, якщо не встигаємо.",
}
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
ROLE_WORD = {"background": "Ground", "surface": "Surface", "surfaceAlt": "Panel", "primary": "Primary",
             "accent": "Accent", "text": "Ink", "textMuted": "Muted ink", "border": "Rule"}


def today(d=None, t=None):
    """'03 OCT 2026'; with t (i18nlib.Strings) the month is in the document language."""
    d = d or date.today()
    mon = t(f"month_short.{d.month}") if t else MONTHS[d.month - 1]
    return f"{d.day:02d} {mon} {d.year}"


def today_long(d=None, t=None):
    """'October, 2026'; with t (i18nlib.Strings) the month is in the document language."""
    d = d or date.today()
    if t:
        return t("date.month_year", month=t(f"month.{d.month}"), year=d.year)
    return f"{MONTHS_LONG[d.month - 1]}, {d.year}"


def role_word(role, t=None):
    """A palette role as a label ('Ink' for text)."""
    return t.word("role", role) if t else ROLE_WORD.get(role, role)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "brand"


def fill(template, values):
    """Replace {{KEY}} placeholders; unknown keys are an error (a template typo must not ship)."""
    def rep(m):
        k = m.group(1)
        if k not in values:
            raise KeyError(f"template placeholder {{{{{k}}}}} has no value")
        return str(values[k])
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", rep, template)


def read_template(*parts):
    with open(os.path.join(TEMPLATES, *parts), encoding="utf-8") as fh:
        return fh.read()


# ----------------------------------------------------------------------------- set data

def mode_of(identity, comp):
    return ((identity.get("components") or {}).get(comp) or {}).get("mode", "new")


def load_set(set_dir):
    """identity (validated; partial skeletons accepted), palette dict or None, and resolved paths."""
    set_dir = os.path.abspath(set_dir)
    path = os.path.join(set_dir, "identity.json")
    try:
        ident = identitylib.load_identity(path)
    except identitylib.IdentityError:
        ident = identitylib.load_identity(path, partial=True)
    pal = None
    pfile = ident.get("palette")
    if mode_of(ident, "palette") != "none" or pfile:
        p = os.path.join(set_dir, pfile or "palette.json")
        if not os.path.isfile(p) and mode_of(ident, "palette") in ("keep", "none"):
            src = (ident["components"]["palette"] or {}).get("source")
            p = os.path.join(identitylib.work_dir_of(set_dir), src) if src else p
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                pal = json.load(fh)
            if not (pal.get("modes") or {}).get("light"):
                pal = None
    return {"dir": set_dir, "work": identitylib.work_dir_of(set_dir), "identity": ident, "palette": pal}


def strings(info):
    """i18nlib.Strings for a load_set() result: tool text in the brand's document language (brand.doc_lang, else
    languages[0]), the work folder's i18n.json first."""
    import i18nlib
    return i18nlib.for_brand(info["identity"].get("brand") or {}, info.get("work"))


def sets_total(set_dir):
    return max(1, len(identitylib.set_dirs(identitylib.work_dir_of(set_dir))))


# ----------------------------------------------------------------------------- colour

NEUTRAL_THEME = {"paper": "#f3f2ef", "panel": "#ffffff", "ink": "#1b1b1a", "muted": "#5c5b57", "line": "#d9d7d1",
                 "primary": "#1b1b1a", "onPrimary": "#ffffff", "accent": "#1b1b1a", "onAccent": "#ffffff",
                 "dark": "#1b1b1a", "onDark": "#f3f2ef", "darkMuted": "#a8a7a2", "danger": "#a3271f"}


def _L(hx):
    return colorlib.hex_to_oklch(hx)[0]


def theme(pal):
    """Template tokens from a built palette (light mode paints the paper; text/primary come from roles)."""
    if not pal:
        return dict(NEUTRAL_THEME)
    lt = pal["modes"]["light"]
    dk = pal["modes"].get("dark") or {}
    neutral = ((pal.get("scales") or {}).get("neutral") or {}).get("steps") or {}
    bg = lt.get("background", "#ffffff")
    if _L(bg) < 0.985:
        paper = bg  # a tinted ground (cream, kraft) is the paper itself
        panel = lt.get("surface", "#ffffff")
        if colorlib.delta_e_ok(panel, paper) < 0.02:
            panel = "#ffffff"
    else:
        paper = neutral.get("100") or lt.get("surfaceAlt") or lt.get("surface") or bg
        panel = bg
    ink = lt["text"]
    muted = lt.get("textMuted", ink)
    if colorlib.contrast_ratio(muted, paper) < 4.6:
        muted = ink
    dark = ink if _L(ink) < 0.3 else (dk.get("background") or "#111111")
    on_dark = dk.get("text") or "#f5f5f5"
    dark_muted = dk.get("textMuted") or on_dark
    if colorlib.contrast_ratio(dark_muted, dark) < 4.6:
        dark_muted = on_dark
    return {"paper": paper, "panel": panel, "ink": ink, "muted": muted, "line": lt.get("border", "#dddddd"),
            "primary": lt["primary"], "onPrimary": lt.get("onPrimary", "#ffffff"),
            "accent": lt.get("accent", lt["primary"]), "onAccent": lt.get("onAccent", "#ffffff"),
            "dark": dark, "onDark": on_dark, "darkMuted": dark_muted, "danger": lt.get("danger", "#a3271f")}


def theme_css(t):
    names = {"paper": "paper", "panel": "panel", "ink": "ink", "muted": "muted", "line": "line",
             "primary": "primary", "onPrimary": "on-primary", "accent": "accent", "onAccent": "on-accent",
             "dark": "dark", "onDark": "on-dark", "darkMuted": "dark-muted", "danger": "danger"}
    return "".join(f"--{v}:{t[k]};" for k, v in names.items())


def ink_on(bg):
    """Label colour on a swatch: soft black or white; pure black when a mid-tone needs it for 4.5:1."""
    c = colorlib.best_text_on(bg, ("#ffffff", "#141414"))
    if colorlib.contrast_ratio(c, bg) < 4.5:
        c = colorlib.best_text_on(bg, ("#ffffff", "#000000"))
    return c


def role_hex(pal, ref, mode="light"):
    """A palette role key, brand id or extended id -> hex in the given mode (None when unknown)."""
    if not pal or not ref:
        return None
    m = (pal.get("modes") or {}).get(mode) or {}
    if ref in m:
        return m[ref]
    for b in pal.get("brand") or []:
        if b.get("id") == ref:
            return b["hex"]
    for x in pal.get("extended") or []:
        if x.get("id") == ref:
            return (x.get("dark") or {}).get("hex", x["hex"]) if mode == "dark" else x["hex"]
    return None


def brand_name_for(pal, hx, role=None, tol=0.12):
    """The brand[] name behind a primary/accent role colour (exact hex, else brand[0]/brand[1] when the role was
    moved a scale step for contrast, within `tol` OKLab). Other roles keep their role word."""
    core = [b for b in (pal or {}).get("brand") or [] if b.get("role") != "extended" and b.get("hex")]
    for b in core:
        if b["hex"].lower() == (hx or "").lower() and role in ("primary", "accent", None):
            return b.get("name") if b.get("name") not in ("Primary", "Accent") else None
    idx = {"primary": 0, "accent": 1}.get(role)
    if idx is not None and len(core) > idx and colorlib.delta_e_ok(core[idx]["hex"], hx) <= tol:
        nm = core[idx].get("name")
        return nm if nm not in ("Primary", "Accent") else None
    return None


def brand_colour(pal, role, hx):
    """For a primary/accent role: the locked brand colour behind it. Returns {name, hex, role_hex} where hex is
    palette.brand[].hex (what the brand owns) and role_hex the step the UI uses (may differ for contrast), or None."""
    core = [b for b in (pal or {}).get("brand") or [] if b.get("role") != "extended" and b.get("hex")]
    idx = {"primary": 0, "accent": 1}.get(role)
    if idx is None or len(core) <= idx:
        return None
    b = core[idx]
    if b["hex"].lower() != (hx or "").lower() and colorlib.delta_e_ok(b["hex"], hx) > 0.2:
        return None
    name = b.get("name") if b.get("name") not in ("Primary", "Accent", None, "") else None
    return {"name": name, "hex": b["hex"].lower(), "role_hex": (hx or "").lower()}


def ground_theme(pal, t, ground=None):
    """Colours for a block painted on the set's card ground (identity.card.ground): "light" (default), "dark" or a
    palette role / brand id. Returns {bg, ink, muted, line, btn, onBtn, rule, mode, name, ref}; every text colour
    is picked for contrast on bg (the probe still measures it)."""
    g = (ground or "light").strip() if isinstance(ground, str) else "light"
    if not pal:
        g = "light"
    modes = (pal or {}).get("modes") or {}
    lt, dk = modes.get("light") or {}, modes.get("dark") or {}
    if g == "light":
        bg, mode = t["panel"], "light"
    elif g == "dark":
        bg, mode = dk.get("background") or t["dark"], "dark"
    else:
        bg = role_hex(pal, g, "light")
        if not bg:
            g, bg, mode = "light", t["panel"], "light"
        else:
            mode = ("dark" if colorlib.contrast_ratio(dk.get("text", "#ffffff"), bg) >
                    colorlib.contrast_ratio(lt.get("text", "#111111"), bg) else "light")
    m = modes.get(mode) or lt

    def best(cands, need):
        cands = [c for c in cands if c]
        ok = [c for c in cands if colorlib.contrast_ratio(c, bg) >= need]
        return ok[0] if ok else max(cands + [ink_on(bg)], key=lambda c: colorlib.contrast_ratio(c, bg))
    ink = best([m.get("text"), lt.get("text"), dk.get("text")], 4.5)
    muted = best([m.get("textMuted"), ink], 4.5)
    btn = None
    for c in (m.get("primary"), m.get("accent"), lt.get("primary"), lt.get("accent"), ink):
        if c and colorlib.contrast_ratio(c, bg) >= 3 and colorlib.delta_e_ok(c, bg) > 0.05:
            btn = c
            break
    btn = btn or ink
    on_btn = max([x for x in (m.get("onPrimary"), m.get("onAccent"), bg, "#ffffff", "#111111") if x],
                 key=lambda c: colorlib.contrast_ratio(c, btn))
    rule = m.get("accent") if m.get("accent") and colorlib.contrast_ratio(m["accent"], bg) >= 1.8 else btn
    line = m.get("border") if g in ("light", "dark") else ink + "40"
    return {"bg": bg, "ink": ink, "muted": muted, "line": line or ink + "40", "btn": btn, "onBtn": on_btn,
            "rule": rule, "mode": mode, "name": g, "ref": g if g not in ("light", "dark") else None}


def ground_css(gt):
    return (f"--g-bg:{gt['bg']};--g-ink:{gt['ink']};--g-muted:{gt['muted']};--g-line:{gt['line']};"
            f"--g-btn:{gt['btn']};--g-on-btn:{gt['onBtn']};--g-rule:{gt['rule']};")


def logo_version(set_dir, gt, one_color=False):
    """The logolib version of the primary logo painted for this ground (device tile/outline included), as SVG
    text, or None. light -> full-color / on-light-background; dark -> on-dark-background / full-color-dark;
    a role ground -> on-<mode>-<role>."""
    man = logo_manifest(set_dir)
    vers = man.get("versions") or {}
    if not vers:
        return None
    if gt.get("ref") and not one_color:
        # a role or brand-id ground: the version logolib painted for that ground colour, whatever its name
        for n, v in vers.items():
            if n.startswith("on-") and v.get("ground") and colorlib.delta_e_ok(v["ground"], gt["bg"]) <= 0.03:
                p = os.path.join(set_dir, "logo", "build", v.get("svg") or f"{n}.svg")
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as fh:
                        return _crop_clear_space(fh.read(), man, bool(v.get("device")))
    if one_color:
        names = ["one-color-light"] if gt["mode"] == "dark" else ["one-color-dark"]
    elif gt["name"] == "light":
        names = ["on-light-background", "full-color"]
    elif gt["name"] == "dark":
        names = ["on-dark-background", "full-color-dark", "one-color-light"]
    else:
        names = [f"on-{gt['mode']}-{gt['ref']}", f"on-light-{gt['ref']}", f"on-dark-{gt['ref']}"]
    for n in names:
        v = vers.get(n)
        if not v:
            continue
        if v.get("ground") and colorlib.delta_e_ok(v["ground"], gt["bg"]) > 0.03 and n.startswith("on-"):
            continue  # painted for another ground colour
        p = os.path.join(set_dir, "logo", "build", v.get("svg") or f"{n}.svg")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return _crop_clear_space(fh.read(), man, bool(v.get("device")))
    return None


def _crop_clear_space(svg, man, device):
    """logolib versions carry the clear space and a ground rect; on a card the mark sits on the card's own ground
    and is sized by itself. Drop the ground rect and crop the viewBox to data-ink (the inked bounds, device
    included); without data-ink, crop the manifest's clear-space units."""
    svg = re.sub(r'<rect\b[^>]*\bid="ground"[^>]*/>', "", svg, count=1)
    m = re.search(r'data-ink="([^"]+)"', svg)
    vb = viewbox(svg)
    if m:
        try:
            x, y, x1, y1 = (float(v) for v in re.split(r"[\s,]+", m.group(1).strip()))  # bounds, not w/h
            w, h = x1 - x, y1 - y
            if w <= 0 or h <= 0:
                raise ValueError("empty ink box")
            pad = 0.02 * max(w, h)
            nv = f"{x - pad:g} {y - pad:g} {w + 2 * pad:g} {h + 2 * pad:g}"
            return re.sub(r'viewBox="[^"]+"', f'viewBox="{nv}"', svg, count=1)
        except ValueError:
            pass
    units = float(((man.get("clear_space") or {}).get("units")) or 0)
    if not units or not vb:
        return svg
    crop = units if not device else max(0.0, units - 0.2 * max(0.0, vb[3] - 2 * units))
    if vb[2] <= 2 * crop or vb[3] <= 2 * crop:
        return svg
    nv = f"{vb[0] + crop:g} {vb[1] + crop:g} {vb[2] - 2 * crop:g} {vb[3] - 2 * crop:g}"
    return re.sub(r'viewBox="[^"]+"', f'viewBox="{nv}"', svg, count=1)


def version_entry_for_bg(set_dir, bg):
    """(name, manifest entry) of the logolib on-* version painted for a ground colour, or (None, None)."""
    for n, v in (logo_manifest(set_dir).get("versions") or {}).items():
        if n.startswith("on-") and v.get("ground") and bg and colorlib.delta_e_ok(v["ground"], bg) <= 0.03:
            return n, v
    return None, None


def logo_version_for_bg(set_dir, bg):
    """The logolib on-* version painted for a ground colour (crop as logo_version), or None."""
    man = logo_manifest(set_dir)
    for n, v in (man.get("versions") or {}).items():
        if n.startswith("on-") and v.get("ground") and bg and colorlib.delta_e_ok(v["ground"], bg) <= 0.03:
            p = os.path.join(set_dir, "logo", "build", v.get("svg") or f"{n}.svg")
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    return _crop_clear_space(fh.read(), man, bool(v.get("device")))
    return None


NUM_GROUPS = {  # (thousands separator, decimal mark) for the figures sample
    "dot_comma": ({"tr", "de", "pt", "es", "it", "nl", "id", "da", "ro", "el", "hr", "sl", "sr", "is", "vi"}, ".", ","),
    "space_comma": ({"fr", "pl", "cs", "sk", "ru", "uk", "sv", "fi", "nb", "no", "nn", "hu", "bg", "lt", "lv", "et",
                     "be", "kk"}, "\u202f", ","),
}


def format_number(value, lang, decimals=2):
    """value formatted for a language: en 3,815.07 · tr/de/pt 3.815,07 · fr/pl 3 815,07 (narrow no-break space)."""
    base = (lang or "en").split("-")[0].split("_")[0].lower()
    sep, dec = ",", "."
    for langs, s_, d_ in NUM_GROUPS.values():
        if base in langs:
            sep, dec = s_, d_
            break
    txt = f"{value:,.{decimals}f}"
    return txt.replace(",", "\x00").replace(".", dec).replace("\x00", sep)


def dedupe(findings):
    """Findings without repeats (the same gate can arrive from identity.audit and from a fresh card probe)."""
    seen, out = set(), []
    for f in findings or []:
        key = (f.get("id"), f.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def merged_findings(identity, card=None):
    """identity.audit findings plus a fresh card probe, deduplicated. A fresh probe replaces the audit's own
    `card` findings (they describe an older render)."""
    audit = (identity.get("audit") or {}).get("findings") or []
    if card is not None:
        audit = [f for f in audit if f.get("component") != "card"]
    return dedupe(list(audit) + list(card or []))


def shown_findings(identity, card=None):
    """What a card and the table show: findings of the components being made (new/refresh) plus the card's own.
    Kept or absent components are constraints; their notes appear once on the board (kept_notes)."""
    out = []
    for f in merged_findings(identity, card):
        comp = f.get("component")
        if comp == "card" or (comp in ("logo", "type", "palette") and mode_of(identity, comp) in ("new", "refresh")):
            out.append(f)
    return out


def kept_notes(identities):
    """{component: [findings]} for kept components across sets, deduplicated by message."""
    out = {}
    for ident in identities:
        for f in merged_findings(ident):
            comp = f.get("component")
            if comp in ("logo", "type", "palette") and mode_of(ident, comp) == "keep":
                lst = out.setdefault(comp, [])
                if all(x.get("message") != f.get("message") for x in lst):
                    lst.append(f)
    return out


def frame_font_css():
    """The fixed frame face for template UI labels (OFL, embedded as data URI in the mock templates)."""
    try:
        import palette_board
        return palette_board.font_faces()
    except Exception:  # noqa: BLE001 - labels fall back to system-ui
        return ""


FRAME_FAMILY = '"BI Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif'


def lang_of(brand):
    return ((brand.get("languages") or ["en"])[0]).split("-")[0].split("_")[0].lower()


def brand_copy(brand, t=None):
    """Brand-specific copy for mock applications: identity.brand.copy = {sentence, nav: [...], cta}. sentence may
    be a string (first brand language) or {lang: text}. Neutral, short fallbacks when absent, in the brand's first
    language (the mock is brand text and carries that lang): the shipped catalogue of that language, else English.
    t (i18nlib.Strings) is reused when its language is the brand's (so <work>/i18n.json applies)."""
    import i18nlib
    c = brand.get("copy") or {}
    sent = c.get("sentence")
    first = lang_of(brand)
    if isinstance(sent, dict):
        by_lang = {str(k).split("-")[0].lower(): v for k, v in sent.items() if v}
    elif isinstance(sent, str) and sent.strip():
        by_lang = {first: sent.strip()}
    else:
        by_lang = {}
    bt = t if t is not None and t.lang == first else i18nlib.Strings(first)
    nav = [str(x) for x in (c.get("nav") or []) if str(x).strip()][:4] or [bt("mock.menu")]
    return {"sentences": by_lang, "sentence": by_lang.get(first), "nav": nav,
            "cta": (c.get("cta") or bt("mock.contact")).strip(), "lang": first}


# ----------------------------------------------------------------------------- fonts

def _font_names(path):
    from fontTools.ttLib import TTFont
    with TTFont(path, lazy=True, fontNumber=0) as f:
        nm = f["name"]
        fams = {str(r.toUnicode()) for r in nm.names if r.nameID in (1, 16) and r.toUnicode()}
        wc = f["OS/2"].usWeightClass if "OS/2" in f else 400
        axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in f["fvar"].axes} if "fvar" in f else {}
    return fams, wc, axes


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _search_dirs(work):
    xdg = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    dirs = [os.path.join(work, ".cache", "fonts"), os.path.join(xdg, "brand-identity", "fonts")]
    if os.path.isdir(DEV_FONT_DIR):
        dirs.append(DEV_FONT_DIR)
    return [d for d in dirs if os.path.isdir(d)]


def _find_family(family, weight, work):
    best = None
    for d in _search_dirs(work):
        for root, _dirs, files in os.walk(d):
            if os.path.basename(root) == "instances":
                continue
            for fn in files:
                if not fn.lower().endswith((".ttf", ".otf")):
                    continue
                p = os.path.join(root, fn)
                try:
                    fams, wc, axes = _font_names(p)
                except Exception:  # noqa: BLE001 - unreadable file: skip
                    continue
                if _norm(family) not in {_norm(x) for x in fams}:
                    continue
                if "wght" in axes and axes["wght"][0] <= weight <= axes["wght"][2]:
                    dist = 0
                else:
                    dist = abs(wc - weight)
                if best is None or dist < best[0]:
                    best = (dist, p)
        if best and best[0] == 0:
            break
    return best[1] if best else None


def _fonts_json(set_dir, role, weight):
    p = os.path.join(set_dir, "type", "fonts.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    entry = (data.get("roles") or data).get(role) if isinstance(data, dict) else None
    if isinstance(entry, dict):
        files = entry.get("files")
        cand = None
        if isinstance(files, dict):
            cand = files.get(str(weight)) or next(iter(files.values()), None)
        elif isinstance(files, list) and files:
            cand = files[0] if isinstance(files[0], str) else files[0].get("path") or files[0].get("file")
        cand = cand or entry.get("file") or entry.get("path")
        if cand:
            cand = cand if os.path.isabs(cand) else os.path.join(set_dir, cand)
            return cand if os.path.isfile(cand) else None
    return None


def static_instance(path, location, cache_dir):
    """A variable font pinned at `location` (missing axes at their defaults) -> cached static TTF. Static fonts
    are returned unchanged."""
    from fontTools.ttLib import TTFont
    with TTFont(path, lazy=True) as probe:
        variable = "fvar" in probe
    if not variable:
        return path
    f = TTFont(path)
    axes = {a.axisTag: a for a in f["fvar"].axes}
    loc = {}
    for tag, a in axes.items():
        v = (location or {}).get(tag, a.defaultValue)
        loc[tag] = max(a.minValue, min(a.maxValue, float(v)))
    key = hashlib.sha256((os.path.abspath(path) + json.dumps(loc, sort_keys=True)).encode()).hexdigest()[:10]
    stem = re.sub(r"[^A-Za-z0-9-]+", "", os.path.splitext(os.path.basename(path))[0]) or "font"
    out = os.path.join(cache_dir, f"{stem}-{'-'.join(f'{k}{v:g}' for k, v in sorted(loc.items()))}-{key}.ttf")
    if os.path.isfile(out):
        return out
    from fontTools.varLib import instancer
    os.makedirs(cache_dir, exist_ok=True)
    try:
        inst = instancer.instantiateVariableFont(f, loc, overlap=instancer.OverlapMode.REMOVE)
    except Exception:  # noqa: BLE001 - overlap removal needs skia-pathops; keep overlaps rather than fail
        f.close()
        f = TTFont(path)
        inst = instancer.instantiateVariableFont(f, loc)
    inst.save(out + ".part")
    inst.close()
    f.close()
    os.replace(out + ".part", out)
    return out


def _typelib():
    if importlib.util.find_spec("typelib") is None:
        return None
    try:
        return importlib.import_module("typelib")
    except Exception:  # noqa: BLE001 - a half-built module must not break rendering
        return None


ROLES = ("display", "text", "mono")


def type_faces(identity):
    ty = identity.get("type") or {}
    return [(r, ty[r]) for r in ROLES if isinstance(ty.get(r), dict)]


def resolve_faces(identity, set_dir):
    """[{role, family, weight, path, location, how, note}] — one static file per role and weight."""
    set_dir = os.path.abspath(set_dir)
    work = identitylib.work_dir_of(set_dir)
    cache = os.path.join(work, ".cache", "fonts", "instances")
    tl = _typelib()
    out = []
    for role, face in type_faces(identity):
        fam = face["family"]
        for w in face.get("weights") or [400]:
            loc = dict(face.get("location") or {})
            loc["wght"] = w
            path, how, note = None, None, None
            if tl is not None and hasattr(tl, "resolve_font"):
                try:
                    fl = os.path.join(set_dir, face["file"]) if face.get("file") else None
                    path = str(tl.resolve_font(fam, location=loc, source=face.get("source"), file=fl))
                    how = "typelib"
                except Exception as exc:  # noqa: BLE001 - fall through to local files, say why
                    note = f"typelib.resolve_font failed: {exc}"
                    path = None
            if not path and face.get("file"):
                p = face["file"] if os.path.isabs(face["file"]) else os.path.join(set_dir, face["file"])
                if os.path.isfile(p):
                    path, how = p, "file"
            if not path:
                p = _fonts_json(set_dir, role, w)
                if p:
                    path, how = p, "fonts.json"
            if not path:
                p = _find_family(fam, w, work)
                if p:
                    path, how = p, "search"
            if path and how != "typelib":
                try:
                    _f, wc, axes = _font_names(path)
                    if not axes and abs(wc - w) >= 100:
                        note = f"{fam} file is weight {wc}, declared as {w} (no {w} file found)"
                    path = static_instance(path, loc, cache)
                except Exception as exc:  # noqa: BLE001
                    note = f"could not instance {path}: {exc}"
            out.append({"role": role, "family": fam, "weight": w, "path": path, "location": loc, "how": how,
                        "note": note, "fallback": face.get("fallback") or "sans-serif"})
    return out


def _url(path, html_dir):
    path = os.path.abspath(path)
    try:
        rel = os.path.relpath(path, html_dir) if html_dir else None
    except ValueError:  # Windows: font cache and work folder on different drives
        rel = None
    if rel and not rel.startswith(".." + os.sep + ".." + os.sep + ".." + os.sep + ".."):
        return quote(rel.replace(os.sep, "/"))
    p = path.replace("\\", "/")
    return "file://" + quote(p if p.startswith("/") else "/" + p, safe="/:")


def font_face_css(faces, html_dir):
    rules, seen = [], set()
    for f in faces:
        if not f["path"] or (f["family"], f["weight"]) in seen:
            continue
        seen.add((f["family"], f["weight"]))
        fmt = "opentype" if f["path"].lower().endswith(".otf") else "truetype"
        rules.append(f'@font-face{{font-family:"{f["family"]}";src:url("{_url(f["path"], html_dir)}") format("{fmt}");'
                     f'font-weight:{f["weight"]};font-style:normal;font-display:block;}}')
    return "\n".join(rules)


def css_family(face, generic="sans-serif"):
    fb = (face or {}).get("fallback") or generic
    return f'"{face["family"]}", {fb}' if face else f"system-ui, {generic}"


def role_vars(identity):
    """CSS custom properties for the roles: family, weight, tracking (em), case, features."""
    ty = identity.get("type") or {}
    disp, text = ty.get("display"), ty.get("text")
    out = {}
    for name, face, dflt in (("display", disp, 600), ("text", text, 400)):
        face = face or (text if name == "display" else disp)
        w = (face or {}).get("weights") or [dflt]
        out[f"--font-{name}"] = css_family(face)
        out[f"--{name}-weight"] = str(w[0])
        out[f"--{name}-weight-2"] = str(w[1] if len(w) > 1 else (700 if w[0] < 600 else w[0]))
        out[f"--{name}-track"] = f"{((face or {}).get('tracking') or 0) / 1000:.3f}em"
        case = (face or {}).get("case", "as-is")
        out[f"--{name}-case"] = {"upper": "uppercase", "lower": "lowercase"}.get(case, "none")
        feats = (face or {}).get("features") or []
        out[f"--{name}-features"] = ", ".join(f'"{x}"' for x in feats) if feats else "normal"
    return "".join(f"{k}:{v};" for k, v in out.items())


def expected_fonts(faces):
    return sorted({(f["family"], f["weight"]) for f in faces if f["path"]})


# ----------------------------------------------------------------------------- logo

LOGO_NAMES = {  # logolib masters first (logo/build/master-*.svg), then plain names (hand-made or older builds)
    "horizontal": ("master-lockup-horizontal.svg", "horizontal.svg", "lockup-horizontal.svg", "logo.svg"),
    "stacked": ("master-lockup-stacked.svg", "stacked.svg", "lockup-stacked.svg"),
    "symbol": ("master-symbol.svg", "symbol.svg", "mark.svg"),
    "wordmark": ("master-wordmark.svg", "wordmark.svg", "logotype.svg"),
    "monogram": ("master-monogram.svg", "monogram.svg"),
    "small": ("master-symbol-small.svg", "symbol-small.svg"),
    "master": ("master-primary.svg", "primary.svg"),
}


def logo_manifest(set_dir):
    p = os.path.join(set_dir, "logo", "build", "manifest.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def logo_svgs(set_info):
    """{'primary','horizontal','stacked','symbol','wordmark','monogram','small'} -> SVG text (missing keys absent).
    Reads logo/build/ (logolib output) then logo/ (model-authored master); for `keep`/`none` with an asset, the
    component's source file (SVG inlined; PNG/JPG as an <img> data path under 'image')."""
    sd, ident = set_info["dir"], set_info["identity"]
    found = {}
    for base in (os.path.join(sd, "logo", "build"), os.path.join(sd, "logo")):
        for key, names in LOGO_NAMES.items():
            if key in found:
                continue
            for n in names:
                p = os.path.join(base, n)
                if os.path.isfile(p):
                    found[key] = _read(p)
                    break
    lg = ident.get("logo") or {}
    if lg.get("symbol") and "symbol" not in found and os.path.isfile(os.path.join(sd, lg["symbol"])):
        found["symbol"] = _read(os.path.join(sd, lg["symbol"]))
    mode = mode_of(ident, "logo")
    src = ((ident.get("components") or {}).get("logo") or {}).get("source")
    if mode in ("keep", "none") and src and not found:
        p = src if os.path.isabs(src) else os.path.join(set_info["work"], src)
        if not os.path.isfile(p):
            p = os.path.join(sd, src)
        if os.path.isfile(p):
            if p.lower().endswith(".svg"):
                found["horizontal"] = _read(p)
            else:
                found["image"] = os.path.abspath(p)
    for key in ("master", "horizontal", "stacked", "wordmark", "symbol", "monogram"):
        if key in found:
            found["primary"] = found[key]
            break
    if "small" not in found:
        for key in ("symbol", "monogram"):
            if key in found:
                found["small"] = found[key]
                break
    return found


_PROLOG = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->", re.S)


def _svg_root_attrs(svg, extra):
    def rep(m):
        attrs = re.sub(r'\s(width|height|class|style)="[^"]*"', "", m.group(1))
        return f"<svg{attrs} {extra}>"
    return re.sub(r"<svg\b([^>]*)>", rep, svg, count=1)


def viewbox(svg):
    m = re.search(r'viewBox="([^"]+)"', svg or "")
    if not m:
        return None
    try:
        return [float(x) for x in re.split(r"[\s,]+", m.group(1).strip())]
    except ValueError:
        return None


def aspect(svg):
    vb = viewbox(svg)
    return (vb[2] / vb[3]) if vb and vb[3] else 1.0


def colorize(svg, pal, mode="light", default=None, mono=None, cls="logo-svg"):
    """Inline SVG with every data-color group filled from the palette role (or all in `mono`)."""
    if not svg:
        return ""
    svg = _PROLOG.sub("", svg).strip()
    svg = re.sub(r'\sid="[^"]*"', "", svg)  # several copies on one page: ids would collide

    def paint(m):
        tag = m.group(0)
        role = re.search(r'data-color="([^"]+)"', tag).group(1)
        hx = mono or role_hex(pal, role, mode) or default
        if not hx:
            return tag
        tag = re.sub(r'\sfill="[^"]*"', "", tag)
        return tag.replace("data-color=", f'fill="{hx}" data-color=', 1)
    svg = re.sub(r"<[a-zA-Z]+\b[^>]*data-color=\"[^\"]+\"[^>]*>", paint, svg)
    root_fill = mono or default or role_hex(pal, "text", mode) or "#111111"
    return _svg_root_attrs(svg, f'class="{cls}" fill="{root_fill}" aria-hidden="true" focusable="false"')


def logo_colors(svg):
    """Palette roles referenced by an SVG's data-color groups."""
    return sorted(set(re.findall(r'data-color="([^"]+)"', svg or "")))


def wordmark_metrics(svg):
    """(cap_height, baseline) in viewBox units from data-cap-height / data-baseline, else None."""
    m1 = re.search(r'data-cap-height="([\d.]+)"', svg or "")
    m2 = re.search(r'data-baseline="([\d.]+)"', svg or "")
    if m1 and m2:
        return float(m1.group(1)), float(m2.group(1))
    return None


def sample_sentences(langs, copy=None):
    """[(lang, sentence)] per brief language: the brand's own copy when given (brand_copy()['sentences']), else a
    neutral real sentence with that language's letters."""
    out = []
    for code in langs or ["en"]:
        base = code.split("-")[0].split("_")[0].lower()
        out.append((base, (copy or {}).get(base) or SAMPLES.get(base)))
    return out


def tint(hx, pct):
    """Print-style tint: pct% of the colour over white (sRGB mix)."""
    r, g, b = colorlib.parse_color(hx)
    a = pct / 100.0
    return colorlib.to_hex((r * a + 1 - a, g * a + 1 - a, b * a + 1 - a))
