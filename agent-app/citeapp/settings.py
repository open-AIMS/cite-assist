"""settings.py — app-mode settings and at-rest token obfuscation (A17, A18, A24).

All app data lives user-global under ``~/.cite-assist/`` (A17). This module owns
``~/.cite-assist/app.json`` (the minimal settings, A24) and the obfuscated API
token. Settings are deliberately minimal: the main-model connection is required;
a search-backend toggle and, for embeddings, the embeddings server URL + model
are the only extras (A24). Everything else stays in ``cite-assist.toml``.

Token storage (A18): the token is *obfuscated, not encrypted*. A random 32-byte
key in ``~/.cite-assist/.appkey`` (``0o600`` where the OS honours it) XORs the
token; the result is base64-stored in ``app.json``. This defeats *untargeted*
malware grepping the disk for ``sk-...`` strings — the token never appears in
plaintext — but **not** a targeted attacker who reads both files or this source.
On Windows ``os.chmod`` cannot apply POSIX modes (spike 09), so the file is not
ACL-restricted there; the honest path for real secret protection is the
``OPENAI_API_KEY`` environment variable (A29), which always takes precedence
over the stored token.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path.home() / ".cite-assist"
SETTINGS_PATH = APP_DIR / "app.json"
APPKEY_PATH = APP_DIR / ".appkey"

# The environment variable that, when set, overrides the stored token (A29).
TOKEN_ENV_VAR = "OPENAI_API_KEY"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_EMB_URL = "http://127.0.0.1:1234"
DEFAULT_EMB_MODEL = "text-embedding-embeddinggemma-300m-qat"


# Provider presets for the Settings dialog (A4). Each fills the base URL and a
# starter list of model names; both stay editable, so an unlisted model or a
# proxy URL is still allowed. ``needs_token`` drives the token help text: hosted
# OpenAI needs a real secret key, while a local LM Studio / Ollama server ignores
# it. The values below match the Stage A0 spike environment (spikes/RESULTS.md).
PROVIDER_PRESETS: dict[str, dict] = {
    "OpenAI": {
        "api_base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
        "needs_token": True,
        "help": ("Hosted OpenAI. Paste your secret API key (sk-\u2026). The key is "
                 "stored obfuscated on disk; to keep it off disk entirely, set the "
                 f"{TOKEN_ENV_VAR} environment variable instead, which always wins."),
    },
    "LM Studio (local)": {
        "api_base_url": "http://127.0.0.1:1234/v1",
        "models": ["google/gemma-4-e4b", "qwen/qwen3-8b", "google/gemma-3-4b"],
        "needs_token": False,
        "help": ("Local LM Studio. In LM Studio open the Developer (Local Server) "
                 "tab, load a chat model, and Start Server. The default address is "
                 "http://127.0.0.1:1234/v1 (use 127.0.0.1 rather than 'localhost' to "
                 "avoid a DNS lookup delay). The token is ignored \u2014 leave it as "
                 "'lm-studio' or anything non-empty."),
    },
    "Ollama (local)": {
        "api_base_url": "http://127.0.0.1:11434/v1",
        "models": ["llama3.1", "qwen2.5", "gemma2"],
        "needs_token": False,
        "help": ("Local Ollama with its OpenAI-compatible endpoint. Run "
                 "'ollama serve' and 'ollama pull <model>'. The default address is "
                 "http://127.0.0.1:11434/v1 (use 127.0.0.1 rather than 'localhost' to "
                 "avoid a DNS lookup delay). The token is ignored \u2014 any value works."),
    },
    "Custom": {
        "api_base_url": "",
        "models": [],
        "needs_token": True,
        "help": ("Any other OpenAI-compatible endpoint (a proxy, a hosted gateway, "
                 "Anthropic via an OpenAI-compatible proxy, \u2026). Enter its "
                 "/v1 base URL and model name. Supply a token if the endpoint needs one."),
    },
}


def detect_provider(api_base_url: str) -> str:
    """Best-effort match of a base URL to a known preset name (else "Custom")."""
    url = (api_base_url or "").rstrip("/")
    for name, preset in PROVIDER_PRESETS.items():
        if name == "Custom":
            continue
        if url == preset["api_base_url"].rstrip("/"):
            return name
    return "Custom"


@dataclass
class Settings:
    """The few settings app mode owns (A24)."""

    api_base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    search_backend: str = "bm25"            # "bm25" | "embeddings"
    embeddings_url: str = DEFAULT_EMB_URL
    embeddings_model: str = DEFAULT_EMB_MODEL
    # In-memory only; persisted obfuscated via ``token_obfuscated``.
    token: str = ""
    _raw: dict = field(default_factory=dict)

    def config_overrides(self) -> dict:
        """The ``load_config(overrides=...)`` dict the GUI owns (A35).

        Only the search backend and (for embeddings) the server URL/model are
        the app's to set; everything else comes from ``cite-assist.toml`` or its
        defaults.
        """
        overrides: dict = {"backend": self.search_backend}
        if self.search_backend == "embeddings":
            overrides["emb_url"] = self.embeddings_url
            overrides["emb_model"] = self.embeddings_model
        return overrides

    def resolved_token(self) -> str:
        """The token to use: the environment variable wins over the stored one (A29)."""
        return os.environ.get(TOKEN_ENV_VAR) or self.token

    def is_configured(self) -> bool:
        """True once the required main-model connection is set (A24)."""
        return bool(self.api_base_url and self.model and self.resolved_token())


# --- token obfuscation (A18) ---------------------------------------------

def _load_key() -> bytes:
    """Return the 32-byte obfuscation key, creating it on first use."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if APPKEY_PATH.is_file():
        key = APPKEY_PATH.read_bytes()
        if len(key) >= 16:
            return key
    key = os.urandom(32)
    APPKEY_PATH.write_bytes(key)
    try:
        os.chmod(APPKEY_PATH, 0o600)  # honoured on POSIX; a no-op on Windows
    except OSError:
        pass
    return key


def obfuscate_token(token: str) -> str:
    """XOR ``token`` with the local key and base64-encode it (A18)."""
    if not token:
        return ""
    key = _load_key()
    raw = token.encode("utf-8")
    xored = bytes(c ^ key[i % len(key)] for i, c in enumerate(raw))
    return base64.b64encode(xored).decode("ascii")


def deobfuscate_token(stored: str) -> str:
    """Reverse :func:`obfuscate_token`; returns ``""`` on any decode failure."""
    if not stored:
        return ""
    try:
        key = _load_key()
        xored = base64.b64decode(stored)
        return bytes(c ^ key[i % len(key)] for i, c in enumerate(xored)).decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError):
        return ""


# --- load / save ---------------------------------------------------------

def load_settings() -> Settings:
    data: dict = {}
    if SETTINGS_PATH.is_file():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    return Settings(
        api_base_url=data.get("api_base_url", DEFAULT_BASE_URL),
        model=data.get("model", DEFAULT_MODEL),
        search_backend=data.get("search_backend", "bm25"),
        embeddings_url=data.get("embeddings_url", DEFAULT_EMB_URL),
        embeddings_model=data.get("embeddings_model", DEFAULT_EMB_MODEL),
        token=deobfuscate_token(data.get("token_obfuscated", "")),
        _raw=data,
    )


def save_settings(settings: Settings) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "api_base_url": settings.api_base_url,
        "model": settings.model,
        "token_obfuscated": obfuscate_token(settings.token),
        "search_backend": settings.search_backend,
        "embeddings_url": settings.embeddings_url,
        "embeddings_model": settings.embeddings_model,
    }
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
