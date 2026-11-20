# -*- coding: utf-8 -*-
"""Страница «Стратегии»: таблица шагов бота с временем до секунды."""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
)

from core.config import load_settings, save_settings
from panel.pages.base import ScrollPage
from panel.widgets import Card

ACTIONS = [
    ("MODAL_PUMP", "Обработать модалки"),
    ("NAV_TAB", "Перейти во вкладку"),
    ("SCAN_NEW", "Собрать RU в «Новых»"),
    ("TAKE_NEW", "Взять задания"),
    ("EXECUTE_ACTIVE", "Выполнить «Активные»"),
    ("WAIT", "Ожидание"),
    ("STOP", "Стоп"),
]

TABS = [("", "—"), ("NEW", "НОВЫЕ"), ("ACTIVE", "АКТИВНЫЕ")]

TIME_FORMAT = "HH:mm:ss"


def _time_edit(value: str) -> QTimeEdit:
    widget = QTimeEdit()
    widget.setDisplayFormat(TIME_FORMAT)
    StrategyPage._set_time(widget, value)
    return widget


class StrategyPage(ScrollPage):
    def __init__(self, parent=None):
        super().__init__(
            "Стратегии",
            "Конструктор шагов: время до секунды, вкладка, условие останова и приоритет модалок.",
            parent,
        )
        self.settings = load_settings()

        top = Card("План", "Выберите план. «auto» — план подбирается по времени суток.")
        h = QHBoxLayout()
        self.cb_plan = QComboBox()
        self.cb_plan.addItem("auto — по времени", "auto")
        for name in (self._strategy().get("plans") or {}):
            self.cb_plan.addItem(name, name)
        active = self._strategy().get("active", "auto")
        idx = self.cb_plan.findData(active)
        self.cb_plan.setCurrentIndex(max(0, idx))
        self.cb_plan.currentIndexChanged.connect(self.reload)
        h.addWidget(self.cb_plan)

        self.ed_name = QLineEdit(self._current_plan().get("name", ""))
        self.ed_name.setPlaceholderText("Название плана")
        h.addWidget(self.ed_name)
        self.ed_from = _time_edit(self._current_plan().get("from", "00:00:00"))
        self.ed_to = _time_edit(self._current_plan().get("to", "23:59:59"))
        h.addWidget(self.ed_from)
        h.addWidget(self.ed_to)
        self.cb_enabled = QCheckBox("Включён")
        self.cb_enabled.setChecked(bool(self._current_plan().get("enabled", True)))
        h.addWidget(self.cb_enabled)
        top.add_layout(h)
        from PyQt6.QtWidgets import QSpinBox
        self.sp_hold = QSpinBox()
        self.sp_hold.setRange(0, 600)
        self.sp_hold.setSuffix(" с")
        self.sp_hold.setValue(int(self._strategy().get("hold_after_take_seconds", 80)))
        self.sp_hold.setToolTip(
            "Сколько секунд держать приложение после взятия задания, "
            "прежде чем вернуться в «Новые» (по условиям 30 с – 2 мин).")
        hold_row = QHBoxLayout()
        hold_row.addWidget(QLabel("Удержание после взятия"))
        hold_row.addWidget(self.sp_hold)
        hold_row.addStretch(1)
        top.add_layout(hold_row)
        self.add(top)

        card = Card("Шаги стратегии", "Порядок сверху вниз. Модалки обрабатываются перед каждым шагом.")
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "№", "Вкл", "Действие", "Вкладка", "С", "До", "Параметры (JSON)", "Подсказка",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        card.add(self.table)

        buttons = QHBoxLayout()
        for label, slot in (
            ("＋ Добавить", self.add_row),
            ("− Удалить", self.remove_row),
            ("↑", lambda: self.move_row(-1)),
            ("↓", lambda: self.move_row(1)),
            ("Дублировать", self.duplicate_row),
            ("Предпросмотр", self.preview),
            ("Сохранить", self.save),
            ("Перечитать", self.reload),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        card.add_layout(buttons)
        self.preview_box = QPlainTextEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setMaximumHeight(160)
        self.preview_box.setPlaceholderText("Здесь появится текстовый план бота")
        card.add(self.preview_box)
        self.add(card)

        self.msg = card.add_msg if hasattr(card, "add_msg") else None
        self.reload()
        self.preview()

    # ------------------------------------------------------------------
    def _strategy(self):
        return self.settings.get("strategy") or {}

    def _plan_name(self):
        return self.cb_plan.currentData() or "auto"

    def _current_plan(self):
        name = self._plan_name()
        plans = self._strategy().get("plans") or {}
        if name in plans:
            return plans[name]
        first = next(iter(plans.values()), {})
        return first if isinstance(first, dict) else {}

    def reload(self):
        plan = self._current_plan()
        self.ed_name.setText(str(plan.get("name", "")))
        self._set_time(self.ed_from, plan.get("from", "00:00:00"))
        self._set_time(self.ed_to, plan.get("to", "23:59:59"))
        self.cb_enabled.setChecked(bool(plan.get("enabled", True)))
        self.table.setRowCount(0)
        for step in plan.get("steps") or []:
            self._append_step(step)

    @staticmethod
    def _set_time(widget, value):
        from PyQt6.QtCore import QTime
        try:
            hh, mm, ss = (int(x) for x in str(value or "00:00:00").split(":"))
            widget.setTime(QTime(hh, mm, ss))
        except (ValueError, TypeError):
            pass

    def _append_step(self, step):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        box = QCheckBox()
        box.setChecked(bool(step.get("enabled", True)))
        self.table.setCellWidget(row, 1, box)

        action = QComboBox()
        for key, label in ACTIONS:
            action.addItem(label, key)
        idx = action.findData(str(step.get("kind", "MODAL_PUMP")))
        action.setCurrentIndex(max(0, idx))
        self.table.setCellWidget(row, 2, action)

        tab = QComboBox()
        for key, label in TABS:
            tab.addItem(label, key)
        idx = tab.findData(str(step.get("tab", "")))
        tab.setCurrentIndex(max(0, idx))
        self.table.setCellWidget(row, 3, tab)

        self.table.setCellWidget(row, 4, _time_edit(step.get("from", "00:00:00")))
        self.table.setCellWidget(row, 5, _time_edit(step.get("to", "23:59:59")))
        self.table.setItem(row, 6, QTableWidgetItem(
            json.dumps(step.get("params") or {}, ensure_ascii=False)))
        self.table.setItem(row, 7, QTableWidgetItem(str(step.get("note", ""))))

    def _row_step(self, row):
        def widget(col):
            return self.table.cellWidget(row, col)
        enabled = widget(1).isChecked()
        action = widget(2)
        tab = widget(3)
        params_text = self.table.item(row, 6).text().strip() or "{}"
        try:
            params = json.loads(params_text)
            if not isinstance(params, dict):
                params = {}
        except ValueError:
            params = {}
        return {
            "id": f"s{row + 1}",
            "enabled": enabled,
            "kind": action.currentData(),
            "tab": tab.currentData(),
            "from": widget(4).time().toString(TIME_FORMAT),
            "to": widget(5).time().toString(TIME_FORMAT),
            "params": params,
            "note": self.table.item(row, 7).text(),
        }

    def steps(self):
        return [self._row_step(row) for row in range(self.table.rowCount())]

    # ------------------------------------------------------------------
    def add_row(self):
        self._append_step({"kind": "MODAL_PUMP", "enabled": True})
        self.table.selectRow(self.table.rowCount() - 1)

    def remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
        self.preview()

    def move_row(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        step = self._row_step(row)
        self.table.removeRow(row)
        self.table.insertRow(target)
        self._restore_row(target, step)
        self.table.selectRow(target)
        self.preview()

    def duplicate_row(self):
        row = self.table.currentRow()
        if row < 0:
            return
        step = self._row_step(row)
        self.table.insertRow(row + 1)
        self._restore_row(row + 1, step)
        self.preview()

    def _restore_row(self, row, step):
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        box = QCheckBox()
        box.setChecked(bool(step.get("enabled", True)))
        self.table.setCellWidget(row, 1, box)
        action = QComboBox()
        for key, label in ACTIONS:
            action.addItem(label, key)
        action.setCurrentIndex(max(0, action.findData(str(step.get("kind")))))
        self.table.setCellWidget(row, 2, action)
        tab = QComboBox()
        for key, label in TABS:
            tab.addItem(label, key)
        tab.setCurrentIndex(max(0, tab.findData(str(step.get("tab", "")))))
        self.table.setCellWidget(row, 3, tab)
        self.table.setCellWidget(row, 4, _time_edit(step.get("from")))
        self.table.setCellWidget(row, 5, _time_edit(step.get("to")))
        self.table.setItem(row, 6, QTableWidgetItem(json.dumps(step.get("params") or {}, ensure_ascii=False)))
        self.table.setItem(row, 7, QTableWidgetItem(str(step.get("note", ""))))

    def preview(self):
        lines = [f"ПЛАН: {self.ed_name.text() or self._plan_name()}",
                 f"Окно: {self.ed_from.time().toString(TIME_FORMAT)} — "
                 f"{self.ed_to.time().toString(TIME_FORMAT)}"]
        for i, step in enumerate(self.steps(), 1):
            mark = "✓" if step["enabled"] else "✗"
            lines.append(
                f"{mark} {i:2d}. {step['kind']:<16} {step['tab']:<7} "
                f"{step['from']}–{step['to']} {step['note']}"
            )
            if step["params"]:
                lines.append(f"      params: {json.dumps(step['params'], ensure_ascii=False)}")
        self.preview_box.setPlainText("\n".join(lines))

    def save(self):
        self.settings = load_settings()
        name = self._plan_name()
        strategy = self.settings.setdefault("strategy", {})
        plans = strategy.setdefault("plans", {})
        if name == "auto" or name not in plans:
            name = "custom"
            plans[name] = {}
        plan = plans[name]
        plan["name"] = self.ed_name.text().strip() or name
        plan["enabled"] = self.cb_enabled.isChecked()
        plan["from"] = self.ed_from.time().toString(TIME_FORMAT)
        plan["to"] = self.ed_to.time().toString(TIME_FORMAT)
        plan["steps"] = self.steps()
        strategy["active"] = name
        strategy["hold_after_take_seconds"] = self.sp_hold.value()
        save_settings(self.settings)
        self.preview()
        self.window().statusBar().showMessage(f"Стратегия «{name}» сохранена", 5000) if hasattr(
            self.window(), "statusBar") else None
