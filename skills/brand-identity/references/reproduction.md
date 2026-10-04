# Reproduction

Print, materials and screens for the identity (logo colours and palette). Read when print, packaging or signage
comes up, and for kit page 6. Tags: `[strong]` `[limited]` `[practice]`.

## What the skill computes, and what it must not fake
Computes (`colorlib`): hex ↔ OKLCH, CIELAB D65 and **D50**, ΔE2000, greyscale L*, contrast, CVD. Never computes:
CMYK (needs the printer's ICC profile), Pantone/RAL/NCS matches, how a colour looks on a given paper or film.
A plausible made-up CMYK or Pantone number is worse than an empty field. Leave such fields `null`.

## Print flow
1. Approve the screen palette (sRGB hex).
2. Derive **Lab D50** per brand colour (`hex_to_lab(hex, white="D50")`), labelled "derived from screen".
3. Ask for the printing condition (printer, paper, profile). Common defaults to confirm: Europe coated FOGRA51
   (PSO Coated v3), uncoated FOGRA52; US GRACoL/SWOP ([ECI](https://eci.org/doku.php_id=en_colorstandards_offset.html)).
   ICC files are free to download but not redistributable; never bundled. `[strong]`
4. CMYK comes from an ICC-aware tool only, profile name recorded next to every value. Formula conversions are
   wrong for production. `[strong]`
5. Vivid primaries (electric blue, neon green, bright orange, violet) dull in process CMYK: consider a spot
   colour, picked by the user or printer from a physical guide. `[practice]`
6. Proof against the Lab target; ΔE00 ≤ 2–3 is a typical contractual tolerance (`[limited]`, no standard fixes
   one number). An approved physical sample replaces the derived value (`source: measured`).

Pantone books need a Pantone Connect licence since 2022
([Pantone FAQ](https://www.pantone.com/uk/en/articles/faq/pantone-connect-adobe-faq)); online hex converters are
approximations. Record user-supplied refs only. Rich black for large areas, 100K for small text. `[practice]`

## Materials `[practice]`
| Medium | Instruction |
|---|---|
| Uncoated paper | darker, flatter mid-tones (uncoated profiles assume more dot gain): separate value |
| Kraft board | light colours vanish, blues grey, reds brick; dark inks hold. White underlay or a one-colour kraft version |
| Label film (clear, metallised) | white underlay behind anything that must read; approve on the real film |
| Vinyl, paint, thread | manufacturer ranges; nearest match approved from a physical sample |
| Backlit signs | approve by day and at night |
| Embroidery, etching, screen print | the one-colour logo carries the brand |

Gold and metallic looks need foil or metallic ink; process "gold" sinks into kraft. Measure the actual stock
(spectro reading, or a photo white-balanced on a grey card, sampled by code) and pass it to
`palette_audit.py --ground kraft=#hex`; without a value the result is an approximation. Test-print before approving.

## Screens
CSS colours are sRGB; define the brand in sRGB hex ([CSS Color 4](https://drafts.csswg.org/css-color-4/)). `[strong]`
Display P3 is an optional enhancement with an sRGB fallback. Promise consistent specification, not identical
appearance across displays.

## One colour, reversed, greyscale
Every kit specifies: full colour on light, reversed on the primary and on dark, one-colour black, one-colour white,
and which version sits on which ground (Spotify's green logo only on black or white:
[guidelines](https://developer.spotify.com/documentation/design)). Greyscale with CIE L* or OKLab L, never HSL
lightness. Newspapers, fax-like print and engraving rely on the one-colour mark. `[practice]`

## Into palette.json and the kit
`brand[].print`: `lab_d50` (derived, then measured), `cmyk` (from an ICC conversion, profile named), `pantone_ref`
(user-supplied). The kit colour page carries HEX/RGB/OKLCH, Lab D50, and the line "ask your printer for CMYK and
Pantone with their profile".
