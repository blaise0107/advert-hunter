# -*- coding: utf-8 -*-
"""Снимки страниц панели без показа окна: проверка вёрстки после правок."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from panel.app import PanelWindow  # noqa: E402

OUT = os.path.join(ROOT, "frames")


def main():
    app = QApplication(sys.argv)
    window = PanelWindow()
    window.resize(1280, 860)
    window.show()
    app.processEvents()
    pages = [
        ("overview", window.overview),
        ("new", window.new_page),
        ("active", window.active_page),
        ("farm", window.farm_page),
        ("screen", window.screen_page),
        ("database", window.database_page),
        ("settings", window.settings_page),
        ("log", window.log_page),
    ]
    os.makedirs(OUT, exist_ok=True)
    for name, page in pages:
        window.switchTo(page)
        app.processEvents()
        path = os.path.join(OUT, f"ui_{name}.png")
        page.grab().save(path)
        print(name, path, page.size().width(), page.size().height())
    window.close()
    app.processEvents()


if __name__ == "__main__":
    main()
