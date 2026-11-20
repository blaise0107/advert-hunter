# -*- coding: utf-8 -*-
"""Импорт иконок из Swift Backup в локальный кэш и привязка к базе.

Swift Backup хранит иконки в /sdcard/SwiftBackup/icon_cache как
<package>.png. Поэтому после копирования в icon_cache/ иконку можно
привязать к приложению по имени пакета — без ручного выбора файла.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from core import device
from core.config import BASE, ICON_CACHE
from core.db import add_icon
from core.matcher import icon_hash, load_icon

REMOTE = "/sdcard/SwiftBackup/icon_cache"


def pull(dest: str | None = None) -> tuple[str, str]:
    """Скачивает папку Swift Backup во временный каталог."""
    dest = dest or tempfile.mkdtemp(prefix="swift_icons_")
    out = device.adb("pull", f"{REMOTE}/.", dest, timeout=180)
    return dest, out


def import_icons(db: dict) -> dict:
    """Копирует новые PNG и привязывает их к приложениям по имени пакета.

    Возвращает статистику: pulled, copied, attached, error.
    """
    os.makedirs(ICON_CACHE, exist_ok=True)
    stats = {"pulled": 0, "copied": 0, "attached": 0, "error": ""}
    tmp, out = pull()
    if isinstance(out, str) and out.startswith("ERROR:"):
        stats["error"] = out
        shutil.rmtree(tmp, ignore_errors=True)
        return stats
    try:
        names = [n for n in os.listdir(tmp) if n.lower().endswith(".png")]
        stats["pulled"] = len(names)
        for name in names:
            src = os.path.join(tmp, name)
            dst = os.path.join(ICON_CACHE, name)
            if os.path.exists(dst):
                continue
            shutil.copy2(src, dst)
            stats["copied"] += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for app in db.get("apps") or []:
        package = (app.get("package") or "").strip()
        if not package:
            continue
        path = os.path.join(ICON_CACHE, f"{package}.png")
        if not os.path.exists(path):
            continue
        image = load_icon(path)
        digest = icon_hash(image) if image is not None else None
        if not digest:
            continue
        rel = os.path.relpath(path, BASE)
        if add_icon(db, app["id"], digest, rel, source="swift"):
            stats["attached"] += 1
    return stats
