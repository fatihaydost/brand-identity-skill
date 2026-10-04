# Accessibility

Contrast, colour-vision and "not by colour alone" for palettes, cards and kits. Read when the audit reports a gate
or the user asks. Tags: `[strong]` `[limited]` `[practice]`.

## The gate: WCAG 2.2 contrast `[strong]`
| Criterion | Threshold |
|---|---|
| [1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) text, AA | 4.5:1; large text (≥ 24 px, or ≥ 18.66 px bold) 3:1 |
| 1.4.6 text, AAA | 7:1 / 4.5:1, a recommendation for body text |
| [1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) non-text | 3:1 for control boundaries, focus, meaningful graphics |
| 1.4.1 use of colour | meaning never by colour alone |

- No rounding: 4.49:1 fails. Measure rendered colours (composite alpha first). Both modes are separate gates.
- Our heuristic: weight ≤ 300 or a hairline serif gets 4.5:1 even at large sizes (thin faces render fainter).
- WCAG 3 is a draft; WCAG 2 is the only gate here. APCA is not computed: its reference code is all rights reserved
  ([apca-w3](https://github.com/Myndex/apca-w3)); if asked, say so and point to its own tools.
- Logos are exempt from 1.4.3 and 1.4.11; the logo audit still asks ≥ 3:1 or a tile as practice (`logo.md`).
  Button fills with a text label need no 3:1 against the page; their focus ring does.

## Pairs the palette audit tests (both modes)
`text`, `textMuted` on `background`/`surface`/`surfaceAlt` (4.5); `onPrimary`/`primary`, `onAccent`/`accent` (4.5);
`link` on surfaces (4.5) and against body text when not underlined (3, [G183](https://www.w3.org/WAI/WCAG22/Techniques/general/G183));
`focus` against background and filled controls (3); status colours as text (4.5) or icons (3); input borders when
they are the only cue (3); extended colours with their `on` (4.5). The card probe adds every text node on the
card and kit pages.

## Colour-vision deficiency
- Simulation: Machado 2009 for protan/deutan in linear RGB, Brettel 1997 for tritan
  ([review](https://daltonlens.org/opensource-cvd-simulation/)). Models describe an average dichromat; treat as a
  screen. `[limited]`
- About 8% of men of European descent have red–green deficiency ([Birch 2012](https://doi.org/10.1364/josaa.29.000313)). `[strong]`
- Read simulated ΔE2000 between meaning-bearing pairs: ≥ 10 distinct, 6–10 risky, < 6 merges (tool defaults,
  `[practice]`). Fix by lightness difference plus icon or label.
- Brand colours meant to be told apart, including logo parts: ΔL* ≥ ~15–20 (also protects one-colour print). `[practice]`

## Fix order
1. Move the failing role along the **same** scale (darker in light mode, lighter in dark).
2. Flip the partner (`best_text_on`); if neither white nor black passes (mid-lightness brand colours), map the UI
   role to a darker step and keep the brand hex for the logo and large areas.
3. Never edit a `locked` brand colour to pass a UI check.
4. Semantic colours may shift hue to clear a brand or CVD clash.
5. Isoluminant or greyscale warnings: change lightness, not hue.
6. Re-run the audit after every change; fixes interact. A new hue is a last resort and needs the user.

## Dark mode
Every gate applies again. Leave margin above 4.5:1 for body text; avoid pure `#000` with long pure `#fff` text;
a white `onPrimary` copied from light mode is the classic failure. Light mode reads better for long text
([Buchner et al. 2007](https://doi.org/10.1080/00140130701306413)) `[strong]`, so dark is an option, not the only theme.

## Reporting
Gates first with measured ratio and threshold ("text-muted on surface-alt 4.21:1, needs 4.5:1 → step 600, 5.02:1").
Quote audit numbers verbatim. Passing contrast is a floor, not proof the palette is good.
