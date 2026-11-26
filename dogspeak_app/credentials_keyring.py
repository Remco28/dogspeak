"""API keys in the system credential store through the keyring package.

On Windows that is Windows Credential Manager. Same interface as
credentials.Keyring (libsecret, used by the GTK app on Linux).
"""

SERVICE = "Dogspeak"
# Windows Credential Manager holds 2560 bytes, stored as UTF-16.
MAX_KEY_CHARS = 1200


class Keyring:
    """Blocking calls; keep them off the UI thread."""

    def __init__(self, backend=None):
        if backend is None:
            import keyring as backend
        self._keyring = backend

    def get(self, address):
        return self._keyring.get_password(SERVICE, address) or ""

    def set(self, address, key):
        if not key:
            self.clear(address)
            return
        if len(key) > MAX_KEY_CHARS:
            raise RuntimeError("That API key is too long for Windows Credential Manager")
        self._keyring.set_password(SERVICE, address, key)

    def clear(self, address):
        try:
            self._keyring.delete_password(SERVICE, address)
        except Exception as error:
            # Deleting a key that was never stored is fine.
            if type(error).__name__ != "PasswordDeleteError":
                raise
