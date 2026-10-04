# brand-identity — architecture

The contracts and moving parts of the skill: what each script does, the files they share and the rules they enforce.
Section numbers are stable; code comments refer to them (`docs/architecture.md section 4.3`).

## 0. Principles

- **Few rules, real measurement.** A gate exists only where the thing is measurably broken or irreversible (contrast,
  licence, glyph coverage, file validity, a render exists). Design preferences are warnings at most. Every numeric
  threshold not from a primary source is labelled "heuristic, uncalibrated" in code and docs. No overall scores.
- **Token budget is a feature** (§9). Scripts print short summaries; detail goes to files.
- English everywhere. Python ≥ 3.10 on Linux, macOS and Windows plus a Chromium-based browser; no Node. Python
  packages come through uv when it is installed (§4.0).
- **Only `pipeline.py`/`brand.py` write `identity.json`** (through `identitylib.save_identity`); modules return values.

## 1. What the skill does

Logo + typography + colour palette as **coherent identity sets**, compared side by side, then a guideline kit for the
chosen one.

| Mode | Trigger | Output |
|---|---|---|
| **Create** | new brand, or any subset of logo / type / palette wanted | N sets → cards + comparison table (+ site preview) → user picks → kit |
| **Critique** | "what do you think of our identity / logo / fonts / colours", URL or files | gates + short findings per component + top-3 measurable fixes |

Refresh is Create with a component in `refresh` state.

### 1.1 Intake (one message, only unresolved blocking questions)

Per component — **logo, type, palette** — one of `new` · `refresh` (user has one, evolve it) · `keep` (use as-is) ·
`none` (not needed). `none` + an existing asset → shown and used as a constraint; `none` + nothing → its block
collapses. Plus number of sets (default **3**, 1–4; 4 is outside the budget), site URL or files, brand languages, and
whether the product shows numbers (tabular figures). With a site, read it first (`brand.py site extract`) and only ask
what is still missing. Sector, audience, 3 attributes and competitors are asked only if the site/files do not answer.

### 1.2 Sets

- Each set has one **mechanism** (one sentence: the single visual idea that yields the mark, the type choice and the
  palette together) and one **expression move** (the one loud thing).
- 2–4 **decision axes** per brief from `references/cohesion.md` (warm–cool, classic–contemporary, playful–serious,
  premium–accessible, quiet–loud, friend–authority), scored −2..+2.
- Sets should differ on ≥ 2 axes and a set's components should sit at the same position — **warnings**, uncalibrated.
- At least one set passes the **reflex test** (§6.3) — a board-level warning if none does. One set is RECOMMENDED.
- `keep` components are fixed constraints; new parts are made to fit them. A conflict is reported as a suggestion; the
  kept component is never changed (gate: its file sha256 is unchanged after every build).
- Mixing on request (`brand.py mix`): new set D from named parts; D's logo is re-coloured to D's palette; cohesion
  warning shown.

### 1.3 Build order inside a set

mechanism → type (display + text) → logo master in one colour (wordmark outlined from the display face; symbol from
primitives) → palette → logo colour versions → card.

### 1.4 Checkpoint, then kit

Board + table (+ site preview). **Stop and wait for the user's pick.** Then the kit (§7.3). Never self-approve.

## 2. Repository layout (repo root = plugin root = marketplace root)

```
.claude-plugin/{plugin.json, marketplace.json}      # name "brand-identity", version 1.0.0
.claude/ (.gitkeep, eval guard)   .github/workflows/test.yml
README.md  LICENSE  THIRD_PARTY_NOTICES.md  TRADEMARKS.md  requirements.txt  requirements-dev.txt
docs/{architecture.md, testing.md}
evals/   tools/{smoke_test.py, package_skill.py, skillmeta.py, build_font_catalog.py, export_languages.py, measure_run.py,
               ai_defaults_experiment.py}
tests/   tests/fixtures/fonts/{Arvo-Regular.ttf (static, lacks Turkish), Outfit[wght].ttf (variable), OFL-*.txt}
skills/brand-identity/
  SKILL.md (≤ 8 KB)   references/*.md (§8)   scripts/ (§4)
  templates/{identity.example.json, palette.example.json, sets.example.json, card/, board/, kit/, mock-sites/}
  assets/{fonts/catalog.json, fonts/gf-latin-core.txt, languages.json.gz, ai-defaults.json, library/ (website palettes),
          i18n/{en.json (canonical tool text), tr.json} (§3.6)}
dist/ (packager output, ignored)
```
Network tests are opt-in (`BI_NETWORK_TESTS=1`); CI installs `requirements-dev.txt` (requirements.txt + gflanguages)
on Python 3.10 and 3.12.

## 3. Contracts

### 3.1 Work folder and files

