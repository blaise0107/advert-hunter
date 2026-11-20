# -*- coding: utf-8 -*-
"""Ядро панели AdvertApp: настройки, база приложений, матчинг, устройство."""

from .config import BASE, DB_PATH, SETTINGS_PATH, load_settings, save_settings

__all__ = ["BASE", "DB_PATH", "SETTINGS_PATH", "load_settings", "save_settings"]
