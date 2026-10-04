---
type: regex
pattern: '(?:"mechanism"\s*:\s*"[^"]{20,}"[\s\S]*?){3}'
target: { source: file, path: 'brand-identity/pedalka/sets.json' }
---
