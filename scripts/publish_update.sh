#!/usr/bin/env bash
# Publish a built installer to the update feed so installed copies update themselves.
#   scripts/publish_update.sh ["release notes"] [path/to/ViperIDE_Setup_X.Y.Z.exe]
# The portable zip from the same build (ViperIDE_Portable_X.Y.Z.zip next to the installer) goes up too,
# so portable copies can update themselves.
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
zip="$(dirname "$exe")/ViperIDE_Portable_${version}.zip"
zipfile=""
if [ -f "$zip" ]; then
    zipfile="$(basename "$zip")"
    cp "$zip" "$dest/$zipfile.tmp" && mv "$dest/$zipfile.tmp" "$dest/$zipfile"
else
    echo "warning: no portable zip at $zip; portable copies won't see this update" >&2
fi
python3 - "$dest" "$file" "$version" "$notes" "$zipfile" <<'PY'
import hashlib, json, os, sys, datetime
dest, file, version, notes, zipfile = sys.argv[1:6]


def sha256(name):
    digest = hashlib.sha256()
    with open(os.path.join(dest, name), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


path = os.path.join(dest, file)
major, minor, patch = (list(map(int, version.split(".")[:3])) + [0, 0, 0])[:3]
manifest = {
    "versionName": version,
    "versionCode": major * 10000 + minor * 100 + patch,
    "file": file,
    "sha256": sha256(file),
    "size": os.path.getsize(path),
    "publishedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "notes": notes,
}
if zipfile:
    manifest["portable"] = {"file": zipfile, "sha256": sha256(zipfile),
                            "size": os.path.getsize(os.path.join(dest, zipfile))}
tmp = os.path.join(dest, "version.json.tmp")  # manifest last and atomically: never points at a partial file
with open(tmp, "w") as f:
    json.dump(manifest, f, indent=2)
os.replace(tmp, os.path.join(dest, "version.json"))
print(json.dumps(manifest, indent=2))
PY
# Keep the three newest installers and zips.
ls -1t "$dest"/ViperIDE_Setup_*.exe 2>/dev/null | tail -n +4 | xargs -r rm -f
ls -1t "$dest"/ViperIDE_Portable_*.zip 2>/dev/null | tail -n +4 | xargs -r rm -f
echo "published $file to $dest"
