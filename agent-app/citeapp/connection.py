"""connection.py — lightweight "Test connection" checks for the Settings dialog.

These probe the two OpenAI-compatible HTTP endpoints the app talks to — the main
chat model and the embeddings server — using only the standard library (D2/D7).
Each check returns a plain ``(ok, message)`` tuple so the GUI can show a green or
red one-line result without knowing anything about HTTP.

The checks are deliberately cheap and side-effect free: the main model is probed
with ``GET {base_url}/models`` (lists models, validates the URL and token, costs
no tokens), and the embeddings server with a single one-word ``POST
{url}/v1/embeddings`` (the smallest real request that proves it can embed).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# Short timeout: a reachable local server answers in well under a second, and a
# wrong address should fail fast rather than freeze the dialog.
_TIMEOUT = 10


def _get_json(url: str, token: str = "", timeout: int = _TIMEOUT) -> dict:
    headers = {"User-Agent": "cite-assist/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict, token: str = "",
               timeout: int = _TIMEOUT) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "cite-assist/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _describe_error(exc: Exception, url: str) -> str:
    """Turn an HTTP/URL failure into a short, human-friendly reason."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"rejected ({exc.code}): check the API token."
        if exc.code == 404:
            return f"not found ({exc.code}) at {url}: check the base URL."
        return f"server returned HTTP {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return f"could not reach {url}: is the server running? ({exc.reason})"
    return f"unexpected error: {exc}"


def test_main_model(base_url: str, model: str, token: str = "",
                    timeout: int = _TIMEOUT) -> tuple[bool, str]:
    """Probe the chat model endpoint with ``GET /models`` (no tokens spent)."""
    base = (base_url or "").rstrip("/")
    if not base:
        return False, "Enter an API base URL first."
    url = f"{base}/models"
    try:
        data = _get_json(url, token, timeout)
    except Exception as exc:  # noqa: BLE001 — every failure becomes a message
        return False, _describe_error(exc, url)

    ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
    if model and ids and model not in ids:
        return True, (f"Connected, but model '{model}' is not in the server's list "
                      f"({len(ids)} available). Check the model name.")
    if model and ids:
        return True, f"Connected. Model '{model}' is available."
    return True, f"Connected. Server reachable ({len(ids)} model(s) listed)."


def test_embeddings(emb_url: str, emb_model: str, token: str = "",
                    timeout: int = _TIMEOUT) -> tuple[bool, str]:
    """Probe the embeddings server with one tiny ``POST /v1/embeddings``."""
    base = (emb_url or "").rstrip("/")
    if not base:
        return False, "Enter an embeddings server URL first."
    url = f"{base}/v1/embeddings"
    payload = {"input": "ping", "model": emb_model or ""}
    try:
        data = _post_json(url, payload, token, timeout)
    except Exception as exc:  # noqa: BLE001
        return False, _describe_error(exc, url)

    rows = data.get("data")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) \
            and rows[0].get("embedding"):
        dims = len(rows[0]["embedding"])
        return True, f"Connected. Embeddings returned ({dims} dimensions)."
    return False, "Server replied but without an embedding — check the model name."
