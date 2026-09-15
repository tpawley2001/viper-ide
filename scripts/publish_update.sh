#!/usr/bin/env bash
# Publish a built installer to the update feed so installed copies update themselves.
#   scripts/publish_update.sh ["release notes"] [path/to/ViperIDE_Setup_X.Y.Z.exe]
# Feed directory: VIPER_UPDATE_DIR (default /var/www/html/viper/windows, served by nginx on :80).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
notes="${1:-}"
version=$(python3 -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"', open('$here/viper_ide/__init__.py').read()).group(1))")
exe="${2:-$here/dist/ViperIDE_Setup_${version}.exe}"
dest="${VIPER_UPDATE_DIR:-/var/www/html/viper/windows}"
[ -f "$exe" ] || { echo "no installer at $exe (build it first)" >&2; exit 1; }
mkdir -p "$dest"
file="$(basename "$exe")"
cp "$exe" "$dest/$file.tmp" && mv "$dest/$file.tmp" "$dest/$file"
python3 - "$dest" "$file" "$version" "$notes" <<'PY'
import hashlib, json, os, sys, datetime
dest, file, version, notes = sys.argv[1:5]
path = os.path.join(dest, file)
digest = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        digest.update(chunk)
major, minor, patch = (list(map(int, version.split(".")[:3])) + [0, 0, 0])[:3]
manifest = {
    "versionName": version,
    "versionCode": major * 10000 + minor * 100 + patch,
    "file": file,
    "sha256": digest.hexdigest(),
    "size": os.path.getsize(path),
    "publishedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "notes": notes,
}
tmp = os.path.join(dest, "version.json.tmp")  # manifest last and atomically: never points at a partial file
with open(tmp, "w") as f:
    json.dump(manifest, f, indent=2)
os.replace(tmp, os.path.join(dest, "version.json"))
print(json.dumps(manifest, indent=2))
PY
# Keep the three newest installers.
ls -1t "$dest"/ViperIDE_Setup_*.exe 2>/dev/null | tail -n +4 | xargs -r rm -f
echo "published $file to $dest"
