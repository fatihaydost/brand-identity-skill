#!/usr/bin/env python3
"""Preview palette directions on the user's live site, or on mock pages when there is no site.

Live site (Python + a Chromium-based browser, see --check):
  python3 scripts/site_preview.py --url https://example.com --extract-only --out preview/
      -> preview/extract.json: role PROPOSAL with confidence. Confirm the roles with the user first.
  python3 scripts/site_preview.py --url https://example.com --palettes harbor.json ember.json --out preview/ --board
      -> reuses preview/extract.json (edited roles win), recolours the site per palette, builds the board.
  python3 scripts/site_preview.py --url https://example.com --palettes a.json b.json c.json --theme both \\
      --recommend B --brief "Decision tools for researchers" --out preview/ --board

No site (stdlib + any Chromium browser):
  python3 scripts/site_preview.py --mock landing,product --palettes a.json b.json --copy my-copy.json \\
      --brand-name "Sisdağı" --tagline "Karadeniz'den fındık" --ground kraft --out preview/ --board
  python3 scripts/site_preview.py --mock landing,app,card,social --palettes harbor.json ember.json \\
      --brand-name "Harbor & Vale" --tagline "Plan less, do more." --logo mark.svg --out preview/ --board
  python3 scripts/site_preview.py --mock landing --palettes new.json --current old.json --out preview/

  python3 scripts/site_preview.py --check          # what is installed, what is missing

--theme light|dark|both (default light) picks the palette mode(s); both puts the light and dark shots of every
direction on one board. --board writes board.html, board.png and audit.md (the technical checks).

Output (both cases): OUT/preview.json (manifest the board reads), OUT/current/ (the site as it is, or --current
palette), OUT/<palette-slug>/ per palette with desktop (1440x900) and mobile (390x844 @2x) PNGs. Live site:
desktop.png, mobile.png, full.png. Mocks: <template>-desktop.png, <template>-mobile.png. Dark adds a "-dark" suffix. Exit codes: 0 ok, 1 failure, 2 bad input, 3 browser or site tool missing,
5 the site blocks automated browsers (OUT/blocked.json).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_png  # noqa: E402

try:
    import colorlib  # noqa: E402
except ImportError:  # pragma: no cover - colorlib ships with the skill
    colorlib = None

SITE_DIR = os.path.join(HERE, "site")
SITE_TOOL = os.path.join(HERE, "site_palette.py")
TEMPLATE_DIR = os.path.normpath(os.path.join(HERE, "..", "templates", "mock-sites"))
TEMPLATES = ("landing", "app", "card", "social", "product")
COPY_DIR = os.path.join(TEMPLATE_DIR, "copy")
PRINT_TEMPLATES = ("product",)
ROLE_KEYS = getattr(colorlib, "ROLE_KEYS", (
    "background", "surface", "surfaceAlt", "border", "text", "textMuted", "primary", "onPrimary",
    "accent", "onAccent", "link", "focus", "success", "warning", "danger", "info"))
VIEWS = {"desktop": dict(width=1440, height=900, scale=1.0, mobile=False),
         "mobile": dict(width=390, height=844, scale=2.0, mobile=True)}
MOCK_FALLBACK = "python3 scripts/brand.py site apply mock WORK   (mock pages instead of the live site)"


def log(msg):
    print(msg, file=sys.stderr)


def fail(msg, code=1):
    log(f"error: {msg}")
    sys.exit(code)


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s).replace("ı", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "palette"


def kebab(key):
    return re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), key)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------- live-site tool

def site_problems():
    """List of human-readable problems blocking the live-site path (empty = ready)."""
    probs = []
    if not os.path.isfile(SITE_TOOL) or not os.path.isfile(os.path.join(SITE_DIR, "site_engine.js")):
        probs.append(f"the live-site tool is missing ({SITE_TOOL}); reinstall the skill")
    if not render_png.find_browser():
        probs.append(render_png.NO_BROWSER)
    return probs


def run_site(args, timeout=1800, expect=()):
    """Run site_palette.py; diagnostics stream to our stderr, the JSON summary is returned. Files in `expect`
    must exist afterwards: a tool that exits 0 without doing anything is an error, not a success."""
    proc = subprocess.run([sys.executable, SITE_TOOL, *args], stdout=subprocess.PIPE, stderr=None,
                          stdin=subprocess.DEVNULL, timeout=timeout)
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode == 5:
        fail("the site blocks automated browsers (bot wall / captcha; see blocked.json). Ask the user for screenshots "
             f"or their colours, or preview on mock pages: {MOCK_FALLBACK}", 5)
    if proc.returncode != 0:
        fail(f"site_palette.py {args[0]} failed (exit {proc.returncode}). If the site cannot be read headless, "
             f"fall back to mocks: {MOCK_FALLBACK}", 3 if proc.returncode in (3, 4) else 1)
    missing = [f for f in expect if not os.path.isfile(f)]
    if not out or missing:
        fail(f"site_palette.py {args[0]} exited 0 but produced " + (f"no {', '.join(missing)}" if missing else
             "no JSON summary") + f". The site tool did not run ({SITE_TOOL}); run `site_preview.py --check`.")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        fail(f"site_palette.py {args[0]} printed something that is not JSON: {out[:200]}")


def require_site():
    probs = site_problems()
    if probs:
        log("error: the live-site preview cannot run:")
        for p in probs:
            log(f"  - {p}")
        log(f"Not installing anything automatically. Preview on mock pages instead:\n  {MOCK_FALLBACK}")
        sys.exit(3)


# ----------------------------------------------------------------------------- palettes

def load_palette(path):
    if colorlib is not None and hasattr(colorlib, "load_palette"):
        try:
            return colorlib.load_palette(path)
        except colorlib.PaletteError as exc:
            fail(str(exc), 2)
    try:
        with open(path, encoding="utf-8") as fh:
            pal = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read palette {path}: {exc}", 2)
    if not isinstance(pal.get("modes"), dict) or "light" not in pal["modes"]:
        fail(f"{path}: modes.light is missing (palette.json contract, docs/architecture.md §3)", 2)
    return pal


def tokens_css(pal, mode, kraft=None):
    """--bi-* custom properties for one mode: roles + scales (what the mock templates consume), optional kraft."""
    lines = [f"  --bi-kraft: {kraft};"] if kraft else []
    roles = (pal.get("modes") or {}).get(mode) or {}
    for k in ROLE_KEYS:
        if roles.get(k):
            lines.append(f"  --bi-{kebab(k)}: {roles[k]};")
    for i, x in enumerate((pal.get("extended") or [])[:5], 1):  # extended colours: --bi-ext-N / --bi-on-ext-N
        src = (x.get("dark") or {}) if mode == "dark" else x
        hx, on = src.get("hex") or x.get("hex"), src.get("on") or x.get("on")
        n = str(x.get("id") or f"ext-{i}").replace("ext-", "")
        if hx:
            lines.append(f"  --bi-ext-{n}: {hx};")
        if on:
            lines.append(f"  --bi-on-ext-{n}: {on};")
    for name, sc in (pal.get("scales") or {}).items():
        for step, hx in ((sc or {}).get("steps") or {}).items():
            lines.append(f"  --bi-{kebab(name)}-{step}: {hx};")
    return ":root {\n" + "\n".join(lines) + "\n}"


def read_logo(path):
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            svg = fh.read()
    except OSError as exc:
        fail(f"cannot read logo {path}: {exc}", 2)
    svg = re.sub(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->", "", svg, flags=re.S | re.I).strip()
    if not svg.lower().startswith("<svg"):
        fail(f"{path} is not an SVG", 2)
    return svg


def load_copy(custom=None):
    """copy/en.json (the template defaults) merged with the user's own JSON, in any language (user keys win)."""
    copy = {}
    p = os.path.join(COPY_DIR, "en.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            copy.update(json.load(fh))
    if custom:
        try:
            with open(custom, encoding="utf-8") as fh:
                user = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"cannot read copy file {custom}: {exc}", 2)
        if not isinstance(user, dict):
            fail(f"{custom}: copy must be a flat JSON object {{\"key\": \"text\"}}", 2)
        copy.update(user)
    return copy


