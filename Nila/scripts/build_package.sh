#!/usr/bin/env bash
# Thin wrapper. The real builder is scripts/build_package.py - see its docstring
# for why packaging moved out of shell (a `*/updates/*` exclusion silently
# swallowed brain/updates/, and the failure was invisible to the test suite).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/build_package.py" "$@"