```
<cwd>/brand-identity/<brand-slug>/
  brief.json            # intake answers (schema below)
  sets.json             # the model's ONE draft file for all sets (§3.4); synced into sets/X/ by logos/build
  site/                 # extract.json, competitors.json, screenshots
  sets/X/ identity.json  palette.json  logo/symbol.svg  logo/symbol-small.svg (optional)  logo/build/*
          type/fonts.json (resolved files + locations)  type/specimen.png  card.html  card.png
  board.html  board.png (user-facing)  review.png (≤ 2000 px long edge, for the model)  table.md
  kit/<SET>/  .cache/ (font instances, intermediate renders; never shipped to the user as fonts)
```

`brief.json` (`brand-identity/brief@1`):
```jsonc
{ "schema": "brand-identity/brief@1", "brand": "Moonvault", "tagline": "", "site": null,
  "languages": ["en", "tr"],            // BCP-47-ish; mapped to gflanguages ids (tr -> tr_Latn) by typelib
  "doc_lang": "tr",                     // language of the tool text on cards, board, kit (§3.6); default languages[0]
  "numbers": true,                      // product shows figures -> tnum check
  "components": { "logo": "new", "type": "new", "palette": "keep" },
  "sources": { "palette": "site/extract.json" },   // for keep/refresh/none-with-asset
  "sets": 3, "sector": "", "audience": "", "attributes": [], "competitors": [], "axes": ["warm_cool", "quiet_loud"] }
```
Component state's authority after `init` is each `identity.json` (copied from the brief).

### 3.2 `identity.json` (`brand-identity/identity@1`) — per set, written only by pipeline/brand.py

```jsonc
{
  "schema": "brand-identity/identity@1",
  "brand": { "name": "Moonvault", "tagline": "…", "languages": ["en", "tr"], "doc_lang": "en", "sector": "…",
             "numbers": true },                     // doc_lang optional (§3.6); missing = languages[0]
  "set": { "id": "A", "name": "Night Ledger", "recommended": true, "mechanism": "…", "expression_move": "…",
           "differs_by": "…" },
  "axes": { "warm_cool": 1, "classic_contemporary": 1, "quiet_loud": -1 },          // −2..+2
  "components": { "logo":    { "mode": "new|refresh|keep|none", "status": "proposed|fixed|not_applicable",
                               "source": null, "sha256": null },
                  "type": { … }, "palette": { … } },
  "type": {
    "pairing_mode": "single-family|superfamily|contrast|same-designer|data-face",
    "display": { "family": "Fraunces", "source": "google|fontshare|commercial|user", "license": "OFL-1.1",
                 "weights": [600],
                 "location": { "wght": 600, "opsz": 144, "SOFT": 0, "WONK": 0 },   // EVERY fvar axis pinned
                 "features": [], "case": "as-is|upper|lower", "tracking": -10,     // tracking: 1/1000 em
                 "fallback": "Georgia, serif", "file": null },                    // file: user-supplied font path
    "text": { …, "location": { "wght": 400, "opsz": 14 } },
    "mono": null
  },
  "logo": {
    "type": "wordmark|symbol+wordmark|symbol|monogram",       // emblem is out of v1
    "concept": "…",
    "symbol": "logo/symbol.svg",                               // null for wordmark
    "wordmark": { "text": "Moonvault", "role": "display", "location": null, "tracking": -10, "case": "as-is" },
    "monogram": null,                                          // { "letters": "MV", "role": "display" }
    "lockups": ["horizontal", "stacked"],
    "colors": { "symbol": "primary", "wordmark": "text" },    // palette role key or brand id; resolved per surface mode
    "tags": ["literal-object", "hidden-letter"],              // vocabulary owned by assets/ai-defaults.json
    "clear_space": { "unit": "cap-height|symbol-half|x-height", "multiple": 1, "inferred": true },
    "min_size": { "px": 24, "mm": 8 },
    "misuse": [ { "do": "stretch" }, { "do": "crowd" }, { "do": "recolour" },        // optional, 4–6 items (§7.3)
                { "svg": "logo/misuse-1.svg", "label": "Don't fill the stencil gaps" } ]
  },
  "palette": "palette.json",
  "palette_build": { "strategy": null, "accent": null, "light_bg": null, "dark_bg": null,
                     "neutral_tint": null, "neutral_chroma": null, "extra": [] },   // passed to palette_build.py
  "defaults_used": [ { "id": "fraunces-display", "why": "brief-grounded reason" } ],
  "rationale": [ { "claim": "…", "evidence": "strong|limited|practice", "source": "…" } ],
  "derived": [ { "file": "card.png", "requires": ["logo", "type", "palette"], "inputs_sha256": "…" } ],
  "audit": { "schema": "brand-identity/audit@1", "passed": true, "counts": { "gate": 0, "warn": 2, "info": 1 },
             "findings": [] }
}
```

- `inputs_sha256` = sha256 over the canonical JSON (sorted keys, no spaces) of the `requires` subtrees of identity.json
  plus the bytes of every file they reference (symbol SVG, palette.json, font files). A rebuild skips outputs whose hash
  is unchanged and marks changed ones stale (no deletes). Palette change → logo re-coloured, not re-drawn; type change
  → wordmark and lockups rebuilt, symbol kept.
