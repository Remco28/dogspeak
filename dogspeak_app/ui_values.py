"""Shared design tokens and timing values for Dogspeak (GTK and Qt).

Field Recorder + ON AIR aesthetic: warm matte paper/graphite surfaces, crisp
1px hardware hairlines, signal-orange ON AIR accent, and clear local vs.
external state signifiers. Decisions are recorded in docs/DESIGN.md.
"""

# Window geometry — narrow enough to snap beside a terminal or IDE.
MIN_WINDOW_WIDTH = 440
DEFAULT_WINDOW_WIDTH = 1040
DEFAULT_WINDOW_HEIGHT = 720
NARROW_BREAKPOINT = 720
EDITOR_MAX_WIDTH = 720
PANEL_WIDTH = 300

# Timing and recording limits (controller auto-stops at 115s, 5s below engine 120s cap).
CLIPBOARD_TIMEOUT_MS = 3000
FEEDBACK_SECONDS = 5
MAX_RECORDING_SECONDS = 115
NEAR_CAP_SECONDS = 95
MAX_RECORDING_LABEL = '1:55'

# Spacing (multiples of 4 px) and hardware radii.
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24

RADIUS_CONTROL = 4
RADIUS_CARD = 6
RADIUS_STRIP = 6

# Field Recorder + ON AIR palettes (WCAG-checked for light and dark).
PALETTE_LIGHT = {
    'canvas': '#F3F1EB',        # warm matte field paper (editor)
    'surface': '#E7E4DC',       # machined warm putty (header, hero strip, dock, rail)
    'elevated': '#FAF9F5',      # raised paper key / card
    'ink': '#1C1B19',           # deep graphite text
    'muted': '#636058',         # captions, hints, gutter numbers
    'border': '#C9C4B8',        # 1px crisp hardware hairline
    'line_highlight': '#EAE6DC',
    'accent': '#E8551E',        # Braun signal orange / ON AIR lamp
    'accent_hover': '#D24915',
    'accent_ink': '#FFFFFF',
    'accent_soft': '#F4DFD4',
    'local': '#2C7A4F',         # local Bark 3.0 ready / verified
    'local_soft': '#DCECE2',
    'warn': '#B87810',          # warming up / approaching 1:55 cap
    'warn_ink': '#1C160A',
    'warn_soft': '#F2E7CF',
    'ext': '#355C8A',           # external LLM clean up (cloud outbound)
    'ext_hover': '#2B4C73',
    'ext_ink': '#FFFFFF',
    'ext_soft': '#DDE5EE',
    'danger': '#C62828',        # errors and destructive actions
    'danger_soft': '#F4DCDC',
}

PALETTE_DARK = {
    'canvas': '#191917',        # matte anodized graphite (editor)
    'surface': '#222220',       # recessed instrument panel
    'elevated': '#2B2B28',      # raised hardware key / card
    'ink': '#ECEAE3',           # warm off-white text
    'muted': '#969389',         # captions, hints, gutter numbers
    'border': '#3A3A35',        # 1px crisp hardware hairline
    'line_highlight': '#232320',
    'accent': '#F0622A',        # Braun signal orange / ON AIR lamp
    'accent_hover': '#FF753E',
    'accent_ink': '#FFFFFF',
    'accent_soft': '#33221A',
    'local': '#55B37C',         # local Bark 3.0 ready / verified
    'local_soft': '#1E2D24',
    'warn': '#E0A229',          # warming up / approaching 1:55 cap
    'warn_ink': '#1A1408',
    'warn_soft': '#31291A',
    'ext': '#6B99CF',           # external LLM clean up (cloud outbound)
    'ext_hover': '#7EA9DA',
    'ext_ink': '#101822',
    'ext_soft': '#1E2733',
    'danger': '#EF5350',        # errors and destructive actions
    'danger_soft': '#351F1F',
}


def format_duration(seconds):
    """Format seconds as M:SS for hardware readouts."""
    seconds = int(max(0, seconds))
    return f'{seconds // 60}:{seconds % 60:02}'


def format_cap_readout(seconds):
    """Elapsed / single-take limit readout, e.g. '0:52 / 1:55'."""
    return f'{format_duration(seconds)} / {MAX_RECORDING_LABEL}'
