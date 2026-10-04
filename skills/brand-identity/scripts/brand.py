#!/usr/bin/env python3
"""brand.py — the one entry point of the brand-identity skill.

Every command prints a short summary (data on stdout, diagnostics on stderr) and exits non-zero on failure.
Add --full for detail and --json for machine-readable output where offered. See docs/architecture.md section 4.1.

Examples:
  python3 brand.py check
  python3 brand.py init "Moonvault" --sets 3 --site https://example.com
  python3 brand.py fonts search --class sans --exclude-defaults
  python3 brand.py fonts audit "IBM Plex Sans" --langs en,tr --numbers
  python3 brand.py logos brand-identity/moonvault
  python3 brand.py build brand-identity/moonvault
  python3 brand.py site apply https://example.com brand-identity/moonvault
  python3 brand.py mix brand-identity/moonvault A:type B:palette --as D
  python3 brand.py kit brand-identity/moonvault/sets/A
  python3 brand.py critique --site https://example.com
"""
import argparse
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pydeps  # noqa: E402

if __name__ == "__main__":
    pydeps.ensure()  # missing packages: re-run in uv's cached environment (or the fallback venv)

import identitylib  # noqa: E402

SKILL = os.path.dirname(HERE)


_requirements = pydeps.requirements


def _utf8():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _fail(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _module(name, what):
    """Import a sibling module that implements a command; explain clearly when it is missing."""
    if importlib.util.find_spec(name) is None:
        _fail(f"{what} needs scripts/{name}.py, which is not installed in this copy of the skill")
    return importlib.import_module(name)


_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c", "ö": "o",
                      "Ö": "o", "ü": "u", "Ü": "u", "ß": "ss", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe",
                      "Œ": "oe", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "þ": "th", "Þ": "th"})


