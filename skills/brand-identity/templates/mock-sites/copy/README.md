# Mock copy contract

The mock pages (`landing`, `app`, `card`, `social`, `product`) keep their visible text separate from their markup.
The defaults are English and neutral; a preview can carry the brand's own words, in any language, through a copy
file.

## Placeholders

| Placeholder | Filled with |
|---|---|
| `{{TOKENS_CSS}}` | palette CSS (`--bi-*` roles, optional scales, optional `--bi-kraft`) |
| `{{BRAND_NAME}}` | brand name, plain text |
| `{{TAGLINE}}` | tagline, plain text |
| `{{LOGO_SVG}}` | optional symbol SVG drawn with `currentColor`; empty string for a type-only wordmark (no whitespace) |
| `{{COPY_JSON}}` | the copy object below, as JSON |

Mode is set on `<html data-theme="light|dark">`.

## Copy object

A flat JSON object: `{"key": "text", ...}`.

- Keys are the `data-copy="key"` attributes in the templates. `common.*` keys are shared across templates (person,
  customer, quote, rating, hours, phone, address, legal); `landing.*`, `app.*`, `card.*`, `social.*` and `product.*`
  belong to one template.
- `"\n"` is a line break. `{brand}` is replaced with the brand name. Text is inserted as text, not HTML.
- `_lang` (optional) sets `<html lang>`, so uppercase labels and hyphenation follow the copy's language.
- A key that is missing, or not a string, keeps the English text written in the markup. A template that gets no
  copy at all (or an unreplaced `{{COPY_JSON}}`) therefore renders the English defaults.

## Defaults and overrides

- `en.json` lists every key with its English default. It is generated from the markup, so it always matches it;
  use it as the reference when writing a copy file.
- To show the brand's own words or language, pass `--copy custom.json` to `site_preview.py`. The file only needs
  the keys it changes; they override the defaults. Write dates, money, phone numbers and addresses in the format
  of the brand's market. For example, a hazelnut brand in Turkish:

```json
{
  "_lang": "tr",
  "product.name": "Fındık Ezmesi",
  "product.variety.1": "Sade",
  "product.variety.2": "Kakaolu",
  "product.variety.3": "Kavrulmuş",
  "product.weight": "300 g"
}
```

- Whoever fills `{{COPY_JSON}}` escapes `</` as `<\/` inside the JSON so it cannot close the script tag.

## Extended colours

Palettes may carry up to five extended colours for low-dose use (product varieties, chart series, categories):
`--bi-ext-1` .. `--bi-ext-5`, each with `--bi-on-ext-N` for text on it. In dark mode `site_preview.py` writes the
dark derivatives under the same names. Main surfaces, buttons and links stay on the core roles. When a palette has
no extended colours the pages look exactly as before, because every use falls back to a core role:

| Variable | Fallback | Used in |
|---|---|---|
| `--bi-ext-1` / `--bi-on-ext-1` | primary / onPrimary | product variety 1 strip; app revenue line and "Online" category; landing value icon 1; social pattern tile |
| `--bi-ext-2` / `--bi-on-ext-2` | accent / onAccent | product variety 2 strip and box underprint strip; app "In person" category; landing value icon 2; social pattern tile |
| `--bi-ext-3` / `--bi-on-ext-3` | text (product: label ink / paper) | product variety 3 strip; app "Phone" category; landing value icon 3; social pattern tile |
| `--bi-ext-4` | primary tint (app), primary (landing) | app "Repeat" category; landing value icon 4 |
| `--bi-ext-5` | border | app "Gift" category |

## product.html and kraft

`product.html` shows a jar label, a kraft box and a shelf of three varieties. The box simulates printing on kraft
with `mix-blend-mode: multiply` (direct print), next to a panel printed over white ink (white underprint).
`--bi-kraft` defaults to `#a99083`, the same approximate value `palette_audit.py --ground kraft` uses (an uncoated
brown kraft liner, CIELAB L* 61.9 a* 8.0 b* 10.6, US 8,114,486 B2). Real kraft varies widely, from grey-brown to
yellow-brown; it is not a standard. When the real board has been measured, pass it with
`site_preview.py --ground kraft=#hex`; the tool writes it into the page as `--bi-kraft`. Labels and white ink
use `--bi-neutral-50` and `--bi-neutral-900` when the palette has a neutral scale, so they stay light in dark mode. This is a print scene, so the light mode render is the one to judge.
