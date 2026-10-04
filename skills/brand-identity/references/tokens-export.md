# Tokens export

How the chosen set leaves the skill as code: `kit/<SET>/tokens/` from `export_tokens.py` (colour) plus font CSS
variables written by the kit. Tags: `[strong]` vendor or spec documentation, `[practice]` convention.

## Rules
- Export only an audited palette (no `gate` in either mode); tokens spread a failing pair into every screen.
- `palette.json` is the only source. Generated files are read-only: change the source, re-export.
- `python3 scripts/export_tokens.py palette.json --out DIR [--formats css,scss,tailwind,dtcg,gpl,swatches]
  [--dtcg-per-mode]`. Open each file once and quote token names from it, not from memory.

## Files
| File | For | Notes |
|---|---|---|
| `tokens.css` | any web code | scales + 16 roles; light on `:root`, dark on `[data-theme="dark"]` and `prefers-color-scheme` scoped by `:root:not([data-theme="light"])` |
| `tokens.scss` | Sass | mirrors the CSS; don't derive shades with Sass `lighten`/`mix` (they shift hue) |
| `tailwind.palette.js` | Tailwind v3, or v4 via `@config` | v3 opacity modifiers (`bg-primary/50`) don't work on hex variables: use a scale step or `color-mix()` |
| `tokens.dtcg.json` | token pipelines | [DTCG 2025.10](https://www.designtokens.org/tr/2025.10/format/) `[strong]`; roles alias scale steps |
| `tokens.{light,dark}.tokens.json` | Figma Variables | `--dtcg-per-mode`; DTCG has no modes, each file becomes one mode |
| `palette.gpl` | GIMP, Inkscape, Krita | entries named "Role: Brand name" |
| `swatches.html` | humans, approvals, print | hex, RGB, OKLCH, scales, key pairs with ratios |

## Naming `[practice]`
- Reference (scales `primary-50 … 950`) → semantic roles (`--bi-background`, `--bi-surface-alt`, `--bi-text-muted`,
  `--bi-on-primary`) → component tokens, which belong to the developer's codebase.
- Name by job, not hue: `--bi-primary`, never `--bi-blue`; colour names ("Harbor Blue") live in the kit.
- Components use roles; a component reaching for `primary-600` breaks in dark mode. Fills pair only with their
  `on-*` partner.
- Extended colours: `--bi-ext-N` + `--bi-on-ext-N`; N is a position, so keep the order stable.
- Another prefix: alias once (`--color-primary: var(--bi-primary)`), don't edit generated files.

## Tailwind v4
CSS-first ([theme variables](https://tailwindcss.com/docs/theme)) `[strong]`:
```css
@import "tailwindcss";
@import "./tokens.css";
@theme inline { --color-primary: var(--bi-primary); --color-on-primary: var(--bi-on-primary); /* … */ }
```
Utilities read the variables, so the dark switch keeps working. Consider `--color-*: initial;` to drop default
colours.

## Fonts
The kit adds font CSS variables for the display and text roles, with fallback stacks and the embed code
(Google Fonts link or self-host note). No font files are shipped; `kit/<SET>/fonts.md` lists sources and licences
(`type.md`). Set `font-variation-settings` for pinned axes and `lang` on content for locale casing.

## Handoff note (README or kit page)
Which file for which stack and the two wiring lines; theme switching (`data-theme` on `<html>`, OS default
otherwise); the tested fill → text pairs with ratios in both modes ("only these pairs are tested"); do/don't
(roles in components, status never by colour alone, links underlined or ≥ 3:1 from body text, focus ring
`--bi-focus`); changes go through `palette.json`. Logo colours are artwork in `kit/<SET>/logo/`, not tokens.

## Figma
Writing into a user's Figma file is an action in their account: only on request, and prefer handing them the
per-mode files to import (Variables → drag in, or *Import mode*; sRGB only;
[Figma help](https://help.figma.com/hc/en-us/articles/15343816063383-Modes-for-variables)). `[strong]`
