"""config.py — read ``cite-assist.toml`` and resolve all paths.

Uses the stdlib ``tomllib`` (Python 3.11+). All on-disk locations are resolved
relative to the repository root (the working directory when the agent runs
``python -m refcite`` from the workspace root), unless given as absolute paths.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent          # .../cite-assist/refcite
CITE_ASSIST_DIR = PACKAGE_DIR.parent                    # .../cite-assist
DEFAULT_CONFIG_PATH = CITE_ASSIST_DIR / "cite-assist.toml"


@dataclass
class Config:
    """Resolved configuration plus convenience path properties."""

    repo_root: Path
    config_path: Path
    # zotero
    zotero_api_url: str
    zotero_library_type: str
    zotero_library_id: str
    csljson_export: str
    sync_max_delta: int
    # cache
    cache_dir: Path
    # search
    backend: str
    top_k: int
    own_collections: list[str]
    own_bias: float
    needs_source_threshold: float
    extract_words: int
    extracts_per_doc: int
    fragment_min_words: int
    # embeddings
    emb_server: str
    emb_url: str
    emb_model: str
    emb_num_shards: int
    # sessions
    sessions_dir: Path
    references_file: Path
    raw: dict = field(default_factory=dict)

    @property
    def records_dir(self) -> Path:
        return self.cache_dir / "records"

    @property
    def index_dir(self) -> Path:
        return self.cache_dir / "index"

    @property
    def manifest_path(self) -> Path:
        return self.cache_dir / "manifest.json"

    @property
    def active_dir(self) -> Path:
        return self.sessions_dir / "active"

    @property
    def done_dir(self) -> Path:
        return self.sessions_dir / "done"

    def resolve(self, path: str | Path) -> Path:
        """Resolve a possibly-relative path against the repo root."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.repo_root / p)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p)


def load_config(config_path: Path | None = None, repo_root: Path | None = None,
                overrides: dict | None = None) -> Config:
    """Load configuration from ``cite-assist.toml`` (or defaults if absent).

    ``overrides`` (A35) is an optional flat dict of ``Config`` field names to
    values, applied after the TOML is loaded. App mode passes the few settings
    it owns (``backend``, ``emb_url``, ``emb_model``) this way so the GUI's
    choice takes effect without writing a ``cite-assist.toml`` into the author's
    report folder. The CLI passes none and is unaffected. Unknown keys are
    ignored so a stale setting never breaks loading.
    """
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    data: dict = {}
    if config_path.is_file():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    root = Path(repo_root).resolve() if repo_root else Path.cwd()

    z = data.get("zotero", {})
    c = data.get("cache", {})
    s = data.get("search", {})
    e = data.get("embeddings", {})
    se = data.get("sessions", {})

    cfg = Config(
        repo_root=root,
        config_path=config_path,
        zotero_api_url=z.get("api_url", "http://127.0.0.1:23119"),
        zotero_library_type=z.get("library_type", "user"),
        zotero_library_id=str(z.get("library_id", "0")),
        csljson_export=z.get("csljson_export", ""),
        sync_max_delta=int(z.get("sync_max_delta", 1000)),
        cache_dir=_resolve(root, c.get("dir", "~/.cite-assist/zotero-cache")),
        backend=s.get("backend", "bm25"),
        top_k=int(s.get("top_k", 8)),
        own_collections=list(s.get("own_collections", [])),
        own_bias=float(s.get("own_bias", 1.25)),
        needs_source_threshold=float(s.get("needs_source_threshold", 0.0)),
        extract_words=int(s.get("extract_words", 120)),
        extracts_per_doc=int(s.get("extracts_per_doc", 2)),
        fragment_min_words=int(s.get("fragment_min_words", 30)),
        emb_server=e.get("server", "lmstudio"),
        emb_url=e.get("url", "http://localhost:1234"),
        emb_model=e.get("model", "text-embedding-nomic-embed-text-v1.5"),
        emb_num_shards=int(e.get("num_shards", 20)),
        sessions_dir=_resolve(root, se.get("dir", "citations")),
        references_file=_resolve(root, se.get("references_file", "references.md")),
        raw=data,
    )

    if overrides:
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)
    return cfg
