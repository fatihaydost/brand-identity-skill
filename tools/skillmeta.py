#!/usr/bin/env python3
"""Frontmatter helpers shared by tools/smoke_test.py and tools/package_skill.py (standard library only).

The parser is deliberately small: it understands the flat `key: value` frontmatter that Agent Skills use and
reports anything fancier (folded scalars, continuation lines) as a problem instead of guessing.
"""
import os
import re

# The six fields of the Agent Skills spec. Anything else (Claude Code extras such as `when_to_use`) makes
# claude.ai uploads and the Skills API fail with "Unexpected key(s) in SKILL.md frontmatter".
FRONTMATTER_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SKILL_NAME = "brand-identity"
MAX_SKILL_BYTES = 8 * 1024          # docs/architecture.md section 9: SKILL.md <= 8 KB
MAX_DESCRIPTION_CHARS = 600         # docs/architecture.md section 9: description <= 600 characters
# docs/architecture.md section 8: byte caps per reference card (1 KB = 1024 bytes). Only files that exist are measured.
REFERENCE_CAPS = {
    "cohesion.md": 5, "type.md": 6, "logo.md": 6, "ai-defaults.md": 3, "color.md": 5, "kit.md": 5,
    "accessibility.md": 4, "reproduction.md": 4, "critique.md": 4, "site-preview.md": 4,
    "tokens-export.md": 4, "discovery-brief.md": 4,
}


def parse_frontmatter(text):
    """Return (fields, problems). fields maps key -> {"value": str, "extra": [continuation lines]}."""
    problems = []
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, ["SKILL.md does not start with a '---' frontmatter block"]
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, ["frontmatter block is not closed with '---'"]
    fields, current = {}, None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            current = m.group(1)
            if current in fields:
                problems.append(f"duplicate frontmatter key {current!r}")
            fields[current] = {"value": m.group(2).strip(), "extra": []}
        elif current is not None:
            fields[current]["extra"].append(raw)
        else:
            problems.append(f"unparseable frontmatter line: {raw!r}")
    return fields, problems


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def check_frontmatter(text, folder_name):
    """Validate SKILL.md frontmatter against the spec. Returns a list of problem strings (empty = ok)."""
    fields, problems = parse_frontmatter(text)
    if not fields:
        return problems
    extra_keys = [k for k in fields if k not in FRONTMATTER_FIELDS]
    if extra_keys:
        problems.append("fields outside the Agent Skills spec: " + ", ".join(extra_keys)
                        + " (allowed: " + ", ".join(FRONTMATTER_FIELDS) + ")")
    name = unquote(fields.get("name", {}).get("value", ""))
    if not name:
        problems.append("missing 'name'")
    else:
        if name != folder_name:
            problems.append(f"name {name!r} must equal the folder name {folder_name!r}")
        if len(name) > 64 or not NAME_RE.match(name):
            problems.append(f"name {name!r} must be 1-64 chars of a-z, 0-9 and single hyphens")
        if "anthropic" in name or "claude" in name:
            problems.append(f"name {name!r} contains a reserved word")
    desc_field = fields.get("description")
    if desc_field is None:
        problems.append("missing 'description'")
    else:
        raw = desc_field["value"]
        if desc_field["extra"] or raw[:1] in (">", "|"):
            problems.append("description must be a single line (no folded/block scalar or continuation lines)")
        desc = unquote(raw)
        if not desc:
            problems.append("description is empty")
        if len(desc) > 1024:
            problems.append(f"description is {len(desc)} chars (max 1024)")
        if "<" in desc or ">" in desc:
            problems.append("description contains '<' or '>'")
    comp = fields.get("compatibility")
    if comp and len(unquote(comp["value"])) > 500:
        problems.append("compatibility is longer than 500 chars")
    return problems


def check_budgets(skill_dir, skill_md_text=None):
    """SKILL.md size, description length and reference caps. Returns a list of problem strings (empty = ok)."""
    problems = []
    path = os.path.join(skill_dir, "SKILL.md")
    if skill_md_text is None:
        with open(path, "rb") as fh:
            skill_md_text = fh.read().decode("utf-8")
    size = len(skill_md_text.replace("\r\n", "\n").encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        problems.append(f"SKILL.md is {size} bytes (cap {MAX_SKILL_BYTES})")
    fields, _ = parse_frontmatter(skill_md_text)
    desc = unquote(fields.get("description", {}).get("value", ""))
    if len(desc) > MAX_DESCRIPTION_CHARS:
        problems.append(f"description is {len(desc)} chars (cap {MAX_DESCRIPTION_CHARS})")
    return problems


def lf_size(path):
    """Byte size with LF line ends, so a Windows checkout (CRLF) measures the same as the repo."""
    with open(path, "rb") as fh:
        return len(fh.read().replace(b"\r\n", b"\n"))


def check_reference_caps(ref_dir):
    """Measure the reference cards that exist against their caps; unknown *.md files are reported as such."""
    problems = []
    if not os.path.isdir(ref_dir):
        return problems
    for fn in sorted(os.listdir(ref_dir)):
        if not fn.endswith(".md"):
            continue
        size = lf_size(os.path.join(ref_dir, fn))
        cap = REFERENCE_CAPS.get(fn)
        if cap is None:
            problems.append(f"{fn} is not a reference card listed in docs/architecture.md section 8")
        elif size > cap * 1024:
            problems.append(f"{fn} is {size} bytes (cap {cap * 1024})")
    return problems
