# Colour

What to decide for a set's palette; `palette_build.py` and `palette_audit.py` do the arithmetic.
Tags: `[strong]` `[limited]` `[practice]` `[popular-unsupported]`.

## Strategy
- **Differ first, fit second.** Colour is the weakest distinctive asset: 12% fame, 39% uniqueness on average vs
  40% / 71% for shapes ([Phua et al. 2026](https://doi.org/10.1080/02650487.2026.2637295)) and the most shared
  ([Ward et al. 2020](https://doi.org/10.1057/s41262-020-00187-6)). `[strong]` Ownership = shape + colour + years.
- **A decision is hue + chroma + lightness + ground.** Lightness and saturation carry their own effects
  ([Labrecque et al. 2024](https://doi.org/10.1177/00222429241296392): saturation reads as power, backfires for
  gentle products) `[strong]`; a dark ground pulls a hue toward its negative meanings
  ([Celhay et al. 2024](https://doi.org/10.1093/jcr/ucae019)) `[strong]`, so choose the ground as strategy.
- **Associations are vocabulary, not rationale:** reported, not shown to cause feelings
  ([Jonauskaite et al. 2025](https://doi.org/10.3758/s13423-024-02615-z)). `[strong]` No behaviour claims ("red
  converts", "colour lifts recognition 80%"). `[popular-unsupported]`
- **Conform or differ:** strong brands gain from looking like themselves, weak ones from the segment
  ([Heitmann et al. 2020](https://doi.org/10.1177/0022243720904004)) `[strong]`; atypical wins with exposure
  ([Landwehr et al. 2013](https://doi.org/10.1509/jm.11.0286)) `[strong]`; in high-risk categories (health, money,
  law, children) stay in the family and differ on lightness/chroma or a small accent
  ([Garaus & Halkias 2019](https://doi.org/10.1007/s11846-018-0325-9)). `[limited]` Moderate deviation beats extreme.
- **Competitor map:** `search_library.py --industry X --summary` (measured public sites), plus `brand.py site
  competitors` for named rivals (≤ 8). Plot primaries in OKLCH; empty hue sector or lightness band = room.
- **Near colour = same colour:** reject a primary within ΔE2000 < 10 of a direct competitor's, or same hue sector
  (±20°) and ±0.08 L while both are chromatic; 10–20 is borderline. Courts treat close shades as one
  ([BGH, Milka lilac, 2004](https://recht.nulegal.eu/rechtsprechung/bgh/2004-10-07/i-zr-91-02)); thresholds `[practice]`.
- **Local meaning:** search each market for party, flag, team or mourning readings of the main pair: a warning;
  a national flag as emblem ground is out ([Paris 6ter](https://www.wipo.int/article6ter/en/)).
- **Refresh:** equity is shown, not declared; marketers overrate their own assets
  ([Brus et al. 2025](https://doi.org/10.1057/s41262-025-00395-y)). `[limited]` Recognised and unshared → keep the
  hue, tune; shared or unknown → free to move. Spread sets tune → evolve → shift.

## Architecture (`palette.json`)
- Two layers: `brand[]` (identity colours, `locked` once approved, never edited to pass a UI check) and
  `modes.light|dark` roles (16 keys) pointing at 11-step OKLCH scales; the anchor step holds the brand hex exactly.
- 1 signature, 0–1 secondary, 1 accent, a tinted neutral, 4 semantic. **2–3 hues total**: one hue and more than
  three both rate lower ([O'Donovan et al. 2011](https://www.dgp.toronto.edu/~donovan/color/colorcomp.pdf)). `[strong]`
- Neutrals tinted toward the brand hue (C ≤ 0.045); C 0.05–0.09 reads as neither grey nor colour. `[practice]`
- The light ground is a decision (`--light-bg`): white reads clinical, cream/stone warm (see `ai-defaults.md`).
  Dark mode is derived: tinted near-black, brand fills lightened along their scale, kept small. `[practice]`
- Extended colours (≤ 5, `--extra`) for variants and charts, never core roles. 60-30-10 has no basis.
  `[popular-unsupported]` The loudest colour takes the least area. `[practice]`

## Pairing
- **Harmony is lightness structure, chroma balance and hue count, not wheel templates.** One very light, one very
  dark, an orderly ladder; surfaces from one hue family separated by lightness; the accent differs in hue and
  stays small (O'Donovan 2011; [Schloss & Palmer 2011](https://doi.org/10.3758/s13414-010-0027-0)). `[strong]`
  Complementary and triadic schemes are not rated harmonious; never justify a pick by "it's the complement".
- Well-rated directions: warm + cyan, orange + navy, green + blue/yellow, light warm figure on dark cool ground. `[strong]`
- Hue physics: yellow, lime and teal are vivid only when light (need a dark partner); blue and violet only when
  dark; red and magenta peak mid-lightness and carry neither white nor black text well. `[strong]` (arithmetic)
- Avoid isoluminant pairs (both saturated, ΔL* < 12: they shimmer;
  [Wolfe & Owens 1981](https://doi.org/10.1068/p100053)) and red–green as the only signal
  ([Birch 2012](https://doi.org/10.1364/josaa.29.000313)). `[strong]` Fix with lightness, not hue.
- Judge on the rendered card or site. The audit finds what is broken; taste decides what is good.
