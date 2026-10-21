"""Small, stdlib-only SQLite persistence for the UI thread."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
from uuid import uuid4


VERSION_LIMIT = 20


@dataclass(frozen=True)
class Draft:
    id: str
    title: str
    content: str
    updated_at: str


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    """Own a database file and connection, used only by its creating UI thread.

    get_draft/save_draft/append_transcript raise KeyError for a missing draft.
    Drafts are listed newest first; segments oldest first.
    Lists for unknown draft IDs are empty. Drafts are deleted only explicitly.
    Typing is covered by editor undo; versions are kept only around Clean up,
    the newest VERSION_LIMIT per draft.
    """

    def __init__(self, path):
        path = Path(path)
        missing = []
        directory = path.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    request_id TEXT NOT NULL UNIQUE,
                    audio_path TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    content TEXT NOT NULL,
                    label TEXT NOT NULL,
                    suggestions TEXT NOT NULL DEFAULT '[]',
                    dismissed TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
            """)
            self._connection.execute("DROP TABLE IF EXISTS revisions")

    def create_draft(self, title="Untitled"):
        draft = Draft(str(uuid4()), title, "", _now())
        with self._connection:
            self._connection.execute(
                "INSERT INTO drafts (id, title, content, updated_at) VALUES (?, ?, ?, ?)",
                (draft.id, draft.title, draft.content, draft.updated_at),
            )
        return draft

    def list_drafts(self):
        return [Draft(**row) for row in self._connection.execute(
            "SELECT * FROM drafts ORDER BY updated_at DESC, id"
        )]

    def get_draft(self, draft_id):
        row = self._connection.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return Draft(**row)

    def save_draft(self, draft_id, content, title=None):
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            return self._save_draft(self.get_draft(draft_id), content, title)

    def _save_draft(self, draft, content, title=None):
        """Update inside the caller's write transaction."""
        title = draft.title if title is None else title
        if content == draft.content and title == draft.title:
            return draft
        updated_at = _now()
        self._connection.execute(
            "UPDATE drafts SET content = ?, title = ?, updated_at = ? WHERE id = ?",
            (content, title, updated_at, draft.id),
        )
        return Draft(draft.id, title, content, updated_at)

    def append_transcript(self, draft_id, request_id, audio_path, text):
        """Append once per globally unique request ID; retries leave all drafts alone."""
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            draft = self.get_draft(draft_id)
            if self._connection.execute(
                "SELECT 1 FROM segments WHERE request_id = ?", (request_id,)
            ).fetchone():
                return draft
            self._connection.execute(
                "INSERT INTO segments (draft_id, request_id, audio_path, text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (draft_id, request_id, str(audio_path), text, _now()),
            )
            if not text.strip():
                return draft
            content = draft.content + ("\n\n" if draft.content else "") + text.strip()
            return self._save_draft(draft, content)

    def delete_draft(self, draft_id):
        """Remove a draft and its stored ASR text; returns paths of audio still on disk."""
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self.get_draft(draft_id)
            paths = [row[0] for row in self._connection.execute(
                "SELECT audio_path FROM segments WHERE draft_id = ? AND audio_path != ''", (draft_id,))]
            self._connection.execute("DELETE FROM segments WHERE draft_id = ?", (draft_id,))
            self._connection.execute("DELETE FROM versions WHERE draft_id = ?", (draft_id,))
            self._connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        return paths

    def add_version(self, draft_id, content, label, suggestions=()):
        """Keep a snapshot of a draft's text; only the newest VERSION_LIMIT survive."""
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self.get_draft(draft_id)
            cursor = self._connection.execute(
                "INSERT INTO versions (draft_id, content, label, suggestions, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (draft_id, content, label, json.dumps(list(suggestions)), _now()))
            self._connection.execute(
                "DELETE FROM versions WHERE draft_id = ? AND id NOT IN "
                "(SELECT id FROM versions WHERE draft_id = ? ORDER BY id DESC LIMIT ?)",
                (draft_id, draft_id, VERSION_LIMIT))
        return cursor.lastrowid

    def list_versions(self, draft_id):
        """Oldest first; suggestions exclude dismissed ones, keeping their index."""
        versions = []
        for row in self._connection.execute(
                "SELECT * FROM versions WHERE draft_id = ? ORDER BY id", (draft_id,)):
            version = dict(row)
            dismissed = set(json.loads(version.pop("dismissed")))
            version["suggestions"] = [(index, text) for index, text in
                                      enumerate(json.loads(version["suggestions"]))
                                      if index not in dismissed]
            versions.append(version)
        return versions

    def dismiss_suggestion(self, version_id, index):
        with self._connection:
            row = self._connection.execute(
                "SELECT dismissed FROM versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                return
            dismissed = sorted(set(json.loads(row[0])) | {int(index)})
            self._connection.execute("UPDATE versions SET dismissed = ? WHERE id = ?",
                                     (json.dumps(dismissed), version_id))

    def list_segments(self, draft_id):
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM segments WHERE draft_id = ? ORDER BY id", (draft_id,)
        )]

    def list_revisions(self, draft_id):
        """Kept for compatibility; content version history is not stored."""
        return []

    def forget_audio(self, request_id):
        """Clear a finished recording path; keep the ASR text. Returns the old path."""
        with self._connection:
            row = self._connection.execute(
                "SELECT audio_path FROM segments WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None or not row["audio_path"]:
                return None
            self._connection.execute(
                "UPDATE segments SET audio_path = '' WHERE request_id = ?", (request_id,)
            )
            return row["audio_path"]

    def get_setting(self, key, default=None):
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return default if row is None else json.loads(row["value"])

    def set_setting(self, key, value):
        """Persist a JSON-compatible value, preserving its type."""
        encoded = json.dumps(value)
        with self._connection:
            self._connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, encoded),
            )

    def close(self):
        self._connection.close()
