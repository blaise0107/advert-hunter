# -*- coding: utf-8 -*-
"""Страница «Активные»: параметры обхода и последние действия."""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
)

from core.config import BASE, load_settings, save_settings
from panel.pages.base import ScrollPage
from panel.widgets import Card, LogView
from panel.workers import CmdWorker

LOGS_DIR = os.path.join(BASE, "logs")


class ActivePage(ScrollPage):
    def __init__(self, parent=None):
        super().__init__(
            "Активные",
            "Все страницы, защита от бесконечной прокрутки, порядок по этапам. "
            "Сначала заказы, взятые из «Новых», и шаги «Установить приложение», "
            "затем «Открыть на 2/8/16/31 дней».",
            parent,
        )
        self.settings = load_settings()
        self.busy = False
        self.cmd_worker = None

        card = Card("Стратегия «Активных»", "0 страниц = без ограничения; стоп после пустых экранов и жёсткий предел — страховка.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        self.sp_pages = QSpinBox()
        self.sp_pages.setRange(0, 50)
        self.sp_pages.setValue(int(self.settings["active"].get("max_pages", 0)))
        self.sp_pages.setSpecialValueText("все")
        self.sp_pages.setToolTip(
            "Сколько страниц «Активных» обходить. 0 = все страницы без ограничения.")

        self.sp_empty = QSpinBox()
        self.sp_empty.setRange(1, 10)
        self.sp_empty.setValue(int(self.settings["active"].get("empty_stop", 2)))
        self.sp_empty.setToolTip(
            "Остановить обход после стольких подряд пустых страниц.")

        self.sp_cap = QSpinBox()
        self.sp_cap.setRange(1, 200)
        self.sp_cap.setValue(int(self.settings["active"].get("hard_cap", 25)))
        self.sp_cap.setToolTip(
            "Жёсткий предел числа карточек за проход — страховка от зацикливания.")

        self.sp_retry = QSpinBox()
        self.sp_retry.setRange(0, 10)
        self.sp_retry.setValue(int(self.settings["active"].get("retry_count", 2)))
        self.sp_retry.setToolTip(
            "Сколько раз повторять задание, если действие не подтвердилось.")

        self.cb_sort = QCheckBox("Сортировать по цене")
        self.cb_sort.setChecked(bool(self.settings["active"].get("sort_by_price")))
        self.cb_sort.setToolTip(
            "Сначала выполнять самые дорогие задания из списка «Активных». "
            "Работает, когда выключен порядок по этапам.")

        self.cb_stage = QCheckBox("Порядок по этапам")
        self.cb_stage.setChecked(bool(self.settings["active"].get("order_by_stage", True)))
        self.cb_stage.setToolTip(
            "Сначала заказы, взятые из «Новых», и шаги «Установить приложение»; "
            "затем «Открыть на 2/8/16/31 дней» по возрастанию срока. "
            "Внутри этапа — цена по убыванию.")

        self.sp_cleanup = QSpinBox()
        self.sp_cleanup.setRange(1, 5)
        self.sp_cleanup.setValue(
            int(self.settings["active"].get("cleanup_confirmations", 2)))
        self.sp_cleanup.setSuffix(" раз")
        self.sp_cleanup.setToolTip(
            "Сколько раз перезапускать AdvertApp и перепроверять «Активные», "
            "прежде чем считать заказ завершённым и удалить его приложение.")

        self.sp_cleanup_delay = QSpinBox()
        self.sp_cleanup_delay.setRange(0, 1440)
        self.sp_cleanup_delay.setValue(
            int(self.settings["active"].get("cleanup_delay_minutes", 0)))
        self.sp_cleanup_delay.setSuffix(" мин")
        self.sp_cleanup_delay.setToolTip(
            "Дополнительная пауза перед удалением завершённого заказа. "
            "0 = удалять сразу после подтверждений.")

        rows = [
            ("Страниц", self.sp_pages),
            ("Пустых подряд", self.sp_empty),
            ("Жёсткий предел", self.sp_cap),
            ("Повторов", self.sp_retry),
            ("Порядок по этапам", self.cb_stage),
            ("Сортировка", self.cb_sort),
            ("Проверок удаления", self.sp_cleanup),
            ("Отложить удаление", self.sp_cleanup_delay),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            grid.addWidget(cap, i // 2, (i % 2) * 2)
            grid.addWidget(widget, i // 2, (i % 2) * 2 + 1)
        card.add_layout(grid)

        btns = QHBoxLayout()
        b_save = QPushButton("Сохранить настройки")
        b_save.setObjectName("AccentButton")
        b_save.setToolTip("Записать параметры «Активных» в panel_settings.json.")
        b_save.clicked.connect(self.save)
        b_run = QPushButton("Выполнить «Активные»")
        b_run.setToolTip("Разовый проход по «Активным»; цикл должен быть остановлен.")
        b_run.clicked.connect(lambda: self.run_cmd("run_active"))
        b_loop = QPushButton("Планировщик «Активных»")
        b_loop.setToolTip("Фоновый планировщик повторных проходов «Активных».")
        b_loop.clicked.connect(lambda: self.run_cmd("run_active_loop"))
        b_stop = QPushButton("Стоп")
        b_stop.setObjectName("DangerButton")
        b_stop.setToolTip("Остановить все процессы бота.")
        b_stop.clicked.connect(lambda: self.run_cmd("stop"))
        for b in (b_save, b_run, b_loop, b_stop):
            btns.addWidget(b)
        btns.addStretch(1)
        card.add_layout(btns)
        self.msg = QLabel("Готово.")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        card.add(self.msg)
        self.add(card)

        live = Card("Последний проход", "Хвост resume.log и tasks.log; обновляется каждые 3 секунды.")
        self.l_count = QLabel("—")
        self.l_action = QLabel("—")
        self.l_hold = QLabel("—")
        self.l_perm = QLabel("Разрешения: авто-подтверждение системных диалогов включено")
        self.l_after = QLabel("—")
        self.l_updated = QLabel("—")
        for w in (self.l_count, self.l_action, self.l_hold, self.l_perm, self.l_after, self.l_updated):
            w.setWordWrap(True)
            live.add(w)
        self.add(live)

        self.log = LogView(
            [
                ("resume.log", os.path.join(LOGS_DIR, "resume.log")),
                ("tasks.log", os.path.join(LOGS_DIR, "tasks.log")),
                ("tasks_run_035543.log", os.path.join(LOGS_DIR, "tasks_run_035543.log")),
            ]
        )
        self.log.setMinimumHeight(320)
        self.add(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_live)
        self.timer.start(3000)
        QTimer.singleShot(300, self.refresh_live)

    # ------------------------------------------------------------------
    def save(self):
        self.settings["active"]["max_pages"] = self.sp_pages.value()
        self.settings["active"]["empty_stop"] = self.sp_empty.value()
        self.settings["active"]["hard_cap"] = self.sp_cap.value()
        self.settings["active"]["retry_count"] = self.sp_retry.value()
        self.settings["active"]["sort_by_price"] = self.cb_sort.isChecked()
        self.settings["active"]["order_by_stage"] = self.cb_stage.isChecked()
        self.settings["active"]["cleanup_confirmations"] = self.sp_cleanup.value()
        self.settings["active"]["cleanup_delay_minutes"] = self.sp_cleanup_delay.value()
        save_settings(self.settings)
        self.msg.setText("Настройки «Активных» сохранены.")

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
        self.msg.setText(("Готово: " if ok else "Ошибка: ") + msg)
        self.refresh_live()

    def set_buttons(self, enabled: bool):
        for b in self.findChildren(QPushButton):
            b.setEnabled(enabled)

    def refresh_live(self):
        lines = self._tail("resume.log", 600)
        count = action = hold = after = perm = ""
        for line in reversed(lines):
            low = line.lower()
            if not count and "активных заказов" in low:
                count = line.strip()
            if not action and "действие:" in low:
                action = line.strip()
            if not hold and ("держим" in low or "запущен com." in low or "ждём" in low):
                hold = line.strip()
            if not perm and "диалог разрешений" in low:
                perm = line.strip()
            if not after and "после обхода активных" in low:
                after = line.strip()
            if count and action and hold and after and perm:
                break
        self.l_count.setText("Заказов: " + (count or "—"))
        self.l_action.setText("Действие: " + (action or "—"))
        self.l_hold.setText("Удержание: " + (hold or "—"))
        if perm:
            self.l_perm.setText("Разрешения: " + perm)
        else:
            self.l_perm.setText("Разрешения: авто-подтверждение системных диалогов включено")
        self.l_after.setText("Итог: " + (after or "—"))
        self.l_updated.setText("Обновлено: " + datetime.now().strftime("%H:%M:%S"))

    @staticmethod
    def _tail(name: str, count: int) -> list[str]:
        candidates = [os.path.join(LOGS_DIR, name), os.path.join(BASE, name)]
        for path in candidates:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read().splitlines()[-count:]
            except Exception:
                pass
        return []
