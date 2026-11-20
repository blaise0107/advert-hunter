#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Приведение экрана телефона к эталону бота: 1080×2412, density 408.

Бот и OCR рассчитаны на одну геометрию. Чтобы одно и то же задание
работало на разных телефонах (Samsung, Xiaomi, Realme и т.д.), каждому
устройству перед циклом выставляется логическое разрешение 1080×2412 и
плотность 408. Оригинальные значения сохраняются в runs/<serial>/display.json,
чтобы потом вернуть экран как было.

Модуль не зависит от hunter2: только adb и файлы.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime

from core import devices

REF_SIZE = "1080x2412"
REF_DENSITY = 408

_SIZE_RE = re.compile(r"(Physical|Override)\s+size:\s*(\d+x\d+)", re.IGNORECASE)
_DENSITY_RE = re.compile(r"(Physical|Override)\s+density:\s*(\d+)", re.IGNORECASE)


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".display_", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def state_path(run_dir: str) -> str:
    return os.path.join(run_dir, "display.json")


def query(serial: str) -> dict:
    """Текущие size/density устройства."""
    size_text = devices.adb_serial(serial, "shell", "wm", "size", timeout=15)
    density_text = devices.adb_serial(serial, "shell", "wm", "density", timeout=15)
    if "Physical size" not in size_text:
        raise RuntimeError(f"{serial}: не удалось прочитать wm size: {size_text!r}")
    if "density" not in density_text.lower():
        raise RuntimeError(f"{serial}: не удалось прочитать wm density: {density_text!r}")

    size_match = _SIZE_RE.findall(size_text)
    density_match = _DENSITY_RE.findall(density_text)
    sizes = {key.lower(): value for key, value in size_match}
    densities = {key.lower(): value for key, value in density_match}
    return {
        "physical_size": sizes.get("physical", ""),
        "override_size": sizes.get("override", ""),
        "physical_density": densities.get("physical", ""),
        "override_density": densities.get("override", ""),
        "size_text": size_text,
        "density_text": density_text,
    }


def _load_state(run_dir: str) -> dict:
    path = state_path(run_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def prepare(serial: str, run_dir: str) -> dict:
    """Выставляет эталонную геометрию и портрет. Идемпотентно.

    Оригинал сохраняется один раз: повторные запуски не затирают его
    текущим (уже нормализованным) состоянием.
    """
    os.makedirs(run_dir, exist_ok=True)
    current = query(serial)
    state = _load_state(run_dir)
    if not state.get("original"):
        state = {
            "serial": serial,
            "original": {
                "physical_size": current["physical_size"],
                "override_size": current["override_size"],
                "physical_density": current["physical_density"],
                "override_density": current["override_density"],
            },
            "saved_at": _stamp(),
        }
        _atomic_json(state_path(run_dir), state)

    # На Realme 1080×2412 — физический размер, и `wm size 1080x2412`
    # не создаёт строку Override size. Поэтому сравниваем эффективное значение.
    effective_size = current["override_size"] or current["physical_size"]
    effective_density = current["override_density"] or current["physical_density"]
    if effective_size != REF_SIZE:
        devices.adb_serial(serial, "shell", "wm", "size", REF_SIZE, timeout=20)
    if effective_density != str(REF_DENSITY):
        devices.adb_serial(serial, "shell", "wm", "density", str(REF_DENSITY), timeout=20)

    # Портрет: бот рассчитан на вертикальный экран.
    devices.adb_serial(serial, "shell", "settings", "put", "system",
                       "accelerometer_rotation", "0", timeout=15)
    devices.adb_serial(serial, "shell", "settings", "put", "system",
                       "user_rotation", "0", timeout=15)
    devices.adb_serial(serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP", timeout=15)
    devices.adb_serial(serial, "shell", "wm", "dismiss-keyguard", timeout=15)
    time.sleep(0.8)

    after = query(serial)
    after_size = after["override_size"] or after["physical_size"]
    after_density = after["override_density"] or after["physical_density"]
    if after_size != REF_SIZE or after_density != str(REF_DENSITY):
        raise RuntimeError(
            f"{serial}: не удалось выставить {REF_SIZE}/{REF_DENSITY}; "
            f"сейчас size={after_size}, density={after_density}")
    state["last_prepared_at"] = _stamp()
    state["last_prepared"] = {
        "size": after_size,
        "density": after_density,
    }
    _atomic_json(state_path(run_dir), state)
    return after


def restore(serial: str, run_dir: str) -> bool:
    """Возвращает оригинальные size/density. Возвращает True, если было что менять."""
    state = _load_state(run_dir)
    original = state.get("original") or {}
    if not original:
        return False
    override_size = original.get("override_size") or ""
    override_density = original.get("override_density") or ""
    if override_size:
        devices.adb_serial(serial, "shell", "wm", "size", override_size, timeout=20)
    else:
        devices.adb_serial(serial, "shell", "wm", "size", "reset", timeout=20)
    if override_density:
        devices.adb_serial(serial, "shell", "wm", "density", override_density, timeout=20)
    else:
        devices.adb_serial(serial, "shell", "wm", "density", "reset", timeout=20)
    time.sleep(0.5)
    state["restored_at"] = _stamp()
    _atomic_json(state_path(run_dir), state)
    return True
