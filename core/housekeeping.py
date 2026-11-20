# -*- coding: utf-8 -*-
"""Обслуживание файлов проекта: размеры, ротация логов, размеры кадров.

Используется панелью («Журнал», «Настройки») и скриптом ротации перед
запуском цикла. Ничего не удаляет без явной команды пользователя.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime

from core.config import BASE

LOG_DIR = os.path.join(BASE, "logs")  # каталог активных логов
ARCHIVE_DIR = os.path.join(LOG_DIR, "archive")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_NAMES = (
    "farm.log",
    "farm_console.log",
    "resume.log",
    "resume.err",
    "hunter2.log",
    "tasks.log",
    "panel_hunter.log",
    "panel_tasks.log",
    "panel_active_loop.log",
    "active_loop.log",
    "active_list.log",
    "migration.log",
    "rotate.log",
)


def _find_log(name: str) -> str:
    path = os.path.join(LOG_DIR, name)
    if os.path.exists(path):
        return path
    legacy = os.path.join(BASE, name)
    return legacy if os.path.exists(legacy) else path


def run_log_files() -> list[dict]:
    """Логи устройств из runs/<serial>/ — по одному набору на телефон."""
    runs = os.path.join(BASE, "runs")
    result = []
    try:
        serials = sorted(os.listdir(runs))
    except OSError:
        return result
    for serial in serials:
        folder = os.path.join(runs, serial)
        if not os.path.isdir(folder):
            continue
        for name in ("resume.log", "hunter2.log", "tasks.log"):
            info = file_info(os.path.join(folder, name))
            if info["exists"]:
                info["name"] = f"{serial}/{name}"
                result.append(info)
    return result


def all_log_files() -> list[dict]:
    return log_files() + run_log_files()


def human_size(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def file_info(path: str) -> dict:
    info = {"path": path, "name": os.path.basename(path), "exists": False,
            "size": 0, "mtime": None, "text": "нет файла"}
    try:
        stat = os.stat(path)
    except OSError:
        return info
    info.update(exists=True, size=stat.st_size, mtime=stat.st_mtime,
                text=human_size(stat.st_size))
    return info


def log_files(names=LOG_NAMES) -> list[dict]:
    return [file_info(_find_log(name)) for name in names]


def rotate_log(path: str, max_mb: float = 2.0, keep: int = 10) -> str | None:
    """Если лог больше max_mb — переносит его в logs/archive/ с меткой времени."""
    if not os.path.exists(path):
        return None
    if os.path.getsize(path) < max_mb * 1024 * 1024:
        return None
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(ARCHIVE_DIR, f"{stem}_{stamp}{ext or '.log'}")
    try:
        shutil.move(path, dst)
    except OSError:
        return None
    prune_dir(ARCHIVE_DIR, keep=keep)
    return dst


def rotate_all(max_mb: float = 2.0, keep: int = 10,
               names=LOG_NAMES) -> list[str]:
    moved = []
    for name in names:
        dst = rotate_log(_find_log(name), max_mb=max_mb, keep=keep)
        if dst:
            moved.append(dst)
    # Логи фермы лежат в runs/<serial>/ и тоже не должны расти вечно.
    for info in run_log_files():
        dst = rotate_log(info["path"], max_mb=max_mb, keep=keep)
        if dst:
            moved.append(dst)
    return moved


def prune_dir(path: str, keep: int = 10) -> int:
    """Оставляет keep самых свежих файлов, остальные удаляет."""
    try:
        files = [
            (os.path.getmtime(os.path.join(path, name)), os.path.join(path, name))
            for name in os.listdir(path)
            if os.path.isfile(os.path.join(path, name))
        ]
    except OSError:
        return 0
    files.sort(reverse=True)
    removed = 0
    for _mtime, file in files[keep:]:
        try:
            os.remove(file)
            removed += 1
        except OSError:
            pass
    return removed


def dir_stats(path: str) -> dict:
    files = 0
    size = 0
    newest = None
    oldest = None
    try:
        names = os.listdir(path)
    except OSError:
        return {"files": 0, "size": 0, "text": "0 файлов, 0 Б",
                "newest": None, "oldest": None}
    for name in names:
        full = os.path.join(path, name)
        try:
            if not os.path.isfile(full):
                continue
            stat = os.stat(full)
        except OSError:
            continue
        files += 1
        size += stat.st_size
        newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
        oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
    return {"files": files, "size": size, "text": f"{files} файлов, {human_size(size)}",
            "newest": newest, "oldest": oldest}
