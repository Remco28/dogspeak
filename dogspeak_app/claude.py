"""Claude (Anthropic API) for Clean up, through the official SDK.

The GTK app runs on distro Python without the SDK, so it starts this module in
the project venv for each request: `.venv/bin/python -m dogspeak_app.claude`.
One JSON request on stdin (the API key travels here, never in argv or the
environment), one JSON reply on stdout: {"ok": true, ...} or {"ok": false,
"error": "<sentence for the banner>"}.
"""
import json
import sys

import anthropic

from .llm import MAX_SUGGESTIONS, build_messages

TIMEOUT_SECONDS = 90
# Models that accept the server-side refusal fallback ("default" routing).
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "suggestions"],
    "additionalProperties": False,
}


def clean_up(client, model, text, purpose):
    system, user = build_messages(text, purpose)
    request = dict(
        model=model,
        max_tokens=16000,
        system=system["content"],
        messages=[{"role": "user", "content": user["content"]}],
        # A rewrite needs little deliberation; low effort keeps the wait short.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
    )
    if model in FALLBACK_MODELS:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
    else:
        response = client.messages.create(**request)
    if response.stop_reason == "refusal":
        return {"ok": False, "error": "Claude declined to clean up this draft"}
    if response.stop_reason == "max_tokens":
        return {"ok": False, "error": "Claude's reply was cut off; the draft may be too long"}
    body = next((block.text for block in response.content if block.type == "text"), "")
    data = json.loads(body)
    suggestions = [s.strip() for s in data["suggestions"] if s.strip()][:MAX_SUGGESTIONS]
    if not data["text"].strip():
        return {"ok": False, "error": "Claude sent back an empty draft; try again"}
    return {"ok": True, "text": data["text"].strip(), "suggestions": suggestions}


def test(client, model):
    """Free: looking a model up checks the key and the name without generating tokens."""
    if not model:
        client.models.list(limit=1)
        return {"ok": True, "lines": ["Key accepted", "No model set yet"]}
    found = client.models.retrieve(model)
    return {"ok": True, "lines": ["Key accepted", f"Model found: {found.display_name} ({found.id})"]}


def run(request):
    client = anthropic.Anthropic(api_key=request.get("api_key") or None,
                                 timeout=TIMEOUT_SECONDS, max_retries=2)
    model = (request.get("model") or "").strip()
    try:
        if request.get("action") == "test":
            return test(client, model)
        if not model:
            return {"ok": False, "error": "No model is set; choose one in Settings"}
        return clean_up(client, model, request["text"], request.get("purpose", ""))
    except anthropic.AuthenticationError:
        return {"ok": False, "error": "Claude: the API key was rejected"}
    except anthropic.PermissionDeniedError as error:
        return {"ok": False, "error": f"Claude: this key isn't allowed to do that ({error.message})"}
    except anthropic.NotFoundError:
        return {"ok": False, "error": f"Claude: there is no model called “{model}”"}
    except anthropic.RateLimitError:
        return {"ok": False, "error": "Claude: too many requests right now; wait a moment"}
    except anthropic.BadRequestError as error:
        # Low credit balance arrives here too, with a readable message.
        return {"ok": False, "error": f"Claude: {error.message}"}
    except anthropic.APITimeoutError:
        return {"ok": False, "error": f"Claude didn't answer within {TIMEOUT_SECONDS} seconds"}
    except anthropic.APIStatusError as error:
        if error.status_code >= 500:
            return {"ok": False, "error": "Claude had a server problem (it may be overloaded); try again"}
        return {"ok": False, "error": f"Claude: {error.message}"}
    except anthropic.APIConnectionError:
        return {"ok": False, "error": "Couldn't reach Claude; check your internet connection"}
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "error": "Claude's reply wasn't in the expected format; try again"}


def main():
    try:
        request = json.loads(sys.stdin.read())
    except ValueError:
        reply = {"ok": False, "error": "Bad request to the Claude helper"}
    else:
        reply = run(request)
    sys.stdout.write(json.dumps(reply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
