"""Clean up: purpose, the provider request, versions and suggestions. No GTK here.

Runs on the UI thread except the provider call, which goes through `spawn` and
comes back through `dispatch`, like the Controller's capture work.
"""
import json
import time

from . import llm

PRESETS = ('Prompt for an AI agent', 'Essay', 'Email', 'Notes')
RECENT_LIMIT = 5


class Cleanup:
    def __init__(self, controller, credentials, client_factory=llm.make_client,
                 dispatch=None, spawn=None):
        self.controller = controller
        self.store = controller.store
        self.credentials = credentials
        self.client_factory = client_factory
        self.dispatch = dispatch or controller.dispatch
        self.spawn = spawn or controller.spawn
        self.busy = None        # the request in flight: draft_id, sent, purpose
        self.problem = None     # ('setup' | 'failed', message) for the banner
        self.position = {}      # draft_id -> index of the version last shown
        self.on_change = lambda: None
        self.on_replace = lambda draft_id, text: None
        self.purpose = self.store.get_setting('purpose', '') or ''
        controller.locked_drafts = self.locked_drafts

    # Provider settings; the key itself stays in the keyring.
    def config(self):
        base = self.store.get_setting('llm_base_url', None)
        return {
            'provider': self.store.get_setting('llm_provider', llm.PROVIDERS[0][0]),
            'base_url': llm.PROVIDERS[0][1] if base is None else base,
            'model': self.store.get_setting('llm_model', '') or '',
        }

    def save_config(self, provider, base_url, model):
        self.store.set_setting('llm_provider', provider)
        self.store.set_setting('llm_base_url', base_url.strip())
        self.store.set_setting('llm_model', model.strip())
        self.problem = None
        self.on_change()

    def needs_key(self, base_url):
        for _name, url, needs_key in llm.PROVIDERS:
            if url and base_url.rstrip('/') == url:
                return needs_key
        return not base_url.startswith(('http://localhost', 'http://127.0.0.1'))

    def provider_name(self):
        return llm.provider_name(self.config()['base_url'])

    # Purpose
    def recent_purposes(self):
        try:
            recent = json.loads(self.store.get_setting('recent_purposes', '[]') or '[]')
        except ValueError:
            recent = []
        return [p for p in recent if isinstance(p, str)][:RECENT_LIMIT]

    def set_purpose(self, purpose, remember=False):
        self.purpose = purpose.strip()
        if remember:
            self.store.set_setting('purpose', self.purpose)
            if self.purpose and self.purpose not in PRESETS:
                recent = [self.purpose] + [p for p in self.recent_purposes() if p != self.purpose]
                self.store.set_setting('recent_purposes', json.dumps(recent[:RECENT_LIMIT]))

    def clear_recent(self):
        self.store.set_setting('recent_purposes', '[]')

    # The request
    def locked_drafts(self):
        return {self.busy['draft_id']} if self.busy else set()

    def can_start(self):
        c = self.controller
        return not (self.busy or c.closed or c.recording_job or c.pending)

    def start(self):
        c = self.controller
        if not self.can_start():
            return False
        config = self.config()
        if not config['base_url'] or not config['model']:
            self.problem = ('setup', 'Clean up needs a model provider')
            self.on_change()
            return False
        c.flush()
        if not c.content.strip():
            self.problem = ('failed', 'There is nothing to clean up yet')
            self.on_change()
            return False
        self.set_purpose(self.purpose, remember=True)
        request = {'draft_id': c.draft.id, 'sent': c.content, 'purpose': self.purpose,
                   'config': config, 'started_at': time.monotonic()}
        self.busy, self.problem = request, None

        def run():
            try:
                try:
                    key = self.credentials.get(config['base_url'])
                except Exception:
                    if self.needs_key(config['base_url']):
                        raise
                    key = ''  # a local model needs no key, so no keyring either
                if not key and self.needs_key(config['base_url']):
                    raise llm.LLMError('setup')
                client = self.client_factory(config['base_url'], key, config['model'])
                result, error = client.clean_up(request['sent'], request['purpose']), None
            except llm.LLMError as exc:
                result, error = None, str(exc)
            except Exception as exc:  # keyring or anything unexpected: never strand the request
                result, error = None, f'Clean up failed: {exc}'
            self.dispatch(self._finished, request, result, error)
        self.on_change()
        self.spawn(run)
        return True

    def cancel(self):
        if not self.busy:
            return False
        self.busy = None
        self.on_change()
        return True

    def _finished(self, request, result, error):
        if self.busy is not request or self.controller.closed:
            return False  # cancelled, or the app is closing
        self.busy = None
        try:
            if error == 'setup':
                self.problem = ('setup', 'Clean up needs an API key')
            elif error:
                self.problem = ('failed', f'{error}. Your draft wasn’t changed.')
            else:
                self._apply(request, *result)
        except Exception as exc:
            self.problem = ('failed', f'Could not save the cleaned-up text: {exc}. '
                                      'Your draft wasn’t changed.')
        self.on_change()
        return False

    def _apply(self, request, text, suggestions):
        c, draft_id = self.controller, request['draft_id']
        if draft_id == c.draft.id:
            c.flush()
        current = self.store.get_draft(draft_id).content
        if not current.startswith(request['sent']):
            raise RuntimeError('the draft changed while it was being cleaned up')
        # A transcript that landed meanwhile was appended; keep it after the result.
        new = text + current[len(request['sent']):]
        versions = self.store.list_versions(draft_id)
        if not versions or versions[-1]['content'] != current:
            self.store.add_version(draft_id, current,
                                   'Original' if not versions else 'Before clean up')
        purpose = request['purpose'] or 'General purpose'
        self.store.add_version(draft_id, new, f'Clean up · {purpose}', suggestions)
        self._set_text(draft_id, new)
        self.position[draft_id] = len(self.store.list_versions(draft_id)) - 1

    def _set_text(self, draft_id, text):
        c = self.controller
        draft = self.store.save_draft(draft_id, text)
        if draft_id == c.draft.id:
            c.draft, c.title, c.content = draft, draft.title, draft.content
            c.dirty = False
        self.on_replace(draft_id, text)

    # Versions
    def versions(self, draft_id=None):
        return self.store.list_versions(draft_id or self.controller.draft.id)

    def current_index(self, draft_id=None):
        draft_id = draft_id or self.controller.draft.id
        versions = self.versions(draft_id)
        if not versions:
            return None
        position = min(self.position.get(draft_id, len(versions) - 1), len(versions) - 1)
        c = self.controller
        content = c.content if draft_id == c.draft.id else self.store.get_draft(draft_id).content
        # Undo or typing can land exactly on a stored version; say so.
        matches = [i for i, v in enumerate(versions) if v['content'] == content]
        return position if not matches or position in matches else matches[-1]

    def show_version(self, index):
        """Put an earlier or later version back; unsaved edits become a version first."""
        c = self.controller
        if self.busy and self.busy['draft_id'] == c.draft.id:
            return False
        c.flush()
        versions = self.versions()
        if not 0 <= index < len(versions):
            return False
        target = versions[index]
        if all(v['content'] != c.content for v in versions):
            self.store.add_version(c.draft.id, c.content, 'Your edits')
            versions = self.versions()
            index = next(i for i, v in enumerate(versions) if v['id'] == target['id'])
        self._set_text(c.draft.id, target['content'])
        self.position[c.draft.id] = index
        self.problem = None
        self.on_change()
        return True

    def revert(self):
        index = self.current_index()
        if index is None or index == 0:
            return False
        return self.show_version(index - 1)

    # Suggestions: from the newest clean up of this draft.
    def suggestions(self, draft_id=None):
        for version in reversed(self.versions(draft_id)):
            if version['label'].startswith('Clean up'):
                return version['id'], version['suggestions']
        return None, []

    def dismiss(self, version_id, index):
        self.store.dismiss_suggestion(version_id, index)
        self.on_change()
