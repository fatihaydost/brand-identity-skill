"""The identity.json contract: load, validate, save, and walk a brand work folder.

identity.json (schema "brand-identity/identity@1") is the single source of truth for one identity set: the set's
idea, its axis position, the state of each component (logo, type, palette), the chosen typefaces, the logo build
instructions and a pointer to the set's palette.json. Every script that renders or audits a set reads it from here;
values are never hand-copied between files. See docs/architecture.md section 3.

Public API (names are a contract, docs/architecture.md section 11):
  load_identity, save_identity, validate_identity, set_dirs, work_dir_of, finding, IdentityError,
  SCHEMA, COMPONENTS, MODES, STATUSES, AXES, LOGO_TYPES, PAIRING_MODES, FONT_SOURCES.
Standard library only.
"""
import json
import os
import re

__all__ = [
    "load_identity", "save_identity", "validate_identity", "set_dirs", "work_dir_of", "IdentityError",
    "finding", "SCHEMA", "AUDIT_SCHEMA", "BRIEF_SCHEMA", "SETS_SCHEMA", "SEVERITIES", "FINDING_COMPONENTS", "COMPONENTS", "MODES", "STATUSES", "AXES", "LOGO_TYPES", "PAIRING_MODES", "FONT_SOURCES",
    "MISUSE_GENERIC", "MISUSE_DEFAULT",
]

SCHEMA = "brand-identity/identity@1"
COMPONENTS = ("logo", "type", "palette")
MODES = ("new", "refresh", "keep", "none")
STATUSES = ("proposed", "fixed", "not_applicable")
# Decision axes, scored -2..+2. The first pole is -2, the second +2 (references/cohesion.md).
AXES = {
    "warm_cool": ("warm", "cool"),
    "classic_contemporary": ("classic", "contemporary"),
    "playful_serious": ("playful", "serious"),
    "premium_accessible": ("premium", "accessible"),
    "quiet_loud": ("quiet", "loud"),
    "friend_authority": ("friend", "authority"),
}
LOGO_TYPES = ("wordmark", "symbol+wordmark", "symbol", "monogram")  # emblem is out of v1
PAIRING_MODES = ("single-family", "superfamily", "contrast", "same-designer", "data-face")
FONT_SOURCES = ("google", "fontshare", "commercial", "user")
CASES = ("as-is", "upper", "lower")
LOCKUPS = ("horizontal", "stacked")
EVIDENCE = ("strong", "limited", "practice", "measured", "judgement")  # measured: a script or site reading
CLEAR_SPACE_UNITS = ("cap-height", "symbol-half", "x-height")
# Kit misuse page (docs/architecture.md section 7.3): generic items the kit draws from the real logo. crowd = clear
# space broken by a neighbour; rearrange = symbol and wordmark swapped/rescaled (symbol+wordmark logos only).
MISUSE_GENERIC = ("stretch", "rotate", "recolour", "effects", "outline", "low-contrast", "crowd", "rearrange")
MISUSE_DEFAULT = ("stretch", "rotate", "recolour", "effects", "outline", "low-contrast")  # when logo.misuse is absent
_LANG_CODE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")

AUDIT_SCHEMA = "brand-identity/audit@1"
SEVERITIES = ("gate", "warn", "info")
FINDING_COMPONENTS = ("logo", "type", "palette", "cohesion", "card", "kit", "site")
BRIEF_SCHEMA = "brand-identity/brief@1"
SETS_SCHEMA = "brand-identity/sets@1"

_SET_ID = re.compile(r"^[A-Z]$")


class IdentityError(ValueError):
    """identity.json does not match the brand-identity/identity@1 contract. The message names the exact field."""


