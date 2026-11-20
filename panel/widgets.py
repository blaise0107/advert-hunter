# -*- coding: utf-8 -*-
"""Небольшие переиспользуемые виджеты панели.

Ничего не знают о бизнес-логике: только карточки, бейджи, лог и
вспомогательные строки. Это позволяет страницам оставаться читаемыми.
"""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QRect, QSize
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRubberBand,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """Карточка с необязательным заголовком."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            box = QVBoxLayout()
            box.setSpacing(2)
            self.title_label = QLabel(title)
            self.title_label.setObjectName("SectionTitle")
            if subtitle:
                self.title_label.setToolTip(subtitle)
            box.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = QLabel(subtitle)
                self.subtitle_label.setObjectName("Muted")
                self.subtitle_label.setWordWrap(True)
                self.subtitle_label.setToolTip(subtitle)
                box.addWidget(self.subtitle_label)
            self.header.addLayout(box, 1)
            self.body.addLayout(self.header)

    def add(self, widget: QWidget, stretch: int = 0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.body.addLayout(layout, stretch)
        return layout

    def add_header_widget(self, widget: QWidget):
        if self.header is None:
            raise RuntimeError("у карточки нет заголовка")
        self.header.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        return widget


class StatCard(Card):
    """Крупная цифра с подписью."""

    def __init__(self, title: str, value: str = "—", hint: str = "", parent=None):
        super().__init__(parent=parent)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(3)
        self.title = QLabel(title)
        self.title.setObjectName("StatLabel")
        self.value = QLabel(value)
        self.value.setObjectName("BigValue")
        self.hint = QLabel(hint)
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.title)
        self.body.addWidget(self.value)
        self.body.addWidget(self.hint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str, hint: str | None = None):
        self.value.setText(value)
        if hint is not None:
            self.hint.setText(hint)


class Badge(QLabel):
    """Цветной бейдж-статус."""

    VARIANTS = {
        "ok": ("#1f9d55", "#e8f7ef"),
        "warn": ("#b9770e", "#fff6e5"),
        "bad": ("#d64545", "#fdecec"),
        "info": ("#2471a3", "#eaf3fa"),
        "muted": ("#66737f", "#eef2f5"),
    }

    def __init__(self, text: str = "", variant: str = "muted", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_variant(variant)

    def set_variant(self, variant: str):
        fg, bg = self.VARIANTS.get(variant, self.VARIANTS["muted"])
        self.setStyleSheet(
            f"QLabel {{ color: {fg}; background: {bg}; border-radius: 8px;"
            f" padding: 2px 8px; font-size: 11px; font-weight: 600; }}"
        )


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background: #2c3742;")


class LogView(QWidget):
    """Хвост логов с фильтром, паузой и выбором источника."""

    refreshed = pyqtSignal()

    def __init__(self, sources: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.sources = sources
        self.last_text = ""
        self.paused = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.source = QComboBox()
        self.source.addItem("все источники", "")
        for name, path in sources:
            self.source.addItem(name, path)
        self.source.setToolTip("Показать один файл лога или все источники сразу.")
        self.source.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.source)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("фильтр: RU, ошибка, 1410517…")
        self.filter.setToolTip(
            "Показывать только строки, содержащие эту подстроку (без учёта регистра).")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.refresh)
        top.addWidget(self.filter, 1)

        self.autoscroll = QCheckBox("автопрокрутка")
        self.autoscroll.setChecked(True)
        self.autoscroll.setToolTip("Прокручивать вниз при появлении новых строк.")
        top.addWidget(self.autoscroll)

        self.btn_pause = QPushButton("Пауза")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip("Остановить автообновление, чтобы спокойно прочитать лог.")
        self.btn_pause.toggled.connect(self.on_pause)
        top.addWidget(self.btn_pause)

        btn = QPushButton("Обновить")
        btn.setToolTip("Прочитать логи прямо сейчас.")
        btn.clicked.connect(self.refresh)
        top.addWidget(btn)
        root.addLayout(top)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 9))
        self.text.setMaximumBlockCount(6000)
        self.text.setToolTip(
            "Хвост выбранных логов: последние 500 строк каждого источника. "
            "Кнопка «Пауза» останавливает автообновление.")
        root.addWidget(self.text, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)

    def on_pause(self, paused: bool):
        self.paused = paused
        self.btn_pause.setText("Продолжить" if paused else "Пауза")
        if not paused:
            self.refresh()

    @staticmethod
    def _fix_line(line: str) -> str:
        """Чинит строки, записанные UTF-8, но прочитанные как CP866.

        Старые задачи писали лог через консоль с кодировкой CP866, из-за
        чего кириллица превращалась в «╨┐╨╛...». Обратное преобразование
        безопасно только для таких строк, поэтому пробуем построчно.
        """
        if not any(ch in line for ch in "╨╤╬╧╪╫"):
            return line
        try:
            fixed = line.encode("cp866").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return line
        return fixed if "\ufffd" not in fixed else line

    def _read(self, path: str, count: int = 500) -> list[str]:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            return []
        out: list[str] = []
        for raw in data.splitlines()[-count:]:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Старые части логов писались консолью в CP866: декодируем
                # построчно, чтобы в «Журнале» не было ромбиков и вопросов.
                try:
                    line = raw.decode("cp866")
                except UnicodeDecodeError:
                    line = raw.decode("utf-8", "replace")
            out.append(self._fix_line(line))
        return out

    def refresh(self):
        if self.paused:
            return
        selected = self.source.currentData()
        parts: list[str] = []
        for name, path in self.sources:
            if selected and os.path.abspath(path) != os.path.abspath(selected):
                continue
            if not os.path.exists(path):
                continue
            lines = self._read(path)
            if not lines:
                continue
            parts.append(f"===== {name} =====")
            parts.extend(lines)
        text = "\n".join(parts)
        needle = self.filter.text().strip().lower()
        if needle:
            text = "\n".join(
                line for line in text.splitlines() if needle in line.lower()
            )
        if text != self.last_text:
            self.last_text = text
            bar = self.text.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 4
            self.text.setPlainText(text)
            if self.autoscroll.isChecked() and at_bottom:
                bar.setValue(bar.maximum())
        self.refreshed.emit()


class IconPreview(QLabel):
    """Превью иконки с плейсхолдером."""

    def __init__(self, size: int = 56, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { background: #141a20; border: 1px solid #2c3742;"
            " border-radius: 10px; color: #93a1ae; font-size: 10px; }"
        )
        self.setText("нет")

    def set_path(self, path: str | None):
        if path and os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.setPixmap(
                    pix.scaled(
                        self._size - 8,
                        self._size - 8,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.setPixmap(QPixmap())
        self.setText("нет")


class FieldRow(QWidget):
    """Подпись + поле в одну строку."""

    def __init__(self, label: str, widget: QWidget, hint: str = "", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        cap = QLabel(label)
        cap.setObjectName("Muted")
        cap.setFixedWidth(150)
        row.addWidget(cap)
        row.addWidget(widget, 1)
        if hint:
            tip = QLabel(hint)
            tip.setObjectName("Muted")
            row.addWidget(tip)


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def propagate_tooltips(root: QWidget) -> None:
    """Копирует подсказки на внутренние редакторы составных виджетов.

    У QSpinBox/QDoubleSpinBox текст рисует внутренний QLineEdit, а у
    QLineEdit с кнопкой очистки — внутренний QToolButton. Без этого
    подсказка видна только на стрелках, а не на самом поле.
    """
    for spin in root.findChildren(QAbstractSpinBox):
        tip = spin.toolTip().strip()
        if tip and spin.lineEdit() is not None:
            spin.lineEdit().setToolTip(tip)
    for edit in root.findChildren(QLineEdit):
        tip = edit.toolTip().strip()
        if tip:
            for button in edit.findChildren(QToolButton):
                button.setToolTip(tip)
    # Подписи слева от полей: при наведении на текст подсказка тоже нужна.
    for grid in root.findChildren(QGridLayout):
        for index in range(grid.count()):
            item = grid.itemAt(index)
            label = item.widget() if item is not None else None
            if not isinstance(label, QLabel):
                continue
            row, column, _, _ = grid.getItemPosition(index)
            if column != 0:
                continue
            for other_index in range(grid.count()):
                other = grid.itemAt(other_index)
                if other is None or other.widget() is None:
                    continue
                other_row, other_column, _, _ = grid.getItemPosition(other_index)
                if other_row == row and other_column == 1:
                    tip = other.widget().toolTip().strip()
                    if tip:
                        label.setToolTip(tip)
                    break



class _CropArea(QLabel):
    """Поле скриншота с выделением прямоугольника мышью."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(520, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("QLabel { background: #10161c; border: 1px solid #2c3742; }")
        self._source = QPixmap()
        self._scale = 1.0
        self._offset = QPoint(0, 0)
        self._origin = None
        self.selection: QRect | None = None
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)

    def set_source(self, pixmap: QPixmap):
        self._source = pixmap
        self.selection = None
        self.rubber.hide()
        self._fit()

    def _fit(self):
        if self._source.isNull():
            super().setPixmap(QPixmap())
            return
        scaled = self._source.scaled(
            self.size() - QSize(4, 4),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)
        self._scale = self._source.width() / max(1, scaled.width())
        self._offset = QPoint(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _to_image(self, point: QPoint) -> QPoint:
        x = int((point.x() - self._offset.x()) * self._scale)
        y = int((point.y() - self._offset.y()) * self._scale)
        x = max(0, min(self._source.width(), x))
        y = max(0, min(self._source.height(), y))
        return QPoint(x, y)

    def mousePressEvent(self, event):
        if self._source.isNull() or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._origin = event.position().toPoint()
        self.rubber.setGeometry(QRect(self._origin, QSize()))
        self.rubber.show()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        self.rubber.setGeometry(
            QRect(self._origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self._origin is None:
            return
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = None
        start = self._to_image(rect.topLeft())
        end = self._to_image(rect.bottomRight())
        if end.x() - start.x() < 8 or end.y() - start.y() < 8:
            self.selection = None
            self.rubber.hide()
            return
        self.selection = QRect(start, end).normalized()


class CropDialog(QDialog):
    """Выделение иконки на скриншоте текущего экрана."""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Иконка со скриншота")
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Выделите мышью иконку приложения. Если иконка мелкая — выделите "
            "чуть больше: при сравнении лишний фон по краям отсекается."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        self.area = _CropArea(self)
        self.area.set_source(pixmap)
        layout.addWidget(self.area, 1)
        row = QHBoxLayout()
        b_all = QPushButton("Весь экран")
        b_all.setToolTip("Выбрать весь снимок целиком — удобно, если иконка одна на экране.")
        b_all.clicked.connect(self.select_all)
        row.addWidget(b_all)
        row.addStretch(1)
        b_cancel = QPushButton("Отмена")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("Сохранить иконку")
        b_ok.setObjectName("AccentButton")
        b_ok.setToolTip("Обрезать выделение и добавить его в иконки выбранного приложения.")
        b_ok.clicked.connect(self.accept_selection)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        layout.addLayout(row)

    def select_all(self):
        self.area.selection = QRect(
            0, 0, self.area._source.width(), self.area._source.height())
        self.area.rubber.hide()

    def accept_selection(self):
        if self.area.selection is None:
            QMessageBox.information(
                self, "Иконка", "Сначала выделите область на скриншоте.")
            return
        self.accept()

    def crop_rect(self) -> QRect:
        return self.area.selection or QRect()
