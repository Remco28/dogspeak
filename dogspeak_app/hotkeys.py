"""Global keyboard shortcuts.

parse() is plain Python and tested everywhere. WindowsHotkeys registers with
RegisterHotKey and hears WM_HOTKEY through a Qt native event filter; it is
only constructed on Windows. Other systems get NoHotkeys.
"""

DEFAULT = "Ctrl+Alt+R"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
VK_RETURN, VK_ESCAPE, VK_SPACE = 0x0D, 0x1B, 0x20
WM_HOTKEY = 0x0312

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
              "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN}
# Keys the user should not lose to a global shortcut, even with modifiers.
SESSION_KEYS = {"stop": (0, VK_RETURN), "cancel": (0, VK_ESCAPE)}


def parse(text):
    """'Ctrl+Alt+R' -> (modifiers, virtual key). Raises ValueError with a sentence."""
    parts = [p.strip() for p in (text or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("Choose a shortcut")
    *names, key = parts
    modifiers = 0
    for name in names:
        flag = _MODIFIERS.get(name.lower())
        if flag is None:
            raise ValueError(f"{name} is not a modifier key")
        modifiers |= flag
    if not modifiers & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise ValueError("A shortcut needs Ctrl, Alt or Win, or it would take over typing")
    if modifiers & MOD_WIN and not modifiers & (MOD_CONTROL | MOD_ALT):
        # Windows reserves most Win+key combinations, including Win+R.
        raise ValueError("Windows keeps most Win+key shortcuts; add Ctrl or Alt")
    upper = key.upper()
    if len(upper) == 1 and (upper.isascii() and upper.isalnum()):
        return modifiers, ord(upper)
    if upper == "SPACE":
        return modifiers, VK_SPACE
    if upper.startswith("F") and upper[1:].isdigit() and 1 <= int(upper[1:]) <= 24:
        return modifiers, 0x6F + int(upper[1:])
    raise ValueError(f"{key} can't be used; pick a letter, a digit, Space or F1–F24")


def label(text):
    """How a shortcut is written in the interface: 'Ctrl+Alt+R'."""
    return "+".join(p.strip().capitalize() if len(p.strip()) > 1 else p.strip().upper()
                    for p in text.split("+"))


class NoHotkeys:
    """No global shortcuts on this system; in-app shortcuts still work."""

    available = False

    def set_shortcut(self, text, callback):
        return "Global shortcuts are only available on Windows here"

    def set_session_keys(self, keys, callback):
        pass

    def close(self):
        pass


def windows_hotkeys(window):
    """WindowsHotkeys bound to a top-level Qt widget's HWND (survives modal loops)."""
    import ctypes
    from ctypes import wintypes

    from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.UnregisterHotKey.restype = wintypes.BOOL

    class WindowsHotkeys(QAbstractNativeEventFilter):
        available = True

        def __init__(self):
            super().__init__()
            self.hwnd = int(window.winId())
            self.callbacks = {}     # id -> callback
            self.next_id = 1
            self.shortcut_id = None
            self.session_ids = {}   # name -> id
            QCoreApplication.instance().installNativeEventFilter(self)

        def _register(self, modifiers, key, callback):
            hotkey_id = self.next_id
            self.next_id += 1
            if not user32.RegisterHotKey(self.hwnd, hotkey_id, modifiers | MOD_NOREPEAT, key):
                return None
            self.callbacks[hotkey_id] = callback
            return hotkey_id

        def _unregister(self, hotkey_id):
            if hotkey_id is not None:
                user32.UnregisterHotKey(self.hwnd, hotkey_id)
                self.callbacks.pop(hotkey_id, None)

        def set_shortcut(self, text, callback):
            """Register the recording shortcut; returns None or a sentence saying why not."""
            try:
                modifiers, key = parse(text)
            except ValueError as error:
                return str(error)
            self._unregister(self.shortcut_id)
            self.shortcut_id = self._register(modifiers, key, callback)
            if self.shortcut_id is None:
                return f"{label(text)} is already used by another app; choose another in Settings"
            return None

        def set_session_keys(self, keys, callback):
            """Hold Enter and Esc system-wide only while a recording needs them."""
            for name in list(self.session_ids):
                if name not in keys:
                    self._unregister(self.session_ids.pop(name))
            for name in keys:
                if name not in self.session_ids and name in SESSION_KEYS:
                    modifiers, key = SESSION_KEYS[name]
                    hotkey_id = self._register(modifiers, key, lambda n=name: callback(n))
                    if hotkey_id is not None:
                        self.session_ids[name] = hotkey_id

        def nativeEventFilter(self, event_type, message):
            if bytes(event_type) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.hWnd == self.hwnd:
                    callback = self.callbacks.get(int(msg.wParam))
                    if callback is not None:
                        callback()
                        return True, 0
            return False, 0

        def close(self):
            for hotkey_id in list(self.callbacks):
                self._unregister(hotkey_id)
            self.session_ids.clear()
            self.shortcut_id = None
            QCoreApplication.instance().removeNativeEventFilter(self)

    return WindowsHotkeys()