# Built logo versions carry clear-space padding; in a mock's logo slot the ink is cropped to its own box.
TRIM_LOGO_JS = ("<script>document.querySelectorAll('.brand-logo > svg').forEach(function(s){try{var b=s.getBBox();"
                "if(b.width&&b.height)s.setAttribute('viewBox',[b.x,b.y,b.width,b.height].join(' '))}catch(e){}});</script>")


def fill_template(tpl_html, pal, mode, brand, tagline, logo_svg, copy=None, kraft=None, fonts_css="", logo_mark_svg=None,
                  logo_mode=None, trim_logo=False):
    """Fill a mock template. fonts_css: identity @font-face + --bi-font-* variables (identity_fonts_css);
    logo_mark_svg: what square slots (monogram tile, avatar) show, default logo_svg; logo_mode "lockup" when logo_svg
    is the full logo with the name drawn in (the typed brand name next to it is then hidden)."""
    css = tokens_css(pal, mode, kraft)
    if '<style id="bi-tokens">{{TOKENS_CSS}}</style>' in tpl_html:
        html = tpl_html.replace('<style id="bi-tokens">{{TOKENS_CSS}}</style>', f'<style id="bi-tokens">{css}</style>')
    elif "{{TOKENS_CSS}}" in tpl_html:
        html = tpl_html.replace("{{TOKENS_CSS}}", css, 1)
    else:  # template without the placeholder: append the tokens last so they win
        html = re.sub(r"</head>", f'<style id="bi-tokens">{css}</style></head>', tpl_html, count=1, flags=re.I)
    if copy is not None:
        blob = json.dumps(copy, ensure_ascii=False).replace("</", "<\\/")
        tag = '<script type="application/json" id="bi-copy">{{COPY_JSON}}</script>'
        if tag in html:
            html = html.replace(tag, tag.replace("{{COPY_JSON}}", blob))
        else:
            html = html.replace("{{COPY_JSON}}", blob, 1)
    html = html.replace("{{TOKENS_CSS}}", "palette CSS").replace("{{COPY_JSON}}", "copy JSON")  # doc comments
    html = html.replace("{{BRAND_NAME}}", _esc(brand)).replace("{{TAGLINE}}", _esc(tagline))
    tag = '<style id="bi-fonts">{{FONTS_CSS}}</style>'
    if tag in html:
        html = html.replace(tag, f'<style id="bi-fonts">{fonts_css}</style>')
    elif fonts_css:  # template without the slot: append last so it wins
        html = re.sub(r"</head>", f'<style id="bi-fonts">{fonts_css}</style></head>', html, count=1, flags=re.I)
    html = html.replace("{{FONTS_CSS}}", "fonts CSS")  # doc comments
    html = html.replace("{{LOGO_MARK_SVG}}", logo_svg if logo_mark_svg is None else logo_mark_svg)
    html = html.replace("{{LOGO_SVG}}", logo_svg)  # empty string when absent: templates rely on :empty
    if logo_mode:
        html = re.sub(r"<html\b", f'<html data-logo="{logo_mode}"', html, count=1, flags=re.I)
    if trim_logo and (logo_svg or logo_mark_svg):
        html = re.sub(r"</body>", TRIM_LOGO_JS + "</body>", html, count=1, flags=re.I)
    if re.search(r"<html\b[^>]*\bdata-theme=", html, re.I):
        html = re.sub(r'(<html\b[^>]*\bdata-theme=)"[^"]*"', rf'\1"{mode}"', html, count=1, flags=re.I)
    else:
        html = re.sub(r"<html\b", f'<html data-theme="{mode}"', html, count=1, flags=re.I)
    left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
    if left:
        log(f"warning: unfilled placeholders {', '.join(left)}")
    return html


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# ----------------------------------------------------------------------------- mock path

