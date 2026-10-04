---
type: regex
pattern: '"symbol_svg"\s*:\s*"<svg'
target: { source: file, path: 'brand-identity/mulberry/sets.json' }
match: 'not_contains'
---
