# -*- coding: utf-8 -*-
"""Диалог добавления приложения из Swift Backup.

Показывает пакеты, для которых у Swift Backup есть резервная копия или
иконка. Пакет, название, component, известные ID и иконка подставляются
автоматически; пользователь может поправить название и component.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import device, devices, swift_catalog
from core.config import BASE
from core.db import add_icon, empty_app, find_app, package_title, save_db
from core.matcher import icon_hash, load_icon
from panel.widgets import IconPreview, propagate_tooltips


class CatalogWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, serial: str, parent=None):
        super().__init__(parent)
        self.serial = serial
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.progress.emit("Читаю Swift Backup…")
            snap = swift_catalog.snapshot(self.serial)
            if self._stop:
                return
            missing = swift_catalog.missing_icons(snap.get("icons") or [])
            pulled = 0
            if missing:
                self.progress.emit(f"Скачиваю иконки: {len(missing)}…")
                pulled = swift_catalog.pull_icons(self.serial)
            snap["pulled"] = pulled
            if not self._stop:
                self.done.emit(snap)
        except Exception as exc:  # noqa: BLE001 - показываем текст в диалоге
            if not self._stop:
                self.failed.emit(str(exc))


class MetaWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, serial: str, package: str, meta: dict, installed: bool,
                 parent=None):
        super().__init__(parent)
        self.serial = serial
        self.package = package
        self.meta = meta or {}
        self.installed = installed
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        component = (self.meta.get("component") or "").strip()
        if not component and self.installed and not self._stop:
            component = swift_catalog.resolve_component(self.serial, self.package)
        if self._stop:
            return

        title = (self.meta.get("title") or "").strip()
        source = "база/известные задания" if title else ""
        human = self._looks_human(title)
        if not human and not self._stop:
            play = swift_catalog.play_title(self.package)
            if play:
                title = play
                source = "Google Play"
        if not title:
            title = package_title(self.package)
            source = "по имени пакета"
        self.done.emit({
            "package": self.package,
            "title": title,
            "component": component,
            "source": source,
            "order_ids": list(self.meta.get("order_ids") or []),
        })

    def _looks_human(self, title: str) -> bool:
        if not title:
            return False
        if title.strip().lower() == package_title(self.package).strip().lower():
            return False
        if " " in title.strip():
            return True
        return len(title) >= 4 and any(ch.isupper() for ch in title[1:])


class SwiftAppDialog(QDialog):
    app_added = pyqtSignal(str)

    def __init__(self, db: dict, parent=None):
        super().__init__(parent)
        self.db = db
        self.snapshot: dict = {}
        self.meta_map: dict[str, dict] = {}
        self._catalog_worker: CatalogWorker | None = None
        self._meta_worker: MetaWorker | None = None
        self._meta_token = 0
        self._selected = ""
        self._title_edited = False
        self._component_edited = False
        self._added: set[str] = set()

        self.setWindowTitle("Добавить из Swift Backup")
        self.resize(1120, 720)
        self.setMinimumSize(900, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ---------------- телефон и обновление ----------------
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Телефон"))
        self.cb_device = QComboBox()
        self.cb_device.setMinimumWidth(280)
        self.cb_device.setToolTip(
            "С какого подключённого телефона читать Swift Backup. "
            "Список берётся из adb devices.")
        self.cb_device.currentIndexChanged.connect(self.reload_catalog)
        top.addWidget(self.cb_device, 1)

        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.setToolTip(
            "Перечитать список приложений и докачать новые иконки из Swift Backup.")
        self.btn_refresh.clicked.connect(self.reload_catalog)
        top.addWidget(self.btn_refresh)
        root.addLayout(top)

        self.l_status = QLabel("Загружаю список…")
        self.l_status.setObjectName("Muted")
        self.l_status.setWordWrap(True)
        root.addWidget(self.l_status)

        # ---------------- фильтры ----------------
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("поиск: название или пакет")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setToolTip("Фильтр по названию приложения и имени пакета.")
        self.ed_search.textChanged.connect(self.rebuild_list)
        filters.addWidget(self.ed_search, 1)
        self.cb_all = QCheckBox("Показывать уже добавленные")
        self.cb_all.setToolTip(
            "По умолчанию показываются только приложения, которых ещё нет в базе. "
            "Включите, чтобы найти существующую запись и открыть её.")
        self.cb_all.toggled.connect(self.rebuild_list)
        filters.addWidget(self.cb_all)
        root.addLayout(filters)

        # ---------------- список и карточка ----------------
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        self.list = QListWidget()
        self.list.setIconSize(QSize(38, 38))
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setSpacing(2)
        self.list.setToolTip(
            "Приложения, найденные в резервных копиях и кэше иконок Swift Backup. "
            "Выберите строку — справа заполнятся поля.")
        self.list.currentItemChanged.connect(self.on_selected)
        split.addWidget(self.list)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 0, 0, 0)
        rv.setSpacing(10)

        head = QHBoxLayout()
        self.preview = IconPreview(92)
        head.addWidget(self.preview)
        head_box = QVBoxLayout()
        self.l_package = QLabel("Пакет не выбран")
        self.l_package.setObjectName("SectionTitle")
        self.l_package.setWordWrap(True)
        self.l_package.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        head_box.addWidget(self.l_package)
        self.l_source = QLabel("—")
        self.l_source.setObjectName("Muted")
        self.l_source.setWordWrap(True)
        head_box.addWidget(self.l_source)
        head.addLayout(head_box, 1)
        rv.addLayout(head)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("название приложения")
        self.ed_title.setToolTip(
            "Название подставляется из базы, известных заданий или Google Play. "
            "Можно исправить вручную.")
        self.ed_title.textEdited.connect(lambda: setattr(self, "_title_edited", True))
        self.ed_component = QLineEdit()
        self.ed_component.setPlaceholderText("необязательно: activity для запуска")
        self.ed_component.setToolTip(
            "Component/activity для запуска. Для установленного приложения "
            "определяется автоматически; можно оставить пустым.")
        self.ed_component.textEdited.connect(
            lambda: setattr(self, "_component_edited", True))
        form.addWidget(QLabel("Название"), 0, 0)
        form.addWidget(self.ed_title, 0, 1)
        form.addWidget(QLabel("Component"), 1, 0)
        form.addWidget(self.ed_component, 1, 1)
        form.setColumnStretch(1, 1)
        rv.addLayout(form)

        self.cb_icon = QCheckBox("Добавить иконку из Swift Backup")
        self.cb_icon.setChecked(True)
        self.cb_icon.setToolTip(
            "Привязать к новой записи иконку из icon_cache. Иконка остаётся "
            "в локальном кэше и участвует в матчинге.")
        rv.addWidget(self.cb_icon)

        self.cb_orders = QCheckBox("Добавить известные ID заданий")
        self.cb_orders.setToolTip(
            "Если для пакета уже встречались задания, их ID будут добавлены "
            "в новую запись автоматически.")
        rv.addWidget(self.cb_orders)

        self.l_known = QLabel("Известные ID: —")
        self.l_known.setObjectName("Muted")
        self.l_known.setWordWrap(True)
        rv.addWidget(self.l_known)

        self.l_hint = QLabel(
            "Выберите приложение слева. Поля «Название» и «Component» можно "
            "поправить перед добавлением.")
        self.l_hint.setObjectName("Muted")
        self.l_hint.setWordWrap(True)
        rv.addWidget(self.l_hint)
        rv.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_add = QPushButton("Добавить")
        self.btn_add.setObjectName("AccentButton")
        self.btn_add.setToolTip("Создать запись в базе и закрыть диалог.")
        self.btn_add.clicked.connect(lambda: self.add_current(True))
        self.btn_add_more = QPushButton("Добавить и продолжить")
        self.btn_add_more.setToolTip(
            "Создать запись и оставить диалог открытым, чтобы добавить ещё "
            "несколько приложений подряд.")
        self.btn_add_more.clicked.connect(lambda: self.add_current(False))
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setToolTip("Закрыть диалог без добавления.")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_add)
        buttons.addWidget(self.btn_add_more)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)
        rv.addLayout(buttons)

        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([640, 460])

        propagate_tooltips(self)
        self.load_devices()
        self._update_buttons()

    # ------------------------------------------------------------------
    def load_devices(self):
        self.cb_device.blockSignals(True)
        self.cb_device.clear()
        try:
            found = [d for d in devices.list_devices() if d.online]
        except Exception as exc:  # noqa: BLE001
            found = []
            self.l_status.setText(f"Не удалось получить список телефонов: {exc}")
        for info in found:
            self.cb_device.addItem(info.label, info.serial)
        if found:
            preferred = next((i for i, d in enumerate(found)
                              if d.serial == device.SERIAL), 0)
            self.cb_device.setCurrentIndex(preferred)
        self.cb_device.blockSignals(False)
        if not found:
            self.l_status.setText(
                "Нет онлайн-телефонов. Подключите телефон по USB и нажмите «Обновить».")
            self._update_buttons()
            return
        self.reload_catalog()

    def serial(self) -> str:
        return str(self.cb_device.currentData() or "")

    def reload_catalog(self):
        serial = self.serial()
        if not serial:
            return
        if self._catalog_worker is not None:
            self._catalog_worker.stop()
            if not self._catalog_worker.wait(3000):
                self._catalog_worker.terminate()
                self._catalog_worker.wait(1000)
            self._catalog_worker = None
        self.list.clear()
        self._selected = ""
        self._update_buttons()
        self.l_status.setText("Читаю Swift Backup…")
        self._catalog_worker = CatalogWorker(serial, self)
        self._catalog_worker.progress.connect(self.l_status.setText)
        self._catalog_worker.done.connect(self.on_catalog)
        self._catalog_worker.failed.connect(self.on_catalog_failed)
        self._catalog_worker.finished.connect(self._catalog_finished)
        self._catalog_worker.start()

    def _catalog_finished(self):
        worker = self.sender()
        if worker is self._catalog_worker:
            self._catalog_worker = None
        if worker is not None:
            worker.deleteLater()

    def on_catalog(self, snap: dict):
        self.snapshot = snap
        self.meta_map = swift_catalog.metadata_map()
        self._added.clear()
        self.rebuild_list()
        self.l_status.setText(
            f"Пакетов: {len(snap.get('packages') or [])} · "
            f"установлено: {len(snap.get('installed') or [])} · "
            f"иконок: {len(snap.get('icons') or [])} · "
            f"докачано иконок: {snap.get('pulled', 0)}"
        )

    def on_catalog_failed(self, message: str):
        self.l_status.setText(f"Ошибка чтения Swift Backup: {message}")
        self._update_buttons()

    def rebuild_list(self, *_):
        if not self.snapshot:
            return
        needle = self.ed_search.text().strip().lower()
        show_all = self.cb_all.isChecked()
        current = self._selected
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        packages = list(self.snapshot.get("packages") or [])
        packages.sort(key=lambda p: (
            ((self.meta_map.get(p) or {}).get("title") or package_title(p)).lower(), p))
        for package in packages:
            existing = find_app(self.db, package=package)
            if existing and not show_all:
                continue
            meta = self.meta_map.get(package) or {}
            title = (meta.get("title") or package_title(package)).strip()
            if needle and needle not in package.lower() and needle not in title.lower():
                continue
            icon_path = swift_catalog.local_icon(package)
            icon = QIcon(icon_path) if icon_path else QIcon()
            installed = package in (self.snapshot.get("installed") or [])
            suffix = " · установлено" if installed else ""
            if existing:
                suffix += " · уже в базе"
            item = QListWidgetItem(icon, f"{title}\n{package}{suffix}")
            item.setSizeHint(QSize(0, 48))
            item.setData(Qt.ItemDataRole.UserRole, package)
            item.setToolTip(
                f"{title}\n{package}\n"
                f"{'установлено на телефоне' if installed else 'только в Swift Backup'}\n"
                f"{'есть резервная копия' if package in (self.snapshot.get('backup') or []) else 'резервной копии нет'}\n"
                f"{'иконка: ' + os.path.basename(icon_path) if icon_path else 'иконки нет'}")
            self.list.addItem(item)
            if package == current:
                self.list.setCurrentItem(item)
            shown += 1
        self.list.blockSignals(False)
        self.l_status.setText(
            f"Показано: {shown} из {len(self.snapshot.get('packages') or [])} · "
            f"установлено: {len(self.snapshot.get('installed') or [])} · "
            f"иконок: {len(self.snapshot.get('icons') or [])}")
        if shown and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        if not shown:
            self._selected = ""
            self._clear_details()
        self._update_buttons()

    def on_selected(self, current, previous):
        package = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        if package == self._selected:
            return
        self._selected = package
        self._meta_token += 1
        token = self._meta_token
        if self._meta_worker is not None:
            self._meta_worker.stop()
            if not self._meta_worker.wait(1500):
                self._meta_worker.terminate()
                self._meta_worker.wait(1000)
            self._meta_worker = None
        if not package:
            self._clear_details()
            self._update_buttons()
            return

        self._title_edited = False
        self._component_edited = False
        meta = self.meta_map.get(package) or {}
        self.l_package.setText(package)
        self.ed_title.setText(meta.get("title") or package_title(package))
        self.ed_component.setText(meta.get("component") or "")
        self.l_source.setText("Название: из базы/известных заданий" if meta.get("title")
                              else "Название: по имени пакета; уточняю…")
        self.preview.set_path(swift_catalog.local_icon(package) or None)
        known = list(meta.get("order_ids") or [])
        self.l_known.setText(
            "Известные ID: " + ", ".join(known) if known else "Известные ID: —")
        self.cb_orders.setChecked(bool(known))
        self.cb_orders.setEnabled(bool(known))
        self.cb_icon.setChecked(bool(swift_catalog.local_icon(package)))
        self.cb_icon.setEnabled(bool(swift_catalog.local_icon(package)))
        existing = find_app(self.db, package=package)
        self.l_hint.setText(
            f"Уже в базе: «{existing.get('title') or existing.get('package')}». "
            "Кнопка «Добавить» откроет существующую запись."
            if existing else
            "Поля можно поправить перед добавлением. Название уточняется в Google Play.")
        self._update_buttons()

        installed = package in (self.snapshot.get("installed") or [])
        self._meta_worker = MetaWorker(self.serial(), package, meta, installed, self)
        self._meta_worker.done.connect(
            lambda data, t=token: self.on_meta(data, t))
        self._meta_worker.finished.connect(self._meta_finished)
        self._meta_worker.start()

    def _meta_finished(self):
        worker = self.sender()
        if worker is self._meta_worker:
            self._meta_worker = None
        if worker is not None:
            worker.deleteLater()

    def on_meta(self, data: dict, token: int):
        if token != self._meta_token or data.get("package") != self._selected:
            return
        if not self._title_edited:
            self.ed_title.setText(data.get("title") or package_title(self._selected))
        if not self._component_edited:
            self.ed_component.setText(data.get("component") or "")
        self.l_source.setText("Название: " + (data.get("source") or "—"))
        orders = list(data.get("order_ids") or [])
        if orders:
            self.l_known.setText("Известные ID: " + ", ".join(orders))
            self.cb_orders.setChecked(True)
            self.cb_orders.setEnabled(True)

    def _clear_details(self):
        self.l_package.setText("Пакет не выбран")
        self.ed_title.clear()
        self.ed_component.clear()
        self.l_source.setText("—")
        self.l_known.setText("Известные ID: —")
        self.preview.set_path(None)
        self.l_hint.setText(
            "Выберите приложение слева. Поля «Название» и «Component» можно "
            "поправить перед добавлением.")

    def _update_buttons(self):
        has = bool(self._selected)
        self.btn_add.setEnabled(has)
        self.btn_add_more.setEnabled(has)

    # ------------------------------------------------------------------
    def add_current(self, close: bool):
        package = self._selected.strip()
        if not package:
            return
        existing = find_app(self.db, package=package)
        if existing:
            self.app_added.emit(existing["id"])
            if close:
                self.accept()
            else:
                self.l_hint.setText(
                    f"Открыта существующая запись «{existing.get('title') or package}».")
                self._added.add(package)
                self.rebuild_list()
            return

        title = self.ed_title.text().strip() or package_title(package)
        component = self.ed_component.text().strip()
        app = empty_app(package, title)
        app["component"] = component
        app["markets"] = ["google", "rustore", "huawei", "xiaomi"]
        app["notes"] = "Добавлено из Swift Backup."
        if self.cb_orders.isChecked():
            known = self.meta_map.get(package) or {}
            app["order_ids"] = [str(x) for x in (known.get("order_ids") or [])]

        # add_icon ищет приложение в базе, поэтому сначала добавляем запись.
        self.db["apps"].append(app)
        if self.cb_icon.isChecked():
            path = swift_catalog.local_icon(package)
            if path:
                image = load_icon(path)
                digest = icon_hash(image) if image is not None else None
                if digest:
                    rel = os.path.relpath(path, BASE)
                    add_icon(self.db, app["id"], digest, rel, source="swift")

        try:
            save_db(self.db)
        except Exception as exc:  # noqa: BLE001
            self.db["apps"].pop()
            QMessageBox.critical(self, "Swift Backup", f"Не удалось сохранить базу: {exc}")
            return

        self.app_added.emit(app["id"])
        if close:
            self.accept()
            return
        self._added.add(package)
        self._selected = ""
        self._clear_details()
        self.rebuild_list()
        self.l_hint.setText(f"Добавлено: {title} ({package}). Можно выбрать следующее.")

    def closeEvent(self, event):
        for worker, timeout in ((self._catalog_worker, 3000),
                                (self._meta_worker, 3000)):
            if worker is None:
                continue
            worker.stop()
            if not worker.wait(timeout):
                # adb/сеть могут зависнуть: не даём Qt уничтожить живой QThread.
                worker.terminate()
                worker.wait(1000)
        super().closeEvent(event)
