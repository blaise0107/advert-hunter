# -*- coding: utf-8 -*-
"""Временный чёрный список заданий AdvertApp по ID.

Задания, которые не берутся (не открылась страница, не нашлась кнопка
магазина, не совпал номер), попадают сюда на ограниченное время. Через
TTL они автоматически возвращаются в работу — это не постоянный запрет.

Файл читают и бот (hunter2.py), и панель, поэтому правки из интерфейса
подхватываются без перезапуска цикла.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta

from core.config import BASE

PATH = os.path.join(BASE, "order_blacklist.json")
DEFAULT_TTL_MINUTES = 360


def _now() -> datetime:
    return datetime.now()


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _empty() -> dict:
    return {"version": 1, "orders": {}}


def load(path: str | None = None) -> dict:
    """Читает файл ЧС; битый файл считается пустым."""
    path = path or PATH
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    orders = data.get("orders")
    if not isinstance(orders, dict):
        data["orders"] = {}
    data.setdefault("version", 1)
    return data


def _save(data: dict, path: str | None = None) -> str:
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".order_blacklist_", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def active(path: str | None = None) -> dict[str, dict]:
    """Действующие записи: просроченные удаляются из файла."""
    data = load(path)
    now = _now()
    alive: dict[str, dict] = {}
    changed = False
    for order, entry in list((data.get("orders") or {}).items()):
        if not isinstance(entry, dict):
            changed = True
            continue
        expires = _parse_stamp(entry.get("expires"))
        if expires is not None and expires <= now:
            changed = True
            continue
        alive[str(order)] = entry
    if changed:
        data["orders"] = alive
        try:
            _save(data, path)
        except OSError:
            pass
    return alive


def active_ids(path: str | None = None) -> set[str]:
    return set(active(path).keys())


def is_blocked(order: str, path: str | None = None) -> bool:
    return str(order) in active(path)


def add(order: str, minutes: int | None = None, reason: str = "",
        path: str | None = None) -> dict:
    """Добавляет ID в ЧС. Повторное добавление продлевает срок."""
    order = str(order).strip()
    if not order:
        raise ValueError("пустой ID задания")
    ttl = int(minutes if minutes is not None else DEFAULT_TTL_MINUTES)
    ttl = max(1, ttl)
    now = _now()
    data = load(path)
    orders = data.setdefault("orders", {})
    entry = {
        "added": now.isoformat(timespec="seconds"),
        "expires": (now + timedelta(minutes=ttl)).isoformat(timespec="seconds"),
        "reason": str(reason or ""),
    }
    orders[order] = entry
    _save(data, path)
    return entry


def remove(order: str, path: str | None = None) -> bool:
    data = load(path)
    orders = data.get("orders") or {}
    if str(order) not in orders:
        return False
    del orders[str(order)]
    data["orders"] = orders
    _save(data, path)
    return True


def clear(path: str | None = None) -> int:
    data = load(path)
    count = len(data.get("orders") or {})
    data["orders"] = {}
    _save(data, path)
    return count


def remaining_minutes(order: str, path: str | None = None) -> float | None:
    entry = active(path).get(str(order))
    if not entry:
        return None
    expires = _parse_stamp(entry.get("expires"))
    if expires is None:
        return None
    return max(0.0, (expires - _now()).total_seconds() / 60.0)
