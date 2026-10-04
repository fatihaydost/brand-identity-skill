#!/usr/bin/env bash
# Seeds the empty eval workspace. Runs only with `claude plugin eval --scaffold`; it only copies files.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fix="$here/../fixtures"
mkdir -p "brand-identity/ferrow"
cp -R "$here/../../skills/brand-identity/templates/demo/." "brand-identity/ferrow/"
cp "$here/../../skills/brand-identity/templates/sets.example.json" "brand-identity/ferrow/sets.json"
