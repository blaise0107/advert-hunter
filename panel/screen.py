# -*- coding: utf-8 -*-
"""Фоновые задачи живого экрана: список устройств и поток кадров.

Поток создаётся только для того телефона, который сейчас смотрят.
Десятки подключённых телефонов не означают десятки потоков: остальные
устройства не нагружаются, пока их не выбрали.
"""
from __future__ import annotations

import subprocess
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from core import device, devices


class DeviceListWorker(QThread):
    """Один вызов `adb devices -l` в фоне, чтобы не морозить панель."""

    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.done.emit(devices.list_devices())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ScreenStream(QThread):
    """Поток PNG-кадров с одного устройства через `adb exec-out screencap`.

    Почему PNG, а не видео: на этом Android 16 `screenrecord
    --output-format=h264` не отдаёт данные, а raw-кадр 1080×2412 весит
    ~10 МБ и идёт медленнее PNG. PNG даёт около 2 кадр/с — достаточно,
    чтобы видеть, что происходит на телефоне, и не мешать OCR-циклу.
    """

    frame = pyqtSignal(QImage, int)  # кадр, миллисекунды на захват
    stats = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, serial: str, fps: float = 2.0, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.fps = max(0.5, min(10.0, float(fps)))
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

    def stop(self):
        """Просит поток завершиться и убивает текущий adb-процесс."""
        self._stop.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def run(self):
        interval = 1.0 / self.fps
        fails = 0
        frames_window = 0
        window_start = time.perf_counter()

        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                proc = subprocess.Popen(
                    [device.ADB, "-s", self.serial,
                     "exec-out", "screencap", "-p"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=device.CREATE_NO_WINDOW,
                )
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(f"Не удалось запустить adb: {exc}")
                return

            with self._proc_lock:
                self._proc = proc

            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
            finally:
                with self._proc_lock:
                    self._proc = None

            if self._stop.is_set():
                break

            if proc.returncode != 0 or not out:
                fails += 1
                text = (err or b"").decode("utf-8", "replace").strip()
                if not text:
                    text = f"adb вернул код {proc.returncode}"
                if fails >= 3:
                    self.failed.emit(text)
                    return
                self._stop.wait(0.5)
                continue

            fails = 0
            image = QImage.fromData(out, "PNG")
            if image.isNull():
                fails += 1
                if fails >= 3:
                    self.failed.emit("Не удалось декодировать PNG-кадр.")
                    return
                self._stop.wait(0.5)
                continue

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.frame.emit(image, elapsed_ms)
            frames_window += 1

            now = time.perf_counter()
            if now - window_start >= 1.0:
                self.stats.emit(
                    {
                        "serial": self.serial,
                        "fps": frames_window / (now - window_start),
                        "ms": elapsed_ms,
                        "w": image.width(),
                        "h": image.height(),
                        "kb": len(out) // 1024,
                    }
                )
                frames_window = 0
                window_start = now

            delay = interval - (time.perf_counter() - started)
            if delay > 0:
                self._stop.wait(delay)
