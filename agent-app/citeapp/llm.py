"""llm.py — a minimal OpenAI-compatible chat client over ``urllib`` (A4, A5).

App mode makes only three scoped LLM calls (DESIGN-app-mode.md §2); this is the
single client they share. It posts to ``{base_url}/chat/completions`` with a
bearer token and asks for **schema-constrained JSON** via ``response_format``.

Server support for ``response_format`` varies (A5): the Stage A0 spike found LM
Studio rejects ``{"type":"json_object"}`` with HTTP 400 but honours a
``json_schema`` format, which current OpenAI models also accept, while some older
proxies accept neither. So the client tries formats in order —
``json_schema`` → ``json_object`` → none (plain text) — moving on when a server
rejects one with a 400 that names ``response_format``. Whatever format is used,
the reply is parsed and validated; a parse/validation failure is retried **once**
with a terse reminder before the next format (or, finally, a typed
:class:`LLMError`) is raised. The GUI shows that error as a friendly message and,
for developers, the raw text (A6).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(Exception):
    """A network/HTTP/parse failure the GUI surfaces to the author.

    ``raw`` carries the last model text (if any) for the Developer raw-JSON
    view; it is empty for connection failures that never returned content.
    """

    def __init__(self, message: str, *, raw: str = ""):
        super().__init__(message)
        self.raw = raw


class _UnsupportedFormat(Exception):
    """Internal: the server rejected this ``response_format`` (HTTP 400)."""


@dataclass
class LLMClient:
    base_url: str
    model: str
    token: str = ""
    timeout: int = 120

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or "").rstrip("/")

    # --- public API ------------------------------------------------------

    def complete_json(self, *, system: str, user: str, schema: dict,
                      schema_name: str, validate=None) -> dict:
        """Return the model's reply parsed as JSON and validated.

        ``validate(obj)`` may raise ``ValueError`` to reject a well-formed-JSON
        reply whose *shape* is wrong; that triggers the same single retry as a
        ``JSONDecodeError``. Raises :class:`LLMError` if every response format is
        exhausted without a valid reply.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_raw = ""
        for fmt in self._response_formats(schema, schema_name):
            try:
                content = self._send(messages, fmt)
            except _UnsupportedFormat:
                continue                      # server rejected this format; try next
            last_raw = content

            obj = _parse_and_validate(content, validate)
            if obj is not None:
                return obj

            # One automatic retry with a terse "JSON only" reminder (A5).
            retry_messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user",
                 "content": ("That was not valid JSON for the requested schema. "
                             "Reply with ONLY the JSON object, no prose, no code "
                             "fences.")},
            ]
            try:
                content = self._send(retry_messages, fmt)
            except _UnsupportedFormat:
                continue
            last_raw = content
            obj = _parse_and_validate(content, validate)
            if obj is not None:
                return obj
            # This format yielded unparseable output twice; fall through to the
            # next (simpler) format before giving up.

        raise LLMError(
            "The model did not return valid JSON for this step. Try again, or "
            "switch to a stronger model in Settings.",
            raw=last_raw)

    # --- HTTP ------------------------------------------------------------

    def _response_formats(self, schema: dict, schema_name: str) -> list:
        return [
            {"type": "json_schema",
             "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
            {"type": "json_object"},
            None,
        ]

    def _send(self, messages: list, response_format) -> str:
        if not self.base_url:
            raise LLMError("No model API base URL is set. Open Settings to configure it.")
        body: dict = {"model": self.model, "messages": messages, "temperature": 0}
        if response_format is not None:
            body["response_format"] = response_format
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "cite-assist/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 400 and "response_format" in detail:
                raise _UnsupportedFormat(detail)
            raise LLMError(f"Model API error (HTTP {exc.code}): {detail}", raw=detail)
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Could not reach the model API at {self.base_url} ({exc.reason}). "
                "Check the base URL and that the server is running.")
        try:
            return payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from the model API: {exc}",
                           raw=json.dumps(payload)[:500])


# --- parsing helpers ------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Remove a surrounding ```json ... ``` code fence some models add."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


def _parse_and_validate(content: str, validate) -> dict | None:
    """Parse ``content`` as a JSON object and validate it; ``None`` on failure."""
    try:
        obj = json.loads(_strip_fences(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if validate is not None:
        try:
            validate(obj)
        except ValueError:
            return None
    return obj
