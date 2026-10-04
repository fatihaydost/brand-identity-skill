"""The build pipeline behind `brand.py build`, `mix` and `critique` (docs/architecture.md sections 4.2, 1.2, 1.1).

build: sets.json -> per set (identity.json, palette build + audit, fonts + font audit, logo build + audit, defaults
check, card) -> cross-set cohesion -> board, review image and table (+ live site when the brief has one).
This module is the only writer of identity.json; the component modules return values.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import colorlib  # noqa: E402
import identitylib  # noqa: E402
from identitylib import finding  # noqa: E402

SKILL = os.path.dirname(HERE)
DEFAULTS = os.path.join(SKILL, "assets", "ai-defaults.json")
STDOUT_CAP = 1200
ROLES = ("display", "text", "mono")


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _optional(name):
    try:
        return __import__(name)
    except ImportError:
        return None


# ---------------------------------------------------------------- palette

def _palette_args(opts):
    """identity.palette_build -> palette_build.py arguments."""
    args = []
    for key, flag in (("strategy", "--strategy"), ("accent", "--accent"), ("light_bg", "--light-bg"),
                      ("dark_bg", "--dark-bg"), ("neutral_tint", "--neutral-tint"),
                      ("neutral_chroma", "--neutral-chroma")):
        if (opts or {}).get(key) not in (None, ""):
            args += [flag, str(opts[key])]
    for extra in (opts or {}).get("extra") or []:
        args += ["--extra", str(extra)]
    return args


def _built(pal):
    return bool(pal and pal.get("scales") and (pal.get("modes") or {}).get("light") and pal["modes"].get("dark"))


def _run(script, args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, (r.stdout + r.stderr).strip()


def build_palette(set_dir, ident, draft_palette, work):
    """Write the set's palette.json (built) and audit it. Returns (palette or None, findings)."""
    out = os.path.join(set_dir, ident.get("palette") or "palette.json")
    comp = (ident.get("components") or {}).get("palette") or {}
    mode, source = comp.get("mode", "new"), comp.get("source")
    findings = []
    partial = None
    if mode in ("keep", "none") and source:
        src = source if os.path.isabs(source) else os.path.join(work, source)
        data = _json(src)
        if data is None:
            return None, [finding("palette.source", "palette", "gate", f"cannot read palette source {source}")]
        if data.get("schema") == "brand-identity/palette@1":
            if _built(data):
                _write_json(out, data)
            else:
                partial = data
        else:  # a site extract: roles proposal -> seeds
            roles = (data.get("roles") or {})
            seeds = [v.get("hex") if isinstance(v, dict) else v for v in (roles.get("primary"), roles.get("accent"))]
            seeds = [s for s in seeds if s]
            if not seeds:
                return None, [finding("palette.source", "palette", "gate",
                                      f"{source} has no primary colour to keep", suggested_fix="confirm roles")]
            partial = {"schema": "brand-identity/palette@1", "name": "Current", "direction": "kept from the site",
                       "brand": [{"id": f"brand-{i + 1}", "name": n, "hex": h, "source": "measured", "locked": True}
                                 for i, (n, h) in enumerate(zip(("Primary", "Accent"), seeds))]}
    elif mode == "none":
        return None, []
    else:
        partial = draft_palette
        if mode == "refresh" and not partial and source:
            partial = _json(source if os.path.isabs(source) else os.path.join(work, source))
        if not partial:
            existing = _json(out)
            if _built(existing):
                partial = None
            else:
                return None, [finding("palette.missing", "palette", "gate", "no palette in sets.json for this set",
                                      suggested_fix="add a partial palette (brand[] seeds, name, direction)")]
    if partial is not None:
        if _built(partial):
            _write_json(out, partial)
        else:
            tmp = os.path.join(set_dir, ".palette.partial.json")
            _write_json(tmp, partial)
            code, msg = _run("palette_build.py", ["--from", tmp, "-o", out, "-q",
                                                  *_palette_args(ident.get("palette_build"))])
            os.remove(tmp)
            if code != 0:
                return None, [finding("palette.build", "palette", "gate", msg.splitlines()[-1] if msg else
                                      "palette_build failed")]
    code, msg = _run("palette_audit.py", [out, "--context", "identity", "--write", "--format", "summary"])
    pal = _json(out)
    for f in ((pal or {}).get("audit") or {}).get("findings") or []:
        sev = f.get("severity") if f.get("severity") in identitylib.SEVERITIES else "info"
        findings.append(finding(f"palette.{f.get('id')}", "palette", sev, f.get("message", ""),
                                measured=f.get("measured"), threshold=f.get("threshold"),
                                suggested_fix=f.get("suggested_fix"), roles=f.get("roles")))
    return pal, findings


