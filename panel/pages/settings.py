# -*- coding: utf-8 -*-
"""Страница «Настройки»: расписание, стратегии, магазины, матчинг, тема."""
from __future__ import annotations

import os

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
)

from core.config import BASE, load_settings, save_settings
from core.housekeeping import dir_stats, human_size, log_files
from panel.pages.base import ScrollPage
from panel.widgets import Card
from panel.workers import CmdWorker

MARKETS = [
    ("google", "Google Play"),
    ("rustore", "RuStore"),
    ("huawei", "Huawei AppGallery"),
    ("xiaomi", "Xiaomi GetApps"),
]


class SettingsPage(ScrollPage):
    themeChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(
            "Настройки",
            "Файл panel_settings.json. Бот перечитывает его перед каждым проходом.",
            parent,
        )
        self.settings = load_settings()
        self.msg = QLabel("Готово.")
        self.msg.setObjectName("Muted")

        hint = QLabel(
            "Подсказки: наведите курсор на поле, его подпись или заголовок карточки.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setToolTip(hint.text())
        self.add(hint)

        # ---------------- общие ----------------
        general = Card(
            "Общие",
            "Цель по балансу, минимальная сумма вывода, комиссия и тема панели.")
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)

        self.ed_deadline = QLineEdit(str(self.settings.get("deadline", "08:30")))
        self.ed_deadline.setMaximumWidth(100)
        self.sp_target = QDoubleSpinBox()
        self.sp_target.setRange(1, 100000)
        self.sp_target.setDecimals(2)
        self.sp_target.setValue(float(self.settings.get("target_balance", 210)))
        self.sp_target.setSuffix(" ₽")
        self.sp_min = QDoubleSpinBox()
        self.sp_min.setRange(1, 100000)
        self.sp_min.setDecimals(2)
        self.sp_min.setValue(float(self.settings.get("min_withdraw", 150)))
        self.sp_min.setSuffix(" ₽")
        self.sp_commission = QDoubleSpinBox()
        self.sp_commission.setRange(0, 100000)
        self.sp_commission.setDecimals(2)
        self.sp_commission.setValue(float(self.settings.get("commission", 50)))
        self.sp_commission.setSuffix(" ₽")
        self.cb_theme = QComboBox()
        for key, label in (("dark", "Тёмная"), ("light", "Светлая"), ("system", "Системная")):
            self.cb_theme.addItem(label, key)
        idx = self.cb_theme.findData(self.settings.get("theme", "dark"))
        self.cb_theme.setCurrentIndex(max(0, idx))
        self.cb_app = QComboBox()
        for key, label in (
            ("", "Определить автоматически"),
            ("com.advert.mobileearnings.app", "AdvertApp (com.advert.mobileearnings.app)"),
            ("com.teamdev.reward", "TeamDev (com.teamdev.reward)"),
            ("ru.teamdev.reward", "TeamDev (ru.teamdev.reward)"),
        ):
            self.cb_app.addItem(label, key)
        idx = self.cb_app.findData(self.settings.get("app_package", ""))
        self.cb_app.setCurrentIndex(max(0, idx))
        self.cb_app.setToolTip(
            "На телефоне может стоять несколько сборок AdvertApp, а одна из них "
            "может быть отключена. Здесь можно указать, какой пакет бот должен "
            "открывать и проверять на переднем плане.")

        rows = [
            ("Остановка цикла", self.ed_deadline),
            ("Цель, ₽", self.sp_target),
            ("Минимум к выводу", self.sp_min),
            ("Комиссия", self.sp_commission),
            ("Тема", self.cb_theme),
            ("Сборка AdvertApp", self.cb_app),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            g.addWidget(cap, i, 0)
            g.addWidget(widget, i, 1)
        g.setColumnStretch(1, 1)
        general.add_layout(g)
        self.add(general)

        # ---------------- цикл ----------------
        cycle = Card("Цикл", "Сколько минут отводить «Новым» и «Активным», пауза между проходами.")
        c = QGridLayout()
        c.setHorizontalSpacing(14)
        c.setVerticalSpacing(8)
        self.sp_new_min = QSpinBox()
        self.sp_new_min.setRange(1, 120)
        self.sp_new_min.setValue(int(self.settings["cycle"].get("new_minutes", 12)))
        self.sp_new_min.setSuffix(" мин")
        self.sp_active_min = QSpinBox()
        self.sp_active_min.setRange(1, 180)
        self.sp_active_min.setValue(int(self.settings["cycle"].get("active_minutes", 25)))
        self.sp_active_min.setSuffix(" мин")
        self.sp_sleep = QSpinBox()
        self.sp_sleep.setRange(0, 600)
        self.sp_sleep.setValue(int(self.settings["cycle"].get("sleep_seconds", 30)))
        self.sp_sleep.setSuffix(" с")
        self.cb_active_first = QCheckBox("Сначала «Активные»")
        self.cb_active_first.setChecked(bool(self.settings["cycle"].get("active_first", True)))
        rows = [
            ("«Новые»", self.sp_new_min),
            ("«Активные»", self.sp_active_min),
            ("Пауза", self.sp_sleep),
            ("Порядок", self.cb_active_first),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            c.addWidget(cap, i, 0)
            c.addWidget(widget, i, 1)
        cycle.add_layout(c)
        self.add(cycle)

        # ---------------- стратегия «Новые» ----------------
        new_card = Card(
            "Стратегия «Новые»",
            "Обход всех страниц списка, долистывание до низа и контрольные перепроверки.",
        )
        ng = QGridLayout()
        ng.setHorizontalSpacing(14)
        ng.setVerticalSpacing(8)
        self.sp_new_pages = QSpinBox()
        self.sp_new_pages.setRange(0, 50)
        self.sp_new_pages.setValue(int(self.settings["new"].get("max_pages", 0)))
        self.sp_new_pages.setSpecialValueText("все")
        self.sp_new_pages.setToolTip(
            "Сколько страниц списка «Новые» обходить. 0 = все страницы без ограничения.")
        self.sp_new_empty = QSpinBox()
        self.sp_new_empty.setRange(1, 10)
        self.sp_new_empty.setValue(int(self.settings["new"].get("empty_stop", 2)))
        self.sp_new_empty.setSuffix(" экр.")
        self.sp_new_empty.setToolTip(
            "Сколько подряд пустых экранов считать концом прокрутки страницы.")
        self.sp_recheck = QSpinBox()
        self.sp_recheck.setRange(0, 10)
        self.sp_recheck.setValue(int(self.settings["new"].get("recheck_count", 2)))
        self.sp_recheck.setSuffix(" раз")
        self.sp_recheck.setToolTip(
            "Контрольные проверки: сколько раз перепроверять весь список «Новые», "
            "если доступных RU-заданий не осталось.")
        self.sp_recheck_delay = QSpinBox()
        self.sp_recheck_delay.setRange(1, 120)
        self.sp_recheck_delay.setValue(
            int(self.settings["new"].get("recheck_delay", 10)))
        self.sp_recheck_delay.setSuffix(" с")
        self.sp_recheck_delay.setToolTip("Пауза между контрольными проверками списка.")
        self.cb_new_sort = QCheckBox("Сортировать по цене")
        self.cb_new_sort.setChecked(
            bool(self.settings["new"].get("sort_by_price", True)))
        self.cb_new_scroll = QCheckBox("Листать до низа")
        self.cb_new_scroll.setChecked(
            bool(self.settings["new"].get("scroll_to_bottom", True)))
        self.cb_new_scroll.setToolTip(
            "Долистывать каждую страницу списка «Новые» до конца, а не только верхний экран.")
        self.sp_new_wait = QSpinBox()
        self.sp_new_wait.setRange(15, 300)
        self.sp_new_wait.setValue(int(self.settings["new"].get("open_wait_seconds", 75)))
        self.sp_new_wait.setSuffix(" с")
        self.sp_new_wait.setToolTip(
            "Сколько ждать открытия пункта меню или задания. Сервер AdvertApp "
            "отвечает медленно: экран может не меняться почти минуту.")
        rows = [
            ("Страниц", self.sp_new_pages),
            ("Пустых подряд", self.sp_new_empty),
            ("Листать до низа", self.cb_new_scroll),
            ("Перепроверок", self.sp_recheck),
            ("Пауза перепроверки", self.sp_recheck_delay),
            ("Ожидание открытия", self.sp_new_wait),
            ("Сортировка", self.cb_new_sort),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            ng.addWidget(cap, i, 0)
            ng.addWidget(widget, i, 1)
        new_card.add_layout(ng)
        self.add(new_card)

        # ---------------- стратегия «Активные» ----------------
        active_card = Card(
            "Стратегия «Активные»",
            "Порядок обхода по этапам, предел обхода и подтверждение удаления приложений.",
        )
        ag = QGridLayout()
        ag.setHorizontalSpacing(14)
        ag.setVerticalSpacing(8)
        self.sp_active_empty = QSpinBox()
        self.sp_active_empty.setRange(1, 10)
        self.sp_active_empty.setValue(
            int(self.settings["active"].get("empty_stop", 2)))
        self.sp_active_empty.setSuffix(" экр.")
        self.sp_active_cap = QSpinBox()
        self.sp_active_cap.setRange(1, 200)
        self.sp_active_cap.setValue(
            int(self.settings["active"].get("hard_cap", 25)))
        self.sp_active_cap.setSuffix(" экр.")
        self.sp_active_retry = QSpinBox()
        self.sp_active_retry.setRange(0, 10)
        self.sp_active_retry.setValue(
            int(self.settings["active"].get("retry_count", 2)))
        self.sp_active_retry.setSuffix(" раз")
        self.cb_active_sort = QCheckBox("Сортировать по цене")
        self.cb_active_sort.setChecked(
            bool(self.settings["active"].get("sort_by_price", True)))
        self.cb_active_stage = QCheckBox("Порядок по этапам")
        self.cb_active_stage.setChecked(
            bool(self.settings["active"].get("order_by_stage", True)))
        self.cb_active_stage.setToolTip(
            "Сначала заказы, взятые из «Новых», и шаги «Установить приложение», "
            "затем «Открыть на 2/8/16/31 дней». Внутри этапа — цена по убыванию.")
        self.sp_cleanup_conf = QSpinBox()
        self.sp_cleanup_conf.setRange(1, 5)
        self.sp_cleanup_conf.setValue(
            int(self.settings["active"].get("cleanup_confirmations", 2)))
        self.sp_cleanup_conf.setSuffix(" раз")
        self.sp_cleanup_conf.setToolTip(
            "Сколько раз перезапускать AdvertApp и перепроверять «Активные», "
            "прежде чем считать заказ завершённым и удалить приложение.")
        self.sp_cleanup_delay = QSpinBox()
        self.sp_cleanup_delay.setRange(0, 1440)
        self.sp_cleanup_delay.setValue(
            int(self.settings["active"].get("cleanup_delay_minutes", 0)))
        self.sp_cleanup_delay.setSuffix(" мин")
        self.sp_cleanup_delay.setToolTip(
            "Дополнительная пауза перед удалением завершённого заказа. "
            "0 = удалять сразу после подтверждений.")
        self.sp_hold_min = QSpinBox()
        self.sp_hold_min.setRange(30, 300)
        self.sp_hold_min.setValue(int(self.settings.get("hold_min_seconds", 70)))
        self.sp_hold_min.setSuffix(" с")
        self.sp_active_wait = QSpinBox()
        self.sp_active_wait.setRange(15, 300)
        self.sp_active_wait.setValue(
            int(self.settings["active"].get("open_wait_seconds", 75)))
        self.sp_active_wait.setSuffix(" с")
        self.sp_active_wait.setToolTip(
            "Сколько ждать открытия «Активных» и страницы задания: сервер "
            "AdvertApp отвечает медленно, экран меняется не сразу.")
        rows = [
            ("Порядок по этапам", self.cb_active_stage),
            ("Сортировка", self.cb_active_sort),
            ("Пустых подряд", self.sp_active_empty),
            ("Жёсткий предел", self.sp_active_cap),
            ("Повторов", self.sp_active_retry),
            ("Проверок удаления", self.sp_cleanup_conf),
            ("Отложить удаление", self.sp_cleanup_delay),
            ("Ожидание открытия", self.sp_active_wait),
            ("Мин. удержание", self.sp_hold_min),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            ag.addWidget(cap, i, 0)
            ag.addWidget(widget, i, 1)
        active_card.add_layout(ag)
        self.add(active_card)

        # ---------------- магазины ----------------
        markets = Card("Магазины и запреты", "Samsung запрещён всегда. VPN/foreign отсекаются по тексту карточки.")
        m = QHBoxLayout()
        m.setSpacing(18)
        self.market_boxes = {}
        enabled = set(self.settings.get("markets", []))
        for key, label in MARKETS:
            cb = QCheckBox(label)
            cb.setChecked(key in enabled)
            self.market_boxes[key] = cb
            m.addWidget(cb)
        m.addStretch(1)
        markets.add_layout(m)
        self.ed_forbidden = QLineEdit(", ".join(self.settings.get("forbidden", [])))
        self.ed_forbidden.setPlaceholderText("samsung, vpn")
        markets.add(self.ed_forbidden)
        self.add(markets)

        # ---------------- матчинг ----------------
        match = Card("Матчинг приложений", "Иконка — приоритет. ID и описание — запасные признаки. Конфликты флагуются.")
        mg = QGridLayout()
        mg.setHorizontalSpacing(14)
        mg.setVerticalSpacing(8)
        self.sp_icon_dist = QDoubleSpinBox()
        self.sp_icon_dist.setRange(0.1, 5.0)
        self.sp_icon_dist.setSingleStep(0.05)
        self.sp_icon_dist.setDecimals(2)
        self.sp_icon_dist.setValue(float(self.settings["match"].get("icon_max_distance", 1.0)))
        self.sp_icon_margin = QDoubleSpinBox()
        self.sp_icon_margin.setRange(0.1, 2.0)
        self.sp_icon_margin.setSingleStep(0.05)
        self.sp_icon_margin.setDecimals(2)
        self.sp_icon_margin.setValue(float(self.settings["match"].get("icon_margin", 0.75)))
        self.sp_hamming = QSpinBox()
        self.sp_hamming.setRange(0, 64)
        self.sp_hamming.setValue(int(self.settings["match"].get("hash_max_hamming", 4)))
        self.sp_min_conf = QDoubleSpinBox()
        self.sp_min_conf.setRange(0.0, 1.0)
        self.sp_min_conf.setSingleStep(0.05)
        self.sp_min_conf.setDecimals(2)
        self.sp_min_conf.setValue(float(self.settings["match"].get("min_confidence", 0.55)))
        self.cb_id_over_icon = QCheckBox("ID важнее иконки")
        self.cb_id_over_icon.setChecked(bool(self.settings["match"].get("id_over_icon", False)))
        rows = [
            ("Порог иконки", self.sp_icon_dist),
            ("Отрыв от второго", self.sp_icon_margin),
            ("Hamming", self.sp_hamming),
            ("Мин. уверенность", self.sp_min_conf),
            ("Приоритет", self.cb_id_over_icon),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            mg.addWidget(cap, i, 0)
            mg.addWidget(widget, i, 1)
        match.add_layout(mg)
        self.add(match)

        # ---------------- обслуживание ----------------
        maint = Card(
            "Обслуживание: кадры и логи",
            "Кадры — временные снимки экрана для OCR, их можно удалять в любой "
            "момент. Логи при превышении размера переносятся в logs/.")
        mg2 = QGridLayout()
        mg2.setHorizontalSpacing(14)
        mg2.setVerticalSpacing(8)
        maintenance = self.settings.get("maintenance", {})
        self.sp_frames_age = QSpinBox()
        self.sp_frames_age.setRange(5, 1440)
        self.sp_frames_age.setSuffix(" мин")
        self.sp_frames_age.setValue(int(maintenance.get("frames_max_age_minutes", 90)))
        self.sp_frames_age.setToolTip("Удалять кадры старше этого возраста.")
        self.sp_frames_files = QSpinBox()
        self.sp_frames_files.setRange(50, 10000)
        self.sp_frames_files.setSuffix(" файлов")
        self.sp_frames_files.setValue(int(maintenance.get("frames_max_files", 600)))
        self.sp_frames_files.setToolTip(
            "Держать не больше этого числа самых свежих кадров.")
        self.sp_frames_mb = QSpinBox()
        self.sp_frames_mb.setRange(20, 5000)
        self.sp_frames_mb.setSuffix(" МБ")
        self.sp_frames_mb.setValue(int(maintenance.get("frames_max_mb", 250)))
        self.sp_frames_mb.setToolTip("Суммарный лимит папки frames/.")
        self.sp_log_mb = QSpinBox()
        self.sp_log_mb.setRange(1, 100)
        self.sp_log_mb.setSuffix(" МБ")
        self.sp_log_mb.setValue(int(maintenance.get("log_max_mb", 2)))
        self.sp_log_mb.setToolTip(
            "При каком размере лог переносится в logs/ перед запуском цикла.")
        self.sp_log_keep = QSpinBox()
        self.sp_log_keep.setRange(1, 100)
        self.sp_log_keep.setSuffix(" файлов")
        self.sp_log_keep.setValue(int(maintenance.get("log_keep", 10)))
        self.sp_log_keep.setToolTip("Сколько старых логов хранить в logs/.")
        rows = [
            ("Кадры: возраст", self.sp_frames_age),
            ("Кадры: файлов", self.sp_frames_files),
            ("Кадры: объём", self.sp_frames_mb),
            ("Лог: размер", self.sp_log_mb),
            ("Лог: хранить", self.sp_log_keep),
        ]
        for i, (name, widget) in enumerate(rows):
            cap = QLabel(name)
            cap.setObjectName("Muted")
            mg2.addWidget(cap, i, 0)
            mg2.addWidget(widget, i, 1)
        maint.add_layout(mg2)
        mrow = QHBoxLayout()
        b_frames = QPushButton("Очистить кадры сейчас")
        b_frames.setToolTip(
            "Сразу применить лимиты к папке frames/ и удалить лишние снимки.")
        b_frames.clicked.connect(lambda: self.run_cmd("prune_frames"))
        b_logs = QPushButton("Ротировать логи")
        b_logs.setToolTip(
            "Перенести большие логи в logs/ и оставить только свежие копии.")
        b_logs.clicked.connect(lambda: self.run_cmd("rotate_logs"))
        mrow.addWidget(b_frames)
        mrow.addWidget(b_logs)
        mrow.addStretch(1)
        maint.add_layout(mrow)
        self.l_maint = QLabel("—")
        self.l_maint.setObjectName("Muted")
        self.l_maint.setWordWrap(True)
        maint.add(self.l_maint)
        self.add(maint)

        # ---------------- кнопки ----------------
        btns = QHBoxLayout()
        b_save = QPushButton("Сохранить все настройки")
        b_save.setObjectName("AccentButton")
        b_save.setToolTip(
            "Записать все настройки в panel_settings.json. Бот перечитает файл "
            "перед следующим проходом, перезапуск не нужен.")
        b_save.clicked.connect(self.save)
        b_reload = QPushButton("Перечитать")
        b_reload.setToolTip("Перечитать panel_settings.json и обновить поля формы.")
        b_reload.clicked.connect(self.reload)
        btns.addWidget(b_save)
        btns.addWidget(b_reload)
        btns.addStretch(1)
        self.add_layout(btns)
        self.add(self.msg)

        tips = {
            self.ed_deadline: "Время, после которого цикл останавливается (формат ЧЧ:ММ).",
            self.sp_target: "Цель по балансу. Панель считает, сколько осталось до неё.",
            self.sp_min: "Минимальная сумма вывода в AdvertApp.",
            self.sp_commission: "Комиссия вывода — учитывается в подсказке о цели.",
            self.cb_theme: "Тема оформления панели.",
            self.cb_app: "Какой пакет AdvertApp открывать боту.",
            self.sp_new_min: "Сколько минут за один цикл отводить фазе «Новые».",
            self.sp_active_min: "Сколько минут за один цикл отводить фазе «Активные».",
            self.sp_sleep: "Пауза между проходами цикла.",
            self.cb_active_first: "Начинать цикл с «Активных» (уже взятые задания).",
            self.sp_new_pages: "Сколько экранов «Новых» просматривать.",
            self.sp_recheck: "Сколько раз перепроверять экран, если RU-заданий нет.",
            self.sp_recheck_delay: "Пауза между перепроверками «Новых».",
            self.cb_new_sort: "Брать самое дорогое RU-задание на экране.",
            self.cb_new_scroll: "Долистывать каждую страницу «Новых» до конца.",
            self.sp_active_empty: "Остановить обход после стольких пустых страниц подряд.",
            self.sp_active_cap: "Жёсткий предел числа карточек за проход.",
            self.sp_active_retry: "Сколько раз повторять задание при неудаче.",
            self.cb_active_sort: "Сначала выполнять самые дорогие задания.",
            self.sp_hold_min: "Минимальное удержание приложения по заданиям «Открыть».",
            self.ed_forbidden: "Запрещённые признаки через запятую: samsung, vpn.",
            self.sp_icon_dist: "Максимальное расстояние вектора иконки для совпадения.",
            self.sp_icon_margin: "Насколько лучшее совпадение должно отрываться от второго.",
            self.sp_hamming: "Максимальное расстояние dHash для совпадения иконки.",
            self.sp_min_conf: "Минимальная уверенность, чтобы принять совпадение.",
            self.cb_id_over_icon: "При конфликте доверять ID задания, а не иконке.",
        }
        for widget, text in tips.items():
            widget.setToolTip(text)
        for cb in self.market_boxes.values():
            cb.setToolTip("Разрешён ли этот магазин для установки приложений.")

        self.size_timer = QTimer(self)
        self.size_timer.timeout.connect(self.refresh_sizes)
        self.size_timer.start(10000)
        QTimer.singleShot(300, self.refresh_sizes)

    # ------------------------------------------------------------------
    def run_cmd(self, action: str):
        self.msg.setText(f"Выполняю: {action}…")
        self.cmd_worker = CmdWorker(action, self)
        self.cmd_worker.done.connect(self.on_cmd)
        self.cmd_worker.start()

    def on_cmd(self, ok: bool, msg: str):
        self.msg.setText(("Готово: " if ok else "Ошибка: ") + msg)
        self.refresh_sizes()

    def refresh_sizes(self):
        frames = dir_stats(os.path.join(BASE, "frames"))
        logs = [info for info in log_files() if info.get("exists")]
        total = sum(info.get("size", 0) for info in logs)
        self.l_maint.setText(
            f"Сейчас: кадры — {frames['text']}; логи — {human_size(total)} "
            f"в {len(logs)} файлах."
        )

    # ------------------------------------------------------------------
    def save(self):
        self.settings["deadline"] = self.ed_deadline.text().strip() or "08:30"
        self.settings["target_balance"] = self.sp_target.value()
        self.settings["min_withdraw"] = self.sp_min.value()
        self.settings["commission"] = self.sp_commission.value()
        self.settings["theme"] = self.cb_theme.currentData()
        self.settings["app_package"] = self.cb_app.currentData() or ""

        self.settings["cycle"]["new_minutes"] = self.sp_new_min.value()
        self.settings["cycle"]["active_minutes"] = self.sp_active_min.value()
        self.settings["cycle"]["sleep_seconds"] = self.sp_sleep.value()
        self.settings["cycle"]["active_first"] = self.cb_active_first.isChecked()

        self.settings["new"]["max_pages"] = self.sp_new_pages.value()
        self.settings["new"]["empty_stop"] = self.sp_new_empty.value()
        self.settings["new"]["scroll_to_bottom"] = self.cb_new_scroll.isChecked()
        self.settings["new"]["recheck_count"] = self.sp_recheck.value()
        self.settings["new"]["recheck_delay"] = self.sp_recheck_delay.value()
        self.settings["new"]["open_wait_seconds"] = self.sp_new_wait.value()
        self.settings["new"]["sort_by_price"] = self.cb_new_sort.isChecked()

        self.settings["active"]["empty_stop"] = self.sp_active_empty.value()
        self.settings["active"]["hard_cap"] = self.sp_active_cap.value()
        self.settings["active"]["retry_count"] = self.sp_active_retry.value()
        self.settings["active"]["sort_by_price"] = self.cb_active_sort.isChecked()
        self.settings["active"]["order_by_stage"] = self.cb_active_stage.isChecked()
        self.settings["active"]["cleanup_confirmations"] = self.sp_cleanup_conf.value()
        self.settings["active"]["cleanup_delay_minutes"] = self.sp_cleanup_delay.value()
        self.settings["active"]["open_wait_seconds"] = self.sp_active_wait.value()
        self.settings["hold_min_seconds"] = self.sp_hold_min.value()

        self.settings["markets"] = [
            key for key, cb in self.market_boxes.items() if cb.isChecked()
        ]
        self.settings["forbidden"] = [
            part.strip().lower()
            for part in self.ed_forbidden.text().split(",")
            if part.strip()
        ]

        self.settings["match"]["icon_max_distance"] = self.sp_icon_dist.value()
        self.settings["match"]["icon_margin"] = self.sp_icon_margin.value()
        self.settings["match"]["hash_max_hamming"] = self.sp_hamming.value()
        self.settings["match"]["min_confidence"] = self.sp_min_conf.value()
        self.settings["match"]["id_over_icon"] = self.cb_id_over_icon.isChecked()

        self.settings["maintenance"]["frames_max_age_minutes"] = self.sp_frames_age.value()
        self.settings["maintenance"]["frames_max_files"] = self.sp_frames_files.value()
        self.settings["maintenance"]["frames_max_mb"] = self.sp_frames_mb.value()
        self.settings["maintenance"]["log_max_mb"] = self.sp_log_mb.value()
        self.settings["maintenance"]["log_keep"] = self.sp_log_keep.value()

        save_settings(self.settings)
        self.msg.setText("Настройки сохранены в panel_settings.json.")
        self.themeChanged.emit(self.settings["theme"])

    def reload(self):
        self.settings = load_settings()
        self.msg.setText("Настройки перечитаны. Откройте страницу заново, чтобы увидеть все поля.")
