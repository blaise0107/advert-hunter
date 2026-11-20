# -*- coding: utf-8 -*-
"""Обзор: статус, баланс, цель и быстрые действия."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import load_settings
from core.processes import LOGS_DIR
from panel.pages.base import ScrollPage
from panel.widgets import Badge, Card, LogView, StatCard
from panel.workers import CmdWorker, StatusWorker


class OverviewPage(ScrollPage):
    def __init__(self, parent=None):
        super().__init__(
            "Обзор",
            "Состояние цикла, баланс и быстрые действия. Кнопка «Вывести» здесь отсутствует намеренно.",
            parent,
        )
        self.settings = load_settings()
        self.busy = False
        self.status_worker = None
        self.cmd_worker = None
        self.balance_value = None

        # ---------------- цифры ----------------
        stats = QGridLayout()
        stats.setSpacing(12)
        self.card_balance = StatCard("Баланс", "—", "считать с экрана «Карта»/«Деньги»")
        self.card_target = StatCard(
            "Цель", f"{self.settings['target_balance']:.2f} ₽", "к выводу"
        )
        self.card_left = StatCard("Осталось", "—", "до цели")
        self.card_state = StatCard("Цикл", "—", "бот не запущен")
        for i, c in enumerate((self.card_balance, self.card_target,
                               self.card_left, self.card_state)):
            stats.addWidget(c, i // 2, i % 2)
        self.add_layout(stats)

        # ---------------- прогресс ----------------
        prog_card = Card("Прогресс к цели")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        prog_card.add(self.progress)
        self.progress_hint = QLabel("Баланс ещё не считан.")
        self.progress_hint.setObjectName("Muted")
        prog_card.add(self.progress_hint)
        self.add(prog_card)

        # ---------------- управление ----------------
        ctrl = Card("Управление", "Кнопки запускают фоновые процессы; панель не блокируется.")
        grid = QGridLayout()
        grid.setSpacing(8)
        buttons = [
            ("Старт цикла", "start", "AccentButton",
             "Запустить полный цикл «Активные» → «Новые» до времени остановки."),
            ("Стоп", "stop", "DangerButton",
             "Мягко остановить бота: текущее удержание не прерывается."),
            ("Перезапуск", "restart", "",
             "Остановить и снова запустить цикл."),
            ("Забрать «Новые» (2 мин)", "scan_new", "",
             "Разовый забор «Новых» на 120 секунд. Требует, чтобы цикл был остановлен."),
            ("Выполнить «Активные»", "run_active", "",
             "Разовый проход по «Активным» до 08:30."),
            ("Планировщик «Активных»", "run_active_loop", "",
             "Фоновый планировщик, который сам повторяет проходы «Активных»."),
            ("Считать баланс", "balance", "",
             "Найти баланс: главная страница сверху или «Деньги» → «Карта». "
             "Кнопку «Вывести» панель не нажимает."),
            ("Пробудить экран", "wake", "",
             "Разбудить телефон, не открывая приложение."),
        ]
        for i, (text, action, style, tip) in enumerate(buttons):
            b = QPushButton(text)
            b.setToolTip(tip)
            if style:
                b.setObjectName(style)
            b.clicked.connect(lambda _=False, a=action: self.run_cmd(a))
            grid.addWidget(b, i // 2, i % 2)
        ctrl.add_layout(grid)
        self.msg = QLabel("Готово.")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        ctrl.add(self.msg)
        self.add(ctrl)

        # ---------------- статус ----------------
        status = Card("Статус")
        self.badge = Badge("не запущен", "muted")
        status.add_header_widget(self.badge)
        self.status_grid = QGridLayout()
        self.status_grid.setHorizontalSpacing(18)
        self.status_grid.setVerticalSpacing(6)
        self.l_phase = QLabel("—")
        self.l_pids = QLabel("—")
        self.l_deadline = QLabel(self.settings["deadline"])
        self.l_updated = QLabel("—")
        self.l_adb = QLabel("—")
        self.l_density = QLabel("—")
        self.l_android = QLabel("—")
        self.l_serial = QLabel("—")
        rows = [
            ("Фаза", self.l_phase),
            ("PID", self.l_pids),
            ("Остановка", self.l_deadline),
            ("Обновлено", self.l_updated),
            ("adb", self.l_adb),
            ("Плотность", self.l_density),
            ("Android", self.l_android),
            ("Серийный", self.l_serial),
        ]
        for i, (name, value) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            self.status_grid.addWidget(cap, i // 2, (i % 2) * 2)
            self.status_grid.addWidget(value, i // 2, (i % 2) * 2 + 1)
        status.add_layout(self.status_grid)
        self.add(status)

        # ---------------- журнал ----------------
        self.log = LogView(
            [
                ("resume.log", str(LOGS_DIR / "resume.log")),
                ("resume.err", str(LOGS_DIR / "resume.err")),
                ("hunter2.log", str(LOGS_DIR / "hunter2.log")),
                ("tasks.log", str(LOGS_DIR / "tasks.log")),
            ]
        )
        self.log.setMinimumHeight(280)
        self.add(self.log)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(5000)
        QTimer.singleShot(300, self.refresh_status)
        self.update_balance(self.balance_value)

    # ------------------------------------------------------------------
    def run_cmd(self, action: str):
        if self.busy:
            return
        self.busy = True
        self.set_buttons(False)
        self.msg.setText(f"Выполняю: {action}…")
        self.cmd_worker = CmdWorker(action, self)
        self.cmd_worker.done.connect(self.on_cmd)
        self.cmd_worker.start()

    def on_cmd(self, ok: bool, msg: str):
        self.busy = False
        self.set_buttons(True)
        if msg.startswith("BALANCE|"):
            value = msg.split("|", 1)[1]
            self.balance_value = value
            self.card_balance.set_value(f"{value} ₽", "считано с экрана")
            self.msg.setText("Баланс считан.")
            self.update_balance(value)
        elif msg.startswith("BALANCE_TOTAL|"):
            value = msg.split("|", 1)[1]
            self.card_balance.set_value(f"{value} ₽", "«Заработано всего»")
            self.msg.setText("Строки баланса нет; показано «Заработано всего».")
        else:
            self.msg.setText(("Готово: " if ok else "Ошибка: ") + msg)
        self.refresh_status()

    def set_buttons(self, enabled: bool):
        for b in self.findChildren(QPushButton):
            b.setEnabled(enabled)

    def update_balance(self, value: str | None):
        if value:
            try:
                current = float(str(value).replace(" ", "").replace(",", "."))
            except ValueError:
                current = None
        else:
            current = None
        # Настройки могли измениться в панели — перечитываем их, чтобы
        # «Цель» в дашборде была актуальной.
        self.settings = load_settings()
        target = float(self.settings.get("target_balance", 210))
        self.card_target.set_value(f"{target:.2f} ₽", "к выводу")
        if current is None:
            self.progress.setValue(0)
            self.card_left.set_value("—", "нужен баланс")
            self.progress_hint.setText("Нажмите «Считать баланс» на экране «Карта»/«Деньги».")
            return
        left = max(0.0, target - current)
        self.card_left.set_value(f"{left:.2f} ₽", "до цели")
        percent = int(min(100, max(0, current / target * 100))) if target else 0
        self.progress.setValue(percent)
        self.progress_hint.setText(
            f"{current:.2f} ₽ из {target:.2f} ₽ — осталось {left:.2f} ₽"
        )

    def refresh_status(self):
        if self.status_worker and self.status_worker.isRunning():
            return
        self.status_worker = StatusWorker(self)
        self.status_worker.done.connect(self.on_status)
        self.status_worker.start()

    def on_status(self, info: dict):
        pids = info.get("pids") or []
        running = bool(pids)
        self.badge.setText("запущен" if running else "не запущен")
        self.badge.set_variant("ok" if running else "muted")
        self.card_state.set_value("запущен" if running else "остановлен", info.get("phase", "—"))
        self.l_phase.setText(info.get("phase", "—"))
        self.l_pids.setText(", ".join(str(p) for p in pids) if pids else "—")
        self.l_updated.setText(datetime.now().strftime("%H:%M:%S"))
        self.l_adb.setText("работает" if info.get("adb") else "не отвечает")
        self.l_density.setText(str(info.get("density", "—")))
        self.l_android.setText(str(info.get("android", "—")))
        self.l_serial.setText(str(info.get("serial", "—")))
        if info.get("error"):
            self.msg.setText("Ошибка статуса: " + info["error"])
