"""Render app/icons.py's code-drawn logo into a multi-resolution .ico.

    cd desktop
    python build/make_icon.py

Produces dist/naru.ico, embedded by the PyInstaller specs (icon=) and by
naru.iss (SetupIconFile) - build-time only, never a runtime dependency of
the app itself (see icons.py's own docstring on why the app draws its icon
in code rather than shipping an asset).

Headless: QGuiApplication is enough, no event loop or display needed to
render QPixmaps offscreen.

Pillow's ICO writer filters candidate `sizes=` against the base image's
own dimensions, so passing the smallest rendered size as the base and the
rest via append_images silently drops every larger size. Passing the
LARGEST image as the base avoids that.
"""

from __future__ import annotations

import os
import sys

here = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(here, "..")))

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from app.icons import SIZES, app_icon  # noqa: E402


def _to_pil(image: QImage):
    from PIL import Image

    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    buf = bytes(image.constBits())
    stride = image.bytesPerLine()
    # QImage rows can be padded; Pillow's "raw" decoder needs the real stride.
    return Image.frombuffer("RGBA", (width, height), buf, "raw", "RGBA", stride, 1)


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841 - required for QPixmap rendering
    icon = app_icon("ok")

    images = []
    for size in sorted(SIZES):
        pixmap = icon.pixmap(size, size)
        images.append(_to_pil(pixmap.toImage()))

    out_dir = os.path.abspath(os.path.join(here, "..", "dist"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "naru.ico")

    largest = images[-1]
    largest.save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in sorted(SIZES)],
        append_images=images[:-1],
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
