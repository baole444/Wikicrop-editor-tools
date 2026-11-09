#!/usr/bin/env bash
# Set up the Python environment for the Wikicrop tools (Linux, macOS, Git Bash on Windows).
#
# Creates a virtual environment in .venv at the project root, installs the packages in
# scripts/requirements.txt into it, and reports which optional external programs were found.
# Safe to run again: an existing .venv is reused and its packages are brought up to date.
#
# Run from anywhere:
#     bash scripts/setup_env.sh            # or: PYTHON=/path/to/python3.12 bash scripts/setup_env.sh
#     bash scripts/setup_env.sh --video    # also faster-whisper, for video-transcript/transcribe_video.py
#     bash scripts/setup_env.sh --video --gpu   # and the CUDA 12 libraries, for an NVIDIA GPU
# Then, in each new terminal:
#     source .venv/bin/activate            # Git Bash: source .venv/Scripts/activate
#     python scripts/fetch_crop_pages.py --help
set -euo pipefail

VIDEO=0; GPU=0
for a in "$@"; do
    case "$a" in
        --video) VIDEO=1 ;;
        --gpu) GPU=1 ;;
        *) echo "Unknown option: $a (expected --video, --gpu)" >&2; exit 2 ;;
    esac
done

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
VENV="$ROOT/.venv"

version_ok() {  # prints the version and succeeds if "$1" is Python 3.11 or newer
    "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2]); sys.exit(sys.version_info < (3, 11))' 2>/dev/null
}

# 1. Find a Python 3.11 or newer.
PY=""
for c in "${PYTHON:-}" python3.13 python3.12 python3.11 python3 python; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if v=$(version_ok "$c"); then PY="$c"; echo "Python $v found: $c"; break; fi
done
if [ -z "$PY" ]; then
    echo "No Python 3.11 or newer found. Install it and run this again (or set PYTHON=...)." >&2
    exit 1
fi

# 2. Create or reuse the virtual environment.
if [ -x "$VENV/bin/python" ]; then VPY="$VENV/bin/python"
elif [ -x "$VENV/Scripts/python.exe" ]; then VPY="$VENV/Scripts/python.exe"
else
    echo "Creating $VENV"
    "$PY" -m venv "$VENV"
    if [ -x "$VENV/bin/python" ]; then VPY="$VENV/bin/python"; else VPY="$VENV/Scripts/python.exe"; fi
fi
echo "Using $VPY"

# 3. Install the packages.
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r "$SCRIPTS/requirements.txt"
if [ "$VIDEO" = 1 ]; then
    "$VPY" -m pip install -r "$SCRIPTS/requirements-video.txt"
    [ "$GPU" = 1 ] && "$VPY" -m pip install -r "$SCRIPTS/requirements-gpu.txt"
elif [ "$GPU" = 1 ]; then
    echo "--gpu only applies together with --video: skipped."
fi

# 4. Optional external programs.
echo
echo "Optional programs:"
lo=""
for p in "/c/Program Files/LibreOffice/program/python.exe" "/usr/lib/libreoffice/program/python" \
         "/Applications/LibreOffice.app/Contents/Resources/python"; do
    [ -x "$p" ] && { lo="$p"; break; }
done
if [ -n "$lo" ] || command -v soffice >/dev/null 2>&1; then
    echo "  LibreOffice  found   - needed by document-export/odt_finish.py, run it with LibreOffice's python"
else
    echo "  LibreOffice  missing - only document-export/odt_finish.py (ODT contents and PDF export) needs it"
fi
for t in "pdftotext:PDF text for source-finder/find_sources.py (Poppler)" "curl:fallback download in fetch_live_sheet.py"; do
    name="${t%%:*}"; why="${t#*:}"
    if command -v "$name" >/dev/null 2>&1; then state="found  "; else state="missing"; fi
    printf "  %-12s %s - %s\n" "$name" "$state" "$why"
done

echo
echo "Done. Activate the environment with:  source .venv/bin/activate   (Git Bash: source .venv/Scripts/activate)"
echo "Settings: copy scripts/settings.example.toml to scripts/settings.local.toml and fill it in (see scripts/project_config.py)."