def _need(cond, path, msg):
    if not cond:
        raise IdentityError(f"{path}: {msg}")


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _check_face(face, where):
    _need(isinstance(face, dict), where, "must be an object")
    _need(_is_str(face.get("family")), f"{where}.family", "required non-empty string")
    _need(face.get("source") in FONT_SOURCES, f"{where}.source", f"must be one of {', '.join(FONT_SOURCES)}")
    _need(_is_str(face.get("license")), f"{where}.license", "required (e.g. OFL-1.1, ITF-FFL-2.0, commercial)")
    weights = face.get("weights")
    _need(isinstance(weights, list) and weights and all(isinstance(w, int) and 1 <= w <= 1000 for w in weights),
          f"{where}.weights", "non-empty list of integers 1..1000")
    loc = face.get("location", {})
    _need(isinstance(loc, dict) and all(isinstance(v, (int, float)) for v in loc.values()), f"{where}.location",
          "object pinning every variable axis, e.g. {\"wght\": 600, \"opsz\": 144}; {} for static fonts")
    _need(isinstance(face.get("features", []), list), f"{where}.features", "must be a list of OpenType tags")
    _need(face.get("case", "as-is") in CASES, f"{where}.case", f"must be one of {', '.join(CASES)}")
    _need(isinstance(face.get("tracking", 0), (int, float)), f"{where}.tracking", "number, 1/1000 em")


def _check_misuse(lg):
    items = lg["misuse"]
    _need(isinstance(items, list) and 4 <= len(items) <= 6, "logo.misuse",
          "list of 4-6 items: {\"do\": \"stretch\"} (generic, drawn from the logo) or "
          "{\"svg\": \"logo/misuse-1.svg\", \"label\": \"...\"} (a misuse of this logo, drawn by you)")
    seen = set()
    for i, it in enumerate(items):
        where = f"logo.misuse[{i}]"
        _need(isinstance(it, dict) and (("do" in it) != ("svg" in it)), where,
              "an object with either \"do\" or \"svg\" + \"label\"")
        if "do" in it:
            _need(it["do"] in MISUSE_GENERIC, f"{where}.do", f"one of {', '.join(MISUSE_GENERIC)}")
            _need(it["do"] not in seen, f"{where}.do", f"{it['do']!r} is listed twice")
            seen.add(it["do"])
            _need(it["do"] != "rearrange" or lg.get("type") in (None, "symbol+wordmark"), f"{where}.do",
                  "rearrange needs a symbol+wordmark logo")
            continue
        svg = it.get("svg")
        _need(_is_str(svg) and svg.lower().endswith(".svg") and not os.path.isabs(svg) and ":" not in svg
              and ".." not in re.split(r"[\\/]", svg), f"{where}.svg",
              "relative path to an SVG in the set folder, e.g. logo/misuse-1.svg")
        _need(_is_str(it.get("label")) and len(it["label"]) <= 90, f"{where}.label",
              "short negative instruction in the document language (<= 90 characters)")


