# -*- coding: utf-8 -*-
"""Фоновые задачи панели: статус, adb-команды и чтение баланса."""
from __future__ import annotations

import os
import re
import sys

from PyQt6.QtCore import QThread, pyqtSignal

from core import device, processes
from core.config import BASE, load_settings

try:
    import hunter2 as hunter
except Exception:  # pragma: no cover - панель должна работать и без OCR
    hunter = None


class StatusWorker(QThread):
    done = pyqtSignal(dict)

    def run(self):
        info = {}
        try:
            info["pids"] = processes.bot_pids()
            info["phase"] = processes.phase() if info["pids"] else "—"
            info["adb"] = device.is_online()
            info["density"] = device.density()
            info["android"] = device.android_version()
            info["serial"] = device.serialno()
            info["packages"] = len(device.third_party_packages())
            info["app_running"] = device.app_running()
        except Exception as e:
            info["error"] = str(e)
        self.done.emit(info)


class CmdWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, action: str, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            a = self.action
            if a == "start":
                if processes.is_running():
                    self.done.emit(False, "Бот уже запущен — сначала «Стоп».")
                    return
                pid = processes.start_cycle()
                self.done.emit(True, f"Цикл запущен, PID {pid}.")
            elif a == "stop":
                n = processes.stop_bot()
                self.done.emit(True, f"Остановлено процессов: {n}.")
            elif a == "restart":
                pid = processes.restart_cycle()
                self.done.emit(True, f"Перезапущено, PID {pid}.")
            elif a == "scan_new":
                if processes.is_running():
                    self.done.emit(False, "Сначала остановите цикл.")
                    return
                pid = processes.run_hunter(120)
                self.done.emit(True, f"Забор «Новых» запущен на 120 с, PID {pid}.")
            elif a == "run_active":
                if processes.is_running():
                    self.done.emit(False, "Сначала остановите цикл.")
                    return
                pid = processes.run_tasks("08:30")
                self.done.emit(True, f"Проход «Активных» запущен, PID {pid}.")
            elif a == "run_active_loop":
                pid = processes.run_active_loop()
                self.done.emit(True, f"Планировщик «Активных» запущен, PID {pid}.")
            elif a == "wake":
                device.wake()
                self.done.emit(True, "Экран разбужен.")
            elif a == "unlock":
                device.unlock()
                self.done.emit(True, "Блокировка снята.")
            elif a == "force_stop":
                device.force_stop()
                self.done.emit(True, "AdvertApp остановлен.")
            elif a == "restart_app":
                device.restart_app()
                self.done.emit(True, "AdvertApp перезапущен.")
            elif a == "balance":
                from core.balance import read_balance
                self.done.emit(*read_balance())
            elif a == "prune_frames":
                if hunter is None:
                    self.done.emit(False, "OCR-модуль hunter2.py недоступен.")
                    return
                settings = load_settings()
                m = settings.get("maintenance", {}) if isinstance(settings, dict) else {}
                removed = hunter.prune_frames(
                    int(m.get("frames_max_age_minutes", 90)),
                    int(m.get("frames_max_files", 600)),
                    int(m.get("frames_max_mb", 250)),
                )
                self.done.emit(True, f"Удалено кадров: {removed}.")
            elif a == "rotate_logs":
                from core.housekeeping import rotate_all
                settings = load_settings()
                m = settings.get("maintenance", {}) if isinstance(settings, dict) else {}
                moved = rotate_all(
                    max_mb=float(m.get("log_max_mb", 2)),
                    keep=int(m.get("log_keep", 10)),
                )
                self.done.emit(True, f"Перенесено логов в logs/: {len(moved)}.")
            else:
                self.done.emit(False, f"Неизвестная команда: {a}")
        except Exception as e:
            self.done.emit(False, f"Ошибка: {e}")

    def _balance(self) -> tuple[bool, str]:
        from core.balance import read_balance
        return read_balance()
