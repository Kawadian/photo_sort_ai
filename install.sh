#!/usr/bin/env bash
set -euo pipefail
PHOTO_SORT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$PHOTO_SORT_ROOT"
photo_device=cpu
photo_minimal=0
photo_skip_exiftool=0
photo_onnx=none
while (($#)); do
    case "$1" in
        --device) photo_device="${2:?Specify cpu or cuda}"; shift 2 ;;
        --minimal) photo_minimal=1; shift ;;
        --skip-exiftool) photo_skip_exiftool=1; shift ;;
        --onnx) photo_onnx="${2:?Specify none or cpu}"; shift 2 ;;
        --help|-h) printf '%s\n' 'Usage: ./install.sh [--device cpu|cuda] [--minimal] [--onnx none|cpu] [--skip-exiftool]'; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
case "$photo_device" in cpu|cuda) ;; *) printf '%s\n' '--device must be cpu or cuda' >&2; exit 2 ;; esac
case "$photo_onnx" in none|cpu) ;; *) printf '%s\n' '--onnx must be none or cpu (DirectML is Windows-only)' >&2; exit 2 ;; esac
if command -v python3.11 >/dev/null 2>&1; then
    photo_python=python3.11
else
    photo_python=python3
fi
"$photo_python" -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'"
"$photo_python" -m venv .venv
photo_python="$PHOTO_SORT_ROOT/.venv/bin/python"
"$photo_python" -m pip install --upgrade pip
if ((photo_minimal)); then
    "$photo_python" -m pip install -e '.[raw]'
    photo_preset=light
else
    photo_index=https://download.pytorch.org/whl/cpu
    if [[ "$photo_device" == cuda ]]; then
        photo_index=https://download.pytorch.org/whl/cu128
    fi
    "$photo_python" -m pip install torch torchvision --index-url "$photo_index"
    "$photo_python" -m pip install -e '.[ai,raw]'
    photo_preset=balanced
fi
if [[ "$photo_onnx" == cpu ]]; then
    "$photo_python" -m pip install -e '.[onnx]'
fi
if [[ ! -f config.json ]]; then
    "$photo_python" -m photo_sort_ai init --preset "$photo_preset"
fi
if ((!photo_skip_exiftool)) && ! command -v exiftool >/dev/null 2>&1; then
    printf '%s\n' 'ExifTool is missing. Ubuntu/Debian: sudo apt install libimage-exiftool-perl'
fi
if ! "$photo_python" -c 'import tkinter' >/dev/null 2>&1; then
    printf '%s\n' 'GUI requires Tkinter. Ubuntu/Debian: sudo apt install python3-tk (or python3.11-tk for Python 3.11)'
fi
"$photo_python" -m photo_sort_ai doctor
printf '%s\n' 'Setup complete. Desktop GUI: ./start.sh; CLI: .venv/bin/python -m photo_sort_ai --help'
