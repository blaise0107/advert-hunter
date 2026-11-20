#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Список ADB-устройств и команды для конкретного серийного номера.

Бот пока работает с одним телефоном (core.device.SERIAL), но панель
должна видеть все подключённые устройства: к ПК могут приходить десятки
телефонов. Здесь нет фоновых процессов — только перечисление и разовые
команды, поэтому список из 50 устройств стоит один вызов adb.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from core import device

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class DeviceInfo:
    """Одна строка из `adb devices -l`."""

    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""

    @property
    def online(self) -> bool:
        return self.state == "device"

    @property
    def is_bot(self) -> bool:
        return self.serial == device.SERIAL

    @property
    def name(self) -> str:
        return self.model or self.product or self.device or "Android"

    @property
    def label(self) -> str:
        suffix = " · бот" if self.is_bot else ""
        return f"{self.serial} · {self.name}{suffix}"

    @property
    def state_ru(self) -> str:
        return {
            "device": "онлайн",
            "offline": "офлайн",
            "unauthorized": "нет разрешения",
            "authorizing": "ожидает разрешения",
            "connecting": "подключение",
            "recovery": "recovery",
            "sideload": "sideload",
            "bootloader": "bootloader",
        }.get(self.state, self.state)


def adb_serial(serial: str, *args: str, timeout: int = 15, binary: bool = False):
    """Вызов adb для конкретного устройства, а не для глобального SERIAL."""
    cmd = [device.ADB, "-s", serial, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001 - вызывающий код показывает текст
        return b"" if binary else f"ERROR: {exc}"
    if binary:
        return result.stdout or b""
    return (result.stdout or b"").decode("utf-8", "replace").strip()


def list_devices() -> list[DeviceInfo]:
    """Возвращает все устройства из `adb devices -l` (онлайн и офлайн).

    Один вызов adb на весь список: при десятках телефонов это дешевле,
    чем по одному запросу на устройство.
    """
    try:
        result = subprocess.run(
            [device.ADB, "devices", "-l"],
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"adb devices: {exc}") from exc

    text = (result.stdout or b"").decode("utf-8", "replace")
    items: list[DeviceInfo] = []
    for raw in text.splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith("List of devices")
            or line.startswith("*")
        ):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                meta[key] = value
        items.append(
            DeviceInfo(
                serial=serial,
                state=state,
                model=meta.get("model", ""),
                product=meta.get("product", ""),
                device=meta.get("device", ""),
                transport_id=meta.get("transport_id", ""),
            )
        )
    items.sort(key=lambda d: (not d.online, not d.is_bot, d.serial.lower()))
    return items


def online_devices() -> list[DeviceInfo]:
    return [d for d in list_devices() if d.online]
