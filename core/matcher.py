#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Матчинг заданий AdvertApp с базой приложений.

Приоритет: иконка -> ID задания -> описание/алиасы -> пакет из магазина.
Иконка считается главным признаком, потому что ID задания может меняться.
ID никогда не затирает историю: он только дополняется.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import numpy as np
from PIL import Image

from .config import BASE, ICON_CACHE, load_settings
from .db import (
    add_icon,
    add_order_id,
    all_icon_entries,
    empty_app,
    find_app,
    icon_path,
    save_db,
    upsert_app,
)

ICON_DIR = os.path.join(BASE, "icons")
os.makedirs(ICON_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# признаки иконки


def icon_vector(image: Image.Image) -> np.ndarray | None:
    """Нормализованный вектор 16x16, как в tasks.py."""
    image = image.convert("RGB")
    gray = image.convert("L")
    if np.asarray(gray).std() < 6:
        return None

    array = np.asarray(image).astype("int16")
    corner = array[0, 0]
    mask = np.abs(array - corner).max(axis=2) > 25
    ys, xs = np.where(mask)
    if len(xs) and len(ys):
        image = image.crop((int(xs.min()), int(ys.min()),
                            int(xs.max()) + 1, int(ys.max()) + 1))

    small = image.convert("L").resize((16, 16), Image.LANCZOS)
    data = np.asarray(small).astype("float32")
    data = (data - data.mean()) / (data.std() + 1e-6)
    return data.ravel()


def icon_hash(image: Image.Image) -> str | None:
    """dHash 16x16, как в tasks.py."""
    gray = image.convert("L")
    if np.asarray(gray).std() < 6:
        return None
    small = gray.resize((17, 16), Image.LANCZOS)
    px = np.asarray(small).reshape(-1)
    bits = 0
    for row in range(16):
        base = row * 17
        for col in range(16):
            bits = (bits << 1) | int(px[base + col] > px[base + col + 1])
    return f"{bits:064x}"


def hamming(left: str, right: str) -> int:
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def load_icon(path: str) -> Image.Image | None:
    try:
        with Image.open(path) as im:
            return im.convert("RGB").copy()
    except Exception:
        return None


def _vector_from_path(path: str) -> tuple[np.ndarray | None, str | None]:
    image = load_icon(path)
    if image is None:
        return None, None
    return icon_vector(image), icon_hash(image)


def save_icon(image: Image.Image, package: str, digest: str) -> str:
    """Сохраняет вариант иконки и возвращает путь относительно BASE."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", package)[:80] or "app"
    name = f"{safe}_{digest[:10]}.png"
    full = os.path.join(ICON_DIR, name)
    os.makedirs(ICON_DIR, exist_ok=True)
    image.convert("RGB").save(full, "PNG")
    return os.path.relpath(full, BASE)


# ---------------------------------------------------------------------------
# индекс


@dataclass
class IconEntry:
    app_id: str | None
    package: str
    title: str
    digest: str
    file: str
    vector: np.ndarray
    source: str


class IconIndex:
    """Векторный и dHash-индекс по базе и icon_cache/."""

    def __init__(self, db: dict, settings: dict | None = None):
        self.db = db
        self.settings = settings or load_settings()
        self.entries: list[IconEntry] = []
        self._seen: set[tuple[str, str]] = set()
        self._load_db_icons()
        self._load_cache_icons()
        if self.entries:
            self.matrix = np.stack([e.vector for e in self.entries])
        else:
            self.matrix = np.empty((0, 256), dtype="float32")
        self.shared_hashes = set((db.get("shared_icons") or {}).keys())

    def _add(self, app_id, package, title, digest, file, vector, source):
        key = (package, digest)
        if not package or not digest or key in self._seen:
            return
        self._seen.add(key)
        self.entries.append(IconEntry(app_id, package, title, digest, file,
                                      vector.astype("float32"), source))

    def _load_db_icons(self):
        for app, icon in all_icon_entries(self.db):
            path = icon_path(icon)
            if not path or not os.path.exists(path):
                continue
            vector, digest = _vector_from_path(path)
            if vector is None:
                continue
            self._add(app.get("id"), app.get("package", ""), app.get("title", ""),
                      digest or icon.get("hash"), icon.get("file", ""), vector,
                      "db")

    def _load_cache_icons(self):
        if not os.path.isdir(ICON_CACHE):
            return
        for name in sorted(os.listdir(ICON_CACHE)):
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(ICON_CACHE, name)
            vector, digest = _vector_from_path(path)
            if vector is None:
                continue
            package = name[:-4]
            app = find_app(self.db, package=package)
            self._add(app.get("id") if app else None, package,
                      app.get("title", "") if app else package,
                      digest, os.path.relpath(path, BASE), vector, "cache")

    # ------------------------------------------------------------------

    def match_icon(self, image: Image.Image) -> dict:
        result: dict[str, Any] = {
            "ok": False, "reason": "", "entry": None, "candidates": [],
            "distance": None, "second_distance": None, "digest": None,
            "hamming": None, "shared": False, "ambiguous": False,
        }
        if not self.entries:
            result["reason"] = "база иконок пуста"
            return result

        vector = icon_vector(image)
        digest = icon_hash(image)
        result["digest"] = digest
        if vector is None:
            result["reason"] = "иконка пустая/однотонная"
            return result

        distances = ((self.matrix - vector) ** 2).mean(axis=1)
        order = np.argsort(distances)
        best_i = int(order[0])
        best_d = float(distances[best_i])
        second_d = float(distances[order[1]]) if len(order) > 1 else None
        result["distance"] = best_d
        result["second_distance"] = second_d

        max_distance = float(self.settings.get("match", {}).get(
            "icon_max_distance", 1.0))
        margin = float(self.settings.get("match", {}).get("icon_margin", 0.75))
        if best_d > max_distance:
            result["reason"] = f"нет близкой иконки (MSE {best_d:.3f})"
            return result
        best = self.entries[best_i]
        best_key = best.app_id or best.package
        second_other = None
        second_entry = None
        for i in order[1:]:
            other = self.entries[int(i)]
            if (other.app_id or other.package) != best_key:
                second_other = float(distances[int(i)])
                second_entry = other
                break
        result["second_distance"] = second_other
        # Одинаковая картинка у двух приложений — это общая иконка, а не
        # неоднозначность: точный dHash совпадает, выбор сделает match_order.
        shared_exact = (
            best_d <= 1e-6 and second_entry is not None
            and second_entry.digest == best.digest
        )
        if (second_other is not None and best_d >= second_other * margin
                and not shared_exact):
            result["reason"] = (
                f"иконка неоднозначна (MSE {best_d:.3f} vs {second_other:.3f})")
            result["ambiguous"] = True
            return result

        result["entry"] = best

        # dHash-подтверждение
        max_hamming = int(self.settings.get("match", {}).get(
            "hash_max_hamming", 4))
        hash_hits = []
        for i, entry in enumerate(self.entries):
            if digest and entry.digest:
                dist = hamming(digest, entry.digest)
                if dist <= max_hamming:
                    hash_hits.append((dist, entry))
        hash_hits.sort(key=lambda x: x[0])
        if hash_hits:
            result["hamming"] = hash_hits[0][0]

        # общая иконка у нескольких приложений
        shared = best.digest in self.shared_hashes
        same_hash_apps = {
            e.app_id or e.package for e in self.entries
            if e.digest == best.digest
        }
        if len(same_hash_apps) > 1:
            shared = True
        result["shared"] = shared

        result["ok"] = True
        result["candidates"] = [
            {
                "app_id": self.entries[int(i)].app_id,
                "package": self.entries[int(i)].package,
                "title": self.entries[int(i)].title,
                "distance": float(distances[int(i)]),
                "digest": self.entries[int(i)].digest,
                "source": self.entries[int(i)].source,
            }
            for i in order[:5]
        ]
        return result


# ---------------------------------------------------------------------------
# текстовый матчинг


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_text(db: dict, text: str) -> tuple[dict | None, float, str]:
    """Ищет приложение по алиасам и описаниям. Возвращает (app, score, why)."""
    haystack = _norm(text)
    if not haystack:
        return None, 0.0, ""
    best_app = None
    best_score = 0.0
    best_why = ""
    for app in db.get("apps", []):
        if not app.get("enabled", True):
            continue
        for alias in app.get("aliases", []) or []:
            needle = _norm(alias)
            if len(needle) >= 3 and needle in haystack:
                score = 0.55 + min(0.2, len(needle) / 100.0)
                if score > best_score:
                    best_app, best_score, best_why = app, score, f"alias:{alias}"
        for desc in app.get("descriptions", []) or []:
            if isinstance(desc, str):
                pattern, is_regex = desc, False
            else:
                pattern = desc.get("text", "")
                is_regex = bool(desc.get("regex"))
            if not pattern:
                continue
            if is_regex:
                try:
                    hit = re.search(pattern, text or "", re.IGNORECASE)
                except re.error:
                    hit = None
            else:
                hit = _norm(pattern) in haystack if len(_norm(pattern)) >= 3 else None
            if hit:
                score = 0.50
                if score > best_score:
                    best_app, best_score, best_why = app, score, f"desc:{pattern}"
    return best_app, best_score, best_why


# ---------------------------------------------------------------------------
# результат


@dataclass
class MatchResult:
    app_id: str | None = None
    package: str = ""
    title: str = ""
    confidence: float = 0.0
    method: str = "none"
    needs_review: bool = False
    conflict: str = ""
    reason: str = ""
    icon_hash: str = ""
    icon_distance: float | None = None
    icon_hamming: int | None = None
    candidates: list[dict] = field(default_factory=list)
    icon_source: str = ""

    def as_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "package": self.package,
            "title": self.title,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "needs_review": self.needs_review,
            "conflict": self.conflict,
            "reason": self.reason,
            "icon_hash": self.icon_hash,
            "icon_distance": self.icon_distance,
            "icon_hamming": self.icon_hamming,
            "candidates": self.candidates,
            "icon_source": self.icon_source,
        }


def _from_app(app: dict | None) -> tuple[str, str, str]:
    if not app:
        return "", "", ""
    return app.get("id", ""), app.get("package", ""), app.get("title", "")


def resolve(
    db: dict,
    *,
    image: Image.Image | None = None,
    order_id: str = "",
    title: str = "",
    description: str = "",
    package: str = "",
    settings: dict | None = None,
    index: IconIndex | None = None,
) -> MatchResult:
    """Каскад: иконка -> ID -> текст -> пакет. Конфликты не скрываются."""
    settings = settings or load_settings()
    index = index or IconIndex(db, settings)
    result = MatchResult()

    icon = index.match_icon(image) if image is not None else {"ok": False}
    id_app = find_app(db, order_id=str(order_id)) if order_id else None
    text_app, text_score, text_why = match_text(
        db, " ".join(x for x in (title, description) if x))
    pkg_app = find_app(db, package=package) if package else None

    result.icon_hash = icon.get("digest") or ""
    result.icon_distance = icon.get("distance")
    result.icon_hamming = icon.get("hamming")
    result.candidates = icon.get("candidates") or []
    if icon.get("entry"):
        result.icon_source = icon["entry"].source

    icon_app = None
    if icon.get("ok"):
        entry = icon["entry"]
        icon_app = find_app(db, app_id=entry.app_id) if entry.app_id else None
        if icon_app is None and entry.package:
            icon_app = find_app(db, package=entry.package)
        if icon_app is None and entry.package:
            # иконка из icon_cache: пакет известен, карточки ещё нет
            result.app_id = ""
            result.package = entry.package
            result.title = entry.title or entry.package
            result.confidence = 0.62
            result.method = "icon-cache"
            result.reason = "иконка найдена в icon_cache, карточки в базе нет"
            result.needs_review = True
            return result

    if icon_app and icon.get("shared"):
        result.reason = "иконка общая для нескольких приложений"
        if id_app:
            aid, pkg, ttl = _from_app(id_app)
            result.app_id, result.package, result.title = aid, pkg, ttl
            result.confidence = 0.74
            result.method = "id-after-shared-icon"
            result.needs_review = True
            return result
        result.needs_review = True
        result.confidence = 0.35
        result.method = "shared-icon"
        return result

    if icon_app:
        aid, pkg, ttl = _from_app(icon_app)
        result.app_id, result.package, result.title = aid, pkg, ttl
        result.confidence = 0.90 if icon.get("hamming") is not None else 0.82
        result.method = "icon"
        if id_app and id_app.get("id") != icon_app.get("id"):
            other = id_app.get("package", "")
            result.conflict = f"иконка: {pkg}; ID {order_id}: {other}"
            result.needs_review = True
            if settings.get("match", {}).get("id_over_icon"):
                oid, opkg, otitle = _from_app(id_app)
                result.app_id, result.package, result.title = oid, opkg, otitle
                result.method = "id-over-icon"
                result.confidence = 0.70
        return result

    if id_app:
        aid, pkg, ttl = _from_app(id_app)
        result.app_id, result.package, result.title = aid, pkg, ttl
        result.confidence = 0.70
        result.method = "id"
        if text_app and text_app.get("id") != id_app.get("id"):
            result.conflict = (
                f"ID {order_id}: {pkg}; описание: {text_app.get('package', '')}")
            result.needs_review = True
        return result

    if text_app:
        aid, pkg, ttl = _from_app(text_app)
        result.app_id, result.package, result.title = aid, pkg, ttl
        result.confidence = min(0.65, text_score)
        result.method = "text"
        result.reason = text_why
        return result

    if pkg_app:
        aid, pkg, ttl = _from_app(pkg_app)
        result.app_id, result.package, result.title = aid, pkg, ttl
        result.confidence = 0.80
        result.method = "package"
        return result

    if package:
        result.package = package
        result.confidence = 0.55
        result.method = "package-new"
        result.needs_review = True
        result.reason = "пакет из магазина, карточки в базе нет"
        return result

    result.reason = icon.get("reason") or "совпадений нет"
    result.needs_review = True
    return result


# ---------------------------------------------------------------------------
# обучение базы


def learn(
    db: dict,
    *,
    order_id: str,
    package: str,
    title: str = "",
    component: str = "",
    query: str = "",
    description: str = "",
    image: Image.Image | None = None,
    source: str = "bot",
    save: bool = True,
) -> dict:
    """Дописывает ID/иконку/описание в карточку приложения."""
    app = find_app(db, package=package)
    if app is None:
        app = empty_app(package, title or package)
        upsert_app(db, app)
        app = find_app(db, package=package)
    if not app:
        return {}
    app_id = app["id"]
    add_order_id(db, app_id, str(order_id))
    if component and not app.get("component"):
        app["component"] = component
    if query and query not in (app.get("aliases") or []):
        app.setdefault("aliases", []).append(query)
    if title and not app.get("title"):
        app["title"] = title
    if description:
        descs = app.setdefault("descriptions", [])
        if description not in [d if isinstance(d, str) else d.get("text") for d in descs]:
            descs.append(description)
    if image is not None:
        digest = icon_hash(image)
        vector = icon_vector(image)
        if digest and vector is not None:
            path = save_icon(image, package, digest)
            add_icon(db, app_id, digest, file=path, source=source)
    app["stats"]["last_seen"] = datetime.now().isoformat(timespec="seconds")
    if save:
        save_db(db)
    return app


# ---------------------------------------------------------------------------
# удобные обёртки


def match_image(db: dict, image: Image.Image, order_id: str = "",
                title: str = "", description: str = "",
                settings: dict | None = None) -> MatchResult:
    return resolve(db, image=image, order_id=order_id, title=title,
                   description=description, settings=settings)


def match_order(db: dict, order_id: str) -> MatchResult:
    return resolve(db, order_id=order_id)
