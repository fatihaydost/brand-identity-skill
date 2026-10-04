---
name: palette-only-with-existing-logo
description: 'Create, palette only: the logo and fonts are kept, two palette sets shown on a page with CSS variables.'
tags: [task, create, palette-only, site, keep, en]
expected_outcome: 'init with logo=keep, type kept or none, --sets 2; no font search; two built palettes; table with only the Palette column; the kept logo file byte-identical; checkpoint and stop.'
max_turns: 45
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

We're Logline, a log search tool for developers. Our page is `site/index.html` (colours are CSS custom properties in :root; local file, do not go online). The logo `brand/logo.svg` stays exactly as it is and we keep our system fonts. We only want a new colour palette: every dev tool is blue and we get lost in the sea of blue. Two directions, shown on the page.