- `wordmark.location` null = the display face's location with `wght` from `type.display.weights[0]`.

### 3.3 `palette.json`

`brand-identity/palette@1` (`templates/palette.example.json` is a full example). For a `new`/`refresh` palette the
model writes a **partial** palette (`brand[]` seeds, `name`, `direction`, optional `feels`); the pipeline runs
`palette_build.py --from palette.json` with `identity.palette_build` options; "built" = `scales` and both `modes`
present. `refresh` seeds from `components.palette.source` (a palette.json or `site/extract.json` roles). `keep` reads
the source as-is (built by `palette_build` only if it lacks modes, written to the set folder, source untouched).

### 3.4 `sets.json` — the model's single draft (`brand-identity/sets@1`)

One file the model writes in one turn (and edits in parallel calls), holding every set's identity fields, its partial
palette, and its symbol SVG inline:
```jsonc
{ "schema": "brand-identity/sets@1",
  "sets": [ { "identity": { /* identity.json fields without schema/brand/components/derived/audit */ },
              "palette": { /* partial palette@1 */ },
              "symbol_svg": "<svg viewBox=\"0 0 100 100\">…</svg>", "symbol_small_svg": null } ] }
```
An entry may also carry `misuse_svgs: {"misuse-1": "<svg…>"}`, written to `logo/misuse-1.svg` (§7.3).
`logos` and `build` sync it into `sets/X/` (identity.json merged with brief data, palette.json, logo/symbol.svg) before
running; the set folders are the build's truth, `sets.json` is the authoring surface. `templates/sets.example.json`
is complete and valid.

### 3.5 Findings and audits (one shape everywhere)

`finding = {id, component: logo|type|palette|cohesion|card|kit|site, severity: gate|warn|info, measured, threshold,
message, suggested_fix, roles?: [...]}`. `identity.audit` as in §3.2. palette.json keeps its own full audit object;
its findings are copied into `identity.audit.findings` with `component: "palette"`.

### 3.6 Tool text language (`i18nlib.py`, `assets/i18n/`)

Two languages, kept apart. `brand.languages` = what the brand writes in (font coverage gate, specimen sentences,
mock copy, brand lines on cards); unchanged by the document language. `brand.doc_lang` = the language of every
label, heading, caption and table header the scripts put on a user-facing output: card, board, `review.png`,
`table.md`, kit pages, `fonts.md`, `tokens/swatches.html`, the logo contact sheet, the board's site-preview
captions. Set by `init --doc-lang` (default: the first of `--langs`); an identity.json without it uses
`languages[0]`. **brief.json is the authority:** `build` copies `brief.doc_lang` into every identity.json, so change
it in brief.json (an edit in identity.json is overwritten by the next build).

- One catalogue: `assets/i18n/en.json` (canonical, flat `key -> text`, `{placeholder}`s; plurals as `<key>.one` /
  `<key>.other`) and full translations (`tr.json`). Code never hard-codes user-visible text; an unknown key raises.
- Lookup for doc_lang L: `<work>/i18n.json` (the agent's translation of en.json, used when its `_lang` is absent or
  L) → `assets/i18n/L.json` → `en.json` per key. Every fallback is reported: one `i18n.missing` warn (component
  `card` from build, `kit` from kit) naming the keys, or saying no translation exists, with the fix (translate
  en.json to `<work>/i18n.json`). No translation at all → English text and English casing.
- `lang`: the root element of each output carries doc_lang (or `en` when nothing is translated), so CSS
  `text-transform` cases the labels by their own language (Turkish i → İ only on Turkish text); brand text (brand
  name, display line, specimen sentences, mock site and header) keeps `lang` = its brand language.
- Findings: messages are English and written for the agent (CLI, audit). A non-English document never shows
  them: `Strings.finding()` gives `finding.<id>` from the catalogue (longest id prefix, or the id without its
  component prefix), else `finding.generic.<severity>` naming the id ("Unresolved blocking issue: <id>"). This
  holds for gates and warnings on the card, board, review, table.md and the contact sheet.
- Numbers in labels follow doc_lang's decimal mark (contrast ratios "14,2:1" in Turkish).
- Mock fallback copy (menu, call to action when brand.copy has none) is brand text: in the brand's first language
  (its shipped catalogue, else English), under that language's `lang`.
- Not translated: CLI stdout/stderr, font style names (Regular, Bold), units and codes (HEX, OKLCH, px). Free text
  the model writes (set names, mechanism, concept, rationale, misuse labels, palette names, sector) is written in
  doc_lang by the model (SKILL.md).
- Test: a pseudo-locale (`tests/test_i18n.py`, every value wrapped in ⟦…⟧ through `<work>/i18n.json`) renders card,
  board, review, table.md, kit, fonts.md, swatches and contact sheet; any visible Latin word outside the markers,
  brand data or the exempt classes (hex, numbers, units, font names) fails.

