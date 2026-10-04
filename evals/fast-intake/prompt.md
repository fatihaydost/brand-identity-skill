---
name: fast-intake
description: 'Thin request in German: one intake message with only the blocking questions, in German; nothing built yet.'
tags: [task, intake, non-english]
expected_outcome: 'The skill fires and asks one message in German: per component new / refresh / keep / none, number of sets (default 3), site or files, languages, numbers; sector is not asked again. No sets.json written, nothing built.'
max_turns: 12
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Ich brauche ein Branding für meine Bäckerei.
