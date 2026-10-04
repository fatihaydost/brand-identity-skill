# Evals

Two suites, two formats. Neither tool reads the other's files.

| | Where | Runner |
|---|---|---|
| Plugin eval (CI-style, with a no-plugin baseline) | `evals/<case>/` (12 cases) | `claude plugin eval` (Claude Code 2.1.269 or newer) |
| skill-creator (iterate on the skill in a chat, human review) | `skills/brand-identity/evals/evals.json` (9 tasks), `trigger-evals.json` (20 queries: 11 should trigger, 9 should not; Turkish, German and Spanish among them) | skill-creator plugin |

Every run calls a model and is billed to your plan. Run them by hand, not on every push.

## Plugin eval

Nine cases are tasks and three (`negative-*`) are near-miss negatives (`brand-identity` must not fire). Each case has
`prompt.md` (frontmatter: tags, `max_turns`, `timeout_seconds`, `allowed_tools`), graders in `graders/`, and, when it
needs files, `case.yaml` plus `scaffold.sh`, which only copies fixtures into the empty run workspace.

| Case | Flow it measures | Seeded files |
|---|---|---|
| `create-no-site-full` | Create, no site, logo + type + palette, 3 sets → checkpoint, stop | — |
| `create-site-keep-logo` | local site + kept logo; new type and palette; site previews | `site/index.html`, `site/logo.svg` |
| `type-only` | fonts only; table collapses to the Type column | — |
| `palette-only-with-existing-logo` | palette only, logo and fonts kept, 2 sets, CSS-variable page | `site/index.html`, `brand/logo.svg` |
| `refresh-logo` | logo `refresh` of a dated mark; given colours kept | `brand/kestrel-logo.svg` |
| `mix-sets` | after the checkpoint: `brand.py mix A:logo A:type B:palette` | `brand-identity/ferrow/` (demo work folder + `sets.json`) |
| `kit-after-approval` | after the pick: `brand.py kit sets/A` (the checkpoint is described in the prompt, not replayed) | same as `mix-sets` |
| `critique-identity` | Critique mode: site + logo with live `<text>` + named fonts; no redesign | `site/index.html`, `site/logo.svg` |
| `fast-intake` | a one-line German request: one intake message, only blocking questions, reply in German | — |
| `negative-css-bug` · `negative-chart-colours` · `negative-photo-edit` | must not trigger | — |

What the graders check (all file and trace checks are free; `llm` graders use the judge model):

- **Work folder and contracts**: `brand-identity/<slug>/sets.json` has `brand-identity/sets@1`; `sets/X/identity.json`
  carries a `brand-identity/audit@1` audit; `board.png`, `review.png`, `table.md` exist; the table's columns match the
  components in `new`/`refresh`.
- **Flow**: `init` flags (`--state`, `--langs`, `--numbers`, `--sets`), `logos` before `build`, `review.png` or
  `logo-sheet.png` read, `site extract` on a local file and no `site competitors` run, `mix`/`kit` arguments.
- **Stop at the checkpoint**: no new file under `kit/` and no `brand.py kit` call in Create cases.
- **AI defaults**: no empty `defaults_used[].why` in `sets.json`; `default-free-or-justified` fails only when the
  `cohesion.no-default-free-set` warning is present and set A still carries an unjustified `default.*` finding.
- **Kept files**: the kept or refreshed source must match the fixture byte for byte (a regex anchored on the whole
  file, equivalent to an unchanged sha256), and no `Write`/`Edit` call may touch it.
- **Turn budget**: `max_turns` is 45 for Create cases (the release ceiling in `docs/architecture.md` §9), 25 after
  the checkpoint, so a run over budget ends before the checkpoint and fails its graders. The report's `turns` column
  gives the exact count (measured mean 40.5); read it there or with `tools/measure_run.py`, it is not graded.

Work-folder paths in the graders assume the slug of the brand named in the prompt (`pedalka`, `mulberry`,
`ledgerline`, `logline`, `kestrel`, `ferrow`). A run that calls `init` with another name fails those file graders;
check the trace before reading it as a skill failure.

```bash
claude plugin eval . --case 'negative-*' --runs 1                       # cheap: no Bash, no files
claude plugin eval . --scaffold --allow-tools Write Edit Bash --trust-plugin --max-cost-usd 20
claude plugin eval . --scaffold --allow-tools Write Edit Bash --case 'create-no-site-full' --runs 1
claude plugin eval . --tag budget --scaffold --allow-tools Write Edit Bash --runs 1   # the three budget-tagged Create cases
```

- `--scaffold` is needed for cases with fixtures (it runs the case's own bash script as you; they only copy files).
- Task cases need `Bash`, `Write` and `Edit` granted. Bash runs in Claude Code's OS sandbox (Linux needs
  `bubblewrap` and `socat`). Renders and site previews need a Chromium-based browser that works inside it.
  Fonts come from Google Fonts, so the sandbox needs network for them or a warm cache in
  `${XDG_CACHE_HOME:-~/.cache}/brand-identity/`.
- The `skill-fired` graders are indicators in the two-arm run, not part of the score. The score difference between
  the with and without arms is what the skill contributes.
- `mix-sets` and `kit-after-approval` copy `skills/brand-identity/templates/demo/` and `templates/sets.example.json`,
  so they follow the shipped demo (fictional brand "Ferrow", three sets).

## skill-creator suite

`evals.json` mirrors the nine task cases with human-readable expectations. `files` paths are relative to the skill
folder; for `mix-sets` and `kit-after-approval` place `templates/demo/` at `brand-identity/ferrow/` and
`templates/sets.example.json` at `brand-identity/ferrow/sets.json` before the run. `trigger-evals.json` covers new
brands, single components (fonts, logo + font), refresh, critique, a guidelines PDF and a bare "branding"; the
negatives are near misses (CSS bug, chart colours, photo editing, a slide deck, colour conversion, wall paint, an
editor theme, a trivia question, token extraction from a screenshot).

## Fixtures

| File | Purpose |
|---|---|
| `fixtures/site-cafe/index.html`, `logo.svg` | "Mulberry" coffee shop: local static site, no CSS variables, brown and cream, header `<img>` logo kept as-is |
| `fixtures/site-saas/index.html` | "Logline": local page with CSS custom properties, blue |
| `fixtures/logline-logo.svg` | Logline's one-colour symbol (kept) |
| `fixtures/kestrel-logo-2011.svg` | a dated courier logo for `refresh`: bird, gradient swoosh, drop-shadow filter |
| `fixtures/site-dental/index.html`, `logo.svg` | "Sunnyside Dental": low-contrast text and buttons (1.3:1 to 2.3:1), Lobster headings, Lato body, a logo with live `<text>` |

All businesses in the fixtures are fictional.
