#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
