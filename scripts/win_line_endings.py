"""Normalise Windows scripts before shipping: .ps1 = ASCII+CRLF+BOM, .bat/.iss = ASCII+CRLF (no BOM).

Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, and cmd needs CRLF labels,
so any drift here breaks the build on Windows while looking fine on Linux.
"""
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"


def fix(path: Path) -> None:
    data = path.read_bytes()
    body = data[3:] if data.startswith(BOM) else data
    try:
        body.decode("ascii")
    except UnicodeDecodeError as e:
        raise SystemExit(f"{path}: non-ASCII byte at offset {e.start}")
    body = body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    out = (BOM + body) if path.suffix == ".ps1" else body
    if out != data:
        path.write_bytes(out)
        print("normalised", path)


def main() -> None:
    for root in sys.argv[1:] or ["."]:
        for path in Path(root).rglob("*"):
            if path.suffix.lower() in (".ps1", ".bat", ".cmd", ".iss"):
                fix(path)


if __name__ == "__main__":
    main()
