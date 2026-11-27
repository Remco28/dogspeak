"""Design values and Field Recorder + ON AIR stylesheet for the Qt window (Windows).

Shared colours, spacing and timing come from ui_values.py. Light and dark mode
follow the system palette automatically (re-applied on PaletteChange).
Decisions: docs/DESIGN.md.
"""
from PySide6.QtGui import QGuiApplication, QPalette

from .ui_values import (
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    EDITOR_MAX_WIDTH,
    MIN_WINDOW_WIDTH,
    NARROW_BREAKPOINT,
    PALETTE_DARK,
    PALETTE_LIGHT,
    PANEL_WIDTH,
    RADIUS_CARD,
    RADIUS_CONTROL,
    RADIUS_STRIP,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XS,
)

# Text, in points (system UI font is Segoe UI 9 pt on Windows).
TEXT_CAPTION = 8
TEXT_BODY = 9
TEXT_TITLE = 11
TEXT_EDITOR = 11          # the draft: readable for long dictation
TEXT_LAMP = 8

# Sizes, in pixels.
CONTROL_HEIGHT = 30
ICON = 16
LOGO_HEADER = 24
LOGO_HERO = 26
RADIUS_SMALL = RADIUS_CONTROL
EDITOR_MARGIN = 22        # vertical breathing room around draft text
EDITOR_LINE_SPACING = 120 # percent
METER_HEIGHT = 6

# The pill (status shown over other apps), matching the GNOME extension.
PILL_BACKGROUND = (25, 25, 23, 240)
PILL_BORDER = (255, 255, 255, 28)
PILL_TEXT = (236, 234, 227, 255)
PILL_HINT = (236, 234, 227, 178)
PILL_PADDING_X = 18
PILL_PADDING_Y = 10
PILL_GAP = 10
PILL_DOT = 10
PILL_BAR_HEIGHT = 3
PILL_FROM_BOTTOM = 72     # above the taskbar's top edge
STATE_COLOURS = {
    "recording": PALETTE_DARK["accent"],
    "nearcap": PALETTE_DARK["warn"],
    "transcribing": PALETTE_DARK["warn"],
    "saved": PALETTE_DARK["local"],
    "error": PALETTE_DARK["danger"],
    "info": PALETTE_DARK["muted"],
}

DANGER = PALETTE_LIGHT["danger"]
CAPTION_STYLE = f"color: {PALETTE_LIGHT['muted']};"
DANGER_STYLE = f"color: {DANGER};"

# Motion: instant. No animations anywhere.


def is_dark(source=None):
    """True when the current palette is dark."""
    pal = None
    if source is not None and hasattr(source, "palette"):
        pal = source.palette()
    elif isinstance(source, QPalette):
        pal = source
    if pal is None:
        app = QGuiApplication.instance()
        pal = app.palette() if app is not None else QPalette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def palette(dark=False):
    return PALETTE_DARK if dark else PALETTE_LIGHT


