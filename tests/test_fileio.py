"""Document round trips and failures that must leave the original file intact."""
import os
import stat

import pytest

from viper_ide.fileio import atomic_write, decode_source, encode_source


@pytest.mark.parametrize("data", [
    b"# coding: latin-1\nvalue = '\xe9\x80'\n",
    "#!/usr/bin/python\n# coding: cp1251\nvalue = 'Привет'\n".encode("cp1251"),
    b"\xef\xbb\xbfprint('hello')\r\n",
    "value = 'café'\r\n".encode("cp1252"),
    "value = '🐍'\n".encode("utf-8"),
])
def test_source_round_trip(data):
    text, encoding, bom = decode_source(data)
    encoded, _ = encode_source(text, encoding, bom)
    assert encoded == data


@pytest.mark.parametrize("data", [b"# coding: unknown-viper-encoding\n", b"# coding: utf-8\n'\xff'\n"])
def test_invalid_declared_encoding_is_reported(data):
    with pytest.raises(UnicodeError):
        decode_source(data)


def test_save_respects_edited_encoding_cookie():
    text = "# coding: cp1251\nvalue = 'Привет'\n"
    data, encoding = encode_source(text, "utf-8", False)
    assert data == text.encode("cp1251")
    assert encoding == "cp1251"


def test_unencodable_declared_source_does_not_silently_switch_encoding():
    with pytest.raises(UnicodeEncodeError):
        encode_source("# coding: latin-1\nvalue = '🐍'\n", "latin-1", False)
    data, encoding = encode_source("value = '🐍'\n", "cp1252", False)
    assert encoding == "utf-8"
    assert data.decode(encoding) == "value = '🐍'\n"


def test_atomic_write_preserves_original_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "script.py"
    path.write_bytes(b"original")

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        atomic_write(path, b"changed")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_preserves_permissions(tmp_path):
    path = tmp_path / "script.py"
    path.write_bytes(b"original")
    path.chmod(0o754)
    mode = stat.S_IMODE(path.stat().st_mode)
    atomic_write(path, b"changed")
    assert path.read_bytes() == b"changed"
    assert stat.S_IMODE(path.stat().st_mode) == mode


def test_atomic_write_keeps_symlink(tmp_path):
    target = tmp_path / "script.py"
    target.write_bytes(b"original")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    atomic_write(link, b"changed")
    assert link.is_symlink()
    assert target.read_bytes() == b"changed"


def test_atomic_write_does_not_overwrite_read_only_file(tmp_path):
    path = tmp_path / "script.py"
    path.write_bytes(b"original")
    path.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            atomic_write(path, b"changed")
        assert path.read_bytes() == b"original"
    finally:
        path.chmod(0o644)
