---
name: refresh-logo
description: 'Refresh an existing logo: keep the recognisable bird, drop the dated gradient and shadow; colours stay.'
tags: [task, create, refresh, logo, en]
expected_outcome: 'init with logo=refresh, symbols drawn as primitives in sets.json (no <text>), logos run and the contact sheet looked at, build, the original SVG untouched, the given colours kept in the palettes, checkpoint and stop.'
max_turns: 45
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Kestrel is a same-day courier in Leeds. Our logo `brand/kestrel-logo.svg` is from 2011: people know the bird, but the gradient swoosh and the drop shadow feel dated. Refresh the logo and keep what people recognise. Our colours stay as they are (navy #1d3557 and signal orange #e76f51); a new typeface for the wordmark is fine. Three options, English only.
