#!/usr/bin/env python3
"""Smoke test for the brand-identity plugin, run the way an agent would use it (CI: Linux, macOS, Windows).

  python3 tools/smoke_test.py

What it checks, in order:
  * manifests: plugin.json / marketplace.json names and one shared version;
  * SKILL.md: frontmatter, size <= 8 KB, description <= 600 characters, every named path exists;
  * references: docs/architecture.md section 8 byte caps, for the cards that exist;
  * no removed flags or files in the skill;
  * every script that has a command line answers --help; every tool does too;
  * the example palette and identity validate;
  * the colour chain (palette_build -> palette_audit -> export_tokens) in a temp folder, including exit status 1 on a
    failing gate and the short default stdout (<= 1.2 KB);
  * a mock-site preview when a Chromium-based browser is installed;
  * the packager (zip contents and licences).
Each script runs in a subprocess with the platform's default console encoding, so UnicodeEncodeError on report
symbols shows up. Standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import skillmeta  # noqa: E402

ROOT = os.path.dirname(HERE)
PLUGIN = skillmeta.SKILL_NAME
SKILL = os.path.join(ROOT, "skills", PLUGIN)
SCRIPTS = os.path.join(SKILL, "scripts")
TEMPLATES = os.path.join(SKILL, "templates")
REFERENCES = os.path.join(SKILL, "references")
SKILL_MD = os.path.join(SKILL, "SKILL.md")
SUMMARY_BYTES = 1200

failed = []


def verdict(ok, label, detail=""):
    print(("[ok]   " if ok else "[FAIL] ") + label)
    if not ok:
        failed.append(label)
        if detail:
            print("       " + str(detail).strip().replace("\n", "\n       ")[-2500:])
    return ok


def skipped(label, why):
    print(f"[skip] {label}: {why}")


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run(label, argv, expect=(), cwd=None, codes=(0,), max_stdout=None):
    """Run a Python script in a subprocess; fail on a wrong exit code, a traceback, missing text or files, or a
    stdout over max_stdout bytes. Returns (combined output, exit code)."""
    env = dict(os.environ)
    for var in ("PYTHONIOENCODING", "PYTHONUTF8"):    # keep the platform's own console encoding
        env.pop(var, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable] + argv, cwd=cwd or SKILL, env=env, capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=300)
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    problems = []
    if proc.returncode not in codes:
        problems.append(f"exit code {proc.returncode}")
    if "Traceback (most recent call last)" in text:
        problems.append("traceback")
    if max_stdout is not None and len(proc.stdout) > max_stdout:
        problems.append(f"stdout is {len(proc.stdout)} bytes (limit {max_stdout})")
    for item in expect:
        if os.path.isabs(item):
            if not os.path.exists(item):
                problems.append(f"missing file {item}")
        elif item not in text:
            problems.append(f"output lacks {item!r}")
    verdict(not problems, label, "; ".join(problems) + "\n" + text if problems else "")
    return text, proc.returncode


def skill_text():
    with open(SKILL_MD, encoding="utf-8") as fh:
        return fh.read()


# ----------------------------------------------------------------------------- static checks

def check_manifests():
    try:
        plugin = read_json(os.path.join(ROOT, ".claude-plugin", "plugin.json"))
        market = read_json(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))
    except (OSError, ValueError) as exc:
        verdict(False, "plugin.json and marketplace.json parse", exc)
        return
    entries = market.get("plugins") or []
    problems = []
    if plugin.get("name") != PLUGIN:
        problems.append(f"plugin.json name is {plugin.get('name')!r}, expected {PLUGIN!r}")
    if not entries or entries[0].get("name") != plugin.get("name"):
        problems.append("the marketplace entry must carry the plugin's name (else install says 'not found')")
    problems += [f"plugin.json lacks {k}" for k in ("description", "version", "license") if not plugin.get(k)]
    versions = {plugin.get("version"), market.get("metadata", {}).get("version")} | {e.get("version") for e in entries}
    if len(versions) != 1:
        problems.append("version differs between plugin.json, marketplace metadata and the marketplace entry: "
                        + ", ".join(sorted(map(str, versions))))
    if not str(plugin.get("homepage", "")).startswith("https://"):
        problems.append("plugin.json homepage must be an https URL")
    verdict(not problems, f"manifests: plugin '{PLUGIN}', one version ({', '.join(sorted(map(str, versions)))})",
            "\n".join(problems))
    verdict(os.path.isfile(os.path.join(ROOT, ".claude", ".gitkeep")), ".claude/.gitkeep exists (eval project-root guard)")


def check_skill_md():
    label = "SKILL.md frontmatter, size and reference paths"
    if not os.path.isfile(SKILL_MD):
        verdict(False, label, "SKILL.md is missing")
        return
    text = skill_text()
    problems = skillmeta.check_frontmatter(text, PLUGIN) + skillmeta.check_budgets(SKILL, text)
    verdict(not problems, "SKILL.md frontmatter, <= 8 KB, description <= 600 chars", "\n".join(problems))
    named = sorted(set(re.findall(r"\b((?:references|scripts|templates|assets)/[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)",
                                  text)))
    gone = [p for p in named if not os.path.exists(os.path.join(SKILL, *p.split("/")))]
    verdict(not gone, f"SKILL.md names {len(named)} files and all exist", "missing: " + ", ".join(gone))


def check_references():
    problems = skillmeta.check_reference_caps(REFERENCES)
    verdict(not problems, "reference cards within their byte caps", "\n".join(problems))


# Flags and files removed on purpose: the market switch and the per-market culture / mock-copy files (c846ded).
# The skill is global; tool text in other languages lives only in assets/i18n/ (document language, §3.6).
REMOVED = ("--market", "--lang", "culture/tr.json", "copy/tr.json")
REMOVED_RE = [re.compile(re.escape(token) + r"(?![\w-])") for token in REMOVED]   # --langs is a current flag


def check_removed_traces():
    hits = []
    for base in (SKILL_MD, REFERENCES, SCRIPTS):
        if os.path.isfile(base):
            paths = [base]
        else:
            paths = [os.path.join(d, f) for d, _, fs in os.walk(base) for f in fs
                     if f.endswith((".md", ".py", ".mjs", ".json")) and "node_modules" not in d]
        for path in paths:
            try:
                with open(path, encoding="utf-8") as fh:
                    body = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            hits += [f"{os.path.relpath(path, SKILL)}: {token}" for token, rx in zip(REMOVED, REMOVED_RE)
                     if rx.search(body)]
    verdict(not hits, "no removed flags or files in the skill (" + ", ".join(REMOVED) + ")", "\n".join(hits))


def has_cli(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return "__main__" in fh.read()


def check_help():
    scripts = sorted(f for f in os.listdir(SCRIPTS) if f.endswith(".py"))
    verdict(bool(scripts), f"scripts found ({len(scripts)})")
    for fn in scripts:
        full = os.path.join(SCRIPTS, fn)
        if has_cli(full):
            run(f"{fn} --help", [full, "--help"], ["usage"])
    for fn in sorted(os.listdir(os.path.join(ROOT, "tools"))):
        full = os.path.join(ROOT, "tools", fn)
        if fn.endswith(".py") and fn != "skillmeta.py" and has_cli(full):
            run(f"tools/{fn} --help", [full, "--help"], ["usage"], cwd=ROOT)


def check_templates():
    palette = os.path.join(TEMPLATES, "palette.example.json")
    snippet = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); import colorlib; "
               "p = colorlib.load_palette(sys.argv[2]); print('valid', p['name'])")
    run("palette.example.json passes colorlib.load_palette", ["-c", snippet, SCRIPTS, palette], ["valid"])
    ident = os.path.join(TEMPLATES, "identity.example.json")
    if os.path.isfile(ident):
        snippet = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); import identitylib; "
                   "d = identitylib.load_identity(sys.argv[2]); print('valid', d['set']['name'])")
        run("identity.example.json passes identitylib.load_identity", ["-c", snippet, SCRIPTS, ident], ["valid"])
    mocks = os.path.join(TEMPLATES, "mock-sites")
    pages = sorted(f for f in os.listdir(mocks) if f.endswith(".html")) if os.path.isdir(mocks) else []
    verdict(bool(pages), f"mock-site templates found ({', '.join(pages) or 'none'})")
    lacking = []
    for fn in pages:
        with open(os.path.join(mocks, fn), encoding="utf-8") as fh:
            if "{{TOKENS_CSS}}" not in fh.read():
                lacking.append(fn)
    verdict(not lacking, "mock templates carry the {{TOKENS_CSS}} placeholder", "without it: " + ", ".join(lacking))
    try:
        copy = read_json(os.path.join(mocks, "copy", "en.json"))
        verdict(isinstance(copy, dict) and bool(copy), "mock copy/en.json parses (default English text for --copy)")
    except (OSError, ValueError) as exc:
        verdict(False, "mock copy/en.json parses (default English text for --copy)", exc)


# ----------------------------------------------------------------------------- colour chain

def check_colour_chain(tmp):
    script = lambda name: os.path.join(SCRIPTS, name)  # noqa: E731
    palette = os.path.join(tmp, "smoke.json")
    run("palette_build: short stdout, writes the file",
        [script("palette_build.py"), "#1f5f7a", "#ea8a28", "--name", "Smoke", "-o", palette],
        [palette, "palette 'Smoke'"], max_stdout=SUMMARY_BYTES)
    run("palette_audit --write: PASS, short stdout", [script("palette_audit.py"), palette, "--write"], ["PASS"],
        max_stdout=SUMMARY_BYTES)
    run("palette_audit --context identity: PASS", [script("palette_audit.py"), palette, "--context", "identity"],
        ["[context: identity]"], max_stdout=SUMMARY_BYTES)
    run("palette_audit --full prints the report", [script("palette_audit.py"), palette, "--full"], ["## Contrast, light"])
    try:
        verdict(isinstance(read_json(palette).get("audit"), dict), "palette_audit stored an 'audit' object in palette.json")
    except (OSError, ValueError) as exc:
        verdict(False, "palette.json readable after audit", exc)
    tokens = os.path.join(tmp, "tokens")
    run("export_tokens: all formats, short stdout", [script("export_tokens.py"), palette, "--out", tokens],
        [os.path.join(tokens, f) for f in ("tokens.css", "tokens.scss", "tailwind.palette.js", "tokens.dtcg.json",
                                           "palette.gpl", "swatches.html")], max_stdout=SUMMARY_BYTES)
    try:
        with open(os.path.join(tokens, "tokens.css"), encoding="utf-8") as fh:
            css = fh.read()
        verdict("--bi-on-primary" in css and "prefers-color-scheme: dark" in css,
                "tokens.css has --bi-* roles and a dark-mode block")
        read_json(os.path.join(tokens, "tokens.dtcg.json"))
    except (OSError, ValueError) as exc:
        verdict(False, "exported tokens parse", exc)
    run("palette_audit exits 1 on a failing gate",
        [script("palette_audit.py"), "--colors", "#cccccc,#ffffff", "--roles", "text,background"], codes=(1,))
    return palette


def check_preview(tmp, palette):
    _, code = run("render_png --which", [os.path.join(SCRIPTS, "render_png.py"), "--which"], codes=(0, 3))
    if code != 0:
        skipped("mock preview PNGs", "no Chromium-based browser on this machine")
        return
    tool = os.path.join(SCRIPTS, "site_preview.py")
    if not os.path.isfile(tool):
        skipped("mock preview PNGs", "site_preview.py not present")
        return
    out = os.path.join(tmp, "preview")
    run("site_preview --mock landing", [tool, "--mock", "landing", "--palettes", palette, "--brand-name", "Smoke",
                                         "--tagline", "Smoke test.", "--out", out], [os.path.join(out, "preview.json")])
    pngs = [os.path.join(d, f) for d, _, fs in os.walk(out) for f in fs if f.endswith(".png")]
    verdict(len(pngs) >= 2, f"mock preview wrote desktop and mobile PNGs ({len(pngs)})")


def check_package():
    target = os.path.join(ROOT, "dist", PLUGIN + ".zip")
    run("package_skill", [os.path.join(ROOT, "tools", "package_skill.py")], [target], cwd=ROOT)
    if not os.path.isfile(target):
        return
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    junk = [n for n in names if "/evals/" in n or "__pycache__" in n or n.endswith(".pyc")]
    verdict(f"{PLUGIN}/SKILL.md" in names and not junk, "zip has SKILL.md and no evals/ or __pycache__", ", ".join(junk))
    absent = [n for n in (f"{PLUGIN}/LICENSE", f"{PLUGIN}/THIRD_PARTY_NOTICES.md") if n not in names]
    verdict(not absent, "zip carries LICENSE and THIRD_PARTY_NOTICES.md", "missing: " + ", ".join(absent))


def note_caches():
    found = [d for d, _, _ in os.walk(SKILL) if os.path.basename(d) == "__pycache__"]
    if found:
        print("[warn] __pycache__ in the skill folder (harmless, left out of the zip): " + ", ".join(found))


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: smoke_test.py\n\n" + __doc__)
        return
    tmp = tempfile.mkdtemp(prefix="brand-identity-smoke-")
    try:
        check_manifests()
        check_skill_md()
        check_references()
        check_removed_traces()
        check_help()
        check_templates()
        palette = check_colour_chain(tmp)
        check_preview(tmp, palette)
        check_package()
        note_caches()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