# ---------------------------------------------------------------- type

def build_type(set_dir, ident):
    """Resolve every role's font, write type/fonts.json, audit each distinct face. Returns findings."""
    ty = ident.get("type")
    comp = (ident.get("components") or {}).get("type") or {}
    if not ty or comp.get("mode") == "none" and not ty:
        return []
    typelib, font_audit = _optional("typelib"), _optional("font_audit")
    if not typelib or not font_audit:
        return [finding("type.modules", "type", "gate", "typelib/font_audit not installed")]
    brand = ident.get("brand") or {}
    langs = brand.get("languages") or ["en"]
    fonts, findings, seen = {}, [], set()
    for role in ROLES:
        face = ty.get(role)
        if not face:
            continue
        loc = dict(face.get("location") or {})
        loc.setdefault("wght", (face.get("weights") or [400])[0])
        try:
            path = typelib.resolve_font(face["family"], loc, face.get("source"), face.get("file"))
            fonts[role] = {"family": face["family"], "file": str(path), "location": loc,
                           "weights": face.get("weights"), "features": face.get("features") or []}
        except Exception as e:  # noqa: BLE001 - FontError and download errors become a gate with the reason
            findings.append(finding(f"type.{role}.resolve", "type", "gate", f"{face['family']}: {e}"))
            continue
        if face["family"] in seen:  # one audit per family; the text role's checks win when it has one
            continue
        seen.add(face["family"])
        roles_of = [r for r in ROLES if (ty.get(r) or {}).get("family") == face["family"]]
        audit_role = "text" if "text" in roles_of else role
        if audit_role == "text" and role != "text":  # measure at the text role's pinned location (e.g. opsz 16)
            tloc = dict((ty.get("text") or {}).get("location") or {})
            tloc.setdefault("wght", ((ty.get("text") or {}).get("weights") or [400])[0])
            loc = tloc
        wm_role = ((ident.get("logo") or {}).get("wordmark") or {}).get("role", "display")
        uses = ("web", "logo") if wm_role in roles_of or "display" in roles_of else ("web",)
        try:
            for f in font_audit.audit(str(path) if face.get("file") else face["family"], langs,
                                      numbers=bool(brand.get("numbers")) and audit_role == "text", uses=uses,
                                      role=audit_role, license=face.get("license"), location=loc):
                f = dict(f)
                f["id"] = f"type.{'+'.join(roles_of)}.{f['id'].removeprefix('type.')}"
                findings.append(f)
        except Exception as e:  # noqa: BLE001
            findings.append(finding(f"type.{role}.audit", "type", "gate", f"{face['family']}: {e}"))
    _write_json(os.path.join(set_dir, "type", "fonts.json"), fonts)
    return findings


# ---------------------------------------------------------------- defaults and cohesion

def _defaults():
    return _json(DEFAULTS, {"entries": []})


def _in(v, rng):
    return rng[0] <= v <= rng[1]


def _lch(colours):
    out = []
    for c in colours:
        try:
            out.append(colorlib.hex_to_oklch(c))
        except Exception:  # noqa: BLE001 - a malformed colour is the palette audit's business
            pass
    return out


