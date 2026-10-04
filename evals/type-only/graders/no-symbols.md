---
type: regex
pattern: '"symbol_svg"\s*:\s*"<svg'
target: { source: file, path: 'brand-identity/ledgerline/sets.json' }
match: 'not_contains'
---
