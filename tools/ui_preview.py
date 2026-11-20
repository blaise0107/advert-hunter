"""Показать одну страницу панели в отдельном окне (для проверки вёрстки).

Запуск: pythonw tools/ui_preview.py new 30
Страницы: overview, new, active, screen, database, settings, log.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from core.config import load_settings
from panel.app import PanelWindow, resolve_theme
from panel.theme import apply_theme

app = QApplication(sys.argv)
settings = load_settings()
apply_theme(app, resolve_theme(settings.get("theme", "dark")))
window = PanelWindow()
window.resize(1280, 860)
if len(sys.argv) > 3:
    window.resize(1280, int(sys.argv[3]))
window.show()

pages = {
    "overview": window.overview,
    "new": window.new_page,
    "active": window.active_page,
    "farm": window.farm_page,
    "screen": window.screen_page,
    "database": window.database_page,
    "settings": window.settings_page,
    "log": window.log_page,
}
name = sys.argv[1] if len(sys.argv) > 1 else "overview"
window.switchTo(pages[name])
if len(sys.argv) > 4 and sys.argv[4] == "bottom":
    page = pages[name]
    bar = page.scroll.verticalScrollBar()

    def _bottom():
        bar.setValue(bar.maximum())

    QTimer.singleShot(400, _bottom)
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
QTimer.singleShot(seconds * 1000, app.quit)
app.exec()
