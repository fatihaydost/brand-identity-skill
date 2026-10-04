# Changelog

## 1.0.0 (2026-10-05)

First public release.

- **Identity sets from one brief:** logo, typography and colour palette built together from one mechanism, three sets
  by default (up to four), each on one identity card, compared on one board with a comparison table.
- **Measured:** contrast (WCAG 2 and APCA), colour-blindness simulation, glyph coverage for the brand's languages,
  font licences, tabular figures, logo renders at 16 and 32 px. Failed measurements are gates; heuristics are warnings.
- **Logo pipeline:** symbol, wordmark and lockups from one SVG source, with one-colour, reversed, app icon and favicon
  versions. A part that does not read on a ground is drawn in that ground's ink.
- **Brand guidelines kit** for the chosen set: an 8-page PDF (13 with `--full`), logo files, design tokens (CSS, SCSS,
  Tailwind, DTCG JSON) and a font sheet with embed code.
- **Your site:** reads the current logo, type and colours from a URL and previews each set on your own pages.
- **Light setup:** Python 3.10+, a Chromium-based browser and uv. No Node, no manual `pip install`.
- Tested in CI on Linux, macOS and Windows (Python 3.10 and 3.12), plus end-to-end runs in a clean container.