def validate_identity(data, partial=False):
    """Raise IdentityError on the first contract violation; returns data unchanged when valid.

    partial=True accepts a skeleton written by `brand.py init`: brand, set id and components must exist, the
    design sections (type, logo, mechanism) may still be empty.
    """
    _need(isinstance(data, dict), "identity", "must be a JSON object")
    _need(data.get("schema") == SCHEMA, "schema", f"must be {SCHEMA!r}")

    brand = data.get("brand")
    _need(isinstance(brand, dict), "brand", "must be an object")
    _need(_is_str(brand.get("name")), "brand.name", "required non-empty string")
    langs = brand.get("languages", ["en"])
    _need(isinstance(langs, list) and langs and all(_is_str(x) for x in langs), "brand.languages",
          "non-empty list of language codes, e.g. [\"en\", \"tr\"]")
    dl = brand.get("doc_lang")
    _need(dl is None or (isinstance(dl, str) and _LANG_CODE.match(dl)), "brand.doc_lang",
          "language code of the cards, board and kit text (BCP-47, e.g. \"tr\"); omit it to use languages[0]")

    st = data.get("set")
    _need(isinstance(st, dict), "set", "must be an object")
    _need(isinstance(st.get("id"), str) and _SET_ID.match(st["id"]), "set.id", "one capital letter A-Z")
    if not partial:
        for key in ("name", "mechanism", "expression_move"):
            _need(_is_str(st.get(key)), f"set.{key}", "required non-empty string")

    axes = data.get("axes", {})
    _need(isinstance(axes, dict), "axes", "must be an object")
    for name, value in axes.items():
        _need(name in AXES, f"axes.{name}", f"unknown axis; use one of {', '.join(AXES)}")
        _need(isinstance(value, int) and -2 <= value <= 2, f"axes.{name}", "integer -2..+2")
    if not partial:
        _need(2 <= len(axes) <= 4, "axes", "choose 2-4 decision axes for the brief")

    comps = data.get("components")
    _need(isinstance(comps, dict), "components", "must be an object")
    for c in COMPONENTS:
        comp = comps.get(c)
        _need(isinstance(comp, dict), f"components.{c}", "must be an object")
        _need(comp.get("mode") in MODES, f"components.{c}.mode", f"must be one of {', '.join(MODES)}")
        _need(comp.get("status") in STATUSES, f"components.{c}.status", f"must be one of {', '.join(STATUSES)}")
        if comp["mode"] == "keep":
            _need(comp["status"] == "fixed", f"components.{c}.status", "a kept component is 'fixed'")
        if partial:
            continue
        if comp["mode"] == "keep":
            _need(_is_str(comp.get("source")), f"components.{c}.source", "a kept component needs its source")
        if comp["mode"] == "refresh":
            _need(_is_str(comp.get("source")), f"components.{c}.source", "a refreshed component needs its source")

    if partial:
        return data

    if comps["type"]["mode"] != "none" or data.get("type"):
        ty = data.get("type")
        _need(isinstance(ty, dict), "type", "must be an object")
        _need(ty.get("pairing_mode") in PAIRING_MODES, "type.pairing_mode",
              f"must be one of {', '.join(PAIRING_MODES)}")
        _check_face(ty.get("display"), "type.display")
        _check_face(ty.get("text"), "type.text")
        if ty.get("mono") is not None:
            _check_face(ty["mono"], "type.mono")

    if comps["logo"]["mode"] in ("new", "refresh"):
        lg = data.get("logo")
        _need(isinstance(lg, dict), "logo", "must be an object")
        _need(lg.get("type") in LOGO_TYPES, "logo.type", f"must be one of {', '.join(LOGO_TYPES)}")
        _need(_is_str(lg.get("concept")), "logo.concept", "one sentence")
        needs_symbol = lg["type"] in ("symbol+wordmark", "symbol")
        _need(not needs_symbol or _is_str(lg.get("symbol")), "logo.symbol",
              f"a {lg['type']} logo needs a symbol SVG path, e.g. logo/symbol.svg")
        if lg["type"] == "monogram":
            mono = lg.get("monogram")
            _need(isinstance(mono, dict) and _is_str(mono.get("letters")), "logo.monogram.letters", "required")
        elif lg["type"] != "symbol":
            wm = lg.get("wordmark")
            _need(isinstance(wm, dict) and _is_str(wm.get("text")), "logo.wordmark.text", "required")
            _need(wm.get("role", "display") in ("display", "text", "mono"), "logo.wordmark.role",
                  "display, text or mono")
            _need(wm.get("case", "as-is") in CASES, "logo.wordmark.case", f"must be one of {', '.join(CASES)}")
        lockups = lg.get("lockups", [])
        _need(isinstance(lockups, list) and all(x in LOCKUPS for x in lockups), "logo.lockups",
              f"list of {', '.join(LOCKUPS)}")
        colors = lg.get("colors", {})
        _need(isinstance(colors, dict), "logo.colors", "must be an object")
        for part, ref in colors.items():
            _need(_is_str(ref) and not ref.startswith("#"), f"logo.colors.{part}",
                  "a palette role key or brand id, never a raw hex (the palette owns the values)")
        tags = lg.get("tags", [])
        _need(isinstance(tags, list) and all(_is_str(t) for t in tags), "logo.tags",
              "list of structure tags from assets/ai-defaults.json (e.g. literal-object, hidden-letter)")
        cs = lg.get("clear_space", {"unit": "cap-height", "multiple": 1})
        _need(cs.get("unit") in CLEAR_SPACE_UNITS, "logo.clear_space.unit",
              f"must be one of {', '.join(CLEAR_SPACE_UNITS)}")
        ms = lg.get("min_size", {})
        _need(isinstance(ms.get("px", 24), int) and ms.get("px", 24) >= 8, "logo.min_size.px", "integer >= 8")

    if isinstance(data.get("logo"), dict) and data["logo"].get("misuse") is not None:
        _check_misuse(data["logo"])

    if comps["palette"]["mode"] != "none":
        _need(_is_str(data.get("palette")), "palette", "relative path to the set's palette.json")

        pb = data.get("palette_build", {}) or {}
        _need(isinstance(pb, dict), "palette_build", "object of palette_build.py options")
        unknown = set(pb) - {"strategy", "accent", "light_bg", "dark_bg", "neutral_tint", "neutral_chroma", "extra"}
        _need(not unknown, "palette_build", f"unknown option(s): {', '.join(sorted(unknown))}")

    card = data.get("card")
    if card is not None:
        _need(isinstance(card, dict), "card", "object like {\"ground\": \"light\"}")
        g = card.get("ground", "light")
        _need(_is_str(g) and not g.startswith("#"), "card.ground",
              "light, dark, or a palette role / brand id (never a raw hex)")

    audit = data.get("audit")
    if audit is not None:
        _need(isinstance(audit, dict) and audit.get("schema") == AUDIT_SCHEMA, "audit",
              f"null or an object with schema {AUDIT_SCHEMA!r}")
        _need(isinstance(audit.get("findings"), list), "audit.findings", "list of findings")
        for i, f in enumerate(audit["findings"]):
            _need(isinstance(f, dict) and f.get("severity") in SEVERITIES and f.get("component") in FINDING_COMPONENTS,
                  f"audit.findings[{i}]", "needs severity gate|warn|info and a component "
                  f"({', '.join(FINDING_COMPONENTS)})")

    for i, item in enumerate(data.get("defaults_used", [])):
        _need(isinstance(item, dict) and _is_str(item.get("id")), f"defaults_used[{i}].id", "required")
        _need(_is_str(item.get("why")), f"defaults_used[{i}].why",
              "a brief-grounded reason (user request, empty competitor space, existing asset, constraint)")
    for i, item in enumerate(data.get("rationale", [])):
        _need(isinstance(item, dict) and _is_str(item.get("claim")), f"rationale[{i}].claim", "required")
        _need(item.get("evidence") in EVIDENCE, f"rationale[{i}].evidence", f"one of {', '.join(EVIDENCE)}")
    for i, item in enumerate(data.get("derived", [])):
        _need(isinstance(item, dict) and _is_str(item.get("file")), f"derived[{i}].file", "required")
        req = item.get("requires", [])
        _need(isinstance(req, list) and all(r in COMPONENTS for r in req), f"derived[{i}].requires",
              f"list of {', '.join(COMPONENTS)}")
    return data


