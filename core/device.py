#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADB-обёртки для Realme fab1701. Только чтение состояния и безопасные действия."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Iterable

from core.config import BASE

SERIAL = "fab1701"
APP = "com.advert.mobileearnings.app"
# AdvertApp встречается в нескольких сборках: старая и новая (TeamDev).
APP_PACKAGES = (
    "com.advert.mobileearnings.app",
    "com.teamdev.reward",
    "ru.teamdev.reward",
)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_ADB_CANDIDATES = [
    r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    shutil.which("adb") or "",
]


def _adb_path() -> str:
    for path in _ADB_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return "adb"


ADB = _adb_path()


def adb(*args: str, timeout: int = 30, binary: bool = False):
    """Вызов adb -s SERIAL. Возвращает stdout (str) или bytes при binary=True."""
    cmd = [ADB, "-s", SERIAL, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        if binary:
            return result.stdout
        return (result.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001 - вызывающий код показывает текст
        return b"" if binary else f"ERROR: {exc}"


def shell(*args: str, timeout: int = 30) -> str:
    return adb("shell", *args, timeout=timeout)


def state() -> str:
    return adb("get-state", timeout=10)


def is_online() -> bool:
    return state() == "device"


def tap(x: int, y: int) -> str:
    return shell("input", "tap", str(x), str(y), timeout=15)


def swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 250) -> str:
    return shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration), timeout=20)


def keyevent(code: int | str) -> str:
    return shell("input", "keyevent", str(code), timeout=15)


def wake() -> str:
    return keyevent("KEYCODE_WAKEUP")


def unlock() -> str:
    return shell("wm", "dismiss-keyguard", timeout=15)


def density() -> str:
    """Возвращает фактическую override-плотность (408), иначе физическую."""
    text = shell("wm", "density", timeout=15)
    for marker in ("Override density:", "Physical density:"):
        for line in text.splitlines():
            if marker in line:
                return line.split(":", 1)[1].strip()
    return "—"


def android_version() -> str:
    return shell("getprop", "ro.build.version.release", timeout=10)


def serialno() -> str:
    return shell("getprop", "ro.serialno", timeout=10)


def third_party_packages() -> list[str]:
    text = shell("pm", "list", "packages", "-3", timeout=30)
    return [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("package:")]


def package_installed(package: str) -> bool:
    if not package:
        return False
    return package in third_party_packages()


def screencap(path: str) -> str:
    """Снимок экрана в PNG. Возвращает путь или текст ошибки."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = adb("exec-out", "screencap", "-p", timeout=30, binary=True)
    if not data:
        return "ERROR: пустой ответ adb screencap"
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def app_running(package: str | None = None) -> bool:
    """Запущен ли AdvertApp (любая из сборок, если пакет не указан)."""
    packages = (package,) if package else APP_PACKAGES
    for pkg in packages:
        text = shell("pidof", pkg, timeout=10)
        if text.strip() and not text.startswith("ERROR"):
            return True
    return False


def force_stop(package: str | None = None) -> str:
    packages = (package,) if package else APP_PACKAGES
    out = ""
    for pkg in packages:
        out = shell("am", "force-stop", pkg, timeout=20)
    return out


def main_activity(package: str = APP) -> str:
    """Главная Activity пакета: по ней запускаем приложение."""
    text = shell("cmd", "package", "resolve-activity", "--brief", package, timeout=20)
    for line in text.splitlines():
        line = line.strip()
        if "/" in line and not line.lower().startswith("no activity"):
            return line
    return f"{package}/.MainActivity"


def start_app(package: str | None = None) -> str:
    """Запускает AdvertApp. Без пакета — ту сборку, что установлена и включена."""
    if package is None:
        package = APP
        installed = set(third_party_packages())
        disabled = set()
        for line in shell("pm", "list", "packages", "-d", timeout=30).splitlines():
            if line.startswith("package:"):
                disabled.add(line.split(":", 1)[1].strip())
        enabled = [p for p in APP_PACKAGES if p in installed and p not in disabled]
        current = current_activity()
        for candidate in enabled:
            if candidate in current:
                package = candidate
                break
        else:
            package = enabled[0] if enabled else APP
    return shell("am", "start", "-n", main_activity(package), timeout=20)


def app_in_foreground() -> bool:
    """Открыт ли сейчас AdvertApp любой из сборок."""
    current = current_activity()
    return any(pkg in current for pkg in APP_PACKAGES)


def open_url(url: str) -> str:
    return shell("am", "start", "-a", "android.intent.action.VIEW", "-d", url, timeout=20)


def current_activity() -> str:
    text = shell("dumpsys", "activity", "activities", timeout=30)
    for line in text.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            return line.strip()
    return ""


def packages_matching(needles: Iterable[str]) -> list[str]:
    needles = [n.lower() for n in needles if n]
    return [p for p in third_party_packages() if any(n in p.lower() for n in needles)]