def mock_preview(a):
    templates = [t.strip() for t in a.mock.split(",") if t.strip()]
    if not templates:
        fail("--mock needs at least one template name", 2)
    missing = [t for t in templates if not os.path.isfile(os.path.join(TEMPLATE_DIR, f"{t}.html"))]
    if missing:
        have = sorted(f[:-5] for f in os.listdir(TEMPLATE_DIR) if f.endswith(".html")) if os.path.isdir(TEMPLATE_DIR) else []
        fail(f"mock template(s) not found: {', '.join(missing)} in {TEMPLATE_DIR} (available: {', '.join(have) or 'none'})", 2)
    browser = render_png.find_browser()
    if not browser:
        fail(render_png.NO_BROWSER, 3)
    logo = read_logo(a.logo)
    copy = load_copy(a.copy)
    kraft = next((g["hex"] for g in a.grounds if g["name"].lower() == "kraft"), None)
    columns, jobs = [], []
    entries = ([("current", a.current)] if a.current else []) + [("direction", p) for p in a.palettes]
    used = set()
    for kind, path in entries:
        pal = load_palette(path)
        for th in a.themes:
            if th not in (pal.get("modes") or {}):
                fail(f"{path}: modes.{th} is missing", 2)
        slug = "current" if kind == "current" else slugify(pal.get("name") or os.path.basename(path)[:-5])
        if kind != "current" and slug == "current":
            slug = "current-palette"
        base, i = slug, 2
        while slug in used:
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        col_dir = os.path.join(a.out, slug)
        os.makedirs(col_dir, exist_ok=True)
        shots = {}
        for th in a.themes:
            sx = "-dark" if th == "dark" else ""
            tshots, extra = {}, []
            for t in templates:
                # a print scene (jar label, kraft box) has no dark mode: always render it with the light roles
                tmode = "light" if t in PRINT_TEMPLATES else th
                with open(os.path.join(TEMPLATE_DIR, f"{t}.html"), encoding="utf-8") as fh:
                    html = fill_template(fh.read(), pal, tmode, a.brand_name, a.tagline, logo, copy, kraft)
                page = os.path.join(col_dir, f"{t}{sx}.html")
                with open(page, "w", encoding="utf-8") as fh:
                    fh.write(html)
                t_shots = {}
                for view, spec in VIEWS.items():
                    rel = f"{slug}/{t}-{view}{sx}.png"
                    t_shots[view] = rel
                    jobs.append((page, os.path.join(a.out, rel), spec))
                if not tshots:
                    tshots = dict(t_shots, template=t)
                extra.append({"label": t, **t_shots})
            tshots["extra"] = extra[1:]
            shots[th] = tshots
        columns.append({"kind": kind, "name": pal.get("name") or slug, "slug": slug, "direction": pal.get("direction", ""),
                        "palette": os.path.abspath(path), "roles": {th: pal["modes"][th] for th in a.themes},
                        "shots": shots})

    def run(job):
        page, png, spec = job
        render_png.screenshot_html(page, png, spec["width"], spec["height"], spec["scale"], mobile=spec["mobile"],
                                   browser=browser)
        return png

    log(f"mock: rendering {len(jobs)} screenshots with {browser}")
    errors = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 2))) as ex:
        futs = [ex.submit(run, j) for j in jobs]
        for f, j in zip(futs, jobs):
            try:
                f.result()
            except Exception as exc:  # keep going; report all failures at the end
                errors.append(f"{os.path.basename(j[1])}: {exc}")
    if errors:
        fail("some mock renders failed:\n  " + "\n  ".join(errors))
    return {"source": "mock", "url": None, "templates": templates, "brandName": a.brand_name, "tagline": a.tagline,
            "columns": columns}


# ----------------------------------------------------------------------------- live-site path

def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def print_roles(ex, out):
    """The short role summary the agent reads (same as site_palette.py prints); details stay in extract.json."""
    det, kept = ex.get("roleDetails") or {}, set((ex.get("preservedRoles") or {}).get("light") or [])
    lines = [f"roles (proposal; confirm with the user, fix by editing 'roles' in {os.path.join(out, 'extract.json')}, "
             "then set \"rolesConfirmed\": true):"]
    for k, v in (ex.get("roles") or {}).items():
        d = det.get(k)
        how = "kept from your earlier edit" if k in kept else (d or {}).get("method", "set by hand")[:46]
        lines.append(f"  {k:<10} {v}  {d['confidence']:.2f}  {how}" if d and k not in kept else f"  {k:<10} {v}     -  {how}")
    if ex.get("rolesDark"):
        lines.append("  dark: " + " · ".join(f"{k} {ex['rolesDark'][k]}" for k in ("background", "surface", "text",
                                                                               "primary", "accent", "link") if ex["rolesDark"].get(k)))
    conf = [c for c in ex.get("confirm") or [] if c.split(" ")[0] not in kept]
    if conf:
        lines.append("confirm with the user: " + "; ".join(c[:70] for c in conf))
    for n in ex.get("notes") or []:
        lines.append("note: " + n[:170])
    print("\n".join(lines))


def site_preview(a):
    require_site()
    ext = a.roles or os.path.join(a.out, "extract.json")
    if a.re_extract or not os.path.isfile(ext):
        if a.roles and not os.path.isfile(a.roles):
            fail(f"roles file not found: {a.roles}", 2)
        ext = os.path.join(a.out, "extract.json")
        run_site(["extract", a.url, "--out", a.out, "--json"], expect=[ext])
        if not a.extract_only and not read_json(ext).get("rolesConfirmed"):
            log("warning: roles were extracted just now and are NOT confirmed; previews use the proposal as-is")
    directions = {}
    if not a.extract_only:
        for th in a.themes:
            sx = "-dark" if th == "dark" else ""
            args = ["apply", a.url, "--out", a.out, "--mode", th, "--roles", ext, "--json"]
            for p in a.palettes:
                args += ["--palette", p]
            if a.recolor_logo:
                args.append("--recolor-logo")
            summary = run_site(args)
            items = summary.get("palettes") or []
            if len(items) != len(a.palettes):
                fail(f"site_palette.py apply returned {len(items)} of {len(a.palettes)} palettes (no JSON summary on "
                     "stdout): the site tool did not run properly; see `site_preview.py --check`")
            for item in items:
                mpath = os.path.join(a.out, item["slug"], f"apply{sx}.json")
                if not os.path.isfile(mpath):
                    fail(f"expected {mpath} after apply, but it is missing")
                meta = read_json(mpath)
                col = directions.setdefault(meta["slug"], {
                    "kind": "direction", "name": meta["name"], "slug": meta["slug"], "direction": meta.get("direction", ""),
                    "palette": meta["palette"], "roles": {}, "shots": {}, "contrastGuard": {}, "pageScheme": {}})
                col["roles"][th] = meta["roles"]
                col["shots"][th] = {**meta["shots"], "extra": []}
                col["contrastGuard"][th] = meta.get("contrastGuard")
                col["pageScheme"][th] = meta.get("pageScheme")
    ex = read_json(ext)  # read after apply: a dark apply may have added the site's dark reading
    if a.extract_only:
        print_roles(ex, a.out)
    if "dark" in a.themes and not ex.get("rolesDark"):
        log("note: this site has no dark scheme we could read; dark previews recolour the light page")
    cur_shots = ex.get("shots") or {}
    host = re.sub(r"^www\.", "", re.sub(r"^[a-z]+://([^/]+).*$", r"\1", ex.get("url") or a.url))
    current = {"kind": "current", "name": host, "slug": "current", "notes": ex.get("notes") or [],
               "rolesConfirmed": bool(ex.get("rolesConfirmed")), "rolesConfirmedBy": ex.get("rolesConfirmedBy"),
               "direction": "", "palette": None, "roleDetails": ex.get("roleDetails") or {},
               "roles": {}, "shots": {}}
    for th in a.themes:
        dark = th == "dark"
        current["roles"][th] = (ex.get("rolesDark") if dark and ex.get("rolesDark") else ex.get("roles")) or {}
        pick = (lambda k: cur_shots.get(f"{k}-dark") or cur_shots.get(k)) if dark else cur_shots.get
        current["shots"][th] = {"desktop": pick("desktop"), "mobile": pick("mobile"), "full": pick("full"), "extra": []}
    signals = ex.get("signals") or {}
    return {"source": "site", "url": ex.get("url") or a.url, "title": signals.get("title"),
            "brandName": signals.get("siteName"), "extract": os.path.relpath(ext, a.out),
            "columns": [current] + list(directions.values())}


