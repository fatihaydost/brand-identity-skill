---
type: tool_order
before: { tool: Bash, input_match: 'brand\.py\W{0,3}\s+logos\b' }
after: { tool: Bash, input_match: 'brand\.py\W{0,3}\s+build\b' }
---
