#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Каталог приложений Swift Backup для быстрого добавления в базу.

Swift Backup хранит резервные копии в
/sdcard/SwiftBackup/accounts/<account>/backups/apps/local/<package>/...
и иконки в /sdcard/SwiftBackup/icon_cache/<package>.png.

Модуль только читает телефон и локальные файлы. Создание записи в базе
делает вызывающая сторона (панель), чтобы не смешивать UI и запись.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request

from core import devices
from core.config import BASE, ICON_CACHE
from core.db import load_db, package_title
from core.matcher import icon_hash, load_icon

SWIFT_ROOT = "/sdcard/SwiftBackup"
ICON_REMOTE = f"{SWIFT_ROOT}/icon_cache"
BACKUP_GLOB = f"{SWIFT_ROOT}/accounts/*/backups/apps/local"

_PLAY_CACHE: dict[str, str] = {}


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _shell(serial: str, command: str, timeout: int = 90) -> str:
    return devices.adb_serial(serial, "shell", command, timeout=timeout)


def snapshot(serial: str) -> dict:
    """Один заход в adb: бэкапы, иконки и установленные пакеты."""
    command = (
        "echo @@BACKUP@@; "
        f"for d in {BACKUP_GLOB}; do [ -d \"$d\" ] && ls -1 \"$d\"; done; "
        "echo @@ICONS@@; ls -1 " + ICON_REMOTE + " 2>/dev/null; "
        "echo @@INSTALLED@@; pm list packages -3"
    )
    out = _shell(serial, command, timeout=120)
    if out.startswith("ERROR:"):
        raise RuntimeError(out)

    section = ""
    backup: set[str] = set()
    icons: set[str] = set()
    installed: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if line == "@@BACKUP@@":
            section = "backup"
            continue
        if line == "@@ICONS@@":
            section = "icons"
            continue
        if line == "@@INSTALLED@@":
            section = "installed"
            continue
        if not line:
            continue
        if section == "backup":
            backup.add(line)
        elif section == "icons":
            if line.lower().endswith(".png"):
                icons.add(line[:-4])
        elif section == "installed":
            if line.startswith("package:"):
                installed.add(line.split(":", 1)[1].strip())

    packages = sorted(backup | icons, key=str.lower)
    return {
        "serial": serial,
        "packages": packages,
        "backup": sorted(backup, key=str.lower),
        "icons": sorted(icons, key=str.lower),
        "installed": sorted(installed, key=str.lower),
    }


def local_icon(package: str) -> str:
    path = os.path.join(ICON_CACHE, f"{package}.png")
    return path if os.path.exists(path) else ""


def icon_digest(package: str) -> str | None:
    path = local_icon(package)
    if not path:
        return None
    image = load_icon(path)
    return icon_hash(image) if image is not None else None


def missing_icons(packages: list[str]) -> list[str]:
    return [p for p in packages if not local_icon(p)]


def pull_icons(serial: str) -> int:
    """Скачивает весь icon_cache с телефона, копируя только новые PNG."""
    os.makedirs(ICON_CACHE, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="swift_pull_")
    try:
        out = devices.adb_serial(serial, "pull", f"{ICON_REMOTE}/.", tmp, timeout=240)
        if isinstance(out, str) and out.startswith("ERROR:"):
            raise RuntimeError(out)
        copied = 0
        for name in os.listdir(tmp):
            if not name.lower().endswith(".png"):
                continue
            dst = os.path.join(ICON_CACHE, name)
            if os.path.exists(dst):
                continue
            shutil.copy2(os.path.join(tmp, name), dst)
            copied += 1
        return copied
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def resolve_component(serial: str, package: str) -> str:
    """Activity для запуска установленного приложения (если оно установлено)."""
    out = _shell(serial, f"cmd package resolve-activity --brief {package}", timeout=20)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if "/" in line and not line.startswith("priority="):
            return line
    return ""


def metadata_map() -> dict[str, dict]:
    """Собирает названия/компоненты/ID из базы и старых JSON."""
    result: dict[str, dict] = {}

    def merge(package: str, *, title: str = "", component: str = "",
              order_id: str = "") -> None:
        package = (package or "").strip()
        if not package:
            return
        item = result.setdefault(package, {
            "title": "", "component": "", "order_ids": [],
        })
        if title and not item["title"]:
            item["title"] = title
        if component and not item["component"]:
            item["component"] = component
        if order_id and order_id not in item["order_ids"]:
            item["order_ids"].append(str(order_id))

    try:
        db = load_db()
    except Exception:
        db = {"apps": []}
    for app in db.get("apps") or []:
        package = (app.get("package") or "").strip()
        title = (app.get("title") or "").strip()
        if title == package_title(package):
            title = ""
        merge(package, title=title, component=app.get("component") or "")
        for oid in app.get("order_ids") or []:
            merge(package, order_id=str(oid))

    known = _load_json(os.path.join(BASE, "known_apps.json"))
    for oid, info in (known.get("orders") or {}).items():
        if isinstance(info, dict):
            merge(info.get("package") or "", title=info.get("query") or "",
                  component=info.get("component") or "", order_id=str(oid))

    legacy = _load_json(os.path.join(BASE, "order_apps.json"))
    for oid, info in legacy.items():
        if isinstance(info, dict):
            merge(info.get("package") or "", title=info.get("query") or "",
                  component=info.get("component") or "", order_id=str(oid))

    return result


def play_title(package: str, timeout: float = 5.0) -> str:
    """Название приложения из Google Play. Пустая строка, если не найдено."""
    if package in _PLAY_CACHE:
        return _PLAY_CACHE[package]
    title = ""
    url = ("https://play.google.com/store/apps/details?id="
           + urllib.parse.quote(package) + "&hl=ru&gl=RU")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            page = response.read().decode("utf-8", "ignore")
        match = re.search(r'"name"\s*:\s*"([^"]{2,120})"', page)
        if match:
            title = html_lib.unescape(match.group(1)).strip()
        if title.lower() in {"google play", "приложение не найдено"}:
            title = ""
    except Exception:
        title = ""
    _PLAY_CACHE[package] = title
    return title


def describe(package: str, meta: dict | None = None) -> dict:
    """Локальные данные для строки списка без обращения к сети."""
    meta = meta or {}
    title = (meta.get("title") or "").strip() or package_title(package)
    return {
        "package": package,
        "title": title,
        "component": meta.get("component") or "",
        "order_ids": list(meta.get("order_ids") or []),
        "icon": local_icon(package),
        "digest": icon_digest(package),
    }