def stylesheet(dark=False):
    """Field Recorder + ON AIR stylesheet for Qt (light or dark)."""
    p = palette(dark)
    return f"""
    QMainWindow, QDialog {{
        background: {p['canvas']};
        color: {p['ink']};
    }}
    QWidget#central, QWidget#editorStage {{
        background: {p['canvas']};
        color: {p['ink']};
    }}
    QLabel {{
        color: {p['ink']};
        background: transparent;
    }}
    QLabel#caption, QLabel[muted="true"] {{
        color: {p['muted']};
    }}
    QLabel#wordmark {{
        color: {p['ink']};
        font-weight: 700;
        font-size: {TEXT_TITLE}pt;
    }}

    /* Slim Field Recorder header */
    QFrame#headerBar {{
        background: {p['surface']};
        border-bottom: 1px solid {p['border']};
    }}
    QLineEdit#titleEntry {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CONTROL}px;
        padding: 4px 8px;
        min-height: {CONTROL_HEIGHT - 8}px;
        selection-background-color: {p['ext']};
        selection-color: {p['ext_ink']};
    }}
    QLineEdit#titleEntry:focus {{
        border-color: {p['ext']};
    }}

    /* Eye-level Hero Instrument Strip */
    QWidget#heroWrap {{
        background: {p['canvas']};
    }}
    QFrame#heroStrip {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_STRIP}px;
    }}
    QFrame#heroStrip[tone="loading"] {{
        background: {p['warn_soft']};
        border-color: {p['warn']};
    }}
    QFrame#heroStrip[tone="recording"] {{
        background: {p['accent_soft']};
        border-color: {p['accent']};
    }}
    QFrame#heroStrip[tone="nearcap"] {{
        background: {p['warn_soft']};
        border-color: {p['warn']};
    }}
    QFrame#heroStrip[tone="transcribing"] {{
        background: {p['local_soft']};
        border-color: {p['local']};
    }}
    QFrame#heroStrip[tone="llm"] {{
        background: {p['ext_soft']};
        border-color: {p['ext']};
    }}
    QFrame#heroStrip[tone="error"] {{
        background: {p['danger_soft']};
        border-color: {p['danger']};
    }}

    QLabel#stateLamp {{
        font-family: Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace;
        font-size: {TEXT_LAMP}pt;
        font-weight: 700;
        padding: 2px 7px;
        border-radius: {RADIUS_CONTROL}px;
        background: {p['local_soft']};
        color: {p['local']};
        border: 1px solid {p['local']};
    }}
    QLabel#stateLamp[lamp="loading"] {{
        background: {p['warn']};
        color: {p['warn_ink']};
        border-color: {p['warn']};
    }}
    QLabel#stateLamp[lamp="recording"] {{
        background: {p['accent']};
        color: {p['accent_ink']};
        border-color: {p['accent']};
    }}
    QLabel#stateLamp[lamp="nearcap"] {{
        background: {p['warn']};
        color: {p['warn_ink']};
        border-color: {p['warn']};
    }}
    QLabel#stateLamp[lamp="transcribing"] {{
        background: {p['local']};
        color: {p['accent_ink']};
        border-color: {p['local']};
    }}
    QLabel#stateLamp[lamp="llm"] {{
        background: {p['ext']};
        color: {p['ext_ink']};
        border-color: {p['ext']};
    }}
    QLabel#stateLamp[lamp="error"] {{
        background: {p['danger']};
        color: {p['accent_ink']};
        border-color: {p['danger']};
    }}

    QLabel#heroStatus {{
        color: {p['ink']};
        font-weight: 600;
    }}
    QLabel#heroStatus[error="true"] {{
        color: {p['danger']};
    }}
    QLabel#heroTimer {{
        font-family: Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace;
        font-weight: 700;
        color: {p['ink']};
    }}
    QLabel#heroHint {{
        color: {p['muted']};
        font-size: {TEXT_CAPTION}pt;
    }}

    /* Draft sheet */
    QPlainTextEdit {{
        border: none;
        background: {p['canvas']};
        color: {p['ink']};
        selection-background-color: {p['ext']};
        selection-color: {p['ext_ink']};
    }}
    QPlainTextEdit[locked="true"] {{
        color: {p['muted']};
    }}

    /* Bottom Instrument Dock */
    QWidget#dockWrap {{
        background: {p['canvas']};
    }}
    QFrame#dock {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
    }}
    QFrame#dockDivider {{
        background: {p['border']};
        max-width: 1px;
        min-width: 1px;
    }}

    /* Buttons & Inputs */
    QPushButton, QToolButton {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CONTROL}px;
        padding: 4px 10px;
        min-height: {CONTROL_HEIGHT - 8}px;
        font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {p['canvas']};
        border-color: {p['ink']};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {p['surface']};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {p['muted']};
        background: {p['surface']};
        border-color: {p['border']};
    }}
    QToolButton#flatBtn {{
        background: transparent;
        border: 1px solid transparent;
        padding: 4px 8px;
    }}
    QToolButton#flatBtn:hover {{
        background: {p['elevated']};
        border-color: {p['border']};
    }}
    QToolButton#menu {{
        padding: 4px 8px;
    }}
    QToolButton#menu::menu-indicator {{ image: none; width: 0; }}
    QToolButton#versions {{ padding-right: {SPACE_L}px; }}

    QPushButton#recordBtn[recording="true"] {{
        background: {p['accent']};
        color: {p['accent_ink']};
        border-color: {p['accent']};
    }}
    QPushButton#recordBtn[recording="true"]:hover {{
        background: {p['accent_hover']};
        border-color: {p['accent_hover']};
    }}
    QPushButton#cleanBtn {{
        background: {p['ext']};
        color: {p['ext_ink']};
        border-color: {p['ext']};
    }}
    QPushButton#cleanBtn:hover {{
        background: {p['ext_hover']};
        border-color: {p['ext_hover']};
    }}
    QPushButton#cleanBtn:disabled {{
        background: {p['surface']};
        color: {p['muted']};
        border-color: {p['border']};
    }}
    QPushButton#copyClearBtn {{
        background: {p['ink']};
        color: {p['canvas']};
        border-color: {p['ink']};
    }}
    QPushButton#copyClearBtn:hover {{
        background: {p['muted']};
        border-color: {p['muted']};
    }}
    QPushButton#copyClearBtn:disabled {{
        background: {p['surface']};
        color: {p['muted']};
        border-color: {p['border']};
    }}

    QLineEdit, QComboBox {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CONTROL}px;
        padding: 4px 8px;
        min-height: {CONTROL_HEIGHT - 8}px;
    }}
    QLineEdit:focus, QComboBox:focus {{
        border-color: {p['ext']};
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        color: {p['muted']};
        background: {p['surface']};
    }}
    QComboBox QAbstractItemView {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        selection-background-color: {p['ext_soft']};
        selection-color: {p['ink']};
    }}

    /* Suggestions drawer & cards */
    QFrame#suggestionsPanel {{
        background: {p['surface']};
        border-left: 1px solid {p['border']};
    }}
    QFrame#suggestionsPanel QScrollArea, QWidget#suggestionsContent {{
        background: {p['surface']};
    }}
    QFrame#card {{
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
        background: {p['elevated']};
    }}

    /* Banner & Notice */
    QFrame#banner {{
        background: {p['warn_soft']};
        border-bottom: 1px solid {p['warn']};
    }}
    QFrame#banner[error="true"] {{
        background: {p['danger_soft']};
        border-bottom: 1px solid {p['danger']};
    }}
    QFrame#notice {{
        background: {p['local_soft']};
        border-top: 1px solid {p['local']};
    }}

    /* Menus, Lists & Settings */
    QMenu {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 5px 14px;
        border-radius: {RADIUS_CONTROL}px;
    }}
    QMenu::item:selected {{
        background: {p['surface']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['border']};
        margin: 4px 6px;
    }}
    QGroupBox {{
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
        margin-top: 10px;
        padding-top: 10px;
        font-weight: 700;
        color: {p['ink']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QListWidget {{
        background: {p['elevated']};
        color: {p['ink']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
    }}
    QListWidget::item {{
        border-bottom: 1px solid {p['border']};
    }}
    QListWidget::item:selected {{
        background: {p['surface']};
    }}
    """
