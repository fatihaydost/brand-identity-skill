---
name: mix-sets
description: 'After the checkpoint the user asks for parts of different sets: brand.py mix builds a new set.'
tags: [task, mix, en]
expected_outcome: 'brand.py mix with A:logo, A:type and B:palette into a new set, board re-rendered, cohesion warning reported, no hand-edited sets.json, no kit before the user confirms.'
max_turns: 25
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

We looked at the three Ferrow directions in `brand-identity/ferrow/`. I like the logo and the fonts of A (Spoke Line) but the colours of B (Workshop Ledger). Can you put that combination together so I can see it next to the others?