def slugify(name):
    """Folder name from a brand name; letters with diacritics fold to ASCII ("Şişdağı Çay" -> "sisdagi-cay")."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", name.translate(_FOLD))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return s or "brand"


# ---------------------------------------------------------------- check

def cmd_check(args):
    missing, lines = [], []
    for mod, _req, feature in pydeps.MODULES:
        ok = importlib.util.find_spec(mod) is not None
        lines.append(f"[{'ok' if ok else '--'}] python: {mod}" + ("" if ok else f"  (needed for {feature})"))
        if not ok:
            missing.append(mod)
    env = os.environ.get(pydeps.ENV_FLAG)
    if env:
        lines.append(f"[ok] python packages from {'uv (cached environment)' if env == 'uv' else pydeps.venv_dir()}")
    lines.append(_browser_line())
    browser_ok = lines[-1].startswith("[ok]")
    print("\n".join(lines))
    if missing:
        print("\n".join(pydeps.install_lines()))
    return 1 if (missing or not browser_ok) else 0


def _browser_line():
    """Start the browser for real (about:blank over DevTools, then close): finding it is not enough, a container
    can have Chromium that cannot start."""
    try:
        import render_png
        r = render_png.launch_check(timeout=20)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the health check
        return f"[--] browser: the start-up check failed ({exc})"
    if not r["browser"]:
        return "[--] browser  (Chrome, Chromium, Edge or Brave; needed for every render)"
    if r["ok"]:
        how = ("; started only with --no-sandbox: this system has no usable Chrome sandbox (typical in a container), "
               "so the skill runs the browser without it" if r["no_sandbox"] else "")
        return f"[ok] browser: {r['browser']} (started in {r['seconds']:g} s{how})"
    return (f"[--] browser: {r['browser']} does not start: {r['error']}\n"
            "     Install Google Chrome or Chromium (in a container: the distribution's chromium package and its "
            "libraries), or point BRAND_IDENTITY_BROWSER at a browser that starts.")


# ---------------------------------------------------------------- init

def _source_path(comp, val, work):
    """--source value -> path relative to the work folder. palette=#hex,#hex writes a locked partial palette."""
    if comp == "palette" and val.strip().startswith("#"):
        hexes = [h.strip() for h in val.split(",") if h.strip()]
        names = ("Primary", "Accent", "Ground", "Extra 1", "Extra 2")
        data = {"schema": "brand-identity/palette@1", "name": "Kept", "direction": "the brand's existing colours",
                "brand": [{"id": f"brand-{i + 1}", "name": names[min(i, 4)], "hex": h, "source": "chosen",
                           "locked": True} for i, h in enumerate(hexes)]}
        os.makedirs(os.path.join(work, "source"), exist_ok=True)
        out = os.path.join(work, "source", "palette-kept.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        return "source/palette-kept.json"
    p = os.path.abspath(os.path.expanduser(val))
    if not os.path.exists(p):
        _fail(f"--source {comp}: {val} not found")
    return p


def _sentences(values):
    """--sentence 'text' or --sentence tr='metin' (repeatable) -> str or {lang: text}."""
    if not values:
        return ""
    out = {}
    for v in values:
        lang, sep, text = v.partition("=")
        if sep and 1 < len(lang) <= 8 and lang.replace("-", "").replace("_", "").isalpha() and " " not in lang:
            out[lang] = text
        else:
            out["*"] = v
    if list(out) == ["*"]:
        return out["*"]
    return out


def _skeleton(brand, set_id, languages, states, numbers=False, sources=None, doc_lang=None):
    comps = {}
    for c in identitylib.COMPONENTS:
        mode = states.get(c, "new")
        comps[c] = {"mode": mode, "status": "fixed" if mode == "keep" else
                    ("not_applicable" if mode == "none" else "proposed"),
                    "source": (sources or {}).get(c), "sha256": None}
    return {
        "schema": identitylib.SCHEMA,
        "brand": {"name": brand, "tagline": "", "languages": languages, "doc_lang": doc_lang or languages[0],
                  "sector": "", "numbers": numbers},
        "set": {"id": set_id, "name": "", "recommended": False, "mechanism": "", "expression_move": "",
                "differs_by": ""},
        "axes": {},
        "components": comps,
        "type": None, "logo": None, "palette": "palette.json",
        "defaults_used": [], "rationale": [], "derived": [], "audit": None,
    }


def cmd_init(args):
    if not 1 <= args.sets <= 4:
        _fail("--sets must be 1..4 (3 is the default; 4 is outside the token budget)")
    states = {}
    for item in args.state or []:
        comp, _, mode = item.partition("=")
        if comp not in identitylib.COMPONENTS or mode not in identitylib.MODES:
            _fail(f"--state {item!r}: use component=mode with component in {identitylib.COMPONENTS} "
                  f"and mode in {identitylib.MODES}")
        states[comp] = mode
    work = os.path.abspath(os.path.join(args.root, slugify(args.name)))
    if os.path.exists(os.path.join(work, "brief.json")) and not args.force:
        _fail(f"{work} already exists; pass --force to rewrite brief.json (sets.json and set folders are kept; "
              "--reset-draft also replaces sets.json)")
    langs = [x.strip() for x in args.langs.split(",") if x.strip()] or ["en"]
    doc_lang = (args.doc_lang or langs[0]).strip()
    try:
        identitylib.validate_identity({"schema": identitylib.SCHEMA, "brand": {"name": args.name, "doc_lang": doc_lang},
                                       "set": {"id": "A"}, "components": {c: {"mode": "new", "status": "proposed"}
                                                                          for c in identitylib.COMPONENTS}}, partial=True)
    except identitylib.IdentityError as exc:
        _fail(f"--doc-lang {doc_lang!r}: {exc}")
    os.makedirs(os.path.join(work, ".cache"), exist_ok=True)
    comps = {c: states.get(c, "new") for c in identitylib.COMPONENTS}
    work_early = os.path.abspath(os.path.join(args.root, slugify(args.name)))
    sources = {}
    for item in args.source or []:
        comp, _, val = item.partition("=")
        if comp not in identitylib.COMPONENTS or not val:
            _fail(f"--source {item!r}: use logo=path, type=path or palette=path|#hex,#hex")
        sources[comp] = _source_path(comp, val, work_early)
    sentence = _sentences(args.sentence)
    split = lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]  # noqa: E731
    brief = {"schema": identitylib.BRIEF_SCHEMA, "brand": args.name, "tagline": args.tagline or "",
             "site": args.site, "languages": langs, "doc_lang": doc_lang, "numbers": args.numbers, "components": comps, "sources": sources,
             "sets": args.sets, "sector": args.sector or "", "audience": args.audience or "",
             "attributes": split(args.attributes), "competitors": split(args.competitors), "axes": [],
             "copy": {"sentence": sentence, "nav": split(args.nav), "cta": args.cta or ""}}
    with open(os.path.join(work, "brief.json"), "w", encoding="utf-8") as fh:
        json.dump(brief, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    draft = {"schema": identitylib.SETS_SCHEMA, "sets": []}
    made = []
    for i in range(args.sets):
        sid = chr(ord("A") + i)
        sdir = os.path.join(work, "sets", sid)
        os.makedirs(os.path.join(sdir, "logo"), exist_ok=True)
        path = os.path.join(sdir, "identity.json")
        if not os.path.exists(path):
            identitylib.save_identity(path, _skeleton(args.name, sid, langs, states, args.numbers, sources, doc_lang),
                                      partial=True)
        made.append(path)
        draft["sets"].append({"identity": {"set": {"id": sid, "name": "", "recommended": False, "mechanism": "",
                                                   "expression_move": "", "differs_by": ""},
                                           "axes": {}, "type": None, "logo": None, "palette_build": {},
                                           "defaults_used": [], "rationale": []},
                              "palette": None, "symbol_svg": None, "symbol_small_svg": None})
    draft_path = os.path.join(work, "sets.json")
    if not os.path.exists(draft_path) or getattr(args, "reset_draft", False):
        with open(draft_path, "w", encoding="utf-8") as fh:
            json.dump(draft, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    print(f"work: {work}")
    print(f"brief: {os.path.join(work, 'brief.json')}")
    print(f"draft: {draft_path}  (write every set here in one go; logos/build sync it into sets/X/)")
    for p in made:
        print(f"set: {p}")
    return 0


# ---------------------------------------------------------------- delegated commands

def _delegate(module, func, what):
    def run(args):
        mod = _module(module, what)
        return getattr(mod, func)(args)
    return run


def build_parser():
    ap = argparse.ArgumentParser(prog="brand.py", description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("Examples:")[1], formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="Python packages and browser")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("init", help="create a brand work folder with set skeletons")
    p.add_argument("name")
    p.add_argument("--sets", type=int, default=3)
    p.add_argument("--site")
    p.add_argument("--langs", default="en", help="comma list of language codes the brand writes in")
    p.add_argument("--doc-lang", help="language of the cards, board and kit text (default: the first of --langs); "
                                      "independent of the brand's languages")
    p.add_argument("--numbers", action="store_true", help="the product shows figures (tabular figures check)")
    p.add_argument("--tagline")
    p.add_argument("--sector", help="the brand's field, written in the document language (it shows on cards and kit)")
    p.add_argument("--audience")
    p.add_argument("--attributes", help="comma list, e.g. 'warm,crafted,local'")
    p.add_argument("--competitors", help="comma list of names or URLs")
    p.add_argument("--sentence", action="append",
                   help="real brand copy for specimens and mocks; per language: --sentence en='...' --sentence pt='...'")
    p.add_argument("--source", action="append", metavar="COMPONENT=PATH",
                   help="existing asset for keep/refresh/none: logo=file.svg, type=font file, "
                        "palette=palette.json or palette=#hex,#hex")
    p.add_argument("--nav", help="comma list of 3-4 menu words for the site-header mock, e.g. 'Menu,Visit,Story'")
    p.add_argument("--cta", help="call-to-action label for the mock, e.g. 'Book a table'")
    p.add_argument("--state", action="append", metavar="COMPONENT=MODE",
                   help="logo|type|palette = new|refresh|keep|none (repeatable; default new)")
    p.add_argument("--root", default="brand-identity")
    p.add_argument("--force", action="store_true", help="rewrite brief.json in an existing work folder")
    p.add_argument("--reset-draft", action="store_true", help="also replace sets.json with an empty draft")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("site", help="read or apply to a live site")
    p.add_argument("action", choices=["extract", "competitors", "apply"])
    p.add_argument("targets", nargs="+", help="extract URL WORK | competitors WORK URL... | apply URL WORK")
    p.add_argument("--sets")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=_delegate("site_preview", "cli_site", "brand.py site"))

    p = sub.add_parser("fonts", help="search the font catalogue or audit a family")
    p.add_argument("action", choices=["search", "audit"])
    p.add_argument("query", nargs="?")
    p.add_argument("--class", dest="klass",
                   help="sans, serif, slab, mono, display, handwriting, geometric, grotesque, neo-grotesque, humanist, "
                        "rounded, didone, transitional, old-style, condensed, wide (aliases: grotesk, neo-grotesk, "
                        "garalde, script, extended); --class list prints them")
    p.add_argument("--mood")
    p.add_argument("--exclude-defaults", action="store_true")
    p.add_argument("--langs", default="en")
    p.add_argument("--numbers", action="store_true")
    p.add_argument("--full", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_delegate("font_audit", "cli_fonts", "brand.py fonts"))

    p = sub.add_parser("logos", help="build marks for all sets and render one contact sheet")
    p.add_argument("work")
    p.add_argument("--sets")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=_delegate("logo_audit", "cli_logos", "brand.py logos"))

    p = sub.add_parser("build", help="full pipeline per set, cards, board and comparison table")
    p.add_argument("work")
    p.add_argument("--sets")
    p.add_argument("--full", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_delegate("pipeline", "cli_build", "brand.py build"))

    p = sub.add_parser("mix", help="new set from components of other sets")
    p.add_argument("work")
    p.add_argument("parts", nargs="+", help="e.g. A:logo A:type B:palette")
    p.add_argument("--as", dest="as_id", required=True)
    p.set_defaults(func=_delegate("pipeline", "cli_mix", "brand.py mix"))

    p = sub.add_parser("kit", help="brand guidelines kit for the chosen set")
    p.add_argument("set_dir")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=_delegate("kit_build", "cli_kit", "brand.py kit"))

    p = sub.add_parser("critique", help="audit an existing identity")
    p.add_argument("--site")
    p.add_argument("--logo")
    p.add_argument("--fonts")
    p.add_argument("--palette")
    p.add_argument("--langs", default="en")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=_delegate("pipeline", "cli_critique", "brand.py critique"))
    return ap


def main(argv=None):
    _utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except identitylib.IdentityError as e:
        _fail(str(e))
    except subprocess.CalledProcessError as e:
        _fail(f"{' '.join(map(str, e.cmd))} exited with {e.returncode}")


if __name__ == "__main__":
    sys.exit(main())
