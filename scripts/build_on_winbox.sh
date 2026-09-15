#!/usr/bin/env bash
# Ship the source to the Windows build box over ssh, build there, and copy the installer back.
#   scripts/build_on_winbox.sh [--selftest]      (host alias via WINBOX, default "winbox")
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
host="${WINBOX:-winbox}"
remote='C:\build\viper-ide'
args=()
[ "${1:-}" = "--selftest" ] && args+=("-SelfTest")

python3 "$here/scripts/win_line_endings.py" "$here/packaging"

ssh "$host" "if not exist $remote mkdir $remote"
tar -C "$here" -czf - --exclude=.venv --exclude=.gl-libs --exclude=build --exclude=dist --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude=.git . | ssh "$host" "tar -xzf - -C $remote"
ssh "$host" "powershell -NoProfile -ExecutionPolicy Bypass -File $remote\\packaging\\build_windows.ps1 ${args[*]:-}"

version=$(python3 -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"', open('$here/viper_ide/__init__.py').read()).group(1))")
mkdir -p "$here/dist"
scp "$host:C:/build/viper-ide/dist/ViperIDE_Setup_${version}.exe" "$here/dist/"
[ "${1:-}" = "--selftest" ] && scp "$host:C:/build/viper-ide/dist/selftest.json" "$host:C:/build/viper-ide/dist/selftest.png" "$here/dist/" || true
ls -la "$here/dist/ViperIDE_Setup_${version}.exe"
