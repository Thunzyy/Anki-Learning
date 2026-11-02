#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$ROOT/scripts/launch.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$ROOT/scripts/launch.py" "$@"
else
    echo 'Install Python 3.10+ and Node.js 22.18+, then run this script again.' >&2
    exit 1
fi