def defaults_matches(ident, pal):
    """ids of ai-defaults entries this set matches. Only what the set chose counts: the palette's own brand[]
    colours (not derived UI tones, not --extra colours) and, for ground entries, the page background; kept
    components are the user's, so they never match."""
    data = _defaults()
    mode = lambda c: (((ident.get("components") or {}).get(c) or {}).get("mode"))  # noqa: E731
    fams = set()
    if mode("type") != "keep":
        fams = {((ident.get("type") or {}).get(r) or {}).get("family", "").lower() for r in ROLES} - {""}
    brand_lch, ground_lch = [], []
    if pal and mode("palette") != "keep":
        brand_lch = _lch([b.get("hex") for b in pal.get("brand") or [] if b.get("hex")])
        light = (pal.get("modes") or {}).get("light") or {}
        ground_lch = _lch([light.get("background")] if light.get("background") else [])
    tags = set((ident.get("logo") or {}).get("tags") or []) if mode("logo") != "keep" else set()
    hits = []
    for e in data.get("entries") or []:
        m = e.get("match") or {}
        pool = ground_lch if "ground" in e["id"] else brand_lch
        if any(f.lower() in fams for f in m.get("fonts") or []):
            hits.append(e["id"])
        elif any(_in(L, r["L"]) and _in(C, r["C"]) and _in(h % 360, r["h"]) for r in m.get("oklch_regions") or []
                 for (L, C, h) in pool):
            hits.append(e["id"])
        elif tags & set(m.get("logo_tags") or []):
            hits.append(e["id"])
    return hits


def _default_aliases():
    """entry id -> names a model may use for it in defaults_used (the id, its logo tags, 'logo-' + tag)."""
    out = {}
    for e in _defaults().get("entries") or []:
        names = {e["id"]}
        for t in (e.get("match") or {}).get("logo_tags") or []:
            names |= {t, f"logo-{t}"}
        out[e["id"]] = names
    return out


def defaults_findings(ident, hits):
    given = {d.get("id") for d in ident.get("defaults_used") or [] if d.get("why")}
    aliases = _default_aliases()
    why = {h for h in hits if aliases.get(h, {h}) & given}
    return [finding(f"default.{h}", "cohesion", "warn",
                    f"matches the AI-defaults list ({h}) without a brief-grounded reason",
                    suggested_fix="justify it in defaults_used[] from the brief, or change it")
            for h in hits if h not in why]


def competitor_findings(work, ident, pal):
    """Warn when one of the set's chosen brand colours sits within CIEDE2000 10 of a measured competitor's primary or
    accent (references/color.md: closer than that, the two read as the same colour). Kept palettes are exempt."""
    if not pal or (((ident.get("components") or {}).get("palette") or {}).get("mode")) == "keep":
        return []
    comp = _json(os.path.join(work, "site", "competitors.json"), {}) or {}
    rivals = []
    for site in comp.get("sites") or []:
        if site.get("status", "ok") != "ok":
            continue
        roles = site.get("roles") or {}
        for role in ("primary", "accent"):
            hx = roles.get(role)
            if isinstance(hx, str) and hx.startswith("#"):
                rivals.append((site.get("host") or site.get("url"), role, hx))
    out = []
    for b in pal.get("brand") or []:
        for host, role, hx in rivals:
            try:
                d = colorlib.delta_e2000(b["hex"], hx)
            except Exception:  # noqa: BLE001
                continue
            if d < 10:
                out.append(finding("cohesion.competitor-colour", "cohesion", "warn",
                                   f"{b.get('name', b['hex'])} {b['hex']} is ΔE2000 {d:.1f} from {host}'s {role} {hx}",
                                   measured=round(d, 1), threshold=10,
                                   suggested_fix="move lightness or hue until ΔE2000 >= 10, or say why sharing it is "
                                                 "deliberate"))
    return out


