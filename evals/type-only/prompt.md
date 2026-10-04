---
name: type-only
description: 'Create, typography only: logo and palette are out of scope, the table collapses to the type column.'
tags: [task, create, type-only, budget, en]
expected_outcome: 'init with logo and palette set to none (or keep), --numbers and de+tr+en languages, a font search, three type sets with no symbols, table.md with only the Type component column, coverage and tabular figures reported, checkpoint and stop.'
max_turns: 45
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

We only need typography, nothing else. Ledgerline is a bookkeeping app for freelancers; the interface is in German, Turkish and English and it is full of figures (invoices, tax totals). Logo and colours are not part of this job - leave them out. Give me three font directions: a display face for headings and a text face for the interface.
