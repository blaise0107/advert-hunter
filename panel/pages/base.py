# -*- coding: utf-8 -*-
"""Базовые страницы панели."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class BasePage(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 24)
        self.root.setSpacing(16)

        head = QVBoxLayout()
        head.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        if subtitle:
            t.setToolTip(subtitle)
        head.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("PageSubtitle")
            s.setWordWrap(True)
            s.setToolTip(subtitle)
            head.addWidget(s)
        self.root.addLayout(head)

    def add(self, widget: QWidget, stretch: int = 0):
        self.root.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.root.addLayout(layout, stretch)
        return layout

    def add_stretch(self):
        self.root.addStretch(1)


class ScrollPage(BasePage):
    """Страница с прокручиваемым содержимым."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(title, subtitle, parent)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 4, 0, 8)
        self.body.setSpacing(14)
        self.scroll.setWidget(self.content)
        self.root.addWidget(self.scroll, 1)

    def add(self, widget: QWidget, stretch: int = 0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.body.addLayout(layout, stretch)
        return layout

    def add_stretch(self):
        self.body.addStretch(1)


def row(*widgets, spacing: int = 10, stretch_last: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, QWidget):
            layout.addWidget(w)
        else:
            layout.addLayout(w)
    if stretch_last:
        layout.addStretch(1)
    return layout
