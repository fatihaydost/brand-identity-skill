# How brand-identity is tested

## Code

- **474 unit tests** (`python -m unittest discover -s tests`), run in CI on Linux, macOS and Windows with Python 3.10
  and 3.12. They include the live-site tool (`tests/test_site_palette.py`, ported from the former Node tests) on
  the fixture pages in `tests/site/fixtures/`, the bundled language data against gflanguages
  (`tests/test_languages_data.py`, needs `requirements-dev.txt`) and the uv re-run of the entry scripts
  (`tests/test_pydeps.py`).
- **Node-to-Python equivalence** (when the live-site tool moved from Node + playwright-core to Python + CDP): both
  tools ran the same 25 cases on fixture and mock pages (extract, palette and identity apply, dark mode, bot walls,
  competitors); all 204 output files, stdout and exit codes were identical (PNGs byte for byte, JSON apart from
  timestamps). Live sites vary run to run with either tool.
- **Smoke test** (`python tools/smoke_test.py`): every script runs end to end the way an agent calls it, the plugin
  manifests agree, SKILL.md stays under 8 KB with a description under 600 characters, and each reference card stays
  under its byte cap.
- **Rendered-output probes**: every card, board and kit page is checked in the browser after it is drawn: fonts
  actually loaded (not a fallback), no clipped text, text contrast, logo size, logo contrast against its own ground,
  fonts embedded in the PDF.

## Real runs

The skill was run 21 times end to end by an agent acting as the user, in three rounds, each round followed by fixes:
six fixed briefs (a Lisbon coffee shop, a London law firm, an AI start-up, a Burgundy wine estate, a crypto wallet, an
Istanbul breakfast restaurant), a live site whose logo was kept while type and palette were redesigned, and a
type-only request. Each run ended at the checkpoint with three identity sets; the agent reported every point where a
tool forced it to change a design. Round 1 found 21 such issues, round 3 one, which was fixed with an end-to-end test.

Before release two more runs (the bookshop and the developer tool in the README) went all the way to the guidelines
kit in a clean Debian container, as a non-root user with only Python, Chromium and uv installed. They found six
issues: Chromium needing `--no-sandbox` in such a container (now a fallback, and `check` starts the browser for
real), dark-first palettes taking the night ground as their seed, the card strip showing the light mode on a dark
card, logo parts too faint on one ground, small symbols used for large avatars, and unlabelled sample details on the
business card. Each fix has a test that fails without it; both runs were rebuilt with the fixed code.

## AI defaults

`tools/ai_defaults_experiment.py` asks plain models for a brand identity on 30 briefs (180 answers) and counts what
they reach for. The answer is not "AI purple": it is cream ground, terracotta, a gold accent, Fraunces headings, Inter
body text, and a logo that draws the name's object around a hidden letter in a circle. On the same six briefs:

| | answers / sets | AI-default hits (type and palette) | sets with none |
|---|---|---|---|
| Plain model | 24 answers | 4.38 per answer | 0 of 24 |
| brand-identity | 18 sets | 0.28 per set | 13 of 18 |

Every brief got at least one set with no AI default at all; every remaining hit carries a brief-grounded reason.

## Budget

`tools/measure_run.py` reads a Claude Code transcript and reports turns, tokens and image reads. A full run (three
sets of logo, type and palette, to the checkpoint) measured 40.5 turns and about 0.86 M billed-input-equivalent tokens
on average; the release ceiling is 45 turns and 1.0 M (`docs/architecture.md` §9).

## Independent review

Before release an independent reviewer read the code and the skill text, reproduced every finding, and reviewed again
after each fix until it approved.
