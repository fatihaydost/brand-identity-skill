# Logo

Conceiving, drawing and fixing a set's mark. Tags: `[strong]` primary text/standard, `[limited]` few studies,
`[practice]` designer consensus or our render test.

## What a mark must do
- **Identify, not explain.** Meaning comes from use; the hard requirement is one colour and very small sizes
  ([Rand 1991](https://www.paulrand.design/writing/articles/1991-logos-flags-and-escutcheons.html)). `[practice]`
- **Distinctive, appropriate, simple, not empty.** Liked, recognisable marks are natural, harmonious and moderately
  elaborate ([Henderson & Cote 1998](https://doi.org/10.1177/002224299806200202)) `[limited]`; complex marks gain more
  with exposure, so a new brand leans simple ([van Grinsven & Das 2016](https://doi.org/10.1080/13527266.2013.866593)). `[limited]`
- **Literal is a trade-off.** Descriptive marks help new, small brands, less once known, and hurt in negative
  categories ([Luffarelli et al. 2019](https://doi.org/10.1177/0022243719845000)) `[limited]`; it costs
  distinctiveness. Describe in a descriptor line or one hint, never a category cliché (`ai-defaults.md`).
- **Shape language from the axes** (context-bound, `[limited]`): round → soft, angular → durable
  ([Jiang et al. 2016](https://doi.org/10.1093/jcr/ucv049)); angular → premium under status motives
  ([Li et al. 2023](https://econpapers.repec.org/article/eeejoreco/v_3a75_3ay_3a2023_3ai_3ac_3as0969698923002631.htm));
  asymmetry → exciting, harmful if the brand is not ([Luffarelli et al. 2019b](https://doi.org/10.1177/0022243718820548)).
- **Refresh = evolution.** Rounding an angular logo lowered committed customers' attitudes
  ([Walsh et al. 2011](https://doi.org/10.1108/07363761111165958)). `[limited]` Name what is kept before drawing.

## Process (per round, all sets on one contact sheet)
1. Per set, 4–6 one-sentence ideas; each says why it is this brand's and not the category's.
2. Build **one**. First write the construction in words: primitives, grid unit, radii, angles.
3. `brand.py logos WORK` renders large, 32, 16 px, colour and one colour. **Look.** In our test a broken mark
   passed every automated check; only looking caught it. `[practice]`
4. **One named defect per mark per round, at most 2 rounds.** Render feedback helps strong models a little
   ([IntroSVG, CVPR 2026](https://arxiv.org/abs/2603.09312)) and is inconsistent in off-the-shelf VLMs, where extra
   rounds add breakage ([arXiv 2608.28678](https://arxiv.org/abs/2608.28678)). `[limited]`
5. Read it cold at 16 px: does a letter read as another, does a join clog?

## SVG construction (`sets.json` → `symbol_svg`)
- `viewBox="0 0 100 100"`, **primitives** (circle, rect, rounded rect, polygon, short arcs), each with a semantic
  `id`. LLMs place primitives well and fail at complex geometry ([Chat2SVG](https://arxiv.org/abs/2411.16602);
  [SVGenius](https://arxiv.org/abs/2506.03139); [VGBench](https://aclanthology.org/2024.emnlp-main.213/)). `[limited]`
- **Fills, not strokes** (strokes are outlined anyway). Holes are real: `data-op="subtract"` or `fill-rule="evenodd"`,
  never a ground-coloured shape on top.
- **Booleans:** `data-op="union|subtract|intersect"` on a shape, resolved with skia-pathops in document order.
- **Colour by role:** `data-color="primary|accent|text|…"` on each part; no hex in the master.
- **No `<text>`, `<image>`, `filter`, `foreignObject`.** The wordmark is outlined from the display face by the
  pipeline (`identity.logo.wordmark`); live text fell back to another font in our test. `[practice]`
- **Compute, don't eyeball:** endpoints from sin/cos; repeated parts share exact radius, angle and thickness.
- `symbol_small_svg` (optional): simplified favicon drawing.

| Failure | Fix |
|---|---|
| False hole (white shape over a ring) | Real subtract/evenodd; render on colour |
| Element duplicated or shifted after an edit | One element per round; ids everywhere |
| Almost-equal angles, radii, weights | Grid unit, computed coordinates |
| Hand-drawn letters misread | Glyphs from the font plus one boolean detail |
| Detail creep | No addition without a named defect |
| Trusting one quality score | CLIP-style metrics barely react to colour, count or position errors ([SVG-Score](https://arxiv.org/abs/2609.03806)) |

## Lockups and system `[practice]`
- Wordmark from the set's display face, tracked, plus at most one ownable detail by boolean (cut terminal, shared
  stroke, the symbol's angle or radius in one letter). Custom letters only for short wordmark-only names.
- Symbol corners, cut angles and weight follow the font's contrast and terminals; a round symbol with a hard
  grotesk only as deliberate contrast.
- Lockups horizontal and stacked; unit = cap height; symbol aligned optically to the cap-height band.
- Clear space from the mark's own unit; minimum size just above where its defining feature vanishes (px, mm).
- Note 1–2 misuses only this mark can suffer (filled stencil gap, moved symbol); the kit draws them (`kit.md`).
- Parts that sit side by side differ in lightness, not hue alone.
- OFL fonts may be outlined into a logo; the graphic is yours, no attribution
  ([OFL-FAQ](https://openfontlicense.org/ofl-faq/)). `[strong]` Other licences need written logo permission.

## Checks (`logo_audit.py`, no score)
Gates: the SVG rules above, 16/32 px renders, ≥ 3:1 on every ground or a declared tile
([WCAG 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html), as practice: logos are exempt),
thinnest feature survives `min_size`, font licence. Warnings (heuristic, uncalibrated): > 3 colours, near-equal
radii/angles, detail growth without a named defect, `logo.tags` hits.

## Optical corrections (kit stage) `[practice]`
Type-design practice (Tracy, *Letters of Credit*; Cheng, *Designing Type*); amounts from the display font:
overshoot = the font's O/H ratio on round and pointed parts; horizontals = its crossbar/stem ratio; tile content
sits slightly above centre; reversed marks spread, so use negative [GRAD](https://fonts.google.com/knowledge/glossary/grade)
if available and inset the symbol slightly, judged at 32 px.
