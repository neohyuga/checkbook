#!/usr/bin/env bash
# Start Checkbook on Linux (creates the Python environment the first time).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python -m checkbook "$@"
