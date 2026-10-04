# Type

Choosing, pairing and checking a set's faces. Tags: `[strong]` `[limited]` `[practice]` `[popular-unsupported]`.

## Selection
- **Display does voice, text does reading.** People attribute consistent personas to faces
  (Brumberger 2003, *Technical Communication* 50:2); fitting the persona
  matters in ads, legibility elsewhere (Shaikh, Chaparro & Fox 2006, *Usability News* 8:1). `[limited]`
- Class → voice (grotesk honest, neo-grotesk neutral, geometric modern, humanist warm, old-style literary, Didone
  dramatic and fragile when small, slab sturdy, mono technical) is vocabulary, culture-bound
  ([Google Fonts checklist](https://fonts.google.com/knowledge/choosing_type/a_checklist_for_choosing_type)). `[practice]`
- Holds up: round ↔ sweet, angular ↔ sour across countries ([Velasco et al. 2015](https://doi.org/10.1177/2041669515593040))
  `[strong]`; its effect on taste or sales is mixed `[limited]`. No "most legible font": speed varies by reader
  ([Wallace et al. 2022](https://doi.org/10.1145/3502222)) `[strong]`; serif vs sans makes no difference once
  x-height is matched ([Arditi & Cho 2005](https://doi.org/10.1016/j.visres.2005.06.013)) `[strong]`. Humanist
  sans reads better at a glance at small sizes ([Dobres et al. 2016](https://doi.org/10.1080/00140139.2015.1137637)).
  `[limited]` "Hard-to-read fonts are remembered" failed replication ([Taylor et al. 2020](https://doi.org/10.1080/09658211.2020.1758726)). `[popular-unsupported]`
- Work from constraints first: languages, smallest and largest size, weights, licence, web budget; then set the
  real brand name and a real sentence in candidates, never Lorem
  ([Type Founders](https://thetypefounders.com/world-of-type/how-custom-font-design-projects-work/)). `[practice]`
- Before picking, run the reflex test (`ai-defaults.md`). `brand.py fonts search --exclude-defaults` hides listed faces.

## Pairing modes (`identitylib.PAIRING_MODES`; give sets different modes)
| Mode | Use |
|---|---|
| `single-family` | one variable family; hierarchy from `wght`/`opsz`/width. Lowest risk, fewest bytes |
| `superfamily` | sans + serif sharing skeleton and x-height (IBM Plex, Source) |
| `contrast` | characterful display + neutral text |
| `same-designer` | two faces by one designer or foundry |
| `data-face` | adds mono or tabular-figure face for data, only when the product shows numbers |

A second face only if it does what the first cannot ([GF, superfamily](https://fonts.google.com/knowledge/choosing_type/pairing_typefaces_within_a_family_superfamily));
mixing is optional and low contrast can work ([Butterick](https://practicaltypography.com/mixing-fonts.html)). `[practice]`
Rule only where measurable: one family per role, every family has a role. For inline mixing keep x/cap ratios
close (CSS `font-size-adjust`); no threshold is evidenced.

## Checks (`brand.py fonts audit FAMILY --langs … --numbers`)
- **Gate:** 100% of base exemplars of every brief language (gflanguages; `tr` → `tr_Latn`). Missing ğ ş İ fall
  back letter by letter.
- **Gate:** licence known and covers logo outline, web, app, PDF embed.
- **Gate:** `tnum` when the product shows numbers.
- Warn: text family with < 2 real weights (a `wght` axis spanning 400–700 counts); I/l/1 raster IoU > 0.9
  (heuristic; suggest an `ss`/`cv` disambiguation feature); high stroke contrast in the text role.
- Info: GF Latin Core coverage, `locl`, total web weight.
- Casing is the browser's job: set `lang` so Turkish `i` uppercases to `İ`, and only there.

## Licences and rendering
| Source | Policy |
|---|---|
| Google Fonts (OFL/Apache/UFL) | rendered and outlined. Pinned instances are OFL Modified Versions: cache only, never shipped. Some families have a Reserved Font Name (Source Sans 3: "Source"), so the kit links the font and gives embed code instead of subsetting; PDF embedding is allowed ([OFL-FAQ](https://openfontlicense.org/ofl-faq/)) `[strong]` |
| Fontshare (ITF FFL 2.0) | link + note only, not rendered: no subsetting or conversion, no handing files to clients ([licence](https://www.fontshare.com/licenses/itf-ffl)) `[strong]` |
| Commercial | name + note "licences per weight and use: desktop / web / app; client buys" + a rendered free alternative. Trial fonts are presentation-only ([Klim](https://klim.co.nz/licences/test-fonts/)) |
| User files | used as given, never redistributed; no file → keep the name, offer a fallback, never call the fallback the original |

## Competitor type map
1. `brand.py site competitors WORK URL…` reads heading/body families.
2. Classify each (catalogue class, or "custom/commercial" by eye; CSS names can be aliases like `sohne-var`).
3. Grid: class × role, cells = competitor count. Crowded cell = category code; empty cell = room to differ.
Leaders often use their own or commercial faces (Wise Sans, Söhne, Monzo Sans; our 6-site sample); an OFL set
differs by class, family and features. Inter is ~1% of the whole web ([Web Almanac 2024](https://almanac.httparchive.org/en/2024/fonts));
its ubiquity is a startup and AI-output effect. Show a face in real use with [Fonts In Use](https://fontsinuse.com/)
links, one at a time, no bulk scraping.