## 4. Scripts

### 4.0 Conventions and dependencies

- `requirements.txt`: `fonttools[woff]`, `uharfbuzz`, `skia-pathops`. No `glyphsets`; the language data is bundled
  (`assets/languages.json.gz`, exported from gflanguages by `tools/export_languages.py`; gflanguages is in
  `requirements-dev.txt` only, and `tests/test_languages_data.py` checks the export field by field against it).
- `pydeps.ensure()` at the top of the entry scripts (brand.py, logo_audit, identity_card, identity_board, kit_build):
  when a package is missing and uv is found it re-runs the same command through `uv run --no-project
  --with-requirements requirements.txt` (cached, isolated, never installed into the user's Python; a first `uv run`
  probes the imports so an offline failure is reported, not fatal); else it uses the venv at
  `${XDG_CACHE_HOME:-~/.cache}/brand-identity/venv` when present. `BRAND_IDENTITY_ENV` marks the re-run (no second
  hop). uv is never installed by the scripts. `brand.py check` lists what is missing and prints the uv installer line
  and a pip-in-a-venv line (PEP 668 safe); no silent fallbacks.
- Every command: `--help` with examples; stdout ≤ 1.2 KB by default (`--full`, `--json`); diagnostics → stderr;
  non-zero exit on failure; UTF-8 console fix.
- Network only for fonts (Google Fonts CSS/files, GitHub raw google/fonts) and site work; cache under
  `${XDG_CACHE_HOME:-~/.cache}/brand-identity/` plus the work folder's `.cache/`.
- `render_png.screenshot_html(...)` is the stable low-level screenshot call; `render_png.render(...)` adds the probe.

### 4.1 `brand.py` — the one entry point

Handlers: `font_audit.cli_fonts`, `logo_audit.cli_logos`, `site_preview.cli_site`, `kit_build.cli_kit`,
`pipeline.cli_build|cli_mix|cli_critique`; each takes the argparse namespace, returns an exit code.

| Command | Does | Prints |
|---|---|---|
| `check` | Python packages (and the uv environment in use), browser (started for real, §4.7) | ok / install lines |
| `init NAME [--site URL] [--sets 3] [--langs en,tr] [--doc-lang tr] [--numbers] [--state logo=keep …]` | work folder, brief.json, sets.json skeleton, set folders | paths |
| `site extract URL WORK` / `site competitors WORK URL…` | colours, fonts, logo, signals | ≤ 15 lines |
| `fonts search [--class …] [--mood …] [--exclude-defaults]` | catalogue query | ≤ 12 rows |
| `fonts audit FAMILY [--langs …] [--numbers]` | §4.3 checks on one family | finding lines |
| `logos WORK [--sets A,B]` | sync sets.json → fonts → wordmarks → symbols → lockups → one-colour checks → **one contact sheet** | sheet path + gate lines |
| `build WORK [--sets …]` | §4.2; calls `site apply` itself when brief.site is set | ≤ 1.2 KB table + failures + paths |
| `site apply URL WORK [--sets …]` | re-apply to the site, re-render board | paths + layoutStress |
| `mix WORK A:logo A:type B:palette --as D` | §1.2 | like build |
| `kit SET_DIR [--full]` | §7.3 | pages + PDF path + gates |
| `critique [--site URL] [--logo f.svg] [--fonts …] [--palette p.json] [--langs …]` | audits on existing assets | findings summary |

### 4.2 `build` pipeline (`pipeline.py`, per set; a gate failure stops that set, others continue)

1. sync sets.json; validate identity.json + palette.json; check `keep` sources' sha256.
2. fonts: `resolve_font` for every role at its pinned location → font audit.
3. logo: wordmark / monogram outlines → symbol boolean resolve → lockups → colour versions → logo audit.
4. palette: build if not built (§3.3) → `palette_audit.py --context identity`.
5. defaults: match against `assets/ai-defaults.json` (fonts, OKLCH regions, `logo.tags`) → flags without a
   `defaults_used[].why` are warnings shown on the card and in the table.
6. render the card through `render_png.render` with the probe (§4.7) → card gates (fonts loaded, overflow, text
   contrast, logo px).
7. write `identity.audit` and `derived[]`.
After all sets: cohesion warnings (axis distance between sets, default-free set exists), board, `review.png`, table.
A set that failed a gate still gets a card, marked FAILED with its first gate message.
stdout: one row per set (name · mechanism · gates ✔/✘ · warnings · flags), then failures, then paths.

### 4.3 Type: `typelib.py`, `font_audit.py`, `tools/build_font_catalog.py`

