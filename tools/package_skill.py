#!/usr/bin/env python3
"""Zip the skill folder for upload (for example claude.ai -> Settings -> Capabilities -> Skills).

  python3 tools/package_skill.py                 # dist/brand-identity.zip
  python3 tools/package_skill.py --out build/    # build/brand-identity.zip

Refuses to package when SKILL.md is missing, its frontmatter uses a field outside the Agent Skills spec (name,
description, license, compatibility, metadata, allowed-tools), SKILL.md is over 8 KB or its description over
600 characters, or a reference card is over its docs/architecture.md section 8 cap. The zip leaves out evals/, __pycache__, *.pyc
and node_modules, and carries LICENSE plus the repository's THIRD_PARTY_NOTICES.md (bundled fonts and data keep
their licences with them). Standard library only.
"""
import argparse
import os
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skillmeta  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = skillmeta.SKILL_NAME
SKILL_DIR = os.path.join(ROOT, "skills", NAME)
DEFAULT_OUT = os.path.join(ROOT, "dist")
NOTICES = os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")
EXCLUDED_DIRS = {"__pycache__", "evals", "node_modules", ".git"}


def preflight():
    """Return a list of reasons the skill cannot be packaged yet."""
    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.isfile(skill_md):
        return [f"{skill_md} not found"]
    with open(skill_md, encoding="utf-8") as fh:
        text = fh.read()
    reasons = skillmeta.check_frontmatter(text, NAME)
    reasons += skillmeta.check_budgets(SKILL_DIR, text)
    reasons += skillmeta.check_reference_caps(os.path.join(SKILL_DIR, "references"))
    for needed in (os.path.join(SKILL_DIR, "LICENSE"), NOTICES):
        if not os.path.isfile(needed):
            reasons.append(f"{os.path.relpath(needed, ROOT)} not found (the zip must carry its licences)")
    return reasons


def members():
    """Yield (absolute path, archive name) for every file that goes into the zip, in a stable order."""
    for here, subdirs, files in os.walk(SKILL_DIR):
        subdirs[:] = sorted(d for d in subdirs if d not in EXCLUDED_DIRS and not d.endswith("-workspace"))
        for fn in sorted(files):
            if fn == ".DS_Store" or fn.endswith(".pyc"):
                continue
            full = os.path.join(here, fn)
            yield full, NAME + "/" + os.path.relpath(full, SKILL_DIR).replace(os.sep, "/")
    yield NOTICES, NAME + "/THIRD_PARTY_NOTICES.md"
    yield os.path.join(ROOT, "requirements.txt"), NAME + "/requirements.txt"  # brand.py check points here


def build(out_dir=DEFAULT_OUT):
    reasons = preflight()
    if reasons:
        sys.exit("error: cannot package:\n  - " + "\n  - ".join(reasons))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, NAME + ".zip")
    n = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for full, arc in members():
            archive.write(full, arc)
            n += 1
    print(f"{target}  {n} files  {os.path.getsize(target) / 1e6:.2f} MB")
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory (default: dist/)")
    build(ap.parse_args().out)
