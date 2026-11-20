# -*- coding: utf-8 -*-
"""Страница «Ферма»: задания на всех подключённых телефонах."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from core import devices, processes
from core.config import load_settings, save_settings
from panel.pages.base import BasePage
from panel.screen import DeviceListWorker
from panel.widgets import Badge, Card
from panel.workers import CmdWorker

STATE_COLORS = {
    "работает": "#2ecc71",
    "ждёт": "#f5b041",
    "онлайн": "#5dade2",
    "офлайн": "#93a1ae",
}


class FarmPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "Ферма",
            "Один супервизор запускает по процессу на каждый онлайн-телефон. "
            "Экран каждого устройства приводится к 1080×2412/408, после круга "
            "возвращается оригинал. Устройства берутся из `adb devices` автоматически.",
            parent,
        )
        self.settings = load_settings()
        self.status: dict = {}
        self.devices: list = []
        self._list_worker = None
        self._cmd_worker = None
        self._busy = False
        self._selected_serial = ""

        farm = self.settings.get("farm", {}) if isinstance(self.settings, dict) else {}

        # ---------------- управление ----------------
        ctrl = Card(
            "Управление фермой",
            "«Старт» поднимает farm.py. «Стоп» сначала просит ферму завершить "
            "круг и вернуть экраны, затем добивает оставшиеся процессы.",
        )
        self.badge = Badge("остановлена", "muted")
        ctrl.add_header_widget(self.badge)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        self.sp_parallel = QSpinBox()
        self.sp_parallel.setRange(1, 8)
        self.sp_parallel.setValue(max(1, min(8, int(farm.get("max_parallel", 2)))))
        self.sp_parallel.setToolTip(
            "Сколько телефонов вести одновременно. Каждый работник — отдельный "
            "процесс adb/OCR. Для слабого ПК начните с 2.")

        self.sp_round = QSpinBox()
        self.sp_round.setRange(4, 120)
        self.sp_round.setValue(max(4, min(120, int(farm.get("round_minutes", 15)))))
        self.sp_round.setSuffix(" мин")
        self.sp_round.setToolTip(
            "Длительность одного круга на телефон. Внутри круга примерно 60% "
            "времени — «Активные», остальное — «Новые».")

        self.l_plan = QLabel("—")
        self.l_plan.setObjectName("Muted")

        grid.addWidget(self._cap("Параллельно"), 0, 0)
        grid.addWidget(self.sp_parallel, 0, 1)
        grid.addWidget(self._cap("Круг"), 0, 2)
        grid.addWidget(self.sp_round, 0, 3)
        grid.addWidget(self._cap("План"), 0, 4)
        grid.addWidget(self.l_plan, 0, 5)
        ctrl.add_layout(grid)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setToolTip("Записать max_parallel и round_minutes в panel_settings.json.")
        self.btn_save.clicked.connect(self.save_settings)
        row.addWidget(self.btn_save)

        self.btn_start = QPushButton("Старт фермы")
        self.btn_start.setObjectName("AccentButton")
        self.btn_start.setToolTip("Запустить farm.py: все онлайн-телефоны будут взяты в работу.")
        self.btn_start.clicked.connect(lambda: self.run_cmd("start"))
        row.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Стоп")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_stop.setToolTip("Мягко остановить ферму и вернуть экраны телефонов.")
        self.btn_stop.clicked.connect(lambda: self.run_cmd("stop"))
        row.addWidget(self.btn_stop)

        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.setToolTip("Перечитать farm_status.json и список adb.")
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        ctrl.add_layout(row)

        self.l_summary = QLabel("Ферма не запущена.")
        self.l_summary.setObjectName("Muted")
        self.l_summary.setWordWrap(True)
        ctrl.add(self.l_summary)

        self.l_msg = QLabel("Готово.")
        self.l_msg.setObjectName("Muted")
        self.l_msg.setWordWrap(True)
        ctrl.add(self.l_msg)
        self.add(ctrl)

        # ---------------- таблица ----------------
        table_card = Card(
            "Телефоны",
            "Двойной клик по строке открывает живой экран этого телефона. "
            "Пока ферма работает, новые устройства подхватываются автоматически.",
        )
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Телефон", "Состояние", "Фаза", "PID", "Последняя строка", "Экран"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(300)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(self.open_screen)
        table_card.add(self.table, 1)
        self.add(table_card, 1)

        self.sp_parallel.valueChanged.connect(self.update_plan)
        self.sp_round.valueChanged.connect(self.update_plan)
        self.update_plan()

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(400, self.refresh)

    @staticmethod
    def _cap(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    # ------------------------------------------------------------------
    def update_plan(self):
        total = self.sp_round.value()
        active = max(3, int(round(total * 0.6)))
        new = max(2, total - active)
        self.l_plan.setText(f"≈ {active} мин «Активные» + {new} мин «Новые»")

    def save_settings(self):
        fresh = load_settings()
        fresh.setdefault("farm", {})
        fresh["farm"]["max_parallel"] = self.sp_parallel.value()
        fresh["farm"]["round_minutes"] = self.sp_round.value()
        save_settings(fresh)
        self.settings = fresh
        self.l_msg.setText("Настройки фермы сохранены. Новые значения — со следующего запуска.")

    def run_cmd(self, action: str):
        if self._busy:
            return
        if action == "start":
            self.save_settings()
        self._busy = True
        self._set_buttons(False)
        self.l_msg.setText("Запускаю…" if action == "start" else "Останавливаю…")
        self._cmd_worker = CmdWorker(action, self)
        self._cmd_worker.done.connect(self.on_cmd)
        self._cmd_worker.start()

    def on_cmd(self, ok: bool, msg: str):
        self._busy = False
        self._set_buttons(True)
        self.l_msg.setText(("Готово: " if ok else "Ошибка: ") + msg)
        self.refresh()

    def _set_buttons(self, enabled: bool):
        for button in (self.btn_start, self.btn_stop, self.btn_save, self.btn_refresh):
            button.setEnabled(enabled)

    # ------------------------------------------------------------------
    def refresh(self):
        status = processes.farm_status()
        self.status = status
        running = bool(status.get("running"))
        if running and status.get("farm_pid") and not processes.pid_alive(status.get("farm_pid")):
            running = False
        self.badge.setText("работает" if running else "остановлена")
        self.badge.set_variant("ok" if running else "muted")
        self.btn_start.setEnabled(not running and not self._busy)
        self.btn_stop.setEnabled(running and not self._busy)

        if status:
            deadline = self._format_time(status.get("deadline"))
            self.l_summary.setText(
                f"Активно {status.get('active', 0)}/{status.get('max_parallel', '?')} · "
                f"в очереди {len(status.get('queue') or [])} · "
                f"круг {status.get('round_minutes', '?')} мин · до {deadline}")
            self._fill_table(status.get("devices") or [])
        elif running:
            self.l_summary.setText("Ферма запущена, статус ещё не записан.")
            self._fill_table([])
        else:
            self.l_summary.setText("Ферма не запущена. Показаны все телефоны из adb.")
            self.fetch_devices()

    @staticmethod
    def _format_time(value) -> str:
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(str(value)).strftime("%H:%M")
        except ValueError:
            return str(value)

    def fetch_devices(self):
        if self._list_worker is not None and self._list_worker.isRunning():
            return
        self._list_worker = DeviceListWorker(self)
        self._list_worker.done.connect(self.on_devices)
        self._list_worker.failed.connect(
            lambda msg: self.l_msg.setText("adb: " + msg))
        self._list_worker.start()

    def on_devices(self, items: list):
        self.devices = items
        rows = [
            {
                "serial": d.serial,
                "model": d.name,
                "state": "онлайн" if d.online else "офлайн",
                "phase": "—",
                "pid": None,
                "last": "",
            }
            for d in items
        ]
        self._fill_table(rows)

    def _fill_table(self, rows: list):
        selected = self._selected_serial
        if self.table.currentRow() >= 0:
            item = self.table.item(self.table.currentRow(), 0)
            if item is not None:
                selected = item.data(Qt.ItemDataRole.UserRole) or selected
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for row, data in enumerate(rows):
            serial = str(data.get("serial") or "")
            model = str(data.get("model") or "")
            name = serial if not model else f"{serial}\n{model}"
            cell = QTableWidgetItem(name)
            cell.setData(Qt.ItemDataRole.UserRole, serial)
            cell.setToolTip(f"Серийный: {serial}\nМодель: {model or '—'}")
            self.table.setItem(row, 0, cell)

            state = str(data.get("state") or "—")
            state_item = QTableWidgetItem(state)
            state_item.setForeground(QColor(STATE_COLORS.get(state, "#93a1ae")))
            self.table.setItem(row, 1, state_item)

            self.table.setItem(row, 2, QTableWidgetItem(str(data.get("phase") or "—")))
            self.table.setItem(row, 3, QTableWidgetItem(
                str(data.get("pid") or data.get("slot") or "—")))
            last = str(data.get("last") or "")
            last_item = QTableWidgetItem(last)
            last_item.setToolTip(last)
            self.table.setItem(row, 4, last_item)

            button = QPushButton("Экран")
            button.setToolTip(f"Открыть живой экран {serial}.")
            button.clicked.connect(
                lambda _=False, s=serial: self.open_screen_for(s))
            self.table.setCellWidget(row, 5, button)
            self.table.setRowHeight(row, 44)

            if serial == selected:
                self.table.selectRow(row)
        self.table.setUpdatesEnabled(True)

    def open_screen(self, row: int, _column: int):
        item = self.table.item(row, 0)
        if item is not None:
            self.open_screen_for(item.data(Qt.ItemDataRole.UserRole) or "")

    def open_screen_for(self, serial: str):
        if not serial:
            return
        self._selected_serial = serial
        window = self.window()
        page = getattr(window, "screen_page", None)
        if page is None:
            return
        page.select_device(serial, start=True)
        try:
            window.switchTo(page)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.refresh)

    def shutdown(self):
        if self._list_worker is not None and self._list_worker.isRunning():
            self._list_worker.wait(2000)
