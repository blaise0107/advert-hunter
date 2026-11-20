# -*- coding: utf-8 -*-
"""Главное окно новой панели AdvertApp."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from core.config import load_settings, save_settings
from panel.pages.active import ActivePage
from panel.pages.database import DatabasePage
from panel.pages.farm import FarmPage
from panel.pages.log import LogPage
from panel.pages.new import NewPage
from panel.pages.overview import OverviewPage
from panel.pages.screen import ScreenPage
from panel.pages.settings import SettingsPage
from panel.pages.strategy import StrategyPage
from panel.theme import apply_theme
from panel.widgets import propagate_tooltips


def resolve_theme(mode: str) -> str:
    if mode != "system":
        return mode if mode in ("dark", "light") else "dark"
    try:
        scheme = QApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except Exception:
        return "dark"


class PanelWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.setWindowTitle("AdvertApp Panel — fab1701")
        self.resize(1280, 860)
        self.setMinimumSize(1000, 700)

        self.overview = OverviewPage(self)
        self.new_page = NewPage(self)
        self.active_page = ActivePage(self)
        self.farm_page = FarmPage(self)
        self.screen_page = ScreenPage(self)
        self.database_page = DatabasePage(self)
        self.settings_page = SettingsPage(self)
        self.strategy_page = StrategyPage(self)
        self.log_page = LogPage(self)

        for page, name in (
            (self.overview, "OverviewPage"),
            (self.new_page, "NewPage"),
            (self.active_page, "ActivePage"),
            (self.farm_page, "FarmPage"),
            (self.screen_page, "ScreenPage"),
            (self.database_page, "DatabasePage"),
            (self.settings_page, "SettingsPage"),
            (self.strategy_page, "StrategyPage"),
            (self.log_page, "LogPage"),
        ):
            page.setObjectName(name)

        self.addSubInterface(self.overview, FluentIcon.HOME, "Обзор")
        self.addSubInterface(self.new_page, FluentIcon.SEARCH, "Новые")
        self.addSubInterface(self.active_page, FluentIcon.PLAY, "Активные")
        self.addSubInterface(self.farm_page, FluentIcon.IOT, "Ферма")
        self.addSubInterface(self.screen_page, FluentIcon.PHONE, "Экран")
        self.addSubInterface(self.database_page, FluentIcon.LIBRARY, "База")
        self.addSubInterface(self.strategy_page, FluentIcon.ROBOT, "Стратегии")
        self.addSubInterface(
            self.log_page, FluentIcon.DOCUMENT, "Журнал",
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Настройки",
            position=NavigationItemPosition.BOTTOM,
        )

        nav_tips = {
            "OverviewPage": "Статус цикла, баланс, цель и быстрые действия.",
            "NewPage": "Задания из «Новых»: стратегия, перепроверки и временный ЧС.",
            "ActivePage": "Задания из «Активных»: проход, удержание и последний результат.",
            "FarmPage": "Ферма: задания на всех телефонах, очередь и переход к экрану.",
            "ScreenPage": "Живой экран любого подключённого телефона; поток только для выбранного.",
            "DatabasePage": "База приложений: иконки, ID, магазины и проверка.",
            "LogPage": "Логи проекта, их размеры и ротация.",
            "SettingsPage": "Параметры цикла, стратегий и обслуживания.",
        }
        for route, tip in nav_tips.items():
            try:
                widget = self.navigationInterface.widget(route)
                if widget is not None:
                    widget.setToolTip(tip)
            except Exception:
                pass

        self.settings_page.themeChanged.connect(self.change_theme)
        propagate_tooltips(self)
        try:
            self.navigationInterface.setExpandWidth(230)
            self.navigationInterface.setMinimumExpandWidth(900)
            self.navigationInterface.setCollapsible(False)
        except Exception:
            pass
        try:
            self.setMicaEffectEnabled(True)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # FluentWindow сворачивает навигацию до иконок при первом показе,
        # если окно ещё не получило финальный размер. Разворачиваем явно.
        try:
            self.navigationInterface.expand(useAni=False)
        except Exception:
            pass

    def change_theme(self, mode: str):
        # Перечитываем файл: страница «Настройки» могла только что сохранить
        # другие поля, и старый self.settings затёр бы их своим значением.
        fresh = load_settings()
        fresh["theme"] = mode
        save_settings(fresh)
        changed = fresh.get("theme") != self.settings.get("theme")
        self.settings = fresh
        if changed:
            apply_theme(QApplication.instance(), resolve_theme(mode))

    def closeEvent(self, event):
        # Панель можно закрывать: бот живёт отдельным процессом.
        # Живой экран останавливаем, иначе поток adb переживёт окно.
        try:
            self.screen_page.shutdown()
        except Exception:
            pass
        try:
            self.farm_page.shutdown()
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AdvertApp Panel")
    settings = load_settings()
    apply_theme(app, resolve_theme(settings.get("theme", "dark")))
    window = PanelWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