def cohesion_findings(states):
    """Cross-set warnings (heuristic, uncalibrated): sets should differ on >= 2 axes by >= 2 steps; at least one
    set should be free of AI-defaults matches."""
    out, close = [], []
    ids = sorted(states)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            xa, xb = states[a]["axes"], states[b]["axes"]
            far = [k for k in set(xa) & set(xb) if abs(xa[k] - xb[k]) >= 2]
            if len(far) < 2:
                close.append(f"{a}–{b}")
    if close:
        out.append(finding("cohesion.sets-close", "cohesion", "warn",
                           f"sets too alike on the axes: {', '.join(close)} differ by 2+ steps on fewer than 2 axes "
                           "(heuristic)", measured=close, threshold="2 axes, 2 steps"))
    if len(ids) > 1 and all(states[s]["hits"] for s in ids):
        out.append(finding("cohesion.no-default-free-set", "cohesion", "warn",
                           "every set uses at least one AI-defaults item; build one set without any"))
    return out


# ---------------------------------------------------------------- per set

def _keep_checks(ident, work):
    out = []
    for c, comp in (ident.get("components") or {}).items():
        if comp.get("mode") != "keep" or not comp.get("source"):
            continue
        src = comp["source"] if os.path.isabs(comp["source"]) else os.path.join(work, comp["source"])
        if not os.path.isfile(src):
            out.append(finding(f"{c}.keep-source", c if c != "type" else "type", "gate",
                               f"kept {c} source not found: {comp['source']}"))
            continue
        sha = _sha_file(src)
        if comp.get("sha256") and comp["sha256"] != sha:
            out.append(finding(f"{c}.keep-changed", c, "gate", f"kept {c} source changed since intake",
                               measured=sha[:12], threshold=comp["sha256"][:12]))
        comp["sha256"] = comp.get("sha256") or sha
    return out


def _inputs_sha(ident, set_dir, requires):
    h = hashlib.sha256()
    for key in sorted(requires):
        h.update(json.dumps(ident.get(key), sort_keys=True, separators=(",", ":")).encode())
    files = []
    if "logo" in requires:
        for name in ("symbol.svg", "symbol-small.svg"):
            files.append(os.path.join(set_dir, "logo", name))
    if "palette" in requires:
        files.append(os.path.join(set_dir, ident.get("palette") or "palette.json"))
    if "type" in requires:
        for f in (_json(os.path.join(set_dir, "type", "fonts.json"), {}) or {}).values():
            files.append(f.get("file"))
    for p in files:
        if p and os.path.isfile(p):
            h.update(_sha_file(p).encode())
    return h.hexdigest()


def _save(path, ident):
    try:
        identitylib.save_identity(path, ident)
        return None
    except identitylib.IdentityError as e:
        _write_json(path, ident)  # keep the draft visible on the FAILED card; the gate says why
        return finding("identity.contract", "cohesion", "gate", str(e))


def _brand_from_brief(ident, brief):
    """brief.json is the authority for brand-level fields (and keep/refresh sources); copy them in on every build."""
    for c, src in (brief.get("sources") or {}).items():
        comp = (ident.get("components") or {}).get(c)
        if isinstance(comp, dict) and not comp.get("source"):
            comp["source"] = src
    b = ident.setdefault("brand", {})
    for src, dst in (("brand", "name"), ("tagline", "tagline"), ("languages", "languages"), ("doc_lang", "doc_lang"),
                     ("sector", "sector"), ("numbers", "numbers"), ("copy", "copy")):
        if brief.get(src) not in (None, "", [], {}):
            b[dst] = brief[src]


