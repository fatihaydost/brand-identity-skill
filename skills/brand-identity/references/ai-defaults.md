# AI defaults

Bias, not bans: a ban tends to move the model to the next default
([Anthropic 2025](https://claude.com/blog/improving-frontend-design-through-skills)) `[practice]`. Data, match rules
and tag vocabulary: `assets/ai-defaults.json`.

## Reflex test
After drafting a set, ask: **"Would I propose this same font, palette or mark idea for another brand in this
sector?"** If yes, justify it from the brief or change it. At least one set uses no listed item.
A hit goes into `defaults_used: [{id, why}]`; a valid `why` is concrete: the user asked, the competitor map shows
the region empty, an existing asset, a production constraint. "Feels warm / premium" is not a
reason: it is how the stereotype works.

## A. Model defaults (measured 2026-10-02; Claude Opus/Sonnet 5.5, Haiku 4.5)
Our experiment, 30 briefs, 180 answers (`tools/ai_defaults_experiment.py`). `[limited]` The default is a **sector
stereotype**: the same brief got the same display face 67–80% on repeat, 60% across models.
1. **Fraunces display** (and soft-serif neighbours such as Recoleta): 24% of display faces, Opus 40%.
2. **Inter text face**: 40% of text faces.
3. **Cream ground + terracotta + gold/brass accent** (+ serif display): strongest cluster, Opus 50%, Sonnet 47%.
4. **Navy primary + gold accent** for law, property, finance, insurance, public sector.
5. **Sector fonts**: Cormorant Garamond (wine, hotel, property), Canela (law), Barlow Condensed (construction,
   logistics), Space Grotesk / Sora (AI, crypto, fintech), Poppins (edtech).
6. **Indigo/violet in technology briefs** only; "AI purple" is not a default elsewhere (6% of primaries).
7. **Logo formula**: the name's object + hidden letter + negative space + circle + local "nod"; symbol +
   wordmark in 97–100%, so also offer a wordmark-only or typographic set.
8. **Gradient**, blue to purple: rare (5%), a cliché when it comes.

## B. Category clichés (human-made logos)
Four-point spark, radial swirl, gradient orb ([Fast Company 2026](https://www.fastcompany.com/91545582/google-workspace-icons-redesign));
initial in a rounded square (that tile is the [app-icon container](https://developer.apple.com/design/human-interface-guidelines/app-icons));
hexagon/nodes/cube; leaf, shield, roof, arrow, bulb, globe, bolt ([Left Hand Design](https://lefthd.com/insights/why-ai-logos-look-alike/));
the swoosh ([Geismar, Print 2011](https://www.printmag.com/branding-identity-design/marks-men-an-interview-with-ivan-chermayeff-tom-geismar-and-sagi-haviv-of-chermayeff-geisma/));
outline icons ([Lucide's 2 px, round-cap grid](https://lucide.dev/contribute/icon-design-guide)); mockup polish
over a weak form (judge flat, one colour, 16 px first). `[practice]` Before building, name three clichés of the
category. A B-hit's `why` says who on the competitor shelf uses it, what second reading is this brand's, and
whether the idea survives without it. Legitimate: tile as the app icon, gradient with a flat master, hexagon for
"Hex…".

Tag marks honestly in `identity.logo.tags`; the audit only flags.
