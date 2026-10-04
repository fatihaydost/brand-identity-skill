---
type: regex
pattern: '"why"\s*:\s*"\s*"'
target: { source: file, path: 'brand-identity/logline/sets.json' }
match: 'not_contains'
---
