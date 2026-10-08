"""Palette and global stylesheet. Dark with purple accents."""
from __future__ import annotations

BG = "#0d0a14"
SURFACE = "#161022"
RAISED = "#1f1730"
BORDER = "#2c2240"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#a78bfa"
ACCENT_PRESSED = "#7c3aed"
TEXT = "#ece8f7"
MUTED = "#9a90b5"
DANGER = "#f87171"
SUCCESS = "#4ade80"

RADIUS = 12
RADIUS_SMALL = 10

QSS = f"""
* {{
    font-family: "Inter", "Noto Sans", "Segoe UI", sans-serif;
    font-size: 14px;
    color: {TEXT};
    outline: 0;
}}
QWidget#root, QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel[role="wordmark"] {{
    font-family: "Playfair Display", "DejaVu Serif", Georgia, serif;
    font-size: 46px; font-weight: 600; letter-spacing: 1px; color: {TEXT};
}}
QLabel[role="tagline"], QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="h1"] {{ font-size: 26px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="error"] {{ color: {DANGER}; }}

QLabel[role="wordmark-small"] {{
    font-family: "Playfair Display", "DejaVu Serif", Georgia, serif;
    font-size: 22px; font-weight: 600; color: {TEXT};
}}
QLabel[role="subtitle"] {{ font-size: 17px; color: {MUTED}; }}
QLabel[role="qccode"] {{
    font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
    font-size: 44px; font-weight: 700; letter-spacing: 6px; color: {ACCENT_HOVER};
}}
QLabel[role="notice"] {{
    background: {RAISED}; border: 1px solid {ACCENT}; border-radius: 8px; padding: 8px 12px;
}}
QPushButton[role="primary"][size="large"] {{ padding: 12px 22px; font-size: 15px; }}
QFrame[role="topbar"] {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QToolButton[role="usermenu"] {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 16px; padding: 6px 14px;
}}
QToolButton[role="usermenu"]:hover {{ border-color: {ACCENT}; }}
QToolButton[role="usermenu"]::menu-indicator {{ image: none; width: 0; }}

QFrame[role="sidebar"] {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QPushButton[role="nav"] {{
    background: transparent; border: none; border-radius: 9px; padding: 9px 14px;
    text-align: left; color: {MUTED}; font-size: 14px;
}}
QPushButton[role="nav"]:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton[role="nav"]:checked {{ background: rgba(139, 92, 246, 0.20); color: {TEXT}; font-weight: 600; }}
QLabel[role="section"] {{
    color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; padding: 4px 14px;
}}
QLabel[role="rowtitle"] {{ font-size: 19px; font-weight: 600; }}
QToolButton[role="navbtn"] {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 17px; font-size: 20px;
    padding-bottom: 3px;
}}
QToolButton[role="navbtn"]:hover {{ border-color: {ACCENT}; }}
QToolButton[role="navbtn"]:disabled {{ color: {BORDER}; background: transparent; }}
QToolButton[role="rowarrow"] {{
    background: rgba(13, 10, 20, 0.86); border: 1px solid {ACCENT}; border-radius: 20px;
    font-size: 24px; padding-bottom: 4px; color: {TEXT};
}}
QToolButton[role="rowarrow"]:hover {{ background: {ACCENT}; }}
QLineEdit[role="search"] {{ border-radius: 18px; padding: 7px 16px; background: {BG}; }}

QFrame[role="card"] {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}

QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: {RADIUS_SMALL}px;
    padding: 8px 18px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: none; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[role="link"] {{ background: transparent; border: none; color: {ACCENT_HOVER}; padding: 4px; }}
QPushButton[role="link"]:hover {{ color: {TEXT}; text-decoration: underline; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_SMALL}px;
    padding: 8px 10px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {RAISED}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
}}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QScrollArea, QListView, QTreeView {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {RAISED}; border-radius: 4px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; width: 0; height: 0;
}}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}
QMenu {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""


def apply(app) -> None:
    """Fusion style + matching QPalette (so native-drawn bits stay dark) + QSS."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.Base: SURFACE, QPalette.AlternateBase: RAISED,
        QPalette.Button: RAISED, QPalette.Text: TEXT, QPalette.WindowText: TEXT,
        QPalette.ButtonText: TEXT, QPalette.Highlight: ACCENT, QPalette.HighlightedText: TEXT,
        QPalette.ToolTipBase: RAISED, QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: MUTED,
        QPalette.Link: ACCENT_HOVER,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(MUTED))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