def build_set(work, rec, entry, brief=None):
    import logo_audit
    set_dir, ident = rec["set_dir"], rec["identity"]
    _brand_from_brief(ident, brief or {})
    logo = (ident.get("components") or {}).get("logo") or {}
    site_logo = os.path.join(work, "site", "logo.svg")
    if logo.get("mode") in ("keep", "refresh", "none") and not logo.get("source") and os.path.isfile(site_logo):
        logo["source"] = "site/logo.svg"  # saved by `site extract`; the user's own mark
    ipath = os.path.join(set_dir, "identity.json")
    findings = [finding("identity.sync", "cohesion", "gate", e) for e in rec.get("errors") or []
                if not e.startswith("palette")]
    findings += _keep_checks(ident, work)
    pal, pf = build_palette(set_dir, ident, (entry or {}).get("palette"), work)
    findings += pf
    findings += build_type(set_dir, ident)
    if pal is not None:
        rec["palette"] = pal
    _manifest, lf = logo_audit.build_set(rec)
    findings += [f for f in lf if f["id"] != "logo.sync"]  # sync errors are already gates above
    hits = defaults_matches(ident, pal)
    findings += defaults_findings(ident, hits)
    findings += competitor_findings(work, ident, pal)
    return {"id": rec["id"], "dir": set_dir, "path": ipath, "ident": ident, "findings": findings, "hits": hits,
            "axes": ident.get("axes") or {}}


def _finish(state, cross, card):
    ident, findings = state["ident"], state["findings"] + cross + (card or [])
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in identitylib.SEVERITIES}
    ident["audit"] = {"schema": identitylib.AUDIT_SCHEMA, "passed": counts["gate"] == 0, "counts": counts,
                      "findings": findings, "defaults": {"matched": state.get("hits") or []}}
    req = [c for c in identitylib.COMPONENTS if ((ident.get("components") or {}).get(c) or {}).get("mode") != "none"]
    ident["derived"] = [{"file": "card.png", "requires": req, "inputs_sha256": _inputs_sha(ident, state["dir"], req)}]
    err = _save(state["path"], ident)
    if err:
        findings.append(err)
        ident["audit"]["findings"] = findings
        ident["audit"]["passed"] = False
        _write_json(state["path"], ident)
    return findings


def run_build(work, sets=None, site=True):
    import identity_board
    import identity_card
    import logolib
    draft = _json(os.path.join(work, "sets.json"), {}) or {}
    entries = {}
    for i, e in enumerate(draft.get("sets") or []):
        sid = (((e or {}).get("identity") or {}).get("set") or {}).get("id") or chr(ord("A") + i)
        entries[sid] = e
    brief = _json(os.path.join(work, "brief.json"), {}) or {}
    records = logolib.sync_symbols(work, sets)
    states = {}
    for rec in records:
        st = build_set(work, rec, entries.get(rec["id"]), brief)
        states[rec["id"]] = st
        _finish(st, [], None)  # identity.json on disk before the card reads it
    all_states = dict(states)
    for d in identitylib.set_dirs(work):  # sets not rebuilt this time still count for cohesion
        sid = os.path.basename(d)
        if sid not in all_states:
            ident = _json(os.path.join(d, "identity.json"), {})
            all_states[sid] = {"axes": ident.get("axes") or {},
                               "hits": defaults_matches(ident, _json(os.path.join(d, "palette.json")))}
    cross = cohesion_findings(all_states)
    for sid, st in states.items():
        _finish(st, cross, None)
        card = identity_card.render(st["dir"])
        st["card"] = card
        st["final"] = _finish(st, cross, [f for f in card["findings"] if f["severity"] != "info"])
    board = identity_board.render(work)
    site_line = None
    if site and brief.get("site") and _optional("site_preview"):
        import site_preview
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                code = site_preview.cli_site(argparse.Namespace(action="apply", targets=[brief["site"], work],
                                                                sets=",".join(sets) if sets else None, full=False))
        except SystemExit as e:  # a missing driver or an unreachable site must not swallow the build summary
            code = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # noqa: BLE001
            code, buf = 1, io.StringIO(str(e))
        last = (buf.getvalue().strip().splitlines() or [""])[-1][:120]
        site_line = f"site: {'applied' if code == 0 else 'apply failed: ' + last} ({brief['site']})"
        board = identity_board.render(work)
    return states, board, site_line


