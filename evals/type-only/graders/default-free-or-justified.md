---
type: regex
pattern: '^(?=[\s\S]*cohesion\.no-default-free-set)(?=[\s\S]*"id"\s*:\s*"default\.)'
target: { source: file, path: 'brand-identity/ledgerline/sets/A/identity.json' }
match: 'not_contains'
---
