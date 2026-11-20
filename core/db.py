# -*- coding: utf-8 -*-
"""База приложений v2.

Одно приложение может иметь несколько ID заданий и несколько иконок.
ID задания считается изменяемым: новые ID добавляются, старые не удаляются.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime

from .config import (
    BASE, DB_PATH, ICON_CACHE, backup_file, load_settings,
)

LEGACY_KNOWN = os.path.join(BASE, "known_apps.json")
LEGACY_ORDERS = os.path.join(BASE, "order_apps.json")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_db() -> dict:
    return {
        "version": 2,
        "updated_at": now_iso(),
        "apps": [],
        "shared_icons": {},
        "unidentified": [],
    }


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9а-яё._-]+", "-", text, flags=re.IGNORECASE)
    return text.strip("-") or "app"


def package_title(package: str) -> str:
    if not package:
        return "Без названия"
    tail = package.split(".")[-1]
    return tail.replace("_", " ").strip() or package


def empty_app(package: str = "", title: str = "") -> dict:
    package = (package or "").strip()
    app_id = _slug(package) if package else _slug(title)
    return {
        "id": app_id,
        "package": package,
        "component": "",
        "title": title or package_title(package),
        "aliases": [],
        "descriptions": [],
        "order_ids": [],
        "icons": [],
        "markets": ["google", "rustore", "huawei", "xiaomi"],
        "exclude_keywords": [],
        "notes": "",
        "enabled": True,
        "verified": False,
        "stats": {
            "seen": 0,
            "matched": 0,
            "installed": 0,
            "completed": 0,
            "last_seen": "",
        },
    }


def _normalize_app(app: dict) -> dict:
    base = empty_app(app.get("package", ""), app.get("title", ""))
    base.update({k: v for k, v in app.items() if k not in ("stats",)})
    stats = app.get("stats") or {}
    base["stats"] = {**empty_app()["stats"], **stats}
    for key in ("aliases", "descriptions", "order_ids", "icons",
                "markets", "exclude_keywords"):
        value = base.get(key)
        if value is None:
            base[key] = []
        elif not isinstance(value, list):
            base[key] = [value]
    # order_ids могут быть строками или объектами — приводим к строкам
    ids = []
    for item in base["order_ids"]:
        if isinstance(item, dict):
            item = item.get("id")
        if item is not None and str(item).strip():
            ids.append(str(item).strip())
    base["order_ids"] = list(dict.fromkeys(ids))
    icons = []
    for icon in base["icons"]:
        if isinstance(icon, str):
            icon = {"hash": icon}
        if not isinstance(icon, dict) or not icon.get("hash"):
            continue
        icons.append({
            "hash": str(icon["hash"]),
            "file": icon.get("file") or "",
            "source": icon.get("source") or "legacy",
            "added_at": icon.get("added_at") or now_iso(),
            "primary": bool(icon.get("primary")),
        })
    if icons and not any(i["primary"] for i in icons):
        icons[0]["primary"] = True
    base["icons"] = icons
    return base


def normalize_db(db: dict) -> dict:
    base = empty_db()
    base.update(db or {})
    base["version"] = 2
    base["apps"] = [_normalize_app(a) for a in (base.get("apps") or [])]
    shared = base.get("shared_icons") or {}
    base["shared_icons"] = {
        str(k): list(dict.fromkeys(str(x) for x in (v or [])))
        for k, v in shared.items()
    }
    unidentified = []
    for item in base.get("unidentified") or []:
        if isinstance(item, dict) and item.get("order_id"):
            unidentified.append(item)
    base["unidentified"] = unidentified
    return base


def load_db(path: str | None = None, migrate: bool = True) -> dict:
    path = path or DB_PATH
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 2:
                return normalize_db(data)
        except Exception:
            pass
    if migrate:
        return migrate_legacy(path=path)
    return empty_db()


def save_db(db: dict, path: str | None = None, backup: bool = True) -> str:
    path = path or DB_PATH
    if backup and os.path.exists(path):
        backup_file(path)
    data = normalize_db(copy.deepcopy(db))
    data["updated_at"] = now_iso()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".panel_apps_", suffix=".json",
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


def find_app(db: dict, *, app_id: str | None = None,
             package: str | None = None,
             order_id: str | None = None) -> dict | None:
    apps = db.get("apps") or []
    if app_id:
        for app in apps:
            if app.get("id") == app_id:
                return app
    if package:
        package = package.strip()
        for app in apps:
            if app.get("package") == package:
                return app
    if order_id:
        oid = str(order_id).strip()
        for app in apps:
            if oid in (app.get("order_ids") or []):
                return app
    return None


def upsert_app(db: dict, app: dict) -> dict:
    normalized = _normalize_app(app)
    for i, old in enumerate(db["apps"]):
        if old.get("id") == normalized["id"] or (
                normalized.get("package") and old.get("package") == normalized["package"]):
            normalized["id"] = old["id"]
            db["apps"][i] = normalized
            _refresh_shared(db)
            return normalized
    db["apps"].append(normalized)
    _refresh_shared(db)
    return normalized


def add_order_id(db: dict, app_id: str, order_id: str,
                 price: str | None = None, title: str | None = None) -> bool:
    app = find_app(db, app_id=app_id)
    if not app or not str(order_id).strip():
        return False
    oid = str(order_id).strip()
    if oid not in app["order_ids"]:
        app["order_ids"].append(oid)
        app["stats"]["seen"] = int(app["stats"].get("seen", 0)) + 1
    app["stats"]["last_seen"] = now_iso()
    if title and not app.get("title"):
        app["title"] = title
    return True


def add_icon(db: dict, app_id: str, icon_hash: str, file: str = "",
             source: str = "import") -> bool:
    app = find_app(db, app_id=app_id)
    if not app or not icon_hash:
        return False
    for icon in app["icons"]:
        if icon.get("hash") == icon_hash:
            if file and not icon.get("file"):
                icon["file"] = file
            return False
    app["icons"].append({
        "hash": icon_hash,
        "file": file,
        "source": source,
        "added_at": now_iso(),
        "primary": not bool(app["icons"]),
    })
    _refresh_shared(db)
    return True


def remove_icon(db: dict, app_id: str, icon_hash: str) -> bool:
    app = find_app(db, app_id=app_id)
    if not app:
        return False
    before = len(app["icons"])
    app["icons"] = [i for i in app["icons"] if i.get("hash") != icon_hash]
    if app["icons"] and not any(i.get("primary") for i in app["icons"]):
        app["icons"][0]["primary"] = True
    _refresh_shared(db)
    return len(app["icons"]) != before


def _refresh_shared(db: dict) -> None:
    shared: dict[str, list[str]] = {}
    for app in db.get("apps") or []:
        for icon in app.get("icons") or []:
            h = icon.get("hash")
            if h:
                shared.setdefault(h, [])
                if app["id"] not in shared[h]:
                    shared[h].append(app["id"])
    db["shared_icons"] = {h: ids for h, ids in shared.items() if len(ids) > 1}


def record_unidentified(db: dict, order_id: str, title: str = "",
                        price: str = "", icon_hash: str = "",
                        reason: str = "") -> None:
    oid = str(order_id).strip()
    if not oid:
        return
    for item in db["unidentified"]:
        if item.get("order_id") == oid:
            item.update({
                "title": title or item.get("title", ""),
                "price": price or item.get("price", ""),
                "icon_hash": icon_hash or item.get("icon_hash", ""),
                "reason": reason or item.get("reason", ""),
                "at": now_iso(),
            })
            return
    db["unidentified"].append({
        "order_id": oid,
        "title": title,
        "price": price,
        "icon_hash": icon_hash,
        "reason": reason,
        "at": now_iso(),
    })


def all_icon_entries(db: dict):
    """Итерирует (app, icon) по всем иконкам базы."""
    for app in db.get("apps") or []:
        for icon in app.get("icons") or []:
            yield app, icon


def icon_path(icon: dict) -> str:
    file = (icon or {}).get("file") or ""
    if not file:
        return ""
    return file if os.path.isabs(file) else os.path.join(BASE, file)


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def migrate_legacy(path: str | None = None, known_path: str | None = None,
                   orders_path: str | None = None) -> dict:
    """Создаёт panel_apps.json из known_apps.json и order_apps.json."""
    path = path or DB_PATH
    known_path = known_path or LEGACY_KNOWN
    orders_path = orders_path or LEGACY_ORDERS
    known = _load_json(known_path)
    legacy = _load_json(orders_path)
    db = empty_db()

    orders = known.get("orders") or {}
    if not orders and legacy:
        orders = legacy

    for oid, info in orders.items():
        if not isinstance(info, dict):
            continue
        package = (info.get("package") or "").strip()
        if not package:
            continue
        app = find_app(db, package=package)
        if not app:
            app = empty_app(package, info.get("query") or package_title(package))
            app["component"] = info.get("component") or ""
            app["verified"] = bool(info.get("component"))
            db["apps"].append(app)
        elif info.get("component") and not app.get("component"):
            app["component"] = info["component"]
        if str(oid) not in app["order_ids"]:
            app["order_ids"].append(str(oid))
        app["stats"]["seen"] += 1
        app["stats"]["last_seen"] = info.get("at") or now_iso()

    icon_dir = ICON_CACHE
    for item in known.get("icons") or []:
        if not isinstance(item, dict):
            continue
        package = (item.get("package") or "").strip()
        h = (item.get("hash") or "").strip()
        if not package or not h:
            continue
        app = find_app(db, package=package)
        if not app:
            continue
        file = ""
        candidate = os.path.join(icon_dir, package + ".png")
        if os.path.exists(candidate):
            file = os.path.relpath(candidate, BASE)
        if not any(i.get("hash") == h for i in app["icons"]):
            app["icons"].append({
                "hash": h,
                "file": file,
                "source": "known_apps.json",
                "added_at": now_iso(),
                "primary": not bool(app["icons"]),
            })

    for oid, info in legacy.items():
        if not isinstance(info, dict):
            continue
        package = (info.get("package") or "").strip()
        h = (info.get("icon") or "").strip()
        if not package or not h:
            continue
        app = find_app(db, package=package)
        if app and not any(i.get("hash") == h for i in app["icons"]):
            file = os.path.join(icon_dir, package + ".png")
            app["icons"].append({
                "hash": h,
                "file": os.path.relpath(file, BASE) if os.path.exists(file) else "",
                "source": "order_apps.json",
                "added_at": now_iso(),
                "primary": not bool(app["icons"]),
            })

    _refresh_shared(db)
    if os.path.exists(path):
        backup_file(path)
    save_db(db, path=path, backup=False)
    return db


def export_db(db: dict, path: str) -> str:
    data = normalize_db(copy.deepcopy(db))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def import_db(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ожидается JSON-объект")
    return normalize_db(data)


def summary(db: dict) -> dict:
    apps = db.get("apps") or []
    icons = sum(len(a.get("icons") or []) for a in apps)
    ids = sum(len(a.get("order_ids") or []) for a in apps)
    return {
        "apps": len(apps),
        "enabled": sum(1 for a in apps if a.get("enabled", True)),
        "verified": sum(1 for a in apps if a.get("verified")),
        "icons": icons,
        "order_ids": ids,
        "shared_icons": len(db.get("shared_icons") or {}),
        "unidentified": len(db.get("unidentified") or []),
        "settings": load_settings(),
    }
