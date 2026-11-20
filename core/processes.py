"""Управление процессами бота AdvertApp.

Все дочерние процессы запускаются без консольного окна.
Чужое окно PowerShell не трогаем: фильтр только по python/pythonw
и по имени скрипта бота.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE / "logs"
LOGS_DIR.mkdir(exist_ok=True)
# Явный путь к Python можно задать переменной окружения ADVERT_PYTHON.
_PY = os.environ.get("ADVERT_PYTHON")
PYTHON = Path(_PY) if _PY else Path(r"C:\Users\RZE\AppData\Local\Programs\Python\Python314\python.exe")
PYTHONW = PYTHON.with_name("pythonw.exe")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BOT_PATTERN = r"farm\.py|resume_after_works|tasks\.py|hunter2\.py|active_loop\.py"
PANEL_PATTERN = r"advert_panel\.py"


def _powershell(script: str, timeout: int = 20) -> str:
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _pids(pattern: str) -> list[int]:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    out = _powershell(script)
    result = []
    for token in out.split():
        try:
            result.append(int(token))
        except ValueError:
            pass
    return sorted(set(result))


def pid_alive(pid) -> bool:
    """Быстрая проверка PID без запуска PowerShell (Windows)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or os.name != "nt":
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def bot_pids() -> list[int]:
    return _pids(BOT_PATTERN)


def panel_pids() -> list[int]:
    return _pids(PANEL_PATTERN)


def is_running() -> bool:
    return bool(bot_pids())


def _kill(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15,
        )
    except Exception:
        pass


def farm_status() -> dict:
    """Свежий farm_status.json или пустой словарь."""
    path = BASE / "farm_status.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def request_stop() -> None:
    """Мягкая остановка: ферма/бот сами завершают текущий круг."""
    try:
        (BASE / "hunter.stop").write_text("stop\n", encoding="utf-8")
    except OSError:
        pass


def stop_bot(graceful: bool = True, wait_seconds: float = 12.0) -> int:
    """Останавливает ферму и всех работников.

    Сначала просим завершиться мягко (hunter.stop), чтобы ферма успела
    вернуть оригинальные wm size/density. Если за wait_seconds не вышло —
    добиваем дерево процессов.
    """
    pids = bot_pids()
    if not pids:
        return 0
    if graceful:
        request_stop()
        deadline = time.time() + max(0.0, wait_seconds)
        while time.time() < deadline:
            if not bot_pids():
                return len(pids)
            time.sleep(0.4)
    for pid in bot_pids():
        _kill(pid)
    return len(pids)


def _python() -> str:
    return str(PYTHON) if PYTHON.exists() else sys.executable


def _log_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    # Имена без подкаталога считаем глобальными логами проекта.
    if path.parent == Path("."):
        path = LOGS_DIR / path.name
    else:
        path = BASE / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def start_process(args: list[str], log_name: str) -> int:
    """Запускает python-скрипт в фоне, возвращает PID."""
    log_path = _log_path(log_name)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    out = open(log_path, "a", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [_python(), "-u", *args], cwd=str(BASE), env=env,
            stdout=out, stderr=out, stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    finally:
        out.close()
    return proc.pid


def start_cycle() -> int:
    # Ферма сама запускает по процессу на каждый онлайн-телефон.
    return start_process(["farm.py"], "farm_console.log")


def restart_cycle() -> int:
    stop_bot()
    time.sleep(1.2)
    return start_cycle()


def run_hunter(duration: int = 120) -> int:
    return start_process(["hunter2.py", "--duration", str(duration)], "panel_hunter.log")


def run_tasks(until: str = "08:30") -> int:
    return start_process(
        ["tasks.py", "--until", until, "--max-tasks", "0"],
        "panel_tasks.log",
    )


def run_active_loop() -> int:
    return start_process(["active_loop.py"], "panel_active_loop.log")


def force_stop_app() -> None:
    from core.device import force_stop
    force_stop()


def restart_app() -> None:
    from core.device import start_app
    start_app()


def read_tail(name: str, count: int = 200) -> list[str]:
    path = Path(name)
    candidates = [path] if path.is_absolute() else [_log_path(name), BASE / name]
    target = next((p for p in candidates if p.exists()), candidates[0])
    if not target.exists():
        return []
    try:
        with open(target, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()[-count:]
    except OSError:
        return []


def tail_text(name: str, count: int = 200) -> str:
    return "\n".join(read_tail(name, count))


def phase() -> str:
    """Фаза фермы (если запущена) или одиночного цикла."""
    status = farm_status()
    if status.get("running"):
        working = [d for d in status.get("devices", []) if d.get("state") == "работает"]
        if working:
            phases = {str(d.get("phase") or "—") for d in working}
            return f"Ферма: {len(working)}/{status.get('max_parallel', '?')} · " + ", ".join(sorted(phases))
        return f"Ферма: ожидание ({status.get('active', 0)} активно)"
    lines = read_tail("resume.log", 400)
    for line in reversed(lines):
        low = line.lower()
        if line.startswith("---"):
            continue
        if "выполняю «активные»" in low or "активных заказов" in low:
            return "Активные"
        if "цикл: «активные»" in low:
            return "Активные"
        if "hunter2 stop" in low:
            return "Активные"
        if "hunter2 start" in low:
            return "Новые"
        if "tasks.py" in low:
            return "Активные"
        if "цикл:" in low:
            return "Новые"
    return "—"
