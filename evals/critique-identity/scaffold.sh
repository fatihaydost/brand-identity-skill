#!/usr/bin/env bash
# Seeds the empty eval workspace. Runs only with `claude plugin eval --scaffold`; it only copies files.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fix="$here/../fixtures"
mkdir -p "site"
cp "$fix/site-dental/index.html" "site/index.html"
cp "$fix/site-dental/logo.svg" "site/logo.svg"