- **Catalogue** `assets/fonts/catalog.json`, built offline from google/fonts METADATA.pb (family, category, designer,
  axes with ranges and defaults, subsets, licence), the fonts.google.com metadata endpoint for popularity (build time
  only; the build tolerates its absence) and our own fontTools measurements (x/cap ratio, stroke contrast, width,
  weights, `tnum`, `locl`, base-exemplar coverage per language, I/l/1 raster similarity, woff2 size estimate).
  Ship only computed fields and factual metadata; Google's `tags/*.csv` scores are fetched at runtime into the cache
  (licence unconfirmed), never committed. Fontshare entries: `license: "ITF-FFL-2.0"`, `render: false`.
  `ai_default` flag from `ai-defaults.json`.
- **`resolve_font(family, location=None, source=None, file=None) -> Path`**: downloads/caches the family, returns a
  **static instance** with every fvar axis pinned (`fontTools.varLib.instancer`, overlaps removed). Instances live only
  in caches: they are OFL Modified Versions and are never written to user outputs as font files.
  `font_info(path) -> dict` (names, metrics, axes, features, cmap size).
- **Coverage:** for each brief language, map to a gflanguages id via its default script (`tr` → `tr_Latn`; data from
  the bundled `assets/languages.json.gz`); **gate**:
  100% of the precomposed **base** exemplars (combining-mark entries skipped); auxiliary + punctuation: warn;
  GF Latin Core (vendored `assets/fonts/gf-latin-core.txt`, Apache-2.0, notice line): info. Non-Latin languages:
  the same rule on their base exemplars; if the catalogue has no family covering them, say so.
- **Other checks:** licence known and covers the use (logo outline, web, app, PDF embed; commercial/FFL → note) — gate;
  `tnum` when `brand.numbers` — gate; text family with < 2 usable weights (a wght axis spanning 400–700 counts) — warn;
  I/l/1 similarity IoU > 0.9 — warn (suggest `ss`/`cv`), heuristic; high stroke contrast in the text role — warn;
  total web weight — info.
- **Rendering policy:** Google Fonts (OFL/Apache/UFL) rendered and outlined; Fontshare link + note only; commercial →
  name + licence note + a rendered free alternative; user files used as given, never redistributed. No subsetting in
  outputs. Locale-aware casing for `case: upper|lower` using the first brief language (Turkish i → İ) and the HarfBuzz
  buffer language set so `locl` applies; CSS uses the same `lang`.

### 4.4 Logo: `logolib.py`, `logo_audit.py`  (craft notes: `references/logo.md`)

- `wordmark_svg(text, font_path, location, features, tracking, case, lang) -> str`: HarfBuzz shaping (kerning on,
  ligatures off unless listed in features) → outlines → overlaps removed (pathops) → one `<g id="wordmark">` with
  `data-cap-height`, `data-baseline`, `data-advance` in viewBox units where cap height = 100.
- `text_path(text, font_path, location, features, lang) -> str`: same outlining for monogram letters.
- `resolve_symbol(svg_text) -> str`: model-authored symbol on a 100-unit grid from primitives; `data-op=
  "union|subtract|intersect"` resolved with skia-pathops; strokes outlined; viewBox normalised; one-colour groups by
  `data-color` (palette role).
- `lockup(symbol_svg, wordmark_svg, kind) -> str`: horizontal/stacked from cap height units; optical alignment to cap
  height, not the box.
- `variants(identity, palette, out_dir) -> dict`: full colour, one-colour dark, one-colour light, on each palette surface,
  symbol only, favicon 16/32/48 (uses `symbol-small.svg` when present), app icon 512 with safe area; SVG + PNG.
  Roles resolve in the mode of the surface they sit on.
- `logo_audit.audit(identity, palette, build_dir) -> list[finding]`. Gates: master has no `<text>/<image>/filter/
  foreignObject` and has a viewBox; colours only from palette roles; 16 and 32 px renders exist; every coloured part
  ≥ 3:1 against each surface it is placed on, or a tile/outline device declared (largest-area part is the main check,
  the others warn). Warns (heuristic, uncalibrated): more than 3 colours without a reason; smallest feature narrower
  than 1 px at 16 px (raster erosion test); false holes (non-zero vs even-odd raster difference); wordmark font licence
  missing.
- `cli_logos`: sync, build all sets, render **one contact sheet** (each set: primary large, 32 and 16 px, one-colour on
  light and dark) ≤ 1600 px long edge.

### 4.5 Colour

`colorlib.py`, `palette_build.py`, `palette_audit.py`, `export_tokens.py`, library tools. Default stdout trimmed to the summary contract (`--full` restores tables); `palette_audit --context identity` skips the
checks the identity audit owns (card-level text contrast).

### 4.6 Site: `site_palette.py`, `site/site_engine.js`, `cdplib.py`, `site_preview.py`

