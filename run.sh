#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required." >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python whale_widget.py
