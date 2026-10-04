---
type: regex
pattern: '"why"\s*:\s*"\s*"'
target: { source: file, path: 'brand-identity/mulberry/sets.json' }
match: 'not_contains'
---
