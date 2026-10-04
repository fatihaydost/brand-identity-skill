---
name: create-site-keep-logo
description: 'Create with a local site and a kept logo: new type + palette around the fixed mark, previews on the site.'
tags: [task, create, site, keep, budget, en]
expected_outcome: 'init with logo=keep, site extract on the local file (no live competitor run), new type and palette built around the mark, site previews, board/review/table, the logo file byte-identical afterwards, checkpoint and stop.'
max_turns: 45
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

We're Mulberry, a third-wave coffee shop. Our site is `site/index.html` (a local file, do not go online). Keep our logo exactly as it is - `site/logo.svg` - but we want new fonts and a new colour palette that fit it: warm, but not the cliché coffee brown. Three directions please, shown on our site. English only; the menu shows prices.
