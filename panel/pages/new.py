# -*- coding: utf-8 -*-
"""Страница «Новые»: стратегия верхнего экрана и последний скан."""
from __future__ import annotations

import os
import re
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
)

from core import blacklist
from core.config import BASE, load_settings, save_settings
from panel.pages.base import ScrollPage
from panel.widgets import Card, LogView
from panel.workers import CmdWorker

LOGS_DIR = os.path.join(BASE, "logs")


class NewPage(ScrollPage):
    def __init__(self, parent=None):
        super().__init__(
            "Новые",
            "Обход всего списка «Новые»: все страницы и долистывание до низа. "
            "Если доступных RU-заданий не осталось — контрольные перепроверки "
            "(N проверок по N секунд), потом уходим в «Активные». «Стоп при простое» "
            "страхует от зависания.",
            parent,
        )
        self.settings = load_settings()
        self.busy = False
        self.cmd_worker = None

        # ---------------- стратегия ----------------
        card = Card("Стратегия «Новых»", "Значения сохраняются в panel_settings.json и читаются ботом перед проходом.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        self.sp_pages = QSpinBox()
        self.sp_pages.setRange(0, 50)
        self.sp_pages.setValue(int(self.settings["new"].get("max_pages", 0)))
        self.sp_pages.setSpecialValueText("все")
        self.sp_pages.setToolTip(
            "Сколько страниц списка «Новые» обходить. 0 = все страницы без ограничения.")
        self.sp_empty = QSpinBox()
        self.sp_empty.setRange(1, 10)
        self.sp_empty.setValue(int(self.settings["new"].get("empty_stop", 2)))
        self.sp_empty.setSuffix(" экр.")
        self.sp_empty.setToolTip(
            "Сколько подряд пустых экранов считать концом прокрутки страницы.")
        self.cb_scroll = QCheckBox("Листать до низа")
        self.cb_scroll.setChecked(bool(self.settings["new"].get("scroll_to_bottom", True)))
        self.cb_scroll.setToolTip(
            "Долистывать каждую страницу списка «Новые» до конца, а не только верхний экран.")
        self.cb_sort = QCheckBox("Сортировать по цене")
        self.cb_sort.setChecked(bool(self.settings["new"].get("sort_by_price")))
        self.cb_sort.setToolTip(
            "Брать самое дорогое доступное RU-задание на экране, а не верхнее.")

        self.sp_recheck = QSpinBox()
        self.sp_recheck.setRange(0, 10)
        self.sp_recheck.setValue(int(self.settings["new"].get("recheck_count", 2)))
        self.sp_recheck.setSuffix(" доп.")
        self.sp_recheck.setToolTip(
            "Контрольные проверки: сколько раз перепроверять весь список «Новые», "
            "если доступных RU-заданий не осталось.")
        self.sp_delay = QSpinBox()
        self.sp_delay.setRange(1, 120)
        self.sp_delay.setValue(int(self.settings["new"].get("recheck_delay", 10)))
        self.sp_delay.setSuffix(" с")
        self.sp_delay.setToolTip("Пауза между контрольными проверками списка «Новые».")
        self.sp_idle = QSpinBox()
        self.sp_idle.setRange(1, 60)
        self.sp_idle.setValue(int(self.settings["new"].get("idle_stop_minutes", 8)))
        self.sp_idle.setSuffix(" мин")
        self.sp_idle.setToolTip(
            "Если доступных RU-заданий нет дольше этого времени — фаза «Новые» "
            "завершается и цикл уходит в «Активные».")
        self.sp_offers = QSpinBox()
        self.sp_offers.setRange(0, 50)
        self.sp_offers.setValue(int(self.settings["new"].get("max_offers_per_run", 3)))
        self.sp_offers.setSuffix(" шт.")
        self.sp_offers.setToolTip(
            "Сколько предложений бот берёт из модального окна за один прогон. "
            "0 — без ограничения. Лимит нужен, пока задания нечем выполнять: "
            "иначе заказы копятся и сгорают.")

        rows = [
            ("Страниц", self.sp_pages),
            ("Пустых подряд", self.sp_empty),
            ("Свайп до низа", self.cb_scroll),
            ("Сортировка", self.cb_sort),
            ("Перепроверки", self.sp_recheck),
            ("Пауза перепроверки", self.sp_delay),
            ("Стоп при простое", self.sp_idle),
            ("Предложений за прогон", self.sp_offers),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            grid.addWidget(cap, i, 0)
            grid.addWidget(widget, i, 1)
        card.add_layout(grid)

        btns = QHBoxLayout()
        b_save = QPushButton("Сохранить настройки")
        b_save.setObjectName("AccentButton")
        b_save.setToolTip(
            "Записать стратегию «Новых» и срок временного ЧС в panel_settings.json.")
        b_save.clicked.connect(self.save)
        b_run = QPushButton("Забрать «Новые» (2 мин)")
        b_run.setToolTip(
            "Разовый забор «Новых» на 120 секунд. Требует, чтобы цикл был остановлен.")
        b_run.clicked.connect(lambda: self.run_cmd("scan_new"))
        b_stop = QPushButton("Стоп")
        b_stop.setObjectName("DangerButton")
        b_stop.setToolTip("Мягко остановить бота: текущее удержание не прерывается.")
        b_stop.clicked.connect(lambda: self.run_cmd("stop"))
        btns.addWidget(b_save)
        btns.addWidget(b_run)
        btns.addWidget(b_stop)
        btns.addStretch(1)
        card.add_layout(btns)
        self.msg = QLabel("Готово.")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        card.add(self.msg)
        self.add(card)

        # ---------------- временный ЧС ----------------
        bl_card = Card(
            "Временный чёрный список заданий",
            "ID, которые не берутся, не пытаемся брать заново до истечения срока. "
            "Бот добавляет сюда задания со статусами unknown / mismatch / unavailable "
            "автоматически; вручную можно добавить любой ID.")
        bl_row = QHBoxLayout()
        bl_row.setSpacing(8)
        bl_cap = QLabel("Срок ЧС")
        bl_cap.setObjectName("Muted")
        bl_row.addWidget(bl_cap)
        self.sp_bl_ttl = QSpinBox()
        self.sp_bl_ttl.setRange(5, 10080)
        self.sp_bl_ttl.setSuffix(" мин")
        self.sp_bl_ttl.setValue(
            int(self.settings["new"].get("blacklist_ttl_minutes", 360)))
        self.sp_bl_ttl.setToolTip(
            "Сколько минут скрывать ID из «Новых» (360 мин = 6 часов). "
            "Для статуса «недоступен» берётся полный TTL, для таймаутов "
            "и несовпадения номера — не больше 60 мин.")
        bl_row.addWidget(self.sp_bl_ttl)
        b_bl_add = QPushButton("Добавить ID")
        b_bl_add.setToolTip("Добавить номер задания в ЧС вручную.")
        b_bl_add.clicked.connect(self.add_blacklist)
        bl_row.addWidget(b_bl_add)
        b_bl_remove = QPushButton("Убрать выбранный")
        b_bl_remove.setToolTip("Вернуть выбранный ID в работу немедленно.")
        b_bl_remove.clicked.connect(self.remove_blacklist)
        bl_row.addWidget(b_bl_remove)
        b_bl_clear = QPushButton("Очистить ЧС")
        b_bl_clear.setObjectName("DangerButton")
        b_bl_clear.setToolTip("Убрать все записи из временного чёрного списка.")
        b_bl_clear.clicked.connect(self.clear_blacklist)
        bl_row.addWidget(b_bl_clear)
        bl_row.addStretch(1)
        bl_card.add_layout(bl_row)

        self.bl_list = QListWidget()
        self.bl_list.setMaximumHeight(140)
        self.bl_list.setToolTip(
            "Двойной щелчок по строке убирает ID из чёрного списка.")
        self.bl_list.itemDoubleClicked.connect(lambda _item: self.remove_blacklist())
        bl_card.add(self.bl_list)
        self.l_bl = QLabel("В ЧС: —")
        self.l_bl.setObjectName("Muted")
        bl_card.add(self.l_bl)
        self.add(bl_card)

        # ---------------- последний скан ----------------
        scan = Card("Последний скан", "Разбор хвоста resume.log; обновляется каждые 3 секунды.")
        self.l_scan = QLabel("—")
        self.l_candidate = QLabel("—")
        self.l_skips = QLabel("—")
        self.l_updated = QLabel("—")
        for w in (self.l_scan, self.l_candidate, self.l_skips, self.l_updated):
            w.setWordWrap(True)
            scan.add(w)
        self.add(scan)

        self.log = LogView(
            [
                ("resume.log", os.path.join(LOGS_DIR, "resume.log")),
                ("hunter2.log", os.path.join(LOGS_DIR, "hunter2.log")),
            ]
        )
        self.log.setMinimumHeight(300)
        self.add(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_scan)
        self.timer.start(3000)
        self.bl_timer = QTimer(self)
        self.bl_timer.timeout.connect(self.refresh_blacklist)
        self.bl_timer.start(5000)
        QTimer.singleShot(300, self.refresh_scan)
        QTimer.singleShot(300, self.refresh_blacklist)

    # ------------------------------------------------------------------
    def add_blacklist(self):
        order, ok = QInputDialog.getText(
            self, "Временный ЧС",
            "Номер задания (ID). Можно несколько через пробел или запятую:")
        order = (order or "").strip()
        if not ok or not order:
            return
        ids = [part for part in re.split(r"[\s,;]+", order) if part]
        bad = [part for part in ids if not part.isdigit()]
        if bad:
            self.msg.setText(
                "ID должен состоять только из цифр: " + ", ".join(bad))
            return
        added = 0
        last_expires = ""
        try:
            for order_id in ids:
                entry = blacklist.add(
                    order_id, minutes=self.sp_bl_ttl.value(), reason="вручную")
                added += 1
                last_expires = str(entry.get("expires", ""))[11:16]
            if added == 1:
                self.msg.setText(f"ID {ids[0]} в ЧС до {last_expires}.")
            else:
                self.msg.setText(
                    f"В ЧС добавлено ID: {added}, до {last_expires}.")
        except Exception as error:  # noqa: BLE001 - текст в интерфейсе
            self.msg.setText(f"Ошибка ЧС: {error}")
        self.refresh_blacklist()

    def remove_blacklist(self):
        item = self.bl_list.currentItem()
        if item is None:
            self.msg.setText("Выберите строку ЧС, которую нужно убрать.")
            return
        order = item.data(Qt.ItemDataRole.UserRole)
        blacklist.remove(str(order))
        self.msg.setText(f"ID {order} убран из ЧС.")
        self.refresh_blacklist()

    def clear_blacklist(self):
        count = blacklist.clear()
        self.msg.setText(f"ЧС очищен, убрано записей: {count}.")
        self.refresh_blacklist()

    def refresh_blacklist(self):
        entries = blacklist.active()
        self.bl_list.clear()
        for order, entry in sorted(entries.items()):
            left = blacklist.remaining_minutes(order) or 0.0
            reason = entry.get("reason") or "—"
            item = QListWidgetItem(f"{order} · ещё {left:.0f} мин · {reason}")
            item.setData(Qt.ItemDataRole.UserRole, order)
            item.setToolTip(
                f"ID {order}\nдобавлен: {entry.get('added', '—')}\n"
                f"истекает: {entry.get('expires', '—')}\nпричина: {reason}")
            self.bl_list.addItem(item)
        self.l_bl.setText(
            f"В ЧС: {len(entries)}" if entries else
            "В ЧС пусто — все задания доступны.")

    # ------------------------------------------------------------------
    def save(self):
        self.settings["new"]["max_pages"] = self.sp_pages.value()
        self.settings["new"]["empty_stop"] = self.sp_empty.value()
        self.settings["new"]["scroll_to_bottom"] = self.cb_scroll.isChecked()
        self.settings["new"]["sort_by_price"] = self.cb_sort.isChecked()
        self.settings["new"]["recheck_count"] = self.sp_recheck.value()
        self.settings["new"]["recheck_delay"] = self.sp_delay.value()
        self.settings["new"]["idle_stop_minutes"] = self.sp_idle.value()
        self.settings["new"]["blacklist_ttl_minutes"] = self.sp_bl_ttl.value()
        self.settings["new"]["max_offers_per_run"] = self.sp_offers.value()
        save_settings(self.settings)
        self.msg.setText("Настройки «Новых» сохранены.")

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
        self.refresh_scan()

    def set_buttons(self, enabled: bool):
        for b in self.findChildren(QPushButton):
            b.setEnabled(enabled)

    def refresh_scan(self):
        lines = self._tail("resume.log", 400)
        scan = ""
        candidate = ""
        skips: list[str] = []
        for line in reversed(lines):
            low = line.lower()
            if not scan and "scan:" in low:
                scan = line.strip()
            if not candidate and ("ru candidate" in low or "handle_dialog" in low):
                candidate = line.strip()
            if "skip:" in low and len(skips) < 4:
                skips.append(line.strip())
            if scan and candidate and len(skips) >= 4:
                break
        self.l_scan.setText("Скан: " + (scan or "пока нет данных"))
        self.l_candidate.setText("Кандидат: " + (candidate or "RU-кандидатов не было"))
        self.l_skips.setText("Отсев:\n" + ("\n".join(skips) if skips else "—"))
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
