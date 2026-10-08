"""Self-update: manifest parsing, base fallback, checksum enforcement."""
import hashlib
import http.server
import json
import socket
import sys
import threading
from functools import partial
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from viper_ide import updater  # noqa: E402


class Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "feed"
    root.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def publish(root: Path, version="9.1.0", payload=b"installer-bytes", file="ViperIDE_Setup_9.1.0.exe", sha=None):
    (root / file).write_bytes(payload)
    (root / "version.json").write_text(json.dumps({
        "versionName": version, "versionCode": 1, "file": file,
        "sha256": sha or hashlib.sha256(payload).hexdigest(), "size": len(payload), "notes": "new stuff"}))


def dead_base() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_version_ordering():
    assert updater.parse_version("1.10.0") > updater.parse_version("1.9.9")
    assert updater.parse_version("1.0") == updater.parse_version("1.0.0")


def test_bases_normalise_and_dedupe(monkeypatch):
    monkeypatch.setenv("VIPER_UPDATE_URLS", "example.com/viper/, http://example.com/viper")
    monkeypatch.setattr(updater, "default_bases", lambda: ["built.in/feed", "http://example.com/viper"])
    got = updater.bases(Settings(update_urls=["https://x.test/feed/version.json"]))
    assert got == ["https://x.test/feed", "http://example.com/viper", "http://built.in/feed"]


def test_default_bases_file(tmp_path, monkeypatch):
    feeds = tmp_path / "update_feeds.txt"
    monkeypatch.setattr(updater, "FEEDS_FILE", feeds)
    assert updater.default_bases() == []  # a source checkout ships no feeds
    feeds.write_text("# comment\n\nhttp://a.test/viper\n  http://b.test/viper  \n")
    assert updater.default_bases() == ["http://a.test/viper", "http://b.test/viper"]


def test_check_falls_through_dead_base_and_downloads(server, tmp_path, monkeypatch):
    root, base = server
    publish(root)
    monkeypatch.setattr(updater, "default_bases", lambda: [])
    release, errors = updater.check(Settings(update_urls=[dead_base(), base]), timeout=2)
    assert release and release.base == base and len(errors) == 1
    assert release.newer_than("1.0.0") and not release.newer_than("9.1.0")
    seen = []
    path = updater.download(release, progress=seen.append, dest_dir=tmp_path)
    assert path.read_bytes() == b"installer-bytes" and seen[-1] == (15, 15)


def test_checksum_mismatch_is_discarded(server, tmp_path, monkeypatch):
    root, base = server
    publish(root, sha="0" * 64)
    monkeypatch.setattr(updater, "default_bases", lambda: [])
    release, _ = updater.check(Settings(update_urls=[base]))
    with pytest.raises(updater.UpdateError):
        updater.download(release, dest_dir=tmp_path)
    assert not list(tmp_path.glob("*.exe")) and not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("file", ["../evil.exe", "C:\\x.exe", "setup.bat"])
def test_unsafe_manifest_file_rejected(server, monkeypatch, file):
    root, base = server
    (root / "version.json").write_text(json.dumps({"versionName": "9.0", "file": file, "sha256": "a" * 64}))
    monkeypatch.setattr(updater, "default_bases", lambda: [])
    release, errors = updater.check(Settings(update_urls=[base]))
    assert release is None and "unsafe" in errors[0]


def test_portable_copy_takes_the_zip(server):
    root, base = server
    zip_bytes = b"zip-bytes"
    publish(root)
    manifest = json.loads((root / "version.json").read_text())
    manifest["portable"] = {"file": "ViperIDE_Portable_9.1.0.zip", "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                            "size": len(zip_bytes)}
    (root / "version.json").write_text(json.dumps(manifest))
    (root / "ViperIDE_Portable_9.1.0.zip").write_bytes(zip_bytes)
    assert updater.fetch_manifest(base, portable=False).file == "ViperIDE_Setup_9.1.0.exe"
    rel = updater.fetch_manifest(base, portable=True)
    assert (rel.file, rel.size, rel.version) == ("ViperIDE_Portable_9.1.0.zip", len(zip_bytes), "9.1.0")


def test_portable_copy_refuses_feed_without_zip(server):
    root, base = server
    publish(root)
    with pytest.raises(updater.UpdateError, match="no portable build"):
        updater.fetch_manifest(base, portable=True)


def test_portable_manifest_rejects_installer_name(server):
    root, base = server
    publish(root)
    manifest = json.loads((root / "version.json").read_text())
    manifest["portable"] = {"file": "evil.exe", "sha256": "0" * 64}
    (root / "version.json").write_text(json.dumps(manifest))
    with pytest.raises(updater.UpdateError, match="unsafe zip"):
        updater.fetch_manifest(base, portable=True)


def test_extract_portable_finds_the_app_folder(tmp_path):
    import zipfile

    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ViperIDE/ViperIDE.exe", b"exe")
        z.writestr("ViperIDE/portable.txt", b"marker")
        z.writestr("ViperIDE/_internal/x.dll", b"dll")
    staged = updater.extract_portable(archive, tmp_path / "stage")
    assert staged == tmp_path / "stage" / "ViperIDE"
    assert (staged / "_internal" / "x.dll").read_bytes() == b"dll"


def test_extract_portable_rejects_path_traversal(tmp_path):
    import zipfile

    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ViperIDE/ViperIDE.exe", b"exe")
        z.writestr("../escaped.txt", b"nope")
    with pytest.raises(updater.UpdateError, match="unsafe path"):
        updater.extract_portable(archive, tmp_path / "stage")
    assert not (tmp_path / "escaped.txt").exists()
