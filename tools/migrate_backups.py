"""Перенос бэкапов из старого аккаунта Swift Backup в новый.

Зачем: в старом аккаунте 24 ГБ и 438 приложений, в новом пока единицы.
Если Swift Backup индексирует папки бэкапов сам, достаточно скопировать
каталоги приложений внутри /sdcard — без установки и повторного бэкапа.

Использование:
    python tools/migrate_backups.py --test com.example.seofast
    python tools/migrate_backups.py --copy-all [--dry-run]
    python tools/migrate_backups.py --report
"""
import argparse
import subprocess
import sys

ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"
ROOT = "/sdcard/SwiftBackup/accounts"
OLD = "8690a48a4fcc72f1"
# Активный аккаунт, в который пишет текущий Swift Backup (проверено по свежим
# бэкапам winline/minigame). ff148abdd59a9632 — предыдущий, тоже читается.
NEW = "d6b2cb337fac68e9"
LOCAL = "backups/apps/local"


def adb(*args, timeout=600):
    return subprocess.run([ADB, *args], capture_output=True, timeout=timeout)


def shell(cmd, timeout=600):
    return adb("shell", cmd, timeout=timeout).stdout.decode("utf-8", "ignore")


def packages(account):
    out = shell(f"ls -1 {ROOT}/{account}/{LOCAL} 2>/dev/null")
    return sorted(x.strip() for x in out.splitlines()
                  if x.strip() and not x.strip().endswith(":"))


def missing():
    old = set(packages(OLD))
    new = set(packages(NEW))
    return sorted(old - new)


def report():
    old, new = packages(OLD), packages(NEW)
    print(f"старый аккаунт: {len(old)} приложений")
    print(f"новый аккаунт:  {len(new)} приложений")
    todo = sorted(set(old) - set(new))
    print(f"перенести:      {len(todo)}")
    for pkg in todo[:10]:
        print("  ", pkg)
    if len(todo) > 10:
        print(f"   ... ещё {len(todo) - 10}")
    return todo


def copy_package(pkg, dry_run=False):
    src = f"{ROOT}/{OLD}/{LOCAL}/{pkg}"
    dst = f"{ROOT}/{NEW}/{LOCAL}/"
    exists = shell(f"ls -d {dst}{pkg} 2>/dev/null").strip()
    if exists:
        print(f"  {pkg}: уже есть в новом аккаунте — пропуск")
        return True
    print(f"  {pkg}: копирую {src} -> {dst}")
    if dry_run:
        return True
    result = adb("shell", f"cp -a {src} {dst}", timeout=1800)
    text = (result.stdout + result.stderr).decode("utf-8", "ignore").strip()
    if text:
        print("   ", text.splitlines()[-1])
    if result.returncode != 0:
        return False
    check = shell(f"ls {dst}{pkg} 2>/dev/null").strip()
    return bool(check)


def main():
    parser = argparse.ArgumentParser(description="Перенос бэкапов Swift Backup")
    parser.add_argument("--report", action="store_true", help="Показать, что переносить")
    parser.add_argument("--test", help="Скопировать один пакет для проверки")
    parser.add_argument("--copy-all", action="store_true", help="Скопировать все отсутствующие")
    parser.add_argument("--dry-run", action="store_true", help="Только показать")
    args = parser.parse_args()

    if args.report:
        report()
        return 0
    if args.test:
        return 0 if copy_package(args.test, args.dry_run) else 1
    if args.copy_all:
        todo = missing()
        print(f"к переносу: {len(todo)}")
        done = failed = 0
        for pkg in todo:
            if copy_package(pkg, args.dry_run):
                done += 1
            else:
                failed += 1
                print(f"  {pkg}: ОШИБКА копирования")
        print(f"готово: {done}, ошибок: {failed}")
        return 0 if failed == 0 else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
