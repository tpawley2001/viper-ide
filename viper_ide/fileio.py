"""Text encoding and replacement writes for editor documents."""
from __future__ import annotations

import io
import os
import stat
import tempfile
import tokenize
from pathlib import Path


def _has_cookie(data: bytes) -> bool:
    return any(tokenize.cookie_re.match(line) for line in data.splitlines()[:2])


def decode_source(data: bytes) -> tuple[str, str, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    explicit = bom or _has_cookie(data)
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
        return text, "utf-8" if encoding == "utf-8-sig" else encoding, bom
    except (SyntaxError, UnicodeError) as ex:
        if explicit:
            raise UnicodeError(f"Cannot decode the file using its declared encoding: {ex}") from ex
    for encoding in ("cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue
    raise UnicodeError("Cannot decode the file")


def encode_source(text: str, encoding: str, bom: bool) -> tuple[bytes, str]:
    source = text.encode("utf-8")
    explicit = _has_cookie(source)
    if explicit:
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        except SyntaxError as ex:
            raise UnicodeError(str(ex)) from ex
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError:
        if explicit:
            raise
        encoding = "utf-8"
        data = source
    if bom and encoding == "utf-8":
        data = b"\xef\xbb\xbf" + data
    return data, encoding


def atomic_write(path: str | Path, data: bytes) -> None:
    # Follow an existing symlink so saving keeps the link and replaces its target.
    target = Path(path).resolve()
    mode = None
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError(f"File is read-only: {path}")
    except FileNotFoundError:
        pass
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
