---
name: critique-identity
description: 'Critique mode on an existing identity: site, logo SVG with live text, named fonts; no redesign.'
tags: [task, critique, site, en]
expected_outcome: 'brand.py critique with --logo and --fonts (and the site), findings per component with measured contrast ratios, the logo''s live <text> flagged, top three measurable fixes, no new identity built, user files untouched.'
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Critique our identity without mercy: the site is `site/index.html` (local, do not go online), the logo is `site/logo.svg`, headings are set in Lobster and body text in Lato. We write English and Turkish. I don't want a redesign, just tell me what is broken and what to fix first.
