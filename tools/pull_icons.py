"""Обновляет локальный кэш иконок Swift Backup.

Swift Backup держит иконки приложений в /sdcard/SwiftBackup/icon_cache.
tasks.py сравнивает иконку задания с этим кэшем, поэтому перед проходами
полезно подтянуть новые PNG.

Запуск:
    python tools/pull_icons.py
"""
import os
import shutil
import subprocess

ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "icon_cache")
TMP = os.path.join(os.environ.get("TEMP", r"C:\Users\RZE\AppData\Local\Temp"), "opencode", "icon_cache_new")


def main():
    os.makedirs(TMP, exist_ok=True)
    subprocess.run([ADB, "pull", "/sdcard/SwiftBackup/icon_cache/.", TMP],
                   check=False, capture_output=True)
    os.makedirs(CACHE, exist_ok=True)
    added = 0
    for name in os.listdir(TMP):
        if not name.lower().endswith(".png"):
            continue
        dst = os.path.join(CACHE, name)
        if os.path.exists(dst):
            continue
        shutil.copy2(os.path.join(TMP, name), dst)
        added += 1
    print(f"новых иконок: {added}, всего: {len(os.listdir(CACHE))}")


if __name__ == "__main__":
    main()