def _summary(states, board, site_line, full=False):
    lines = []
    failed = False
    for sid in sorted(states):
        st = states[sid]
        fs = st.get("final") or st["findings"]
        g = [f for f in fs if f["severity"] == "gate"]
        w = [f for f in fs if f["severity"] == "warn"]
        failed = failed or bool(g)
        s = st["ident"].get("set") or {}
        star = "*" if s.get("recommended") else " "
        lines.append(f"{sid}{star} {s.get('name', '')} · {'✘ ' + str(len(g)) + ' gate' if g else '✔'} · "
                     f"{len(w)} warn · defaults: {', '.join(st['hits']) or 'none'}")
        for f in (fs if full else g[:3]):
            lines.append(f"  {f['severity'].upper()} {f['id']}: {f['message']}"[:170])
    i18n = next((f for sid in sorted(states) for f in (states[sid].get("final") or states[sid]["findings"])
                 if f.get("id") == "i18n.missing"), None)
    if i18n:  # once for the run: the labels of every output are affected, not one set
        lines.append(f"warn i18n.missing: {i18n['message']}"[:170] + f" → {i18n.get('suggested_fix') or ''}"[:200])
    lines.append(f"review: {board['review']}")
    lines.append(f"board: {board['board']} · table: {board['table']}")
    if site_line:
        lines.append(site_line)
    text = "\n".join(lines)
    if not full and len(text.encode()) > STDOUT_CAP:
        text = text.encode()[:STDOUT_CAP - 60].decode("utf-8", "ignore").rsplit("\n", 1)[0]
        text += "\n... details: sets/X/identity.json audit (--full)"
    return text, failed


def cli_build(args):
    identitylib.utf8_console()
    work = os.path.abspath(args.work)
    sets = [s.strip().upper() for s in (getattr(args, "sets", None) or "").split(",") if s.strip()] or None
    if not os.path.isfile(os.path.join(work, "sets.json")):
        print(f"error: {work}/sets.json not found (run brand.py init)", file=sys.stderr)
        return 2
    states, board, site_line = run_build(work, sets)
    if getattr(args, "json", False):
        print(json.dumps({sid: {"findings": st.get("final"), "defaults": st["hits"]} for sid, st in states.items()},
                         indent=1, default=str))
        return 0
    text, failed = _summary(states, board, site_line, getattr(args, "full", False))
    print(text)
    return 1 if failed else 0


# ---------------------------------------------------------------- mix

def cli_mix(args):
    """New set from named parts: A:logo A:type B:palette --as D. The logo is re-coloured with D's palette."""
    identitylib.utf8_console()
    work = os.path.abspath(args.work)
    path = os.path.join(work, "sets.json")
    draft = _json(path)
    if not draft:
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    by_id = {(((e.get("identity") or {}).get("set") or {}).get("id")): e for e in draft.get("sets") or []}
    new_id = args.as_id.upper()
    if not (len(new_id) == 1 and "A" <= new_id <= "Z"):
        print(f"error: --as must be one letter A-Z, got {args.as_id!r}", file=sys.stderr)
        return 2
    if new_id in by_id:
        print(f"error: set {new_id} already exists", file=sys.stderr)
        return 2
    parts = {}
    for p in args.parts:
        sid, _, comp = p.partition(":")
        if sid.upper() not in by_id or comp not in identitylib.COMPONENTS:
            print(f"error: {p!r}: use SET:logo|type|palette with an existing set", file=sys.stderr)
            return 2
        parts[comp] = sid.upper()
    base = json.loads(json.dumps(by_id[parts.get("logo") or parts.get("type") or parts.get("palette")]))
    ident = base["identity"]
    for comp, sid in parts.items():
        src = by_id[sid]
        if comp == "logo":
            ident["logo"] = json.loads(json.dumps(src["identity"].get("logo")))
            base["symbol_svg"], base["symbol_small_svg"] = src.get("symbol_svg"), src.get("symbol_small_svg")
        elif comp == "type":
            ident["type"] = json.loads(json.dumps(src["identity"].get("type")))
        else:
            base["palette"] = json.loads(json.dumps(src.get("palette")))
            ident["palette_build"] = json.loads(json.dumps(src["identity"].get("palette_build") or {}))
    names = ", ".join(f"{c} from {s}" for c, s in parts.items())
    ident["set"] = dict(ident.get("set") or {}, id=new_id, recommended=False,
                        name=f"{(ident.get('set') or {}).get('name', 'Mix')} (mix)",
                        differs_by=f"Mixed set: {names}. Check that one idea still drives all three parts.")
    draft["sets"].append(base)
    _write_json(path, draft)
    states, board, site_line = run_build(work, [new_id], site=False)
    states[new_id]["final"].append(finding("cohesion.mixed", "cohesion", "warn",
                                           f"set {new_id} mixes parts ({names}); the mechanism may no longer hold"))
    text, failed = _summary(states, board, site_line)
    print(text)
    return 1 if failed else 0