def utf8_console():
    """UTF-8 stdout/stderr so summaries with ✔ ✘ · print on Windows consoles (cp1252) when a CLI is called directly."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def finding(id, component, severity, message, measured=None, threshold=None, suggested_fix=None, roles=None):
    """Build one finding in the shape every audit uses (docs/architecture.md section 3.5)."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if component not in FINDING_COMPONENTS:
        raise ValueError(f"component must be one of {FINDING_COMPONENTS}")
    f = {"id": id, "component": component, "severity": severity, "measured": measured, "threshold": threshold,
         "message": message, "suggested_fix": suggested_fix}
    if roles:
        f["roles"] = list(roles)
    return f


def load_identity(path, partial=False):
    """Read and validate identity.json; returns the dict. Raises IdentityError naming the file and field."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise IdentityError(f"{path}: file not found") from None
    except json.JSONDecodeError as e:
        raise IdentityError(f"{path}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    try:
        return validate_identity(data, partial=partial)
    except IdentityError as e:
        raise IdentityError(f"{path}: {e}") from None


def save_identity(path, data, partial=False):
    """Validate, then write atomically (temp file + rename) with stable formatting."""
    validate_identity(data, partial=partial)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def set_dirs(work):
    """Set folders of a brand work folder, sorted by set id: [<work>/sets/A, <work>/sets/B, ...]."""
    root = os.path.join(work, "sets")
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, d) for d in sorted(os.listdir(root))
            if _SET_ID.match(d) and os.path.isfile(os.path.join(root, d, "identity.json"))]


def work_dir_of(set_dir):
    """The brand work folder that contains a set folder (<work>/sets/A -> <work>)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(set_dir)))
