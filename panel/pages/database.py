# -*- coding: utf-8 -*-
"""Редактор базы приложений v2: несколько иконок, ID, алиасы и описания."""
from __future__ import annotations

import os
from datetime import datetime

from PIL import Image
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import device
from core.config import BASE, ICON_CACHE, load_settings
from core.db import (
    add_icon,
    empty_app,
    export_db,
    find_app,
    icon_path,
    import_db,
    load_db,
    remove_icon,
    save_db,
    summary,
)
from core.matcher import IconIndex, icon_hash, load_icon, match_text, save_icon
from core.swift_icons import import_icons
from panel.pages.base import BasePage
from panel.swift_dialog import SwiftAppDialog
from panel.widgets import Card, CropDialog, IconPreview


class DatabasePage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "База приложений",
            "Несколько иконок и ID на одно приложение, алиасы, описания, магазины и очередь неопознанных.",
            parent,
        )
        self.db = load_db()
        self.current_id: str | None = None
        self._loading = False

        split = QSplitter(Qt.Orientation.Horizontal)
        self.root.addWidget(split, 1)

        # ---------------- слева: список ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        lv.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("поиск: пакет, название, ID…")
        self.search.setToolTip("Поиск по пакету, названию, алиасам и ID заданий.")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_list)
        lv.addWidget(self.search)

        self.app_list = QListWidget()
        self.app_list.setToolTip(
            "Список приложений базы. Выберите запись, чтобы редактировать её справа.")
        self.app_list.currentItemChanged.connect(self.on_app_changed)
        lv.addWidget(self.app_list, 1)

        b_swift_add = QPushButton("Добавить из Swift Backup")
        b_swift_add.setToolTip(
            "Выбрать приложение из резервных копий Swift Backup. Пакет, название, "
            "component, известные ID и иконка заполнятся автоматически.")
        b_swift_add.clicked.connect(self.add_from_swift)
        lv.addWidget(b_swift_add)

        row = QHBoxLayout()
        b_manual = QPushButton("Вручную")
        b_manual.setToolTip("Создать пустую запись и ввести имя пакета вручную.")
        b_manual.clicked.connect(self.new_app)
        b_del = QPushButton("Удалить")
        b_del.setObjectName("DangerButton")
        b_del.setToolTip("Удалить выбранное приложение из базы.")
        b_del.clicked.connect(self.delete_app)
        row.addWidget(b_manual)
        row.addWidget(b_del)
        lv.addLayout(row)

        self.l_summary = QLabel("—")
        self.l_summary.setObjectName("Muted")
        self.l_summary.setWordWrap(True)
        lv.addWidget(self.l_summary)
        self.l_unidentified = QLabel("—")
        self.l_unidentified.setObjectName("Muted")
        self.l_unidentified.setWordWrap(True)
        lv.addWidget(self.l_unidentified)
        b_unid = QPushButton("Очередь неопознанных")
        b_unid.setToolTip(
            "Задания, которые бот не смог сопоставить с приложением в базе.")
        b_unid.clicked.connect(self.show_unidentified)
        lv.addWidget(b_unid)

        split.addWidget(left)

        # ---------------- справа: форма ----------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form_host = QWidget()
        fv = QVBoxLayout(form_host)
        fv.setContentsMargins(8, 0, 0, 0)
        fv.setSpacing(12)

        info = Card("Приложение")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.ed_package = QLineEdit()
        self.ed_component = QLineEdit()
        self.ed_title = QLineEdit()
        self.ed_aliases = QLineEdit()
        self.ed_aliases.setPlaceholderText("через запятую: займиго, zaymigo")
        self.cb_enabled = QCheckBox("Включено")
        self.cb_verified = QCheckBox("Проверено")
        self.ed_package.setToolTip(
            "Имя пакета Android, например com.zaymigo.zaymigo_loans. "
            "По нему проверяется установка приложения.")
        self.ed_component.setToolTip(
            "Необязательный component (activity) для запуска приложения.")
        self.ed_title.setToolTip("Человеческое название приложения для списка и поиска.")
        self.ed_aliases.setToolTip(
            "Другие названия через запятую — помогают текстовому матчу.")
        self.cb_enabled.setToolTip("Выключенные приложения не участвуют в матчинге.")
        self.cb_verified.setToolTip(
            "Отметка «проверено вручную»: пакет и иконки сверены.")
        rows = [
            ("Пакет", self.ed_package),
            ("Component", self.ed_component),
            ("Название", self.ed_title),
            ("Алиасы", self.ed_aliases),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            grid.addWidget(cap, i, 0)
            grid.addWidget(widget, i, 1)
        flags = QHBoxLayout()
        flags.addWidget(self.cb_enabled)
        flags.addWidget(self.cb_verified)
        flags.addStretch(1)
        grid.addLayout(flags, 4, 1)
        grid.setColumnStretch(1, 1)
        info.add_layout(grid)
        self.l_stats = QLabel("Статистика: —")
        self.l_stats.setObjectName("Muted")
        self.l_stats.setWordWrap(True)
        info.add(self.l_stats)
        fv.addWidget(info)

        icons = Card("Иконки", "Можно хранить сколько угодно вариантов. Первая помеченная ★ — основная.")
        self.icon_list = QListWidget()
        self.icon_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.icon_list.setIconSize(QSize(52, 52))
        self.icon_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.icon_list.setMovement(QListWidget.Movement.Static)
        self.icon_list.setSpacing(6)
        self.icon_list.setMinimumHeight(120)
        self.icon_list.setMaximumHeight(170)
        self.icon_list.setToolTip(
            "Иконки выбранного приложения. Клик — показать и проверить; "
            "звезда ★ отмечает основную.")
        self.icon_list.currentItemChanged.connect(self.on_icon_selected)
        icons.add(self.icon_list)

        icon_row = QHBoxLayout()
        self.icon_preview = IconPreview(84)
        icon_row.addWidget(self.icon_preview)
        self.l_icon_info = QLabel("Иконка не выбрана.")
        self.l_icon_info.setObjectName("Muted")
        self.l_icon_info.setWordWrap(True)
        self.l_icon_info.setMinimumWidth(1)
        icon_row.addWidget(self.l_icon_info, 1)
        icons.add_layout(icon_row)

        b_file = QPushButton("Из файла")
        b_file.setToolTip("Добавить иконку из PNG/JPG на компьютере.")
        b_file.clicked.connect(self.add_icon_file)
        b_cache = QPushButton("Из icon_cache")
        b_cache.setToolTip("Добавить иконку из локального кэша icon_cache/.")
        b_cache.clicked.connect(self.import_cache)
        b_shot = QPushButton("Скриншот экрана")
        b_shot.setToolTip(
            "Снять текущий экран телефона и выделить иконку мышью. "
            "Удобно, когда приложение уже открыто на телефоне."
        )
        b_shot.clicked.connect(self.screenshot_icon)
        b_swift = QPushButton("Иконки Swift Backup")
        b_swift.setToolTip(
            "Скачать /sdcard/SwiftBackup/icon_cache, скопировать новые PNG "
            "в icon_cache/ и привязать их к приложениям по имени пакета."
        )
        b_swift.clicked.connect(self.import_swift_icons)
        b_primary = QPushButton("Сделать основной")
        b_primary.setToolTip("Пометить выбранную иконку как основную (★).")
        b_primary.clicked.connect(self.make_primary)
        b_check = QPushButton("Проверить матч")
        b_check.setToolTip(
            "Проверить ТОЛЬКО выбранную иконку: с чем она совпадает в базе."
        )
        b_check.clicked.connect(self.check_icon)
        b_check_all = QPushButton("Проверить ВСЁ — детально")
        b_check_all.setToolTip(
            "Полная проверка базы: пакеты установлены, файлы иконок и их hash, "
            "ID заданий, текстовый матч, дубликаты. Отчёт со списком замечаний."
        )
        b_check_all.clicked.connect(self.check_all)
        b_remove = QPushButton("Удалить иконку")
        b_remove.setObjectName("DangerButton")
        b_remove.setToolTip("Удалить выбранную иконку из базы (файл на диске остаётся).")
        b_remove.clicked.connect(self.remove_selected_icon)

        ib = QGridLayout()
        ib.setHorizontalSpacing(8)
        ib.setVerticalSpacing(8)
        icon_buttons = (b_file, b_cache, b_shot, b_swift,
                        b_primary, b_check, b_check_all, b_remove)
        for i, b in enumerate(icon_buttons):
            ib.addWidget(b, i // 2, i % 2)
        icons.add_layout(ib)
        fv.addWidget(icons)

        features = Card("Признаки задания")
        fg = QGridLayout()
        fg.setHorizontalSpacing(12)
        fg.setVerticalSpacing(8)
        self.ed_descriptions = QPlainTextEdit()
        self.ed_descriptions.setPlaceholderText("по одному на строку; regex: ^займ")
        self.ed_descriptions.setMaximumHeight(80)
        self.ed_descriptions.setToolTip(
            "Описания/ключевые слова задания, по одному на строку. "
            "Поддерживаются regex, например ^займ.")
        self.ed_orders = QPlainTextEdit()
        self.ed_orders.setPlaceholderText("ID заданий через запятую или по одному на строку")
        self.ed_orders.setMaximumHeight(80)
        self.ed_orders.setToolTip(
            "Номера заданий AdvertApp, привязанные к этому приложению.")
        self.ed_exclude = QLineEdit()
        self.ed_exclude.setPlaceholderText("через запятую: samsung, vpn")
        self.ed_exclude.setToolTip(
            "Стоп-слова: если они есть в задании, приложение не выбирается.")
        self.ed_notes = QPlainTextEdit()
        self.ed_notes.setMaximumHeight(60)
        self.ed_notes.setToolTip("Свободные заметки по приложению.")
        fg.addWidget(self._cap("Описания"), 0, 0)
        fg.addWidget(self.ed_descriptions, 0, 1)
        fg.addWidget(self._cap("ID заданий"), 1, 0)
        fg.addWidget(self.ed_orders, 1, 1)
        fg.addWidget(self._cap("Стоп-слова"), 2, 0)
        fg.addWidget(self.ed_exclude, 2, 1)
        fg.addWidget(self._cap("Заметки"), 3, 0)
        fg.addWidget(self.ed_notes, 3, 1)
        fg.setColumnStretch(1, 1)
        features.add_layout(fg)

        markets = QHBoxLayout()
        markets.setSpacing(16)
        self.market_boxes = {}
        for key, label in (
            ("google", "Google Play"),
            ("rustore", "RuStore"),
            ("huawei", "Huawei"),
            ("xiaomi", "Xiaomi"),
        ):
            cb = QCheckBox(label)
            cb.setToolTip("Разрешён ли этот магазин для данного приложения.")
            self.market_boxes[key] = cb
            markets.addWidget(cb)
        markets.addStretch(1)
        features.add_layout(markets)
        fv.addWidget(features)

        bottom = QGridLayout()
        bottom.setHorizontalSpacing(8)
        bottom.setVerticalSpacing(8)
        b_apply = QPushButton("Применить")
        b_apply.setToolTip("Перенести изменения формы в текущую запись (без записи на диск).")
        b_apply.clicked.connect(lambda: self.apply_current(True))
        b_save = QPushButton("Сохранить базу")
        b_save.setObjectName("AccentButton")
        b_save.setToolTip("Записать panel_apps.json (с автобэкапом в backups/).")
        b_save.clicked.connect(self.save_all)
        b_revert = QPushButton("Отменить изменения")
        b_revert.setToolTip("Перечитать базу с диска и отбросить правки формы.")
        b_revert.clicked.connect(self.revert)
        b_export = QPushButton("Экспорт JSON")
        b_export.setToolTip("Сохранить копию базы в выбранный файл.")
        b_export.clicked.connect(self.export_json)
        b_import = QPushButton("Импорт JSON")
        b_import.setToolTip("Заменить базу файлом JSON (с автобэкапом текущей).")
        b_import.clicked.connect(self.import_json)
        for i, b in enumerate((b_apply, b_save, b_revert, b_export, b_import)):
            bottom.addWidget(b, i // 2, i % 2)
        fv.addLayout(bottom)
        self.msg = QLabel("Готово.")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        fv.addWidget(self.msg)
        fv.addStretch(1)

        scroll.setWidget(form_host)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 800])

        self.refresh_list()
        if self.db["apps"]:
            self.app_list.setCurrentRow(0)

    @staticmethod
    def _cap(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    # ------------------------------------------------------------------
    def refresh_list(self, *_):
        needle = self.search.text().strip().lower()
        current = self.current_id
        self.app_list.blockSignals(True)
        self.app_list.clear()
        apps = sorted(
            self.db.get("apps", []),
            key=lambda a: ((a.get("title") or a.get("package") or "").lower()),
        )
        for app in apps:
            hay = " ".join(
                [
                    app.get("package", ""),
                    app.get("title", ""),
                    " ".join(app.get("aliases", [])),
                    " ".join(str(x) for x in app.get("order_ids", [])),
                ]
            ).lower()
            if needle and needle not in hay:
                continue
            mark = "" if app.get("enabled", True) else "  (выкл)"
            verified = " ✓" if app.get("verified") else ""
            item = QListWidgetItem(
                f"{app.get('title') or app.get('package')}{verified}{mark}\n"
                f"{app.get('package') or '—'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, app.get("id"))
            self.app_list.addItem(item)
            if app.get("id") == current:
                self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        self.update_summary()
        if self.app_list.count() and self.app_list.currentRow() < 0:
            self.app_list.setCurrentRow(0)

    def update_summary(self):
        s = summary(self.db)
        self.l_summary.setText(
            f"Приложений: {s.get('apps', 0)} · включено {s.get('enabled', 0)} · "
            f"проверено {s.get('verified', 0)}\n"
            f"Иконок: {s.get('icons', 0)} · ID: {s.get('order_ids', 0)} · "
            f"общих иконок: {s.get('shared', 0)}"
        )
        unid = self.db.get("unidentified", [])
        self.l_unidentified.setText(f"Неопознанных: {len(unid)}")

    def on_app_changed(self, current, previous):
        if self._loading:
            return
        if previous is not None and self.current_id:
            self.apply_current(False)
        if current is None:
            return
        app_id = current.data(Qt.ItemDataRole.UserRole)
        self.select_app(app_id)

    def select_app(self, app_id: str):
        app = find_app(self.db, app_id=app_id)
        if not app:
            return
        self.current_id = app_id
        self._loading = True
        self.ed_package.setText(app.get("package", ""))
        self.ed_component.setText(app.get("component", ""))
        self.ed_title.setText(app.get("title", ""))
        self.ed_aliases.setText(", ".join(app.get("aliases", [])))
        self.ed_descriptions.setPlainText(
            "\n".join(
                ("re:" if d.get("regex") else "") + str(d.get("text", ""))
                for d in app.get("descriptions", [])
            )
        )
        self.ed_orders.setPlainText("\n".join(str(x) for x in app.get("order_ids", [])))
        self.ed_exclude.setText(", ".join(app.get("exclude_keywords", [])))
        self.ed_notes.setPlainText(app.get("notes", ""))
        self.cb_enabled.setChecked(bool(app.get("enabled", True)))
        self.cb_verified.setChecked(bool(app.get("verified", False)))
        selected = set(app.get("markets", []))
        for key, cb in self.market_boxes.items():
            cb.setChecked(key in selected)
        stats = app.get("stats", {})
        self.l_stats.setText(
            "Статистика: "
            f"видели {stats.get('seen', 0)}, совпало {stats.get('matched', 0)}, "
            f"установлено {stats.get('installed', 0)}, выполнено {stats.get('completed', 0)}, "
            f"последний раз {stats.get('last_seen') or '—'}"
        )
        self.refresh_icons()
        self._loading = False

    def apply_current(self, show: bool = False):
        if not self.current_id:
            return
        app = find_app(self.db, app_id=self.current_id)
        if not app:
            return
        app["package"] = self.ed_package.text().strip()
        app["component"] = self.ed_component.text().strip()
        app["title"] = self.ed_title.text().strip()
        app["aliases"] = [
            x.strip() for x in self.ed_aliases.text().split(",") if x.strip()
        ]
        descriptions = []
        for line in self.ed_descriptions.toPlainText().splitlines():
            text = line.strip()
            if not text:
                continue
            regex = text.startswith("re:")
            descriptions.append({"text": text[3:].strip() if regex else text, "regex": regex})
        app["descriptions"] = descriptions
        ids = []
        for part in self.ed_orders.toPlainText().replace(",", "\n").splitlines():
            part = part.strip()
            if part and part not in ids:
                ids.append(part)
        app["order_ids"] = ids
        app["exclude_keywords"] = [
            x.strip().lower() for x in self.ed_exclude.text().split(",") if x.strip()
        ]
        app["notes"] = self.ed_notes.toPlainText().strip()
        app["enabled"] = self.cb_enabled.isChecked()
        app["verified"] = self.cb_verified.isChecked()
        app["markets"] = [k for k, cb in self.market_boxes.items() if cb.isChecked()]
        if show:
            self.msg.setText("Изменения применены в памяти. Нажмите «Сохранить базу».")

    def save_all(self):
        self.apply_current(False)
        path = save_db(self.db)
        self.msg.setText(f"База сохранена: {path}")
        self.refresh_list()
        self.update_summary()

    def revert(self):
        self.db = load_db()
        self.current_id = None
        self.refresh_list()
        if self.db["apps"]:
            self.app_list.setCurrentRow(0)
        self.msg.setText("Изменения отменены, база перечитана с диска.")

    # ------------------------------------------------------------------
    def add_from_swift(self):
        # Не теряем несохранённые правки формы: переносим их в self.db,
        # диалог сохранит базу вместе с новой записью.
        self.apply_current(False)
        dialog = SwiftAppDialog(self.db, self)
        dialog.app_added.connect(self.on_swift_added)
        dialog.exec()

    def on_swift_added(self, app_id: str):
        self.db = load_db()
        self.current_id = app_id
        self.refresh_list()
        self.select_app(app_id)
        self.msg.setText(
            "Приложение добавлено из Swift Backup. Проверьте ID заданий "
            "и нажмите «Сохранить базу», если что-то поправили.")

    def new_app(self):
        package, ok = QInputDialog.getText(self, "Новое приложение", "Пакет:")
        package = package.strip()
        if not ok or not package:
            return
        if find_app(self.db, package=package):
            QMessageBox.warning(self, "База", "Такое приложение уже есть.")
            return
        app = empty_app(package)
        self.db["apps"].append(app)
        save_db(self.db)
        self.current_id = app["id"]
        self.refresh_list()
        self.select_app(app["id"])

    def delete_app(self):
        if not self.current_id:
            return
        app = find_app(self.db, app_id=self.current_id)
        if not app:
            return
        answer = QMessageBox.question(
            self,
            "Удалить приложение",
            f"Удалить «{app.get('title') or app.get('package')}» из базы?\n"
            "Файлы иконок останутся на диске.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db["apps"] = [a for a in self.db["apps"] if a.get("id") != self.current_id]
        self.current_id = None
        save_db(self.db)
        self.refresh_list()
        if self.db["apps"]:
            self.app_list.setCurrentRow(0)
        self.msg.setText("Приложение удалено из базы.")

    # ------------------------------------------------------------------
    def refresh_icons(self):
        self.icon_list.clear()
        app = find_app(self.db, app_id=self.current_id) if self.current_id else None
        if not app:
            return
        for icon in app.get("icons", []):
            path = icon_path(icon)
            pix = QPixmap(path) if path and os.path.exists(path) else QPixmap()
            item = QListWidgetItem(
                QIcon(pix),
                icon.get("hash", "")[:8] + (" ★" if icon.get("primary") else ""),
            )
            item.setData(Qt.ItemDataRole.UserRole, icon.get("hash"))
            item.setToolTip(
                f"{icon.get('hash')}\n{icon.get('file') or 'файл не указан'}\n"
                f"источник: {icon.get('source')}"
            )
            self.icon_list.addItem(item)
        if self.icon_list.count():
            self.icon_list.setCurrentRow(0)
        else:
            self.icon_preview.set_path(None)
            self.l_icon_info.setText("Иконок нет. Добавьте файл или импортируйте из icon_cache.")

    def selected_icon(self) -> dict | None:
        app = find_app(self.db, app_id=self.current_id) if self.current_id else None
        if not app:
            return None
        item = self.icon_list.currentItem()
        if not item:
            return None
        digest = item.data(Qt.ItemDataRole.UserRole)
        for icon in app.get("icons", []):
            if icon.get("hash") == digest:
                return icon
        return None

    def on_icon_selected(self, current, previous):
        icon = self.selected_icon()
        if not icon:
            self.icon_preview.set_path(None)
            self.l_icon_info.setText("Иконка не выбрана.")
            return
        path = icon_path(icon)
        self.icon_preview.set_path(path)
        digest = str(icon.get("hash") or "")
        short_hash = digest if len(digest) <= 24 else digest[:24] + "…"
        info = (
            f"hash: {short_hash}\n"
            f"файл: {icon.get('file') or '—'}\n"
            f"источник: {icon.get('source')} · добавлена: {icon.get('added_at')}\n"
            f"основная: {'да' if icon.get('primary') else 'нет'}"
        )
        self.l_icon_info.setText(info)
        self.l_icon_info.setToolTip(info)

    def add_icon_file(self):
        if not self.current_id:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Иконка", ICON_CACHE, "Изображения (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._add_icon_path(path, "file")

    def import_cache(self):
        if not self.current_id:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Иконка из icon_cache", ICON_CACHE, "PNG (*.png)"
        )
        if path:
            self._add_icon_path(path, "icon_cache")

    def _add_icon_path(self, path: str, source: str):
        self.apply_current(False)
        app = find_app(self.db, app_id=self.current_id)
        if not app:
            return
        try:
            image = Image.open(path).convert("RGB")
            digest = icon_hash(image)
            rel = save_icon(image, app.get("package") or app.get("id") or "app", digest)
            added = add_icon(self.db, app["id"], digest, rel, source)
            save_db(self.db)
            self.refresh_icons()
            self.msg.setText(
                f"Иконка добавлена: {digest[:12]}…" if added else "Такая иконка уже есть."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Иконка", f"Не удалось добавить: {exc}")

    def remove_selected_icon(self):
        icon = self.selected_icon()
        if not icon:
            return
        answer = QMessageBox.question(
            self, "Удалить иконку", f"Удалить иконку {icon.get('hash', '')[:12]}…?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        remove_icon(self.db, self.current_id, icon.get("hash"))
        save_db(self.db)
        self.refresh_icons()
        self.msg.setText("Иконка удалена из базы.")

    def make_primary(self):
        icon = self.selected_icon()
        if not icon:
            return
        app = find_app(self.db, app_id=self.current_id)
        for item in app.get("icons", []):
            item["primary"] = item.get("hash") == icon.get("hash")
        save_db(self.db)
        self.refresh_icons()
        self.msg.setText("Основная иконка обновлена.")

    def check_icon(self):
        icon = self.selected_icon()
        if not icon:
            return
        path = icon_path(icon)
        if not os.path.exists(path):
            QMessageBox.warning(self, "Проверка", "Файл иконки не найден.")
            return
        try:
            image = load_icon(path)
            index = IconIndex(self.db, load_settings())
            result = index.match_icon(image)
            if result.get("ok") and result.get("entry"):
                entry = result["entry"]
                text = (
                    f"Совпало: {entry.title or entry.package}\n"
                    f"пакет: {entry.package}\n"
                    f"расстояние: {result.get('distance'):.3f}\n"
                    f"второе: {result.get('second_distance'):.3f}\n"
                    f"Hamming: {result.get('hamming')}"
                )
            else:
                text = "Уверенного совпадения нет.\n" + str(result.get("reason", ""))
            QMessageBox.information(self, "Проверка иконки", text)
        except Exception as exc:
            QMessageBox.critical(self, "Проверка", str(exc))

    # ------------------------------------------------------------------
    def screenshot_icon(self):
        """Снимок текущего экрана телефона → выделение иконки → добавление."""
        app = find_app(self.db, app_id=self.current_id) if self.current_id else None
        if not app:
            QMessageBox.information(
                self, "Скриншот", "Сначала выберите приложение в списке слева.")
            return
        os.makedirs(os.path.join(BASE, "frames"), exist_ok=True)
        path = os.path.join(
            BASE, "frames", f"panel_shot_{datetime.now():%Y%m%d_%H%M%S}.png")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = device.screencap(path)
        finally:
            QApplication.restoreOverrideCursor()
        if not os.path.exists(path):
            QMessageBox.warning(
                self, "Скриншот", f"Не удалось снять экран телефона.\n{result}")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(self, "Скриншот", "Снимок не читается как изображение.")
            return
        dialog = CropDialog(pixmap, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rect = dialog.crop_rect()
        if rect.isEmpty():
            return
        image = Image.open(path).convert("RGB").crop(
            (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()))
        digest = icon_hash(image)
        if not digest:
            QMessageBox.warning(
                self, "Скриншот",
                "Выделенная область слишком однотонная — иконка не распознается.")
            return
        package = app.get("package") or app.get("id") or "app"
        rel = save_icon(image, package, digest)
        if add_icon(self.db, app["id"], digest, rel, source="screenshot"):
            save_db(self.db)
            self.refresh_icons()
            self.refresh_list()
            QMessageBox.information(
                self, "Скриншот", "Иконка добавлена в выбранное приложение.")
        else:
            QMessageBox.information(
                self, "Скриншот", "Такая иконка уже есть у этого приложения.")

    def import_swift_icons(self):
        """Скачивает иконки Swift Backup и привязывает их по имени пакета."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            stats = import_icons(self.db)
        except Exception as error:  # noqa: BLE001 - показываем текст в диалоге
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Swift Backup", f"Ошибка импорта: {error}")
            return
        else:
            QApplication.restoreOverrideCursor()
        if stats.get("error"):
            QMessageBox.warning(self, "Swift Backup", str(stats["error"]))
            return
        if stats.get("attached"):
            save_db(self.db)
            self.refresh_icons()
            self.refresh_list()
        QMessageBox.information(
            self,
            "Swift Backup",
            f"Файлов в папке: {stats.get('pulled', 0)}\n"
            f"Скопировано новых: {stats.get('copied', 0)}\n"
            f"Привязано иконок к приложениям: {stats.get('attached', 0)}",
        )

    def check_all(self):
        """Полная проверка базы: пакеты, файлы, hash, ID, текст, дубликаты."""
        apps = self.db.get("apps") or []
        problems: list[str] = []
        installed = set(device.third_party_packages())
        order_owner: dict[str, str] = {}
        icon_total = 0
        missing_files = 0
        hash_mismatch = 0
        settings = load_settings()
        try:
            index = IconIndex(self.db, settings)
        except Exception:
            index = None

        for app in apps:
            name = app.get("title") or app.get("package") or app.get("id") or "—"
            package = (app.get("package") or "").strip()
            if not package:
                problems.append(f"{name}: не указан пакет")
            elif package not in installed:
                problems.append(f"{name}: пакет {package} не установлен на телефоне")
            if not app.get("enabled", True):
                problems.append(f"{name}: приложение выключено в базе")
            icons = app.get("icons") or []
            if not icons:
                problems.append(f"{name}: нет ни одной иконки")
            for icon in icons:
                icon_total += 1
                icon_file = icon_path(icon)
                if not icon_file or not os.path.exists(icon_file):
                    missing_files += 1
                    problems.append(
                        f"{name}: файл иконки не найден ({icon.get('file') or '—'})")
                    continue
                image = load_icon(icon_file)
                digest = icon_hash(image) if image is not None else None
                if digest != icon.get("hash"):
                    hash_mismatch += 1
                    problems.append(
                        f"{name}: hash в базе не совпадает с dHash файла "
                        f"(старый формат; матч идёт по вектору)")
                if index is not None:
                    match = index.match_icon(image)
                    entry = match.get("entry")
                    if not match.get("ok") or entry is None:
                        problems.append(
                            f"{name}: иконка не находится в индексе базы")
                    elif (not match.get("shared") and package
                          and entry.package and entry.package != package):
                        problems.append(
                            f"{name}: иконка совпадает с чужим пакетом "
                            f"{entry.package}")
            order_ids = [str(x) for x in (app.get("order_ids") or [])]
            for order_id in order_ids:
                if order_id in order_owner and order_owner[order_id] != app.get("id"):
                    problems.append(
                        f"{name}: ID {order_id} уже привязан к другому приложению")
                order_owner[order_id] = app.get("id") or ""
            if not icons and not order_ids:
                title = app.get("title") or ""
                if title:
                    found, _score, why = match_text(self.db, title)
                    if not found or found.get("id") != app.get("id"):
                        problems.append(
                            f"{name}: текстовый матч даёт "
                            f"«{(found or {}).get('title', '—')}» ({why})")

        summary = (
            f"Приложений: {len(apps)}\n"
            f"Иконок: {icon_total} (нет файла: {missing_files}, "
            f"hash старого формата: {hash_mismatch})\n"
            f"ID заданий: {len(order_owner)}\n"
            f"Замечаний: {len(problems)}"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Проверка ВСЕГО — детально")
        box.setIcon(QMessageBox.Icon.Warning if problems else QMessageBox.Icon.Information)
        box.setText(summary)
        if problems:
            box.setDetailedText("\n".join(problems[:300]))
        else:
            box.setInformativeText("Замечаний нет.")
        box.exec()

    # ------------------------------------------------------------------
    def show_unidentified(self):
        items = self.db.get("unidentified", [])
        if not items:
            QMessageBox.information(self, "Неопознанные", "Очередь пуста.")
            return
        lines = [
            f"{x.get('order_id')} · {x.get('title') or '—'} · {x.get('price') or '—'} · "
            f"{x.get('reason') or ''}"
            for x in items[-30:]
        ]
        QMessageBox.information(self, "Неопознанные", "\n".join(lines))

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт базы", "panel_apps_export.json", "JSON (*.json)"
        )
        if path:
            export_db(self.db, path)
            self.msg.setText(f"Экспортировано: {path}")

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт базы", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            db = import_db(path)
            save_db(db)
            self.db = db
            self.current_id = None
            self.refresh_list()
            if self.db["apps"]:
                self.app_list.setCurrentRow(0)
            self.msg.setText(f"Импортировано: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Импорт", str(exc))
