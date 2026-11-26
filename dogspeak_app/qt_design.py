"""Design values for the Qt window (Windows). Use these names; never type a size in place.

Colours come from the system palette, so light and dark mode follow Windows.
The pill and the state dots keep the GNOME overlay's colours, so both
platforms show the same states the same way. Decisions: docs/DESIGN.md.
"""

# Spacing, multiples of 4.
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24

# Text, in points (the system font is Segoe UI 9 pt).
TEXT_CAPTION = 8
TEXT_BODY = 9
TEXT_TITLE = 11
TEXT_EDITOR = 11          # the draft: readable for long dictation

# Sizes, in pixels.
CONTROL_HEIGHT = 30
ICON = 16
PANEL_WIDTH = 320
RADIUS_SMALL = 4
RADIUS_CARD = 8
EDITOR_MARGIN = 24        # around the draft text
EDITOR_LINE_SPACING = 115 # percent

# The pill (status shown over other apps), matching the GNOME extension.
PILL_BACKGROUND = (30, 30, 30, 235)
PILL_BORDER = (255, 255, 255, 20)
PILL_TEXT = (255, 255, 255, 255)
PILL_HINT = (255, 255, 255, 178)
PILL_PADDING_X = 20
PILL_PADDING_Y = 10
PILL_GAP = 12
PILL_DOT = 12
PILL_FROM_BOTTOM = 72     # above the taskbar's top edge
STATE_COLOURS = {
    "recording": "#e01b24",
    "transcribing": "#f6d32d",
    "saved": "#33d17a",
    "error": "#ff7800",
    "info": "#9a9996",
}

DANGER = "#c62828"         # errors and destructive actions
CAPTION_STYLE = "color: palette(placeholder-text);"
DANGER_STYLE = f"color: {DANGER};"

# Motion: instant. No animations anywhere.


def stylesheet():
    """The few rules Qt's own Windows style doesn't cover: cards and the banner."""
    return f"""
    QFrame#card {{
        border: 1px solid palette(mid);
        border-radius: {RADIUS_CARD}px;
        background: palette(base);
    }}
    QFrame#banner {{
        background: palette(alternate-base);
        border-bottom: 1px solid palette(mid);
    }}
    QFrame#notice {{
        background: palette(alternate-base);
        border-top: 1px solid palette(mid);
    }}
    QFrame#banner[error="true"] {{
        background: rgba(198, 40, 40, 0.12);
        border-bottom: 1px solid {DANGER};
    }}
    QPlainTextEdit {{ border: none; background: palette(base); }}
    QToolButton#menu::menu-indicator {{ image: none; width: 0; }}
    QToolButton#versions {{ padding-right: {SPACE_L}px; }}
    """