`apply --identity sets/X/identity.json`: colours as today, plus fonts served to the page as `FontFace`s from the
font bytes, `font-family` rewritten for the heading/body roles found by `extract` (stylesheets are rewritten in
flight, `<style>` rules and inline styles in the page), `size-adjust` set from the x-height ratio of new vs old face
(measured in-page), `font-optical-sizing: none` with the role's `font-variation-settings`; header logo replaced
(img/svg/text logo found by `extract`) inside ±10% of its box. `layoutStress` = elements that overflow or gain
lines, after vs before. Mocks (`templates/mock-sites/`) gain `{{LOGO_SVG}}`, display/text font slots. `cli_site`
implements `site extract|competitors|apply` by running `site_palette.py` with `sys.executable`.

The tool was Node + playwright-core until 0.2; it is now Python (standard library) + a Chromium-based browser. The
JavaScript that runs in the page is unchanged in `site/site_engine.js` (page functions are sent with their source,
as `page.evaluate` did); the pure functions of the old Node side (`buildRoles`, `layoutStress`, `parkedReason`, the
colour and font libraries) run unchanged in a blank engine page of the same browser, so the arithmetic is the same
JavaScript. Values cross the protocol as a tree that keeps NaN, ±Infinity, −0 and undefined; JSON files are written
exactly as `JSON.stringify(v, null, 2)` writes them. What `cdplib.py` does for each playwright-core feature used:

| Playwright (Node tool) | CDP in `cdplib.py` |
|---|---|
| `chromium.launch({headless})` | same executable search; Playwright 1.63's switches except `--no-sandbox` (start-up below); `--remote-debugging-pipe` + `--no-startup-window` on POSIX (over the websocket port Chrome orders custom properties and rasterizes images differently), websocket port on Windows |
| `browser.newContext({viewport, deviceScaleFactor, isMobile, hasTouch, userAgent, reducedMotion, colorScheme, bypassCSP, locale, ignoreHTTPSErrors})` | `Target.createBrowserContext` + paused `Target.createTarget` (auto-attach, `waitForDebuggerOnStart`); then, in Playwright's order: `Page.enable`, `Log.enable`, lifecycle events, `Runtime.enable`, utility world, `Network.enable`, `Target.setAutoAttach`, `Emulation.setFocusEmulationEnabled`, `Page.setBypassCSP`, `Security.setIgnoreCertificateErrors`, `Browser.setWindowBounds`, `Emulation.setDeviceMetricsOverride` (+ screen orientation), `Emulation.setTouchEmulationEnabled`, `Emulation.setUserAgentOverride` (+ client hints), `Emulation.setLocaleOverride`, `Page.setFontFamilies` (headless defaults), `Emulation.setEmulatedMedia`, `Runtime.runIfWaitingForDebugger` |
| `page.goto(url, {waitUntil: 'domcontentloaded', timeout})` | `Page.navigate` → first committed document → `DOMContentLoaded` lifecycle event; response = the main document's final `Network.responseReceived`; error texts as Playwright's (`page.goto: net::… at URL`) |
| `page.waitForLoadState('networkidle')` | per-frame inflight requests (favicon, EventSource excluded), 500 ms idle timer, page idle when every frame is, sticky until the next commit |
| `page.evaluate(fn, arg)` | `Runtime.evaluate` of `(fn)(arg)` in the main world, awaited, result by value through the tree |
| `page.addStyleTag({content})` | the same `<style>` insertion; plus the capture listeners Playwright's injected script registers when it first describes the returned handle (non-passive touch listeners change image rasterization) |
| `page.screenshot({animations: 'disabled', caret: 'hide', fullPage, clip})` | screenshot preparation in every frame's utility world (finish/cancel animations, hide carets), `document.fonts.ready`, `Page.getLayoutMetrics`, `Page.captureScreenshot` with Playwright's clip and `captureBeyondViewport` |
| `locator(sel).first().screenshot()` | visible + stable (two animation frames) check, `DOM.scrollIntoViewIfNeeded`, `DOM.getBoxModel`, enclosing integer rect |
| `newCDPSession` + `Page.captureScreenshot` (header) | the same call on the page session |
| `page.emulateMedia({colorScheme})` | `Emulation.setEmulatedMedia` on every frame session |
| `page.route('**/*')` + `route.fetch()` + `route.fulfill()` (stylesheets) | `Fetch.enable` for stylesheets at the response stage + `Network.setCacheDisabled`; `Fetch.getResponseBody`, rewrite in the engine page, `Fetch.fulfillRequest` (redirects continue) |
| `page.on('response')` + `response.text()` (stylesheets) | body read with `Network.getResponseBody` as soon as each OK stylesheet finishes |
| `context.request.get(src)` (logo download) | `urllib` GET, 20 s, ≤ 5 http(s) redirects, browser user agent, TLS errors ignored (as `ignoreHTTPSErrors`) |
| `browser.newPage()` for the k-means of the screenshot | the engine page |
| OOPIFs and workers | `Target.setAutoAttach` on the page; iframes get the same set-up, workers `Runtime`/`Network`; their requests count for network idle |
| `competitors --concurrency 3` | three browsers over the pipe (one client each), or three websocket clients |

