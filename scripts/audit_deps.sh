#!/usr/bin/env bash
# Check the pinned requirements for known vulnerabilities.
# pip-audit is installed into the current environment only when it is missing.
# It is not a runtime dependency of the application image.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! python -m pip_audit --version >/dev/null 2>&1; then
  python -m pip install pip-audit
fi
python -m pip_audit -r requirements.txt -r requirements-dev.txt "$@"
