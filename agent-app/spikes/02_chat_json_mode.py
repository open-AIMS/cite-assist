"""Spike 02 — OpenAI-compatible chat client + JSON mode (A4/A5).

Risk: app mode talks to an OpenAI-compatible `/v1/chat/completions` endpoint
over plain `urllib`, asking for strict JSON via
`response_format={"type": "json_object"}`. Not every server (OpenAI vs LM Studio
vs Ollama proxy) honours that flag, and small local models sometimes wrap JSON
in prose. This spike verifies the request shape, auth, and that the response
parses as JSON.

Configure via environment variables (so no secret is hard-coded):
    CITEAPP_BASE   default http://localhost:1234/v1   (LM Studio); or
                   https://api.openai.com/v1
    CITEAPP_MODEL  the served model name
    CITEAPP_TOKEN  bearer token (LM Studio ignores it; OpenAI requires it).
                   Falls back to OPENAI_API_KEY.

Run from the repo root:
    CITEAPP_BASE=http://localhost:1234/v1 CITEAPP_MODEL=your-model \
        python spikes/02_chat_json_mode.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    base = os.environ.get("CITEAPP_BASE", "http://localhost:1234/v1").rstrip("/")
    model = os.environ.get("CITEAPP_MODEL", "")
    token = os.environ.get("CITEAPP_TOKEN") or os.environ.get("OPENAI_API_KEY", "")

    if not model:
        print("Set CITEAPP_MODEL to the served model name.", file=sys.stderr)
        return 2

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "Return only valid JSON, no prose."},
            {"role": "user", "content": 'Return exactly {"sentences": ["a", "b"]}.'},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{base}/chat/completions"
    print(f"POST {url}  (model={model}, json_object requested)")
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach the model API at {url}: {exc}", file=sys.stderr)
        return 1

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        print(f"Unexpected response shape: {exc}\n{body!r}", file=sys.stderr)
        return 1

    print("\nraw content:", repr(content))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"\nFAIL: content is not strict JSON ({exc}).")
        print("=> the app must rely on the A5 parse-retry path for this server/model.")
        return 1

    ok = isinstance(parsed, dict) and isinstance(parsed.get("sentences"), list)
    print("\nparsed:", parsed)
    print("VERDICT:", "PASS — strict JSON honoured" if ok
          else "PARTIAL — JSON parsed but not the expected shape (retry/validate)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
