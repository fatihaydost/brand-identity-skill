---
type: regex
pattern: '"why"\s*:\s*"\s*"'
target: { source: file, path: 'brand-identity/kestrel/sets.json' }
match: 'not_contains'
---
