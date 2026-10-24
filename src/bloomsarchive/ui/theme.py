"""Palette and global stylesheet. Dark with purple accents.

Text size is one app-wide scale (Settings → App → Text size). Every
font-size in the stylesheet goes through it, and painted text uses px().
Layout sizes stay fixed, so the scale is clamped to a range the layouts
were checked at.
"""
from __future__ import annotations

import re

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

_QSS = f"""
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
QToolButton[role="filter"], QPushButton[role="chip"] {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 7px 14px;
}}
QToolButton[role="filter"]:hover, QPushButton[role="chip"]:hover {{ border-color: {ACCENT}; }}
QToolButton[role="filter"][active="true"], QPushButton[role="chip"]:checked {{
    background: rgba(139, 92, 246, 0.22); border-color: {ACCENT}; color: {TEXT};
}}
QToolButton[role="filter"]::menu-indicator {{ image: none; width: 0; }}
QToolButton[role="letter"] {{
    background: transparent; border: none; border-radius: 6px; color: {MUTED};
    font-size: 11px /* fixed */; font-weight: 600; padding: 0;
}}
QToolButton[role="letter"]:hover {{ background: {RAISED}; color: {ACCENT_HOVER}; }}
QToolButton[role="letter"]:disabled {{ color: {BORDER}; }}
QComboBox {{ min-width: 120px; }}
QLabel[role="detailtitle"] {{ font-size: 36px; font-weight: 700; }}
QScrollArea[role="plain"], QWidget[role="plain"] {{ background: transparent; }}
QPushButton[role="tag"] {{
    background: rgba(139, 92, 246, 0.16); border: 1px solid rgba(167, 139, 250, 0.35);
    border-radius: 10px; padding: 3px 10px; font-size: 12px; color: {TEXT};
}}
QPushButton[role="tag"]:hover {{ background: rgba(139, 92, 246, 0.40); border-color: {ACCENT_HOVER}; }}
QPushButton[role="primary"], QPushButton[role="toggle"] {{ font-size: 15px; }}
QLabel[role="tag"] {{
    background: rgba(139, 92, 246, 0.16); border: 1px solid rgba(167, 139, 250, 0.35);
    border-radius: 10px; padding: 3px 10px; font-size: 12px; color: {TEXT};
}}
QLabel[role="tagline"] {{ font-style: italic; color: {ACCENT_HOVER}; font-size: 15px; }}
QLabel[role="overview"] {{ font-size: 15px; color: #d9d3ec; }}
QLabel[role="fieldlabel"] {{ color: {MUTED}; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}
QPushButton[role="toggle"] {{
    background: rgba(31, 23, 48, 0.85); border: 1px solid {BORDER}; border-radius: 10px;
    padding: 12px 18px; font-size: 15px;
}}
QPushButton[role="toggle"]:hover {{ border-color: {ACCENT_HOVER}; }}
QPushButton[role="toggle"]:checked {{ background: rgba(139, 92, 246, 0.28); border-color: {ACCENT}; }}
QFrame[role="popup"] {{ background: {RAISED}; border: 1px solid {ACCENT}; border-radius: 12px; }}
QFrame[role="popup"] QPushButton[role="chip"] {{ padding: 6px 10px; }}
QMenu::item:checked {{ color: {ACCENT_HOVER}; font-weight: 600; }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QLineEdit[role="search"] {{ border-radius: 18px; padding: 7px 16px; background: {BG}; }}

QLabel[role="hint"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="hint"][tone="ok"] {{ color: {SUCCESS}; }}
QLabel[role="hint"][tone="error"] {{ color: {DANGER}; }}
QLabel[role="pill"] {{
    background: rgba(139, 92, 246, 0.22); border: 1px solid {ACCENT}; border-radius: 9px;
    padding: 1px 8px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; color: {ACCENT_HOVER};
}}
QLabel[role="toast"] {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {ACCENT};
    border-radius: 10px; padding: 10px 18px; font-size: 14px;
}}
QLabel[role="toast"][tone="error"] {{ border-color: {DANGER}; }}
QPushButton[role="icon"] {{ padding: 0; min-width: 38px; max-width: 38px; font-size: 15px; }}
QLineEdit[role="code"] {{
    font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
    font-size: 26px; letter-spacing: 6px; padding: 10px 14px;
}}
QPlainTextEdit[role="mono"] {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px;
}}
QFrame[role="addrrow"] {{ background: transparent; border: 1px solid transparent; border-radius: 12px; }}
QFrame[role="addrrow"][inuse="true"] {{ background: rgba(139, 92, 246, 0.08); border-color: rgba(139, 92, 246, 0.55); }}
QFrame[role="serverrow"] {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame[role="serverrow"][inuse="true"] {{ border-color: {ACCENT}; }}

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
QSpinBox, QDoubleSpinBox {{ padding-right: 30px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 26px; background: {RAISED}; border-left: 1px solid {BORDER};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right; border-top-right-radius: {RADIUS_SMALL}px;
    border-bottom: 1px solid {BORDER};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right; border-bottom-right-radius: {RADIUS_SMALL}px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {ACCENT}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(@ARROWS@/up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url(@ARROWS@/down.svg); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
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


# ------------------------------------------------------------------ text size

TEXT_SCALE_MIN = 85          # percent
TEXT_SCALE_MAX = 130
TEXT_SCALE_STEP = 5
TEXT_SCALE_DEFAULT = 100

_scale = 1.0
_notifier = None


def clamp_text_scale(percent) -> int:
    try:
        v = int(round(float(percent)))
    except (TypeError, ValueError):
        return TEXT_SCALE_DEFAULT
    return max(TEXT_SCALE_MIN, min(TEXT_SCALE_MAX, v))


def text_scale() -> float:
    return _scale


def set_text_scale(percent) -> int:
    """Set the scale (clamped); returns the percent actually used."""
    global _scale
    pct = clamp_text_scale(percent)
    _scale = pct / 100
    return pct


def px(size: float) -> int:
    """A font pixel size at the current text scale."""
    return max(1, round(size * _scale))


def _arrow_dir() -> str:
    """Spin-box arrow images, written once to the cache (QSS can only load images from files)."""
    from ..config import cache_dir

    d = cache_dir() / "ui"
    shapes = {"up": "M2 7 L6 3 L10 7", "down": "M2 4 L6 8 L10 4"}
    try:
        d.mkdir(parents=True, exist_ok=True)
        for name, path in shapes.items():
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                   f'<path d="{path}" fill="none" stroke="{TEXT}" stroke-width="1.8" '
                   f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
            f = d / f"{name}.svg"
            if not f.exists() or f.read_text() != svg:
                f.write_text(svg)
    except OSError:
        return ""
    return d.as_posix()


def qss() -> str:
    """The stylesheet at the current text scale. Sizes marked /* fixed */ don't scale."""
    out = re.sub(r"font-size: (\d+)px(?! /\* fixed)", lambda m: f"font-size: {px(int(m[1]))}px", _QSS)
    return out.replace("@ARROWS@", _arrow_dir())


def notifier():
    """A QObject whose changed() fires after the text scale is re-applied."""
    global _notifier
    if _notifier is None:
        from PySide6.QtCore import QObject, Signal

        class _Notifier(QObject):
            changed = Signal()

        _notifier = _Notifier()
    return _notifier


def apply_text_scale(app, percent) -> int:
    """Change the text size live: restyle every widget, then tell painted views."""
    pct = set_text_scale(percent)
    app.setStyleSheet(qss())
    notifier().changed.emit()
    return pct


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
    app.setStyleSheet(qss())
