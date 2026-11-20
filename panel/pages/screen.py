# -*- coding: utf-8 -*-
"""Страница «Экран»: живой экран выбранного телефона."""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
)

from core.config import BASE, load_settings, save_settings
from core.devices import DeviceInfo
from panel.pages.base import BasePage
from panel.screen import DeviceListWorker, ScreenStream
from panel.widgets import Badge, Card

SHOTS = os.path.join(BASE, "shots")


class ScreenPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "Экран",
            "Живой экран выбранного телефона. Список общий для всех подключённых "
            "устройств, но кадры идут только с одного — остальные не нагружаются. "
            "Частота — целевой интервал запросов; реальная скорость зависит от adb/устройства.",
            parent,
        )
        self.settings = load_settings()
        screen_cfg = self.settings.get("screen", {})
        self.devices: list[DeviceInfo] = []
        self._sig = None
        self._stream: ScreenStream | None = None
        self._retiring_streams: list[ScreenStream] = []
        self._list_worker: DeviceListWorker | None = None
        self._streaming = False
        self._stream_serial = ""
        self._resume = False
        self._suppress_select_start = False
        self._last_image: QImage | None = None
        self._last_ms = 0
        self._fps = 0.0
        self._size = ""

        # ---------------- живой экран ----------------
        live = Card("Живой экран", "Кадры запрашиваются только при открытой странице.")
        self.badge = Badge("остановлен", "muted")
        live.add_header_widget(self.badge)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Частота"))
        self.sp_fps = QSpinBox()
        self.sp_fps.setRange(1, 6)
        self.sp_fps.setValue(max(1, min(6, int(screen_cfg.get("fps", 1)))))
        self.sp_fps.setSuffix(" цел./с")
        self.sp_fps.setToolTip(
            "Целевой интервал запросов кадров (не гарантированный FPS). "
            "PNG-screencap на 1080×2412 часто даёт ~0.5–1.5 кадр/с в зависимости от "
            "adb/телефона. При активном цикле OCR оставьте 1, чтобы меньше мешать боту.")
        controls.addWidget(self.sp_fps)

        self.btn_show = QPushButton("Показать")
        self.btn_show.setObjectName("AccentButton")
        self.btn_show.setToolTip("Запустить поток кадров с выбранного телефона.")
        self.btn_show.clicked.connect(self.start_stream)
        controls.addWidget(self.btn_show)

        self.btn_stop = QPushButton("Стоп")
        self.btn_stop.setToolTip(
            "Остановить поток. Бот и приложение на телефоне не затрагиваются.")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(lambda: self.stop_stream())
        controls.addWidget(self.btn_stop)

        self.btn_snap = QPushButton("Сохранить кадр")
        self.btn_snap.setToolTip("Сохранить последний полученный кадр в shots/.")
        self.btn_snap.setEnabled(False)
        self.btn_snap.clicked.connect(self.save_frame)
        controls.addWidget(self.btn_snap)
        controls.addStretch(1)
        live.add_layout(controls)

        self.canvas = QLabel("Выберите телефон справа и нажмите «Показать».")
        self.canvas.setObjectName("ScreenCanvas")
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setMinimumSize(320, 420)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        # QLabel с pixmap в некоторых темах не красит фон по глобальному QSS,
        # поэтому фон «монитора» задаём прямо на виджете.
        self.canvas.setStyleSheet(
            "QLabel { background: #0b0e11; border: 1px solid #2c3742;"
            " border-radius: 10px; color: #93a1ae; }")
        live.add(self.canvas, 1)

        self.l_info = QLabel("Поток не запущен.")
        self.l_info.setObjectName("Muted")
        self.l_info.setWordWrap(True)
        live.add(self.l_info)

        # ---------------- устройства ----------------
        side = Card(
            "Устройства",
            "Список обновляется сам. Двойной клик по телефону сразу запускает поток.",
        )
        side.setMinimumWidth(300)
        side.setMaximumWidth(390)
        self.l_count = QLabel("—")
        self.l_count.setObjectName("Muted")
        side.add_header_widget(self.l_count)

        self.btn_refresh = QPushButton("Обновить список")
        self.btn_refresh.setToolTip("Перечитать `adb devices -l` прямо сейчас.")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        side.add(self.btn_refresh)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("фильтр: серийник или модель")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip(
            "Скрыть устройства, в серийнике/модели/состоянии которых нет этой "
            "подстроки. Удобно, когда подключены десятки телефонов.")
        self.filter.textChanged.connect(self.apply_filter)
        side.add(self.filter)

        self.list = QListWidget()
        self.list.setToolTip(
            "Все телефоны, которые видит adb. «бот» — устройство, с которым "
            "работает цикл. Офлайн-устройства показаны серым.")
        self.list.itemSelectionChanged.connect(self.on_select)
        self.list.itemDoubleClicked.connect(lambda _item: self.start_stream())
        side.add(self.list, 1)

        self.l_sel = QLabel("Устройство не выбрано.")
        self.l_sel.setObjectName("Muted")
        self.l_sel.setWordWrap(True)
        side.add(self.l_sel)

        self.cb_autostop = QCheckBox("Пауза при уходе со страницы")
        self.cb_autostop.setChecked(bool(screen_cfg.get("auto_stop", True)))
        self.cb_autostop.setToolTip(
            "Останавливать поток, когда вы открываете другую страницу панели, "
            "и возобновлять при возврате. Экономит adb и батарею телефона.")
        side.add(self.cb_autostop)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(live, 3)
        body.addWidget(side, 1)
        self.add_layout(body, 1)

        self.sp_fps.valueChanged.connect(self.save_prefs)
        self.cb_autostop.toggled.connect(self.save_prefs)

        self._list_timer = QTimer(self)
        self._list_timer.setInterval(4000)
        self._list_timer.timeout.connect(self.refresh_devices)

    # ------------------------------------------------------------------
    # список устройств
    def refresh_devices(self):
        if self._list_worker is not None and self._list_worker.isRunning():
            return
        self._list_worker = DeviceListWorker(self)
        self._list_worker.done.connect(self.on_devices)
        self._list_worker.failed.connect(self.on_devices_failed)
        self._list_worker.finished.connect(self.on_list_finished)
        self._list_worker.start()

    def on_list_finished(self):
        worker = self.sender()
        if worker is self._list_worker:
            self._list_worker = None
        if worker is not None:
            worker.deleteLater()

    def on_devices_failed(self, msg: str):
        self.l_count.setText("adb не ответил")
        self.l_info.setText(f"Не удалось получить список устройств: {msg}")

    def on_devices(self, items: list[DeviceInfo]):
        self.devices = items
        online = sum(1 for d in items if d.online)
        self.l_count.setText(f"{len(items)} · онлайн {online}")
        sig = tuple((d.serial, d.state, d.model) for d in items)
        if sig == self._sig:
            self.update_buttons()
            return
        self._sig = sig

        selected = self.selected_serial()
        scroll = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        self.list.clear()
        for d in items:
            item = QListWidgetItem(f"{d.serial}\n{d.name} · {d.state_ru}")
            item.setData(Qt.ItemDataRole.UserRole, d.serial)
            item.setSizeHint(QSize(0, 48))
            item.setToolTip(
                f"Серийный: {d.serial}\nМодель: {d.name}\n"
                f"Состояние: {d.state_ru}\nТранспорт: {d.transport_id or '—'}")
            if not d.online:
                item.setForeground(Qt.GlobalColor.gray)
            if d.is_bot:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
        self.list.blockSignals(False)

        target = selected
        if not self.find_item(target):
            target = next((d.serial for d in items if d.is_bot), "")
        if not self.find_item(target):
            target = next((d.serial for d in items if d.online), "")
        if not self.find_item(target) and items:
            target = items[0].serial
        item = self.find_item(target)
        if item is not None:
            self.list.setCurrentItem(item)
        self.list.verticalScrollBar().setValue(scroll)
        self.apply_filter(self.filter.text())

        if self._streaming and self._stream_serial:
            current = next(
                (d for d in items if d.serial == self._stream_serial), None)
            if current is None or not current.online:
                lost = self._stream_serial
                self.stop_stream()
                self.l_info.setText(
                    f"Устройство {lost} отключилось — поток остановлен.")
        self.on_select()

    def apply_filter(self, text: str = ""):
        needle = (text or "").strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def find_item(self, serial: str):
        if not serial:
            return None
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == serial:
                return item
        return None

    def selected_serial(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def selected_device(self) -> DeviceInfo | None:
        serial = self.selected_serial()
        return next((d for d in self.devices if d.serial == serial), None)

    def select_device(self, serial: str, start: bool = False) -> bool:
        """Выбрать телефон из другого места панели (например, со страницы «Ферма»)."""
        item = self.find_item(serial)
        if item is None:
            self.refresh_devices()
            item = self.find_item(serial)
        if item is None:
            self.l_info.setText(f"Устройство {serial} не найдено в adb.")
            return False
        self._suppress_select_start = True
        try:
            self.filter.clear()
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)
            self.on_select()
        finally:
            self._suppress_select_start = False
        if start:
            self.start_stream()
        return True

    def on_select(self):
        d = self.selected_device()
        if d is None:
            self.l_sel.setText("Устройство не выбрано.")
            self.update_buttons()
            return
        self.l_sel.setText(f"{d.serial}\n{d.name} · {d.state_ru}")
        if (not self._suppress_select_start
                and self._streaming and self._stream_serial != d.serial):
            # Выбор другого телефона сразу переключает поток.
            self.start_stream()
        self.update_buttons()

    def update_buttons(self):
        d = self.selected_device()
        can = bool(d and d.online and not self._streaming)
        self.btn_show.setEnabled(can)
        self.btn_stop.setEnabled(self._streaming)
        self.btn_snap.setEnabled(self._last_image is not None)

    # ------------------------------------------------------------------
    # поток
    def _retire_stream(self, stream: ScreenStream | None):
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        if stream in self._retiring_streams:
            return
        self._retiring_streams.append(stream)
        try:
            stream.finished.connect(self.on_stream_finished)
        except Exception:
            pass
        # На случай, если поток завершился до/вне нормального цикла сигналов.
        QTimer.singleShot(15000, lambda s=stream: self._forget_stream(s))

    def _forget_stream(self, stream: ScreenStream | None):
        if stream is None:
            return
        if stream in self._retiring_streams:
            try:
                self._retiring_streams.remove(stream)
            except ValueError:
                pass
        if stream is self._stream:
            self._stream = None
        try:
            stream.deleteLater()
        except Exception:
            pass

    @staticmethod
    def _is_device_error(msg: str) -> bool:
        text = (msg or "").lower()
        needles = (
            "not found", "device offline", "offline", "unauthorized",
            "no devices", "device '", "closed", "transport",
        )
        return any(n in text for n in needles)

    def start_stream(self):
        d = self.selected_device()
        if d is None:
            self.l_info.setText("Сначала выберите телефон в списке справа.")
            return
        if not d.online:
            self.l_info.setText(
                f"{d.serial}: состояние «{d.state_ru}». Разрешите отладку на телефоне.")
            self.refresh_devices()
            return

        self.stop_stream(silent=True)
        self._resume = False
        self._stream_serial = d.serial
        self._streaming = True
        self._last_ms = 0
        self._fps = 0.0
        self._size = ""
        self.badge.setText("подключение…")
        self.badge.set_variant("info")
        self.l_info.setText(f"{d.serial}: запуск потока…")
        self.update_buttons()

        self._stream = ScreenStream(d.serial, float(self.sp_fps.value()), self)
        self._stream.frame.connect(self.on_frame)
        self._stream.stats.connect(self.on_stats)
        self._stream.failed.connect(self.on_failed)
        self._stream.finished.connect(self.on_stream_finished)
        self._stream.start()

    def stop_stream(self, silent: bool = False, wait_ms: int = 0):
        was = self._streaming or self._stream is not None
        self._streaming = False
        stream = self._stream
        self._stream = None
        self._stream_serial = ""
        self._last_ms = 0
        self._fps = 0.0
        if stream is not None:
            self._retire_stream(stream)
            if wait_ms > 0:
                try:
                    stream.wait(wait_ms)
                except Exception:
                    pass
                self._forget_stream(stream)
        self.badge.setText("остановлен")
        self.badge.set_variant("muted")
        self.update_buttons()
        if was and not silent:
            self.l_info.setText("Поток остановлен. Бот и приложение не затронуты.")

    def on_frame(self, image: QImage, elapsed_ms: int):
        if self.sender() is not self._stream:
            return
        self._last_image = image
        self._last_ms = elapsed_ms
        self._size = f"{image.width()}×{image.height()}"
        if self._streaming:
            self.badge.setText("LIVE")
            self.badge.set_variant("ok")
        self._render()
        self.update_buttons()
        self.update_info()

    def on_stats(self, stats: dict):
        if self.sender() is not self._stream:
            return
        self._fps = float(stats.get("fps", 0.0))
        self._last_ms = int(stats.get("ms", self._last_ms))
        if self._streaming:
            self.badge.setText("LIVE")
            self.badge.set_variant("ok")
        self.update_info()

    def on_failed(self, msg: str):
        if self.sender() is not self._stream:
            return
        serial = self._stream_serial or "?"
        self._streaming = False
        self.badge.setText("ошибка")
        self.badge.set_variant("bad")
        hint = f"Поток остановлен: {msg}"
        if self._is_device_error(msg):
            hint += " (проверьте USB/разрешение отладки; список устройств обновлён)"
            self.refresh_devices()
        self.l_info.setText(f"{serial}: {hint}")
        self.update_buttons()

    def on_stream_finished(self):
        sender = self.sender()
        if sender is self._stream:
            self._stream = None
            self._streaming = False
            self.badge.setText("остановлен")
            self.badge.set_variant("muted")
            self.update_buttons()
        self._forget_stream(sender)

    def update_info(self):
        if not self._stream_serial:
            return
        parts = [self._stream_serial]
        if self._size:
            parts.append(self._size)
        if self._fps:
            parts.append(f"{self._fps:.1f} кадр/с")
        if self._last_ms:
            parts.append(f"{self._last_ms} мс")
        self.l_info.setText(" · ".join(parts))

    # ------------------------------------------------------------------
    # отрисовка
    def _render(self):
        if self._last_image is None:
            return
        area = self.canvas.size()
        width = max(64, area.width() - 6)
        height = max(64, area.height() - 6)
        pix = QPixmap.fromImage(self._last_image)
        self.canvas.setPixmap(
            pix.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._last_image is not None:
            QTimer.singleShot(0, self._render)

    # ------------------------------------------------------------------
    def save_frame(self):
        if self._last_image is None:
            self.l_info.setText("Кадр ещё не получен.")
            return
        os.makedirs(SHOTS, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        serial = self._stream_serial or "device"
        path = os.path.join(SHOTS, f"screen_{serial}_{stamp}.png")
        if self._last_image.save(path, "PNG"):
            self.l_info.setText(f"Кадр сохранён: {path}")
        else:
            self.l_info.setText(f"Не удалось сохранить кадр: {path}")

    def save_prefs(self):
        fresh = load_settings()
        section = fresh.setdefault("screen", {})
        section["fps"] = self.sp_fps.value()
        section["auto_stop"] = self.cb_autostop.isChecked()
        save_settings(fresh)

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self._list_timer.start()
        self.refresh_devices()
        if self._resume:
            self._resume = False
            QTimer.singleShot(80, self.start_stream)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._list_timer.stop()
        if self._streaming and self.cb_autostop.isChecked():
            self._resume = True
            self.stop_stream(silent=True)

    def shutdown(self):
        self._list_timer.stop()
        self._resume = False
        self.stop_stream(silent=True)
        # Корректно завершаем потоки при закрытии окна, но не держим UI долго.
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            deadline = 15  # ~1.5s
            while deadline > 0 and (
                self._stream is not None
                or self._retiring_streams
                or (self._list_worker is not None and self._list_worker.isRunning())
            ):
                if self._stream is not None:
                    try:
                        self._stream.wait(100)
                    except Exception:
                        pass
                for s in list(self._retiring_streams):
                    try:
                        s.wait(50)
                    except Exception:
                        pass
                if self._list_worker is not None and self._list_worker.isRunning():
                    self._list_worker.wait(50)
                if app is not None:
                    app.processEvents()
                deadline -= 1
        except Exception:
            pass
        if self._stream is not None:
            self._forget_stream(self._stream)
        for s in list(self._retiring_streams):
            self._forget_stream(s)
        if self._list_worker is not None:
            try:
                self._list_worker.wait(500)
            except Exception:
                pass
