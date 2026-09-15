"""Render the app icon to viper_ide/resources/viper.png and packaging/viper.ico."""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from viper_ide.icons import app_icon_pixmap  # noqa: E402


def png_bytes(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    app_icon_pixmap(size).save(buf, "PNG")
    return bytes(buf.data())


def main() -> None:
    app = QApplication([])  # noqa: F841 - must stay alive while pixmaps are drawn
    res = ROOT / "viper_ide" / "resources"
    res.mkdir(exist_ok=True)
    (res / "viper.png").write_bytes(png_bytes(256))
    big = Image.open(io.BytesIO(png_bytes(256)))
    big.save(ROOT / "packaging" / "viper.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("wrote", res / "viper.png", ROOT / "packaging" / "viper.ico")


if __name__ == "__main__":
    main()
