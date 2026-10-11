#!/usr/bin/env bash
set -euo pipefail
PHOTO_SORT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$PHOTO_SORT_ROOT/.venv/bin/python" ]]; then
    printf '%s\n' 'Run ./install.sh first.' >&2
    exit 1
fi
exec "$PHOTO_SORT_ROOT/.venv/bin/python" -m photo_sort_ai --config "$PHOTO_SORT_ROOT/config.json" watch "$@"
