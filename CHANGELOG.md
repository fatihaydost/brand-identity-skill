# Changelog

## Unreleased

- **Fixed (docs):** contrast is measured with WCAG 2.2 only. The 1.0.0 notes and README said "WCAG 2 and APCA";
  APCA was never implemented, because its licence restricts independent implementations.

## 1.1.0 (2026-10-10)

Fixes from a first outside contribution (thanks, @brunomolteni, #2).

- **One-colour logos:** parts that end up the same colour are one path, so knocked-out parts no longer show hairline
  seams in PNGs and vector viewers.
- **Extended colours in logos:** `logos` builds the palette with `palette_build.extra`, so a part coloured `ext-n`
  no longer gates as "not a palette role". The saved palette is reused whatever the spelling of the extra's hex
  (`#9EA`, `9ea3a0`, `#9EA3A0`).
- **Kit type page:** every role keeps a weight column, so a mono face is shown, embedded in the PDF and in the embed
  code (`--font-mono`).
- **Kit colour page:** past six full rows, extended colours shrink to name + HEX rows instead of overflowing.

## 1.0.0 (2026-10-05)

First public release.

- **Identity sets from one brief:** logo, typography and colour palette built together from one mechanism, three sets
  by default (up to four), each on one identity card, compared on one board with a comparison table.
- **Measured:** contrast (WCAG 2.2; the original note said "and APCA", corrected above), colour-blindness simulation, glyph coverage for the brand's languages,
  font licences, tabular figures, logo renders at 16 and 32 px. Failed measurements are gates; heuristics are warnings.
- **Logo pipeline:** symbol, wordmark and lockups from one SVG source, with one-colour, reversed, app icon and favicon
  versions. A part that does not read on a ground is drawn in that ground's ink.
- **Brand guidelines kit** for the chosen set: an 8-page PDF (13 with `--full`), logo files, design tokens (CSS, SCSS,
  Tailwind, DTCG JSON) and a font sheet with embed code.
- **Your site:** reads the current logo, type and colours from a URL and previews each set on your own pages.
- **Light setup:** Python 3.10+, a Chromium-based browser and uv. No Node, no manual `pip install`.
- Tested in CI on Linux, macOS and Windows (Python 3.10 and 3.12), plus end-to-end runs in a clean container.
