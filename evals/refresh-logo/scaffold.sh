#!/usr/bin/env bash
# Seeds the empty eval workspace. Runs only with `claude plugin eval --scaffold`; it only copies files.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fix="$here/../fixtures"
mkdir -p "brand"
cp "$fix/kestrel-logo-2011.svg" "brand/kestrel-logo.svg"
