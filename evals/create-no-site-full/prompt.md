---
name: create-no-site-full
description: 'Create, no site: full identity (logo + type + palette), three sets, stops at the checkpoint.'
tags: [task, create, no-site, budget, en]
expected_outcome: 'init with en+tr and --numbers, one sets.json with three sets, logos then build, review.png looked at, board + review + table written, AI-default flags justified or one default-free set, checkpoint message per set, then a stop: no kit.'
max_turns: 45
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

We're launching Pedalka, a cargo-bike delivery co-op in Istanbul that brings goods from wholesalers to small neighbourhood shops. No website yet. We need the whole identity: logo, fonts and colour palette, and I'd like three directions to compare. The brand writes in Turkish and English, and our app is full of delivery times and prices. Audience: shop owners, mostly over 40. Three words: quick, neighbourly, sturdy. Competitors (names only, don't look them up): Kargoist, Yoldaş Kurye, BiKurye - all of them use a red or orange arrow.