# ----------------------------------------------------------------------------- identity sets (brand.py site)
# `brand.py site extract|competitors|apply` (docs/architecture.md §4.1, §4.6). Everything lands in WORK/site/; stdout stays
# within SUMMARY_LINES lines (detail in the JSON files, --full prints everything).

SUMMARY_LINES = 15
MOCK_DEFAULT = ("landing", "app")
LOCKUP_TYPES = ("wordmark", "symbol+wordmark")
_LOGO_SKIP = re.compile(r"(^|[-_.])(black|mono|one-?colou?r|outline|sheet|contact|test|small|favicon|16|24|32)([-_.]|$)", re.I)


def _cap(lines, full=False):
    lines = [x for x in lines if x is not None]
    if full or len(lines) <= SUMMARY_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:SUMMARY_LINES - 1] + [f"... {len(lines) - SUMMARY_LINES + 1} more lines (--full)"])


def _first_family(stack):
    return (stack or "").split(",")[0].strip().strip("'\"") or "-"


def _font_axes(path):
    """{tag: (min, default, max)} from the sfnt 'fvar' table; {} for a static font, None when unreadable (woff)."""
    import struct
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None
    n = struct.unpack(">H", data[4:6])[0]
    for i in range(n):
        rec = 12 + i * 16
        if data[rec:rec + 4] != b"fvar":
            continue
        off = struct.unpack(">I", data[rec + 8:rec + 12])[0]
        axes_off, _, count, size = struct.unpack(">HHHH", data[off + 4:off + 12])
        axes = {}
        for j in range(count):
            a = off + axes_off + j * size
            tag = data[a:a + 4].decode("latin-1")
            mn, df, mx = (v / 65536 for v in struct.unpack(">iii", data[a + 4:a + 16]))
            axes[tag] = (mn, df, mx)
        return axes
    return {}


def _set_slug(identity, set_dir):
    st = identity.get("set") or {}
    sid = st.get("id") or os.path.basename(set_dir)
    return slugify(f"{sid}-{st.get('name') or 'Set ' + sid}")


def identity_fonts(set_dir, identity):
    """Faces a set brings, from type/fonts.json ({role: {family, file | files: [{file, location}], location}}),
    falling back to identity.type.<role>.file. Paths are absolute or relative to the set / work folder.
    Returns ({role: {family, location, files: [(path, axes, location)]}}, notes). keep/none type -> no faces."""
    comp = ((identity.get("components") or {}).get("type") or {}).get("mode", "new")
    ty = identity.get("type") or {}
    if comp not in ("new", "refresh") or not ty:
        return {}, [f"type: {comp}; existing fonts kept"]
    try:
        with open(os.path.join(set_dir, "type", "fonts.json"), encoding="utf-8") as fh:
            fj = json.load(fh)
    except (OSError, json.JSONDecodeError):
        fj = {}
    work = os.path.dirname(os.path.dirname(os.path.abspath(set_dir)))
    out, notes = {}, []
    for role in ("display", "text"):
        face, e = ty.get(role) or {}, fj.get(role) or {}
        loc = e.get("location") or face.get("location") or {}
        if isinstance(e.get("files"), list):
            entries = [x for x in e["files"] if isinstance(x, dict) and isinstance(x.get("file"), str)]
        elif e.get("file") or face.get("file"):
            entries = [{"file": e.get("file") or face.get("file"), "location": loc}]
        else:
            entries = []
        files = []
        for x in entries:
            cand = [x["file"]] if os.path.isabs(x["file"]) else [os.path.join(set_dir, x["file"]), os.path.join(work, x["file"])]
            path = next((c for c in cand if os.path.isfile(c)), None)
            if not path:
                notes.append(f"{role} font: {x['file']} not found; not applied")
                files = []
                break
            files.append((path, _font_axes(path), x.get("location") or loc))
        if not entries:
            notes.append(f"{role} font: no file in type/fonts.json yet; not applied")
        if files:
            out[role] = {"family": e.get("family") or face.get("family"), "location": loc, "files": files,
                         "tracking": face.get("tracking"), "case": face.get("case")}
    return out, notes


def identity_fonts_css(set_dir, identity):
    """@font-face rules (data: URIs, so the page needs no network or file access) + the --bi-font-* / --bi-fvs-*
    variables the mock templates read. Variable files keep their wght range; one static file covers every weight
    (no faux bold: bold text shows the file's own weight)."""
    import base64
    fonts, notes = identity_fonts(set_dir, identity)
    sid = ((identity.get("set") or {}).get("id") or "x").lower()
    css, root = [], []
    for role, f in fonts.items():
        name = f"BI {role} {sid}"
        for path, axes, loc in f["files"]:
            ext = os.path.splitext(path)[1].lower()
            mime, fmt = {".woff2": ("font/woff2", "woff2"), ".woff": ("font/woff", "woff"),
                         ".otf": ("font/otf", "opentype")}.get(ext, ("font/ttf", "truetype"))
            if axes and "wght" in axes:
                weight = f"{axes['wght'][0]:g} {axes['wght'][2]:g}"
            elif len(f["files"]) > 1:
                weight = str(round((loc or {}).get("wght", 400)))
            else:
                weight = "1 1000"
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            css.append(f"@font-face{{font-family:'{name}';font-style:normal;font-display:block;font-weight:{weight};"
                       f"src:url(data:{mime};base64,{b64}) format('{fmt}')}}")
        axes = f["files"][0][1]
        fvs = ", ".join(f'"{k}" {v:g}' for k, v in (f["location"] or {}).items()
                        if not (role == "text" and k == "wght") and (axes is None or k in axes))
        root.append(f"  --bi-font-{role}: '{name}', var(--font);")
        root.append(f"  --bi-fvs-{role}: {fvs or 'normal'};")
    if not fonts:
        return "", notes
    extra = []
    d = fonts.get("display")
    if d and isinstance(d.get("tracking"), (int, float)):
        extra.append(f"letter-spacing: {d['tracking'] / 1000:g}em;")
    if d and d.get("case") in ("upper", "lower"):
        extra.append(f"text-transform: {d['case']}case;")
    rules = ":root {\n" + "\n".join(root) + "\n}\nbody { font-optical-sizing: none; }"
    if extra:
        rules += "\nh1, h2, h3, .brand-word, .label .name { " + " ".join(extra) + " }"
    return "\n".join(css) + "\n" + rules, notes