Equivalence (2026-10-03, Chrome for Testing 153, Linux): on the 25 fixture/mock cases every output file, stdout and
exit code is the same as the Node tool's (204 files; PNGs byte for byte, JSON identical apart from timestamps). On
live sites both tools vary run to run (content, custom-property enumeration order, a few anti-aliased pixels); the
differences between them are of the same kind.

### 4.7 Presentation: `render_png.py`, `identity_card.py`, `identity_board.py`, `kit_build.py`

- `render(html, out, width, height, pdf=False, pages=False, probe=True) -> dict`: one CDP session; returns
  `{png|pdf|pages, fonts: [{family, weight, status}], overflow: [selector…], text_contrast: [{selector, ratio,
  required}], logo_px: {selector: px}}`. Font gate: every expected family+weight has a `FontFace` with status
  `loaded` and each text role's computed family resolves to it (not `document.fonts.check`). `--pages`: clip-screenshot
  each `.page` element in the same session. PDF check: every used family appears as `/BaseFont` with `/FontFile2` or
  `/FontFile3`; Type3 = a variable font leaked through → gate.
- Text contrast probe: ≥ 4.5:1 (≥ 3:1 large text; weight ≤ 300 or high-contrast serif → 4.5:1, heuristic).
- Templates in `templates/{card,board,kit}/`, filled from identity.json; local `@font-face` from the cache instances;
  no external requests in outputs.
- Browser start-up (shared by `render_png.py` and `cdplib.py`, `render_png.start_browser`): the browser starts with
  its sandbox. If it exits during start-up (a container or CI runner without user namespaces: "No usable sandbox!"),
  it starts once more with `--no-sandbox`, and that browser keeps the flag for the rest of the process; as root the
  flag is set from the start. The stderr line that explains a failed start goes into the error. `brand.py check`
  really starts the browser on `about:blank` and says when it needed `--no-sandbox`. DevTools runs on a random
  local port without `--remote-allow-origins` (our client sends no Origin header; web pages get 403). Localhost
  DevTools requests bypass any proxy (`HTTP_PROXY`, the Windows registry proxy); `cdplib` takes the browser socket
  path from `DevToolsActivePort` and makes no HTTP request. `site_palette.py` closes its browser in a `finally`, so
  an interrupt does not leave Chrome running.

## 5. AI defaults (`assets/ai-defaults.json` + `references/ai-defaults.md`)

Section **A. model defaults** (measured with `tools/ai_defaults_experiment.py`, dated with model versions) and **B. category
clichés** (patterns common in human-made logos of some categories). Entry `{id, section, component, match: {fonts?: [...], oklch_regions?:
[{L:[a,b],C:[a,b],h:[a,b]}], logo_tags?: [...]}, note}`. `ai-defaults.json` holds the `logo.tags` vocabulary (literal-object,
hidden-letter, negative-space, circle-container, local-nod, spark, gradient-orb, initial-in-rounded-square, …). The
audit only flags; a flag needs `defaults_used[].why` grounded in the brief. "Feels warm/premium" is not a reason.

## 6. The skill's judgement (SKILL.md + references)

### 6.1 Principles (short)
Mechanism first; one loud thing; differentiate against the user's real competitors; look at every render; a logo
identifies, it does not explain; literal marks are a cost/benefit choice; display type does voice, text type does
reading; colour = hue + chroma + lightness + ground; measurement finds what is broken, taste decides what is good.

### 6.2 Flow and turn plan (written into SKILL.md)
1 load skill · 1 `check` + `init` · 1 intake question · (site: 1 extract, 1 competitors) · 1 read references ·
1 font search · 1 write `sets.json` · 1 `logos` · 1 look at the sheet · 1 parallel edits · 1 `logos` · 1 look ·
1 `build` · 1 look at `review.png` · 1 answer = **~16–20 turns** without fixes, ≤ 30 with gate fixes and site work.
Logo craft: 4–6 one-sentence ideas per set, build one, ≤ 2 look-and-fix rounds, one defect per mark per round.
Independent commands go in one turn.

### 6.3 Reflex test (no bans)
"Would I propose this same font / palette / mark idea for another brand in this sector?" If yes, justify it from the
brief or change it. At least one set is built without any defaults-list item.

## 7. Presentation

Swiss/editorial: top meta strip, big title, generous margins, 12-column
grid, quiet ground. The template is fixed; the set's fonts and palette paint it. One layout in v1.

### 7.1 Card (1600×1000)
Meta strip `Brand · → [A] SET NAME · IDENTITY DIRECTIONS · v0.1 · date · A/3` → name + mechanism → logo hero
(colour) + one-colour 64/32/16 row → axes strip → palette proportion band (role names, hex) → type specimen (brand
name + a real sentence in each brief language, both roles, weights) → one mini application (site header or business
card) → checks row (gates ✔/✘, flags) → footer. `none` blocks collapse; `keep` blocks show the user's asset tagged
"kept"; FAILED sets show the gate message.

