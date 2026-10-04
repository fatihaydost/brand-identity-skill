# Kit

The guideline kit for the chosen set (`brand.py kit SET_DIR [--full]`), and where professionals look for
precedent. Read after the user's pick. Tags: `[strong]` `[limited]` `[practice]`.

## Anatomy
The order has barely changed in 50 years: idea → logo (rationale, versions, clear space, minimum size, on colour,
misuse) → colour (codes, proportions, approved pairs) → type (families, hierarchy, fallback) → grid → applications
→ files. Consistent across the eight guides below. `[practice]`

| Guide | Worth copying |
|---|---|
| [NASA 1976](https://www.nasa.gov/wp-content/uploads/2015/01/nasa_graphics_manual_nhb_1430-2_jan_1976.pdf) | misuse page, construction grid, colour reasons, type derived from the identity |
| [NYCTA 1970](https://archive.org/details/nycta-gs-manual) | every measure tied to one module |
| [Mozilla](https://mozilla.design/mozilla/) | personality before logo; the logo is never the largest element on a page |
| [GitLab](https://design.gitlab.com/brand-logo/core-logo) | clear space = x-height of the wordmark's "a"; ≤ 20% of frame height; min 20 px / 11 mm |
| [Spotify](https://developer.spotify.com/documentation/design) | exclusion zone = half the icon; min sizes in px and mm; green logo only on black or white |
| [Uber Eats](https://merchants.ubereats.com/us/en/resources/learning-center/co-marketing-tools/) | clear space = height of the "U"; co-branding divider |
| [Atlassian](https://atlassian.design/foundations/logos) | alt-text rule; never rebuild the lockup |
| [Mailchimp](https://mailchimp.com/about/brand-assets/) | the shortest useful guide: one hero colour, one accent, reversed version |

Three lessons: **clear space comes from the mark's own part** (cap height, a letter, half the symbol), so it scales;
**minimum size in px and mm**, found by rendering; **digital items** belong in: alt text, favicon, app icon,
social avatar, dark mode, share of frame. `[practice]`

## Pages (v1 minimum, one job each)
1 Cover · 2 Brand idea (mechanism, axes, three words) · 3 Logo: primary, construction, clear-space grid ·
4 Versions + minimum sizes · 5 Logo on colour (2×2) + 4–6 misuses · 6 Colour: proportion bands, 80/50/20
tints, HEX/RGB/OKLCH, Lab D50, "ask your printer for CMYK/Pantone with their profile" · 7 Type: roles, weight
columns, giant "Aa", scale, features, licence line, embed code · 8 Applications: business card, avatar, site
header. `--full` adds grid, iconography, imagery, dark mode, accessibility. Pages for `none` components drop out;
`keep` components are documented as they are.

Rules: show only what was approved, never invent mission statements or stats; no unlabelled placeholder;
`clear_space.inferred: true` is stated as "derived, adjust after use".

**Misuse page** (`logo.misuse`, 4–6 items): 3–4 generic, drawn from the real mark (`{"do": "stretch"}`, `rotate`,
`recolour`, `effects`, `outline`, `low-contrast`, `crowd`, `rearrange` for symbol+wordmark) and 1–2 only this mark
can suffer, drawn by you: `{"svg": "logo/misuse-1.svg", "label": "…"}` (stencil gaps filled, symbol moved, detailed
version at favicon size). Copy `logo/build/master-*.svg` and change it: filled shapes, `data-color`, no text,
images, scripts or `data-op`. Label: a short prohibition. After the pick add both to the set in
`sets.json` (`misuse_svgs: {"misuse-1": "<svg…>"}`), `build WORK --sets X`, `kit`. Only generic items on a new logo
warn (`kit.misuse.generic`).

**Language:** labels follow `brand.doc_lang`; for a language without `assets/i18n/<lang>.json`, translate
`en.json` into `<work>/i18n.json` (`"_lang"` set). Template fixed, the brand's
fonts and palette paint it; quiet ground, 12-column grid, top meta strip, as in the reference pages. `[practice]`

Outputs: page PNGs, one PDF (fonts embedded, OFL allows it), `kit/<SET>/logo/` SVG + PNG variants, `kit/<SET>/tokens/`, and
`kit/<SET>/fonts.md` with sources, licences and links instead of font files (see `type.md`).

## Inspiration: link, never copy
Cite live, one to three links per point with a sentence on why it is a precedent; never download images into
the card or kit. `[practice]`

| Source | Use for |
|---|---|
| [Brand New](https://www.underconsideration.com/brandnew/) | refresh precedent and reactions: "<sector> rebrand" |
| [Fonts In Use](https://fontsinuse.com/) | a face in real identities; best type source |
| [brandingstyleguides.com](https://brandingstyleguides.com/) | real guideline PDFs by sector |
| [Standards Manual](https://standardsmanual.com/) | historic manuals (NASA, NYCTA, EPA) |
| [Are.na](https://www.are.na/) | the user's own channel as a mood board → axes (public API) |
| Savee | only the user's own boards, via their connection; scraping is forbidden ([terms](https://savee.com/terms/)) |
| Agency case studies (Pentagram, Koto, Wolff Olins) | idea → system → use narrative; all rights reserved |

Behance is the biggest showcase and the source of today's guideline-template look (beige paper, giant "Aa",
bracketed section numbers); use that look knowingly, painted by the brand, or it reads as a template.
