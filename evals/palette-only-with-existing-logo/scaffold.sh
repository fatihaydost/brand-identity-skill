#!/usr/bin/env bash
# Seeds the empty eval workspace. Runs only with `claude plugin eval --scaffold`; it only copies files.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fix="$here/../fixtures"
mkdir -p "site" "brand"
cp "$fix/site-saas/index.html" "site/index.html"
cp "$fix/logline-logo.svg" "brand/logo.svg"
