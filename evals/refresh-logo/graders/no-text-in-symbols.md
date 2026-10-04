---
type: regex
pattern: '<text\b'
target: { source: file, path: 'brand-identity/kestrel/sets.json' }
match: 'not_contains'
---
