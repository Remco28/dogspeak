"""API keys live in the desktop keyring (Secret Service), never in SQLite or the checkout."""
import gi

gi.require_version('Secret', '1')
from gi.repository import Secret  # noqa: E402

SCHEMA = Secret.Schema.new(
    'io.github.Remco28.Dogspeak.ApiKey', Secret.SchemaFlags.NONE,
    {'address': Secret.SchemaAttributeType.STRING})


class Keyring:
    """Blocking calls; the keyring may prompt to unlock, so keep them off hot paths."""

    def get(self, address):
        return Secret.password_lookup_sync(SCHEMA, {'address': address}, None) or ''

    def set(self, address, key):
        if not key:
            self.clear(address)
            return
        if not Secret.password_store_sync(SCHEMA, {'address': address}, Secret.COLLECTION_DEFAULT,
                                          f'Dogspeak API key for {address}', key, None):
            raise RuntimeError('The keyring refused to store the API key')

    def clear(self, address):
        Secret.password_clear_sync(SCHEMA, {'address': address}, None)
