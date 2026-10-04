# Critique

Critique mode: "what do you think of our identity / logo / fonts / colours". Output = gates, short findings per
component, the three measurable fixes that matter most. **No overall score**: the evidence supports no weighting.
Critique the work, never the person; name what works before what doesn't. Tags: `[strong]` `[limited]` `[practice]`.

## Flow
1. **Context:** sector, competitors, position, markets, languages. Read the site first; ask ≤ 3 questions;
   write guesses under "Assumptions".
2. **Collect:** `brand.py critique --site URL` and/or `--logo f.svg --fonts … --palette p.json --langs …`.
   Values read from a site are approximate and labelled `measured`; confirm colour roles with the user before
   judging (`site-preview.md`).
3. **First impression (two seconds):** what would a stranger say this brand is, what do you remember?
4. **Gates**, then **findings** per component, then **top three**.

## Gates (measurable or irreversible)
| Gate | Pass when | Basis |
|---|---|---|
| Contrast | text roles and card text ≥ 4.5:1 (large 3:1) in every shipped mode; UI parts ≥ 3:1 | [WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) `[strong]` |
| Glyphs | every brand language's base exemplars in every face | font audit |
| Licence | each face's licence known and covers logo, web, app | font audit |
| Logo file | vector, no `<text>`/`<image>`, works in one colour, 16 px render exists | `logo.md` |
| Confusion | primary not "the same colour" as a direct competitor's (`color.md`) | thresholds `[practice]` |
| Local meaning | no national flag as emblem ground; other readings reported as risk | [6ter](https://www.wipo.int/article6ter/en/) `[strong]` |

Unchecked gates are marked "not checked: reason", never passed.

## Findings (two to four lines per component; evidence for each)
- **Logo:** distinctive vs the competitor shelf; does it identify without explaining; one colour, reversed, 16 px;
  construction (near-equal radii, clogged joins, false holes); category clichés from `ai-defaults.md` §B.
- **Type:** role split (voice vs reading); class against the competitor type map; weights, `tnum`, I/l/1;
  default-list hits; licence cost.
- **Palette:** category legibility vs differentiation, ownability, system (roles, scales, drift: many near
  duplicates), producibility (dark ground, print), fit of lightness and chroma to the personality.
- **Cohesion:** one mechanism or three unrelated picks? Do the components sit at the same axis positions? Is
  there one loud thing or several?
"Feels modern" is not evidence; "primary L 0.62, C 0.19, saturated mid-light, reads energetic
([Labrecque & Milne 2012](https://doi.org/10.1007/s11747-010-0245-y), `[limited]`)" is.

## Top three
Order: failed gates (unreadable first, then confusion, licence, glyphs), then the biggest weakness against the
brief, cheapest change first among equals (tokens before logo). Each fix states:
- **What:** "CTA fill #4FA3E0 → #1F6FB2 (primary-700)".
- **Measured effect:** "white label 2.6:1 → 5.1:1".
- **Why:** one sentence tied to the brief or a finding.
- **Cost:** tokens · site CSS · reprint · logo redraw.
Never "improve contrast". Re-run the audit after a fix; quote before/after. A clean audit means nothing is
measurably broken, not that the identity is good.

## Template (plain words; numbers go to the audit file)
```markdown
## Your identity: <Brand>
**First impression:** <1–2 sentences>
**Checks:** easy to read ✓/✗ · fonts cover your languages ✓/✗ · font licences OK ✓/✗ · logo works small and in
one colour ✓/✗ · not mistaken for a competitor ✓/✗ (— not checked, why)
**Logo:** … **Type:** … **Colours:** … **Together:** …
**Worth keeping:** <what and why>
**Three changes, most important first:** 1. … 2. … 3. …
**Assumed:** <competitors, market> · **Next:** "See these fixes on your site?" / "Want refresh directions?"
```
Refresh = Create with that component in `refresh` state.
