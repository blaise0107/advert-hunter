# -*- coding: utf-8 -*-
"""Страница «Журнал»: все источники, фильтр, пауза и размеры файлов."""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from core.config import BASE, load_settings
from core.housekeeping import (
    LOG_DIR,
    all_log_files,
    human_size,
    rotate_all,
    run_log_files,
)
from panel.pages.base import BasePage
from panel.widgets import Card, LogView

LOGS_DIR = os.path.join(BASE, "logs")
SOURCES = [
    ("farm.log", "farm.log"),
    ("farm_console.log", "farm_console.log"),
    ("resume.log", "resume.log"),
    ("resume.err", "resume.err"),
    ("hunter2.log", "hunter2.log"),
    ("tasks.log", "tasks.log"),
    ("panel_hunter.log", "panel_hunter.log"),
    ("panel_tasks.log", "panel_tasks.log"),
    ("panel_active_loop.log", "panel_active_loop.log"),
    ("active_loop.log", "active_loop.log"),
    ("active_list.log", "active_list.log"),
    ("migration.log", "migration.log"),
    ("rotate.log", "rotate.log"),
]


def _resolve_log(name: str) -> str:
    path = os.path.join(LOGS_DIR, name)
    if os.path.exists(path):
        return path
    legacy = os.path.join(BASE, name)
    return legacy if os.path.exists(legacy) else path


class LogPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "Журнал",
            "Все логи проекта. Фильтр ищет по подстроке, «Пауза» останавливает "
            "автообновление. Логи пишутся без буферизации, поэтому строки "
            "появляются сразу.",
            parent,
        )
        self.settings = load_settings()
        existing = [
            (label, _resolve_log(name))
            for label, name in SOURCES
            if os.path.exists(_resolve_log(name))
        ]
        existing += [(info["name"], info["path"]) for info in run_log_files()]

        info = Card(
            "Источники и размеры",
            "resume.log — общий ход цикла; hunter2.log — OCR-забор «Новых»; "
            "tasks.log — обход «Активных». Большие файлы переносятся в logs/ "
            "перед запуском цикла, старые копии удаляются по лимиту.")
        self.l_files = QLabel("—")
        self.l_files.setObjectName("Muted")
        self.l_files.setWordWrap(True)
        info.add(self.l_files)

        row = QHBoxLayout()
        self.btn_sizes = QPushButton("Обновить размеры")
        self.btn_sizes.setToolTip("Перечитать размеры и время изменения логов.")
        self.btn_sizes.clicked.connect(self.refresh_sizes)
        row.addWidget(self.btn_sizes)
        self.btn_rotate = QPushButton("Ротировать большие логи")
        self.btn_rotate.setToolTip(
            "Перенести логи больше лимита из «Настроек» в папку logs/.")
        self.btn_rotate.clicked.connect(self.rotate_now)
        row.addWidget(self.btn_rotate)
        self.btn_folder = QPushButton("Открыть папку logs")
        self.btn_folder.setToolTip("Открыть папку с архивными копиями логов.")
        self.btn_folder.clicked.connect(self.open_logs_folder)
        row.addWidget(self.btn_folder)
        row.addStretch(1)
        info.add_layout(row)
        self.l_action = QLabel("")
        self.l_action.setObjectName("Muted")
        info.add(self.l_action)
        self.add(info)

        self.log = LogView(existing or [("resume.log", os.path.join(LOGS_DIR, "resume.log"))])
        self.root.addWidget(self.log, 1)

        self.sizes_timer = QTimer(self)
        self.sizes_timer.timeout.connect(self.refresh_sizes)
        self.sizes_timer.start(10000)
        self.refresh_sizes()

    # ------------------------------------------------------------------
    def refresh_sizes(self):
        parts = []
        for info in all_log_files():
            if not info.get("exists"):
                continue
            stamp = datetime.fromtimestamp(info["mtime"]).strftime("%d.%m %H:%M")
            parts.append(f"{info['name']} — {human_size(info['size'])} ({stamp})")
        self.l_files.setText(" · ".join(parts) if parts else "Логов пока нет.")

    def rotate_now(self):
        self.settings = load_settings()
        maintenance = self.settings.get("maintenance", {})
        moved = rotate_all(
            max_mb=float(maintenance.get("log_max_mb", 2)),
            keep=int(maintenance.get("log_keep", 10)),
        )
        self.l_action.setText(
            f"Перенесено в logs/: {len(moved)}." if moved
            else "Все логи меньше лимита — ротация не нужна.")
        self.refresh_sizes()

    def open_logs_folder(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_DIR))