### 7.2 Board, review image and table
`board.png`: cards side by side, RECOMMENDED framed; with a site, each card's applied first screen + header close-up.
`review.png` for the model: ≤ 2000 px long edge, 2×2 grid, logo rows legible. Table (`table.md` + on the board):
set · mechanism · then one column per component in `new`/`refresh` · where it differs · risks/flags. Components in
`keep`/`none` get no column; a single-component request gives set · mechanism · that component · where it differs ·
risks.

### 7.3 Kit (after the pick)
≥ 8 pages, reference page template (meta strip `→ [ n.m ] SECTION`, `BRAND GUIDELINES · VERSION · date · page`):
1 Cover · 2 Brand idea · 3 Logo (primary, construction, clear space) · 4 Variations + minimum sizes (px, mm) ·
5 Logo on colour (2×2) + 4–6 misuses · 6 Colours (proportion bands, 80/50/20 tints, HEX/RGB/OKLCH, Lab D50;
"ask your printer for CMYK/Pantone with their profile") · 7 Typography (roles, weight columns, giant "Aa", scale,
features, licence line, embed code) · 8 Applications (business card, social avatar, site header).
Outputs (per set, `kit/<SET>/`): `pages/*.png`, `<brand>-guidelines.pdf`, `logo/` (variants SVG+PNG), `tokens/`
(`export_tokens.py`, plus font CSS variables), `fonts.md` (sources, licences, links; no font files).
`--full` adds grid, iconography, imagery, dark mode and accessibility pages. Pages for `none` components are omitted.

**Misuse page.** `logo.misuse` = 4–6 items, each either `{"do": <generic>}` or `{"svg": <path>, "label": <text>}`.
Generic (`identitylib.MISUSE_GENERIC`), drawn by the kit from the real logo with the label from i18n: `stretch`,
`rotate`, `recolour` (off-palette hue), `effects` (drop shadow), `outline`, `low-contrast`, `crowd` (text blocks
inside the clear space), `rearrange` (wordmark then an oversized symbol; symbol+wordmark logos only). Brand-specific:
a model-drawn wrong version of this logo (`svg`, relative path in the set folder, usually `logo/misuse-N.svg` from
`sets.json` `misuse_svgs`) and its label in doc_lang. The kit sanitises it with an allow-list (svg, g, path, circle,
ellipse, rect, polygon, polyline, line; no `<text>`, `<image>`, `<script>`, `<style>`, `<use>`, filters, event
attributes, `href`, `url()` or `data-op`; a positive viewBox) and paints `data-color` roles from the palette; a
rejected or missing file is a `kit.misuse.svg` gate and the tile is left out. Validation (identitylib): 4–6 items,
known generic ids, no duplicates, `rearrange` only on symbol+wordmark, svg a relative `.svg` path without `..`,
label ≤ 90 characters. Rule (warn, not a ban): a `new`/`refresh` logo with no brand-specific item gets
`kit.misuse.generic`; without the field the six default generic items are drawn (`MISUSE_DEFAULT`) with the same
warn. Kept logos may list brand-specific items; they get no warn. Layout: 4 items 2×2, 5 items 3 + 2, 6 items 3×2;
the tiles are not probed for logo contrast (they are wrong on purpose).

## 8. References (English cards; caps CI-checked; a Create run reads ≤ 24 KB)

| File | Cap | Source |
|---|---|---|
| `cohesion.md` | 5 KB | axes, mechanism, set difference, colour–type interplay, mixing |
| `type.md` | 6 KB | selection, pairing modes, checks, licences, rendering policy, competitor type map |
| `logo.md` | 6 KB | logo craft: process, SVG construction, checks, optical corrections |
| `ai-defaults.md` | 3 KB | §5 |
| `color.md` | 5 KB | brand colour strategy, architecture, pairing |
| `kit.md` | 5 KB | kit anatomy, real guideline examples and inspiration sources as links |
| `accessibility.md`, `reproduction.md`, `critique.md`, `site-preview.md`, `tokens-export.md`, `discovery-brief.md` | 4 KB each | trimmed; read only when needed |

## 9. Budget

Measured with `tools/measure_run.py` (turn = unique assistant `message.id`; tokens ≈ bytes/2.6) on real Opus runs:
3 sets of logo + type + palette, to the checkpoint.

| | Measured mean | Release ceiling |
|---|---|---|
| Turns | 40.5 | 45 |
| Peak context | 160 K | 175 K |
| Billed-input-equivalent (input 1, cache write 1.25, cache read 0.1, output 5) | 0.86 M | 1.0 M |
| Image reads | 6 | 8 |
| SKILL.md / description | 7.9 KB / 494 chars | 8 KB / 600 chars (CI) |

## 10. Quality gates

Unit tests (Linux, macOS, Windows; Python 3.10 and 3.12; they include the live-site tool on fixture pages), the smoke test,
`claude plugin validate`, byte caps for SKILL.md and references, and the end-to-end checks described in
`docs/testing.md`.