# ---------------------------------------------------------------- critique

def cli_critique(args):
    """Audit existing assets: fonts, palette, logo SVG, site. Prints findings grouped by component."""
    identitylib.utf8_console()
    langs = [x.strip() for x in (args.langs or "en").split(",") if x.strip()]
    findings = []
    if args.site:
        sp = _optional("site_preview")
        if sp:
            buf = io.StringIO()
            from urllib.parse import urlparse
            host = re.sub(r"[^a-z0-9]+", "-", (urlparse(args.site).netloc or args.site).lower()).strip("-")[:40] or "site"
            crit = os.path.abspath(os.path.join("brand-identity", ".critique", host))
            shutil.rmtree(crit, ignore_errors=True)  # a fresh read per run: no stale logo from an earlier site
            os.makedirs(crit, exist_ok=True)
            try:
                with contextlib.redirect_stdout(buf):
                    sp.cli_site(argparse.Namespace(action="extract", targets=[args.site, crit], sets=None,
                                                   full=False))
            except SystemExit:
                pass
            if not args.logo and os.path.isfile(os.path.join(crit, "site", "logo.svg")):
                args.logo = os.path.join(crit, "site", "logo.svg")  # audit the site's own mark
            findings.append(finding("site.extract", "site", "info", buf.getvalue().strip()[:400]))
    if args.fonts:
        fa = _optional("font_audit")
        for fam in [x.strip() for x in args.fonts.split(",") if x.strip()]:
            try:
                findings += fa.audit(fam, langs)
            except Exception as e:  # noqa: BLE001
                findings.append(finding("type.audit", "type", "gate", f"{fam}: {e}"))
    if args.palette:
        code, msg = _run("palette_audit.py", [args.palette, "--format", "summary"])
        findings.append(finding("palette.audit", "palette", "gate" if code else "info", msg[:600]))
    if args.logo:
        import logolib
        with open(args.logo, encoding="utf-8") as fh:
            svg = fh.read()
        for tag in ("<text", "<image", "<filter", "<foreignObject"):
            if tag in svg:
                findings.append(finding("logo.master", "logo", "gate", f"logo file contains {tag}>",
                                        suggested_fix="outline text and remove raster/filters"))
        try:
            logolib.resolve_symbol(svg)
        except Exception as e:  # noqa: BLE001
            findings.append(finding("logo.parse", "logo", "warn", f"could not resolve shapes: {e}"))
    full = getattr(args, "full", False)
    order = {"gate": 0, "warn": 1, "info": 2}
    lines = [f"{f['severity'].upper()} {f['component']} {f['id']}: {f['message']}"[:200]
             for f in sorted(findings, key=lambda f: order[f["severity"]])]
    text = "\n".join(lines) or "no assets given (use --site, --fonts, --palette, --logo)"
    if not full and len(text.encode()) > STDOUT_CAP:
        text = text.encode()[:STDOUT_CAP - 40].decode("utf-8", "ignore").rsplit("\n", 1)[0] + "\n... (--full)"
    print(text)
    return 1 if any(f["severity"] == "gate" for f in findings) else 0
