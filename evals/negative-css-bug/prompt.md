---
name: negative-css-bug
description: 'Near-miss: a CSS bug about a hover colour; brand-identity must not be used.'
tags: [negative, trigger, en]
expected_outcome: 'brand-identity is not invoked (CSS bug).'
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

My `button:hover` background colour doesn't change in Safari 17 but works in Chrome. What could cause that?
