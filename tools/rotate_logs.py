"""Ротация логов перед запуском цикла.

Вызывается из run_cycle.bat: если resume.log/hunter2.log больше лимита
из panel_settings.json, они переносятся в logs/ с меткой времени.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core.config import load_settings
from core.housekeeping import rotate_all


def main():
    settings = load_settings()
    maintenance = settings.get("maintenance", {}) if isinstance(settings, dict) else {}
    try:
        max_mb = float(maintenance.get("log_max_mb", 2))
    except (TypeError, ValueError):
        max_mb = 2.0
    try:
        keep = max(1, int(maintenance.get("log_keep", 10)))
    except (TypeError, ValueError):
        keep = 10
    moved = rotate_all(max_mb=max_mb, keep=keep)
    for path in moved:
        print(f"log rotated: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
