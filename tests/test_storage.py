"""Storage contract tests; all databases live in disposable Hermes scratch dirs."""

import dataclasses
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "drafts.sqlite3"

    def open_store(self):
        from dogspeak_app.storage import Store

        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def test_versions_keep_the_newest_twenty_and_track_dismissed_suggestions(self):
        from dogspeak_app.storage import VERSION_LIMIT

        store = self.open_store()
        draft, other = store.create_draft(), store.create_draft()
        first = store.add_version(draft.id, "v0", "Original")
        cleaned = store.add_version(draft.id, "v1", "Clean up · Essay", ["a", "b", "c"])
        store.add_version(other.id, "elsewhere", "Original")
        store.dismiss_suggestion(cleaned, 1)
        store.dismiss_suggestion(cleaned, 1)
        store.dismiss_suggestion(999, 0)
        versions = store.list_versions(draft.id)
        self.assertEqual([v["content"] for v in versions], ["v0", "v1"])
        self.assertEqual(versions[1]["suggestions"], [(0, "a"), (2, "c")])
        self.assertEqual(versions[0]["id"], first)
        for n in range(2, VERSION_LIMIT + 5):
            store.add_version(draft.id, f"v{n}", "Your edits")
        versions = store.list_versions(draft.id)
        self.assertEqual(len(versions), VERSION_LIMIT)
        self.assertEqual(versions[-1]["content"], f"v{VERSION_LIMIT + 4}")
        self.assertEqual(len(store.list_versions(other.id)), 1)
        with self.assertRaises(KeyError):
            store.add_version("missing", "x", "Original")
        store.delete_draft(draft.id)
        self.assertEqual(store.list_versions(draft.id), [])
        self.assertEqual(len(store.list_versions(other.id)), 1)

    def test_delete_draft_removes_it_and_its_segments_only(self):
        store = self.open_store()
        keep, gone = store.create_draft("Keep"), store.create_draft("Gone")
        store.append_transcript(keep.id, "k1", "", "kept text")
        store.append_transcript(gone.id, "g1", "/tmp/old.wav", "gone text")
        self.assertEqual(store.delete_draft(gone.id), ["/tmp/old.wav"])
        self.assertEqual([d.id for d in store.list_drafts()], [keep.id])
        self.assertEqual(store.list_segments(gone.id), [])
        self.assertEqual(len(store.list_segments(keep.id)), 1)
        with self.assertRaises(KeyError):
            store.delete_draft(gone.id)
        store.close()
        self.assertEqual([d.id for d in self.open_store().list_drafts()], [keep.id])

    def test_created_drafts_survive_reopen(self):
        from dogspeak_app.storage import Draft

        store = self.open_store()
        self.assertEqual(store.list_drafts(), [])
        first = store.create_draft()
        second = store.create_draft("Notes 'quoted'; DROP TABLE drafts; --")
        self.assertTrue(dataclasses.is_dataclass(first))
        self.assertIsInstance(first, Draft)
        self.assertEqual(first.title, "Untitled")
        self.assertEqual(first.content, "")
        self.assertTrue(all(isinstance(value, str) for value in dataclasses.astuple(first)))
        self.assertTrue(first.id)
        self.assertTrue(first.updated_at)
        self.assertNotEqual(first.id, second.id)
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.get_draft(first.id), first)
        self.assertEqual(reopened.get_draft(second.id), second)
        self.assertEqual(reopened.list_drafts(), [second, first])

    def test_saves_do_not_keep_content_version_history(self):
        store = self.open_store()
        draft = store.create_draft("Original title")
        original = "  Typed text\n\nwith whitespace  \n"
        saved = store.save_draft(draft.id, original)
        self.assertEqual(saved.content, original)
        self.assertEqual(saved.title, draft.title)
        self.assertEqual(store.save_draft(draft.id, original), saved)
        renamed = store.save_draft(draft.id, original, title="New title")
        self.assertEqual(renamed.title, "New title")
        cleared = store.save_draft(draft.id, "")
        self.assertEqual(cleared.content, "")
        self.assertEqual(store.list_revisions(draft.id), [])
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.get_draft(draft.id), cleared)
        self.assertEqual(reopened.list_revisions(draft.id), [])

    def test_transcript_appends_to_latest_origin_without_changing_raw_segment(self):
        store = self.open_store()
        origin = store.create_draft("Recording origin")
        other = store.create_draft("Now selected")
        peer = self.open_store()
        latest = "Typed after recording started; keep 'quotes' and negation: NOT"
        peer.save_draft(origin.id, latest)
        raw = " \n  Spoken paragraph.\nSecond line.  \n"
        audio = str(Path(self.temp.name) / "audio 'original'.wav")
        result = store.append_transcript(origin.id, "job-1", audio, raw)
        self.assertEqual(result.content, latest + "\n\n" + raw.strip())
        self.assertEqual(result.title, origin.title)
        self.assertEqual(store.get_draft(other.id), other)
        segments = store.list_segments(origin.id)
        self.assertEqual(len(segments), 1)
        self.assertIsInstance(segments[0], dict)
        self.assertEqual(segments[0]["draft_id"], origin.id)
        self.assertEqual(segments[0]["request_id"], "job-1")
        self.assertEqual(segments[0]["audio_path"], audio)
        self.assertEqual(segments[0]["text"], raw)
        self.assertTrue(segments[0]["created_at"])
        self.assertEqual(store.list_segments(other.id), [])
        self.assertEqual(store.list_revisions(origin.id), [])
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.get_draft(origin.id), result)
        self.assertEqual(reopened.list_segments(origin.id), segments)

    def test_request_id_is_idempotent_after_reopen_or_wrong_origin_retry(self):
        store = self.open_store()
        origin = store.create_draft()
        other = store.create_draft("Other")
        store.append_transcript(origin.id, "same-job", "first.wav", "first text")
        newer = store.save_draft(origin.id, "User revised the appended text.")
        segments = store.list_segments(origin.id)
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.append_transcript(
            origin.id, "same-job", "different.wav", "duplicate"), newer)
        self.assertEqual(reopened.append_transcript(
            other.id, "same-job", "different.wav", "duplicate"), other)
        self.assertEqual(reopened.list_segments(origin.id), segments)
        self.assertEqual(reopened.list_revisions(origin.id), [])
        self.assertEqual(reopened.list_segments(other.id), [])
        self.assertEqual(reopened.list_revisions(other.id), [])

    def test_empty_transcripts_are_retained_without_modifying_drafts(self):
        store = self.open_store()
        for content in ("", "Existing text \n\n", "   "):
            with self.subTest(content=content):
                draft = store.create_draft()
                draft = store.save_draft(draft.id, content)
                for index, raw in enumerate(("", " \n\t ")):
                    result = store.append_transcript(
                        draft.id, draft.id + str(index), "silent.wav", raw)
                    self.assertEqual(result, draft)
                self.assertEqual([row["text"] for row in store.list_segments(draft.id)],
                                 ["", " \n\t "])
                self.assertEqual(store.list_revisions(draft.id), [])

    def test_settings_round_trip_values_and_defaults(self):
        store = self.open_store()
        key = "microphone 'quoted'; DROP TABLE drafts; --"
        sentinel = object()
        self.assertIsNone(store.get_setting(key))
        self.assertIs(store.get_setting(key, sentinel), sentinel)
        for value in ("alsa_input.local", None, False, 12, {"mode": "local", "ids": [1, 2]}):
            with self.subTest(value=value):
                store.set_setting(key, value)
                self.assertEqual(store.get_setting(key, "fallback"), value)
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.get_setting(key), {"mode": "local", "ids": [1, 2]})
        self.assertEqual(reopened.list_drafts(), [])

    def test_private_permissions_on_new_and_existing_storage(self):
        self.path = self.path.parent / "nested" / self.path.name
        old_umask = os.umask(0)
        try:
            store = self.open_store()
            draft = store.create_draft()
        finally:
            os.umask(old_umask)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.parent.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        store.close()
        self.path.parent.chmod(0o755)
        self.path.chmod(0o666)
        reopened = self.open_store()
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(reopened.get_draft(draft.id), draft)

    def test_foreign_keys_prevent_orphaned_history(self):
        store = self.open_store()
        with self.assertRaises(sqlite3.IntegrityError):
            with store._connection:
                store._connection.execute(
                    "INSERT INTO segments (draft_id, request_id, audio_path, text, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("missing", "orphan-job", "audio.wav", "orphan", "now"),
                )

    def test_database_symlink_is_rejected_without_modifying_target(self):
        target = Path(self.temp.name) / "unrelated.sqlite3"
        with sqlite3.connect(target) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.close()
        original = target.read_bytes()
        target.chmod(0o644)
        self.path.parent.mkdir()
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.open_store()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_missing_draft_raises_keyerror_without_partial_writes(self):
        store = self.open_store()
        missing = "unknown 'id'; DROP TABLE drafts; --"
        operations = (
            lambda: store.get_draft(missing),
            lambda: store.save_draft(missing, "text"),
            lambda: store.append_transcript(missing, "retryable-job", "audio.wav", "text"),
        )
        for operation in operations:
            with self.assertRaises(KeyError) as error:
                operation()
            self.assertEqual(error.exception.args, (missing,))
        self.assertEqual(store.list_drafts(), [])
        draft = store.create_draft()
        result = store.append_transcript(draft.id, "retryable-job", "audio.wav", "text")
        self.assertEqual(result.content, "text")

    def test_failed_segment_rolls_back_draft_and_allows_retry(self):
        store = self.open_store()
        draft = store.create_draft()
        draft = store.save_draft(draft.id, "Original user edits")
        with store._connection:
            store._connection.execute("""
                CREATE TRIGGER fail_segment BEFORE INSERT ON segments
                BEGIN SELECT RAISE(ABORT, 'injected write failure'); END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected write failure"):
            store.append_transcript(draft.id, "retry-job", "audio.wav", "append")
        self.assertEqual(store.get_draft(draft.id), draft)
        self.assertEqual(store.list_segments(draft.id), [])
        with store._connection:
            store._connection.execute("DROP TRIGGER fail_segment")
        result = store.append_transcript(draft.id, "retry-job", "audio.wav", "append")
        self.assertEqual(result.content, draft.content + "\n\nappend")
        self.assertEqual(len(store.list_segments(draft.id)), 1)
        self.assertEqual(store.list_revisions(draft.id), [])

    def test_forget_audio_clears_path_without_changing_text(self):
        store = self.open_store()
        draft = store.create_draft()
        audio = Path(self.temp.name) / "keep-me.wav"
        audio.write_bytes(b"wav")
        saved = store.append_transcript(draft.id, "job-audio", audio, "Spoken text")
        self.assertEqual(store.forget_audio("job-audio"), str(audio))
        self.assertIsNone(store.forget_audio("job-audio"))
        self.assertEqual(store.get_draft(draft.id), saved)
        self.assertEqual(store.list_segments(draft.id)[0]["text"], "Spoken text")
        self.assertEqual(store.list_segments(draft.id)[0]["audio_path"], "")

    def test_existing_database_with_revisions_is_migrated(self):
        store = self.open_store()
        draft = store.append_transcript(store.create_draft().id, "old-job", "old.wav", "old")
        with store._connection:
            store._connection.execute("""
                CREATE TABLE revisions (id INTEGER PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    content TEXT NOT NULL, created_at TEXT NOT NULL)""")
            store._connection.execute(
                "INSERT INTO revisions (draft_id, content, created_at) VALUES (?, ?, ?)",
                (draft.id, "old snapshot", "then"))
        store.close()
        reopened = self.open_store()
        self.assertIsNone(reopened._connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'revisions'").fetchone())
        self.assertEqual(reopened.get_draft(draft.id), draft)
        self.assertEqual(reopened.forget_audio("old-job"), "old.wav")


if __name__ == "__main__":
    unittest.main()