def identity_logo(set_dir, identity):
    """(logo_svg, mark_svg, mode) for the mocks: logo/build/primary.svg (a lockup when the logo type draws the name),
    the symbol for square slots. keep/none logo or nothing built -> ("", "", None)."""
    lg = identity.get("logo") or {}
    comp = ((identity.get("components") or {}).get("logo") or {}).get("mode", "new")
    if comp not in ("new", "refresh"):
        return "", "", None
    build = os.path.join(set_dir, "logo", "build")

    def svg(path):
        if not path or not os.path.isfile(path):
            return ""
        txt = read_logo(path)
        txt = re.sub(r"<script\b.*?</script>", "", txt, flags=re.S | re.I)
        m = re.match(r"<svg\b[^>]*>", txt, re.I)
        if not m:
            return txt
        tag = m.group(0)
        if not re.search(r"\sviewBox\s*=", tag, re.I):  # keep the aspect when width/height go
            w = re.search(r"\swidth\s*=\s*[\"']?([\d.]+)", tag)
            h = re.search(r"\sheight\s*=\s*[\"']?([\d.]+)", tag)
            if w and h:
                tag = tag[:-1].rstrip("/") + f' viewBox="0 0 {w.group(1)} {h.group(1)}"' + ("/>" if tag.endswith("/>") else ">")
        tag = re.sub(r"\s(?:width|height)\s*=\s*(\"[^\"]*\"|'[^']*')", "", tag)  # CSS sizes it
        return tag + txt[m.end():]

    # logolib's painted versions first (full-color.svg, symbol-only.svg); primary.svg / symbol.svg = hand-made builds
    pick = lambda *names: next((p for p in names if p and os.path.isfile(p)), None)  # noqa: E731
    primary = svg(pick(os.path.join(build, "full-color.svg"), os.path.join(build, "primary.svg")))
    mark = svg(pick(os.path.join(build, "symbol-only.svg"), os.path.join(build, "symbol.svg")))
    try:
        manifest = read_json(os.path.join(build, "manifest.json"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if manifest.get("primary"):
        lockup = bool(primary) and manifest["primary"] not in ("symbol", "monogram")
    else:
        lockup = bool(primary) and lg.get("type") in LOCKUP_TYPES
    if lockup:
        return primary, mark, "lockup"
    return primary or mark, mark or primary, None


def _sets(work, only):
    import identitylib
    dirs = identitylib.set_dirs(work)
    if only:
        want = {x.strip().upper() for x in only.split(",") if x.strip()}
        dirs = [d for d in dirs if os.path.basename(d) in want]
    if not dirs:
        fail(f"no sets found in {work}/sets" + (f" matching --sets {only}" if only else "") + " (run brand.py init)", 2)
    return dirs


def _read_identity(set_dir):
    try:
        return read_json(os.path.join(set_dir, "identity.json"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {set_dir}/identity.json: {exc}", 2)


def _finding(id_, severity, message, measured=None, threshold=None, fix=None):
    import identitylib
    return identitylib.finding(id_, "site", severity, message, measured=measured, threshold=threshold, suggested_fix=fix)


def _site_findings(meta):
    """§3.5 findings from one set's apply.json. layoutStress > 0 is a warning (heuristic: any new overflow)."""
    out = []
    if meta.get("fonts") and not meta.get("fontsLoaded"):
        out.append(_finding("site-font-not-loaded", "gate", "a set font did not load on the site; the screenshot "
                            "would show a fallback face", measured=False, threshold=True,
                            fix="check type/fonts.json paths and the font file"))
    stress = meta.get("layoutStress") or {}
    total = sum(v for v in stress.values() if isinstance(v, int))
    if total:
        samples = []
        for vp in ("desktop", "mobile"):
            samples += ((meta.get("viewports") or {}).get(vp, {}).get("layoutStress") or {}).get("samples") or []
        out.append(_finding("site-layout-stress", "warn", f"{total} element(s) (desktop + mobile) overflow or gain "
                            f"lines with the new faces/logo (heuristic, uncalibrated): " + "; ".join(samples[:3]),
                            measured=stress, threshold=0,
                            fix="look at the screenshots; x-height is already matched with size-adjust, so this is "
                                "the face's width - consider a narrower text face or a looser site layout"))
    for vp in ("desktop", "mobile"):
        v = (meta.get("viewports") or {}).get(vp) or {}
        for role, r in ((v.get("fonts") or {}).get("roles") or {}).items():
            if r.get("ok") and not r.get("applied"):
                out.append(_finding(f"site-font-unused-{role}", "warn", f"{vp}: the {role} face loaded but no {role} "
                                    "text on the site uses it (the site's family was not matched)",
                                    fix="correct fonts in site/extract.json"))
        lg = v.get("logo") or {}
        if lg.get("replaced") and isinstance(lg.get("fill"), (int, float)) and lg["fill"] < 0.5:
            out.append(_finding(f"site-logo-small-{vp}", "info", f"{vp}: the logo fills {lg['fill']:.0%} of the site's "
                                f"logo box (aspect mismatch; heuristic)", measured=lg["fill"], threshold=0.5,
                                fix="a symbol or stacked variant in logo/build/ fits a square slot"))
        if meta.get("logoVariants") and not lg.get("replaced"):
            out.append(_finding(f"site-logo-kept-{vp}", "info", f"{vp}: site logo not replaced ({lg.get('reason')})"))
    return out


def _work_and_url(targets):
    """apply targets: [URL] WORK. URL may be 'mock' or 'mock:landing,app'; no URL -> brief.site, else mocks."""
    if len(targets) == 1:
        work = os.path.abspath(targets[0])
        try:
            url = read_json(os.path.join(work, "brief.json")).get("site")
        except (OSError, json.JSONDecodeError):
            url = None
        return work, url or "mock"
    if len(targets) != 2:
        fail("site apply takes [URL] WORK", 2)
    return os.path.abspath(targets[1]), targets[0]


def _site_extract(args, work, url):
    out = os.path.join(work, "site")
    os.makedirs(out, exist_ok=True)
    require_site()
    run_site(["extract", url, "--out", out, "--json"], expect=[os.path.join(out, "extract.json")])
    ex = read_json(os.path.join(out, "extract.json"))
    roles, det = ex.get("roles") or {}, ex.get("roleDetails") or {}
    lines = [f"extract {ex.get('url') or url} -> {os.path.join(out, 'extract.json')} (roles are a proposal; "
             "confirm, fix 'roles', set \"rolesConfirmed\": true)"]
    if ex.get("status") == "empty":
        lines.append("WARNING: " + (ex.get("notes") or ["page looks empty; pass the full URL"])[0][:200])
    items = [f"{k} {v}" + (f" {det[k]['confidence']:.2f}" if k in det else "") for k, v in roles.items()]
    for i in range(0, len(items), 4):
        lines.append("  " + " · ".join(items[i:i + 4]))
    f = ex.get("fonts") or {}
    lines.append(f"fonts: heading {_first_family(f.get('heading'))} · body {_first_family(f.get('paragraph') or f.get('body'))}"
                 f" · {len(f.get('loaded') or [])} faces loaded")
    lg = ex.get("logo")
    if lg and lg.get("source"):
        lines.append(f"logo: {lg['kind']} {round(lg['box']['w'])}x{round(lg['box']['h'])} -> {os.path.join(out, lg['source'])}"
                     + (f" (+ {lg['sourceDark']})" if lg.get("sourceDark") else "") + " (kept-logo source)")
    else:
        lines.append(f"logo: {lg['kind']} {round(lg['box']['w'])}x{round(lg['box']['h'])} (score {lg['score']})"
                     + (f" -> {os.path.join(out, lg['file'])} (screenshot only)" if lg.get("file") else "") if lg else "logo: not found")
    if ex.get("confirm"):
        lines.append("confirm: " + "; ".join(c[:60] for c in ex["confirm"])[:240])
    for n in (ex.get("notes") or [])[1 if ex.get("status") == "empty" else 0:][:3]:
        lines.append("note: " + n[:170])
    lines.append(f"shots: {out}/current/(desktop|mobile|full|header).png")
    print(_cap(lines, args.full))
    return 0


def _site_competitors(args, work, urls):
    out = os.path.join(work, "site")
    os.makedirs(out, exist_ok=True)
    require_site()
    for u in urls:
        if not re.match(r"^(https?|file)://", u):
            fail(f"not an http(s) or file URL: {u}", 2)
    run_site(["competitors", *urls, "--out", out, "--json"], expect=[os.path.join(out, "competitors.json")])
    d = read_json(os.path.join(out, "competitors.json"))
    print(_cap(competitor_lines(d, out), args.full))
    return 0


def competitor_lines(d, out):
    """One line per competitor: domain · status (ok / parked / blocked / error) · dominant colour · heading / body font.
    Parked, blocked and failed sites are named and skipped: they are never part of the competitor map."""
    errors = d.get("errors") or []
    status = lambda e: e.get("status") or ("blocked" if e.get("blocked") else "error")  # noqa: E731
    count = {k: sum(status(e) == k for e in errors) for k in ("parked", "blocked", "error")}
    lines = [f"competitors: {len(d.get('sites') or [])} ok, {count['parked']} parked, {count['blocked']} blocked, "
             f"{count['error']} failed -> {os.path.join(out, 'competitors.json')}"]
    if errors:
        total = len(d.get("sites") or []) + len(errors)
        lines.append(f"{len(d.get('sites') or [])} of {total} competitors measured; positioning falls back to the brief")
    for st in d.get("sites") or []:
        r, f = st.get("roles") or {}, st.get("fonts") or {}
        host = st.get("host") or os.path.basename((st.get("url") or "").rstrip("/"))
        dom = r.get("primary") or next((c.get("hex") for c in st.get("topColors") or [] if c.get("hex")), None) or "-"
        lines.append(f"  {host[:24]:<24} · ok · {dom} · {_first_family(f.get('heading'))[:18]} / "
                     f"{_first_family(f.get('paragraph') or f.get('body'))[:18]}")
    for e in errors:
        host = e.get("host") or e.get("url") or ""
        word = {"parked": "parked, skipped", "blocked": "blocked, skipped"}.get(status(e), "error, skipped")
        lines.append(f"  {host[:24]:<24} · {word}: {(e.get('reason') or e.get('error') or '')[:60]}")
    return lines


def _apply_live(args, work, url, dirs):
    out = os.path.join(work, "site")
    os.makedirs(out, exist_ok=True)
    require_site()
    cmd = ["apply", url, "--out", out, "--json"]
    ext = os.path.join(out, "extract.json")
    if os.path.isfile(ext):
        cmd += ["--roles", ext]
    for d in dirs:
        cmd += ["--identity", os.path.join(d, "identity.json")]
    summary = run_site(cmd)
    items = summary.get("sets") or []
    if len(items) != len(dirs):
        fail(f"site_palette.py apply returned {len(items)} of {len(dirs)} sets; see stderr")
    sets = {}
    for it in items:
        meta = read_json(os.path.join(it["dir"], "apply.json"))
        sets[it["set"]] = {"name": it["name"], "dir": os.path.relpath(it["dir"], work),
                           "shots": {k: os.path.join("site", v) for k, v in meta["shots"].items()},
                           "layoutStress": meta["layoutStress"], "fontsLoaded": meta["fontsLoaded"],
                           "logo": it.get("logo"), "notes": meta.get("notes") or [], "findings": _site_findings(meta)}
    return {"source": "site", "url": url, "current": {k: os.path.join("site", v) for k, v in
                                                      ((read_json(ext).get("shots") or {}) if os.path.isfile(ext) else {}).items()},
            "sets": sets}


def _apply_mock(args, work, templates, dirs):
    out = os.path.join(work, "site")
    browser = render_png.find_browser()
    if not browser:
        fail(render_png.NO_BROWSER, 3)
    missing = [t for t in templates if not os.path.isfile(os.path.join(TEMPLATE_DIR, f"{t}.html"))]
    if missing:
        fail(f"mock template(s) not found: {', '.join(missing)} (available: {', '.join(TEMPLATES)})", 2)
    copy = load_copy(None)
    sets, jobs = {}, []
    for d in dirs:
        ident = _read_identity(d)
        sid = (ident.get("set") or {}).get("id") or os.path.basename(d)
        brand = ident.get("brand") or {}
        pal_path = os.path.join(d, ident.get("palette") or "palette.json")
        pal_mode = ((ident.get("components") or {}).get("palette") or {}).get("mode", "new")
        try:
            pal = read_json(pal_path) if pal_mode != "none" else None
        except (OSError, json.JSONDecodeError):
            pal = None
        if pal_mode != "none" and not (pal and (pal.get("modes") or {}).get("light")):
            sets[sid] = {"name": (ident.get("set") or {}).get("name"), "error": f"{pal_path} is not built (no modes.light); "
                         "run brand.py build first", "findings": []}
            continue
        if pal is None:  # no palette in this set: the example's neutral roles keep the mock readable
            pal = load_palette(os.path.join(os.path.dirname(TEMPLATE_DIR), "palette.example.json"))
        fonts_css, notes = identity_fonts_css(d, ident)
        logo, mark, logo_mode = identity_logo(d, ident)
        slug = _set_slug(ident, d)
        col = os.path.join(out, slug)
        os.makedirs(col, exist_ok=True)
        shots = {}
        for t in templates:
            with open(os.path.join(TEMPLATE_DIR, f"{t}.html"), encoding="utf-8") as fh:
                html = fill_template(fh.read(), pal, "light", brand.get("name") or "Your Brand", brand.get("tagline") or "",
                                     logo, copy, fonts_css=fonts_css, logo_mark_svg=mark, logo_mode=logo_mode,
                                     trim_logo=True)
            page = os.path.join(col, f"{t}.html")
            with open(page, "w", encoding="utf-8") as fh:
                fh.write(html)
            for view, spec in VIEWS.items():
                rel = os.path.join("site", slug, f"{t}-{view}.png")
                shots[f"{t}-{view}"] = rel
                jobs.append((page, os.path.join(work, rel), spec))
        if not logo:
            notes.append("logo: nothing built in logo/build/ yet; mock shows the typed name")
        sets[sid] = {"name": (ident.get("set") or {}).get("name"), "dir": os.path.relpath(col, work), "shots": shots,
                     "layoutStress": None, "fontsLoaded": None, "logo": logo_mode or ("symbol" if logo else None),
                     "notes": notes, "findings": []}

    def run(job):
        page, png, spec = job
        render_png.screenshot_html(page, png, spec["width"], spec["height"], spec["scale"], mobile=spec["mobile"],
                                   browser=browser)

    errors = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 2))) as ex:
        for fut, job in [(ex.submit(run, j), j) for j in jobs]:
            try:
                fut.result()
            except Exception as exc:  # report every failed render
                errors.append(f"{os.path.basename(job[1])}: {exc}")
    if errors:
        fail("some mock renders failed:\n  " + "\n  ".join(errors))
    return {"source": "mock", "templates": list(templates), "sets": sets}


def merge_site_apply(path, body):
    """`build --sets B` re-applies one set: the sets of an earlier apply.json on the same source (same URL, or mocks)
    stay, the re-applied ones are replaced. Merges into body["sets"] in place; returns the ids kept from before."""
    try:
        old = read_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    same = old.get("source") == body.get("source") and (body.get("source") == "mock" or old.get("url") == body.get("url"))
    if not same or not isinstance(old.get("sets"), dict):
        return []
    kept = sorted(k for k in old["sets"] if k not in body["sets"])
    body["sets"] = dict(sorted({**{k: old["sets"][k] for k in kept}, **body["sets"]}.items()))
    if body.get("source") == "mock":
        body["templates"] = sorted(set(old.get("templates") or []) | set(body.get("templates") or []))
    return kept


def _rerender_board(work):
    """Re-render the board after a site apply when the board module is installed (§4.1); never fatal."""
    import importlib.util
    if importlib.util.find_spec("identity_board") is None:
        return None
    try:
        import identity_board
        res = identity_board.render(work) or {}
        return res.get("png") or res.get("html") or "board re-rendered"
    except Exception as exc:  # the site shots are still valid without a board
        log(f"note: board not re-rendered: {exc}")
        return None


def cli_site(args):
    """brand.py site extract URL WORK | competitors WORK URL... | apply [URL|mock[:t1,t2]] WORK [--sets A,B]."""
    if colorlib is not None and hasattr(colorlib, "setup_utf8_console"):
        colorlib.setup_utf8_console()
    t = list(args.targets)
    if args.action == "extract":
        if len(t) != 2:
            fail("site extract takes URL WORK", 2)
        if not re.match(r"^(https?|file)://", t[0]):
            fail(f"not an http(s) or file URL: {t[0]}", 2)
        return _site_extract(args, os.path.abspath(t[1]), t[0])
    if args.action == "competitors":
        if len(t) < 2:
            fail("site competitors takes WORK URL [URL ...]", 2)
        return _site_competitors(args, os.path.abspath(t[0]), t[1:])
    work, url = _work_and_url(t)
    dirs = _sets(work, getattr(args, "sets", None))
    if url.startswith("mock"):
        tpls = [x.strip() for x in url.partition(":")[2].split(",") if x.strip()] or list(MOCK_DEFAULT)
        body = _apply_mock(args, work, tpls, dirs)
    else:
        if not re.match(r"^(https?|file)://", url):
            fail(f"not an http(s) or file URL: {url} (or 'mock[:landing,app]')", 2)
        body = _apply_live(args, work, url, dirs)
    path = os.path.join(work, "site", "apply.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kept = merge_site_apply(path, body)
    manifest = {"schema": "brand-identity/site-apply@1", "createdAt": now(), **body}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    lines = [f"site apply ({body['source']}{': ' + body['url'] if body.get('url') else ''}) -> {path}"]
    gates = 0
    for sid, st in ((k, v) for k, v in body["sets"].items() if k not in kept):
        if st.get("error"):
            lines.append(f"  {sid} {st.get('name') or ''}: FAILED {st['error']}")
            gates += 1
            continue
        if body["source"] == "site":
            ls = st["layoutStress"]
            lines.append(f"  {sid} {st.get('name') or ''}: layoutStress {ls.get('desktop')} desktop, {ls.get('mobile')} mobile"
                         f" · fonts {'loaded' if st['fontsLoaded'] else 'NOT LOADED'}"
                         f" · logo {(st.get('logo') or {}).get('desktop') or 'kept'} -> {st['dir']}/")
        else:
            lines.append(f"  {sid} {st.get('name') or ''}: {len(st['shots'])} mock shots · logo {st.get('logo') or 'none'}"
                         f" -> {st['dir']}/")
        for f in st["findings"]:
            if f["severity"] in ("gate", "warn"):
                lines.append(f"    {f['severity']}: {f['message'][:150]}")
            gates += f["severity"] == "gate"
        for n in st.get("notes") or []:
            if "not found" in n or "not applied" in n:
                lines.append(f"    note: {n[:150]}")
    if kept:
        lines.append(f"  kept from the earlier apply: set {', '.join(kept)}")
    board = _rerender_board(work)
    if board:
        lines.append(f"board: {board}")
    print(_cap(lines, getattr(args, "full", False)))
    return 1 if gates and gates >= len(body["sets"]) - len(kept) else 0


# ----------------------------------------------------------------------------- main

def site_check():
    """Run `site_palette.py check` (a real extract on a bundled offline page) through the same path we call."""
    try:
        proc = subprocess.run([sys.executable, SITE_TOOL, "check"], capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"`site_palette.py check` printed no JSON (exit {proc.returncode}); "
                                      "the tool did not run"}


def check():
    probs = site_problems()
    browser = render_png.find_browser()
    sc = site_check() if not probs else None
    if sc is not None and not sc.get("ok"):
        st = sc.get("selfTest") or {}
        probs.append("live-site self-test failed: " + (sc.get("error") or st.get("error") or
                     "; ".join(st.get("wrong") or []) or sc.get("browserError") or "see siteCheck"))
    print(json.dumps({"siteTool": SITE_TOOL, "browserForMocks": browser, "livePreviewReady": not probs,
                      "mockPreviewReady": bool(browser), "problems": probs, "siteCheck": sc,
                      "templates": sorted(f[:-5] for f in os.listdir(TEMPLATE_DIR) if f.endswith(".html"))
                      if os.path.isdir(TEMPLATE_DIR) else []}, indent=2))
    if probs:
        log("live-site preview not ready:\n  - " + "\n  - ".join(probs))
    return 0 if (not probs and browser) else 3


def main(argv=None):
    if colorlib is not None and hasattr(colorlib, "setup_utf8_console"):
        colorlib.setup_utf8_console()
    else:
        render_png.setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="live site to read and recolour")
    src.add_argument("--mock", help=f"comma list of mock templates ({', '.join(TEMPLATES)})")
    src.add_argument("--check", action="store_true", help="report browser and live-site tool status and exit")
    ap.add_argument("--palettes", nargs="+", default=[], metavar="PALETTE.json", help="brand-identity/palette@1 files")
    ap.add_argument("--out", default="preview", help="output folder (default: ./preview)")
    ap.add_argument("--theme", choices=("light", "dark", "both"), help="which palette mode(s) to preview (default light; "
                    "both = light and dark shots side by side on one board)")
    ap.add_argument("--mode", choices=("light", "dark"), help="alias of --theme light|dark")
    ap.add_argument("--roles", help="[site] roles file (default OUT/extract.json; edit it to correct roles)")
    ap.add_argument("--extract-only", action="store_true", help="[site] only read the site and print the role proposal")
    ap.add_argument("--re-extract", action="store_true", help="[site] read the site again even if extract.json exists")
    ap.add_argument("--recolor-logo", action="store_true", help="[site] also recolour an inline-SVG logo")
    ap.add_argument("--current", help="[mock] existing palette.json shown as the 'current' column")
    ap.add_argument("--brand-name", default="Your Brand", help="[mock] brand name in the templates")
    ap.add_argument("--tagline", default="A short line about what you do.", help="[mock] tagline")
    ap.add_argument("--logo", help="[mock] symbol SVG drawn with currentColor (optional)")
    ap.add_argument("--copy", help="[mock] JSON with your own text in any language {\"key\": \"text\", \"_lang\": \"tr\"} "
                    "(templates/mock-sites/copy/README.md)")
    ap.add_argument("--ground", action="append", metavar="NAME[=#HEX]",
                    help="print ground for the board's print check and the product mock (bare 'kraft' = "
                    "palette_audit's documented approximation)")
    ap.add_argument("--board", action="store_true", help="also build board.html + board.png + audit.md")
    ap.add_argument("--brief", help="one-line brief shown under the board title")
    ap.add_argument("--recommend", help="letter, name or slug of the recommended direction (framed on the board)")
    ap.add_argument("--json", action="store_true", help="print the manifest JSON on stdout")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    if not a.url and not a.mock:
        ap.error("give --url URL or --mock TEMPLATES (or --check)")
    if not a.palettes and not (a.url and a.extract_only):
        ap.error("--palettes is required (except with --url --extract-only)")
    for p in a.palettes + ([a.current] if a.current else []):
        if not os.path.isfile(p):
            fail(f"palette not found: {p}", 2)
    if a.url and not re.match(r"^(https?|file)://", a.url):
        fail(f"--url must start with http://, https:// or file:// ({a.url})", 2)
    theme = a.theme or a.mode or "light"
    a.themes = ["light", "dark"] if theme == "both" else [theme]
    a.grounds = []
    for g in a.ground or []:
        try:
            from palette_audit import AuditError, parse_ground
            a.grounds.append(parse_ground(g))
        except AuditError as exc:
            fail(str(exc), 2)
    os.makedirs(a.out, exist_ok=True)
    body = site_preview(a) if a.url else mock_preview(a)
    manifest = {"schema": "brand-identity/preview@2", "createdAt": now(), "themes": a.themes,
                "grounds": [{"name": g["name"], "hex": g["hex"]} for g in a.grounds],
                "brief": a.brief, "recommend": a.recommend, **body}
    path = os.path.join(a.out, "preview.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    log(f"wrote {path} ({len(manifest['columns'])} columns)")
    if a.board and len(manifest["columns"]) > 0:
        import palette_board
        html, png = palette_board.build(a.out, png=True)
        log(f"board: {png or html}")
    if a.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print(os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
