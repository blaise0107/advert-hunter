"""Темы и общий QSS для панели AdvertApp."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication

try:
    from qfluentwidgets import Theme, setTheme
except Exception:  # pragma: no cover - панель должна пережить отсутствие Fluent
    Theme = None
    setTheme = None


PALETTES = {
    "dark": {
        "bg": "#101418",
        "surface": "#171c22",
        "card": "#1c232b",
        "card2": "#222a33",
        "border": "#2c3742",
        "text": "#eef3f7",
        "muted": "#93a1ae",
        "accent": "#2ecc71",
        "accent2": "#27ae60",
        "danger": "#ff6b6b",
        "warning": "#f5b041",
        "info": "#5dade2",
        "input": "#141a20",
    },
    "light": {
        "bg": "#f3f6f8",
        "surface": "#ffffff",
        "card": "#ffffff",
        "card2": "#f7fafc",
        "border": "#dbe3ea",
        "text": "#17212b",
        "muted": "#66737f",
        "accent": "#1f9d55",
        "accent2": "#17864a",
        "danger": "#d64545",
        "warning": "#b9770e",
        "info": "#2471a3",
        "input": "#ffffff",
    },
}


def palette(mode: str) -> dict:
    return PALETTES.get(mode, PALETTES["dark"])


def apply_theme(app: QApplication, mode: str = "dark") -> dict:
    """Применяет тему Fluent и QSS. Возвращает палитру."""
    mode = mode if mode in PALETTES else "dark"
    if setTheme and Theme is not None:
        try:
            setTheme(Theme.DARK if mode == "dark" else Theme.LIGHT, save=False)
        except Exception:
            pass
    p = palette(mode)
    app.setStyleSheet(_qss(p))
    return p


def _qss(p: dict) -> str:
    return f"""
    QWidget {{
        color: {p['text']};
        font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background: {p['bg']};
    }}
    QScrollArea, QAbstractScrollArea {{
        background: transparent;
        border: none;
    }}
    QFrame#Card {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QFrame#CardSoft {{
        background: {p['card2']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}
    QLabel#PageTitle {{
        font-size: 26px;
        font-weight: 700;
        color: {p['text']};
    }}
    QLabel#PageSubtitle {{
        font-size: 13px;
        color: {p['muted']};
    }}
    QLabel#SectionTitle {{
        font-size: 15px;
        font-weight: 600;
        color: {p['text']};
    }}
    QLabel#Muted {{
        color: {p['muted']};
    }}
    QLabel#Accent {{
        color: {p['accent']};
        font-weight: 600;
    }}
    QLabel#Danger {{
        color: {p['danger']};
        font-weight: 600;
    }}
    QLabel#BigValue {{
        font-size: 28px;
        font-weight: 700;
        color: {p['text']};
    }}
    QLabel#StatLabel {{
        color: {p['muted']};
        font-size: 12px;
    }}
    QLabel#ScreenCanvas {{
        background: #0b0e11;
        border: 1px solid #2c3742;
        border-radius: 10px;
        color: #93a1ae;
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 7px 9px;
        selection-background-color: {p['accent']};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {p['accent']};
    }}
    QComboBox QAbstractItemView {{
        background: {p['card']};
        border: 1px solid {p['border']};
        selection-background-color: {p['accent2']};
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        outline: none;
        alternate-background-color: {p['card2']};
    }}
    QListWidget::item, QTreeWidget::item {{
        padding: 7px 8px;
        border-radius: 6px;
    }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background: {p['accent2']};
        color: #ffffff;
    }}
    QHeaderView::section {{
        background: {p['card2']};
        color: {p['muted']};
        border: none;
        border-bottom: 1px solid {p['border']};
        padding: 7px;
        font-weight: 600;
    }}
    QTableWidget {{
        gridline-color: {p['border']};
    }}
    QPushButton {{
        background: {p['card2']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 7px 12px;
    }}
    QPushButton:hover {{
        border-color: {p['accent']};
    }}
    QPushButton:pressed {{
        background: {p['border']};
    }}
    QPushButton#AccentButton {{
        background: {p['accent2']};
        border: 1px solid {p['accent2']};
        color: #ffffff;
        font-weight: 600;
    }}
    QPushButton#AccentButton:hover {{
        background: {p['accent']};
        border-color: {p['accent']};
    }}
    QPushButton#DangerButton {{
        background: transparent;
        border: 1px solid {p['danger']};
        color: {p['danger']};
    }}
    QPushButton#DangerButton:hover {{
        background: {p['danger']};
        color: #ffffff;
    }}
    QCheckBox, QRadioButton {{
        spacing: 7px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 17px;
        height: 17px;
    }}
    QCheckBox::indicator:checked {{
        background: {p['accent2']};
        border: 1px solid {p['accent2']};
        border-radius: 4px;
    }}
    QCheckBox::indicator:unchecked {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 4px;
    }}
    QProgressBar {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 7px;
        text-align: center;
        height: 14px;
    }}
    QProgressBar::chunk {{
        background: {p['accent2']};
        border-radius: 6px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border']};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['muted']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QToolTip {{
        background: {p['card']};
        color: {p['text']};
        border: 1px solid {p['border']};
        padding: 6px;
    }}
    """
