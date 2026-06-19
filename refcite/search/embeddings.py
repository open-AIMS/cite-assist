"""search.embeddings — optional conceptual search via a local model API (DESIGN.md §6).

Encodes fragments and the query by calling a local embeddings server over plain
HTTP (no ML packages installed locally, D7). LM Studio's OpenAI-compatible
``/v1/embeddings`` is the default (D13); Ollama's ``/api/embeddings`` is also
supported. Ranks by cosine similarity.

The server is only contacted for ``backend = "embeddings"`` or ``"hybrid"``.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from pathlib import Path

from .base import DEFAULT_EXTRACT_WORDS, Fragment, Hit, SearchBackend, make_snippet, tokenize, top_snippets

# Fragments encoded per HTTP request to the model server, keeping individual
# requests modest while still amortising round-trip latency.
_EMBED_BATCH = 32

# The embeddings index is written as a directory of shard files rather than one
# giant single-line JSON, so that (a) a long build saves partial progress and
# resumes after an interruption, and (b) each shard is small and pretty-printed
# (one fragment per line) for inspection during debugging. ~20 shards keeps the
# file count low while keeping each file editor-friendly.
DEFAULT_NUM_SHARDS = 20

# Stored vector precision. 6 decimals is far more than cosine similarity needs
# and roughly halves the on-disk size versus full float repr, which also makes
# the shard files easier to read.
_VECTOR_PRECISION = 6


def _text_hash(text: str) -> str:
    """Short stable hash of a fragment's text, used to detect when a cached
    vector can be reused on an incremental rebuild (skip re-embedding)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _shard_for(record_id: str, num_shards: int) -> int:
    """Map a record to a shard. Keyed on ``record_id`` so every fragment of a
    record lands in the same shard (nice for inspection and localised updates).
    Falls back to a hash for non-numeric ids."""
    try:
        n = int(record_id)
    except (TypeError, ValueError):
        n = int(hashlib.sha1(str(record_id).encode("utf-8")).hexdigest(), 16)
    return n % num_shards


def _round_vec(vec: list[float]) -> list[float]:
    return [round(float(x), _VECTOR_PRECISION) for x in vec]


class EmbeddingsError(Exception):
    pass


class EmbeddingsBackend(SearchBackend):
    name = "embeddings"

    def __init__(self, server: str = "lmstudio", url: str = "http://localhost:1234",
                 model: str = "text-embedding-nomic-embed-text-v1.5",
                 num_shards: int = DEFAULT_NUM_SHARDS):
        self.server = server
        self.url = url.rstrip("/")
        self.model = model
        self.num_shards = max(1, int(num_shards))
        self.fragments: list[Fragment] = []
        self.vectors: list[list[float]] = []
        self._qv_query: str | None = None
        self._qv_vec: list[float] | None = None

    # --- index lifecycle ---

    def build(self, fragments: list[Fragment], progress=None) -> None:
        self.fragments = list(fragments)
        texts = [f.text for f in self.fragments]
        if not texts:
            self.vectors = []
            return
        vectors: list[list[float]] = []
        if progress is not None:
            progress.start()
        for start in range(0, len(texts), _EMBED_BATCH):
            batch = texts[start:start + _EMBED_BATCH]
            vectors.extend(self._embed(batch))
            if progress is not None:
                progress.update(len(vectors))
        if progress is not None:
            progress.done(f"{len(vectors)} fragments embedded")
        self.vectors = vectors

    def search(self, query: str, top_k: int,
               max_words: int = DEFAULT_EXTRACT_WORDS) -> list[Hit]:
        if not self.fragments:
            return []
        query_vec = self._query_vector(query)
        scored = [
            (_cosine(query_vec, vec), i)
            for i, vec in enumerate(self.vectors)
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        query_tokens = tokenize(query)
        hits: list[Hit] = []
        for score, i in scored[:top_k]:
            if score <= 0:
                continue
            frag = self.fragments[i]
            hits.append(Hit(
                record_id=frag.record_id,
                fragment_id=frag.fragment_id,
                score=float(score),
                snippet=make_snippet(frag.text, query_tokens, max_words),
                source_path=frag.source_path,
                line_range=frag.line_range,
            ))
        return hits

    def record_extracts(self, query: str, record_id: str, n: int,
                        max_words: int = DEFAULT_EXTRACT_WORDS) -> list[str]:
        if not self.fragments:
            return []
        query_vec = self._query_vector(query)
        scored = [
            (_cosine(query_vec, self.vectors[i]), i)
            for i, frag in enumerate(self.fragments)
            if frag.record_id == record_id
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return top_snippets(scored, self.fragments, tokenize(query), n, max_words)

    def _query_vector(self, query: str) -> list[float]:
        """Embed ``query``, memoising the most recent one so a ``search``
        followed by ``record_extracts`` for the same claim costs one HTTP call,
        not one per candidate document."""
        if query == self._qv_query and self._qv_vec is not None:
            return self._qv_vec
        vec = self._embed([query])[0]
        self._qv_query = query
        self._qv_vec = vec
        return vec

    def save(self, path) -> None:
        data = {
            "backend": self.name,
            "server": self.server,
            "url": self.url,
            "model": self.model,
            "fragments": [f.to_dict() for f in self.fragments],
            "vectors": self.vectors,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "EmbeddingsBackend":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        backend = cls(
            server=data.get("server", "lmstudio"),
            url=data.get("url", "http://localhost:1234"),
            model=data.get("model", ""),
        )
        backend.fragments = [Fragment.from_dict(d) for d in data.get("fragments", [])]
        backend.vectors = data.get("vectors", [])
        return backend

    # --- sharded, resumable, human-readable store ---

    def build_sharded(self, fragments: list[Fragment], shard_dir,
                      progress=None, verbose: bool = False) -> dict:
        """Embed ``fragments`` into a directory of shard files, resuming from
        whatever is already on disk.

        Each shard is written as soon as its fragments are embedded, so an
        interrupted run keeps its progress: a later call reuses every cached
        vector whose fragment text is unchanged and only embeds the rest.
        ``meta.json`` is written last and marks the build as complete.
        """
        shard_dir = Path(shard_dir)
        shard_dir.mkdir(parents=True, exist_ok=True)

        # Group fragments by shard, preserving order within each shard.
        groups: dict[int, list[Fragment]] = {}
        for frag in fragments:
            groups.setdefault(_shard_for(frag.record_id, self.num_shards), []).append(frag)

        reuse, existing_ids = self._load_reuse(shard_dir)
        total = len(fragments)
        done = 0
        embedded = 0
        if progress is not None:
            progress.start()

        self.fragments = []
        self.vectors = []
        written = 0
        for shard in range(self.num_shards):
            frags = groups.get(shard, [])
            shard_path = shard_dir / f"shard-{shard:02d}.json"
            if not frags:
                # No fragments map here any more; drop a stale shard file.
                if shard_path.exists():
                    shard_path.unlink()
                continue

            items: list[dict | None] = []
            pending: list[tuple[int, Fragment, str]] = []
            for frag in frags:
                thash = _text_hash(frag.text)
                cached = reuse.get(frag.fragment_id)
                if cached is not None and cached[0] == thash:
                    items.append(self._item(frag, thash, cached[1]))
                    done += 1
                else:
                    items.append(None)
                    pending.append((len(items) - 1, frag, thash))

            for start in range(0, len(pending), _EMBED_BATCH):
                batch = pending[start:start + _EMBED_BATCH]
                vecs = self._embed([f.text for _, f, _ in batch])
                for (slot, frag, thash), vec in zip(batch, vecs):
                    items[slot] = self._item(frag, thash, _round_vec(vec))
                    done += 1
                    embedded += 1
                if progress is not None:
                    progress.update(done)

            resolved = [it for it in items if it is not None]
            # Only rewrite a shard whose contents actually changed, so a no-op
            # rebuild (records unchanged) does not churn every file.
            new_ids = [it["fragment_id"] for it in resolved]
            if pending or new_ids != existing_ids.get(shard):
                _write_shard(shard_path, self._header(shard), resolved)
                written += 1
            for it in resolved:
                self.fragments.append(Fragment(
                    fragment_id=it["fragment_id"], record_id=it["record_id"],
                    text=it["text"], source_path=it["source_path"],
                    line_range=tuple(it["line_range"]),
                ))
                self.vectors.append(it["vector"])
            if progress is not None:
                progress.update(done)

        _write_meta(shard_dir / "meta.json", self._meta(total))
        if progress is not None:
            progress.done(f"{total} fragments embedded ({embedded} new, "
                          f"{total - embedded} reused)")
        return {"fragments": total, "embedded": embedded,
                "reused": total - embedded, "shards_written": written}

    @classmethod
    def load_sharded(cls, shard_dir) -> "EmbeddingsBackend":
        shard_dir = Path(shard_dir)
        meta = _read_json(shard_dir / "meta.json") or {}
        backend = cls(
            server=meta.get("server", "lmstudio"),
            url=meta.get("url", "http://localhost:1234"),
            model=meta.get("model", ""),
            num_shards=meta.get("num_shards", DEFAULT_NUM_SHARDS),
        )
        for path in sorted(shard_dir.glob("shard-*.json")):
            data = _read_json(path) or {}
            for it in data.get("items", []):
                backend.fragments.append(Fragment(
                    fragment_id=it["fragment_id"], record_id=it["record_id"],
                    text=it["text"], source_path=it.get("source_path", ""),
                    line_range=tuple(it.get("line_range", (0, 0))),
                ))
                backend.vectors.append(it["vector"])
        return backend

    def _load_reuse(self, shard_dir: Path):
        """Read existing shards into a ``{fragment_id: (text_hash, vector)}``
        reuse map plus the per-shard id lists. An incompatible prior build
        (different model or shard count) is ignored so it rebuilds cleanly."""
        meta = _read_json(shard_dir / "meta.json")
        reuse: dict[str, tuple[str, list[float]]] = {}
        existing_ids: dict[int, list[str]] = {}
        for path in sorted(shard_dir.glob("shard-*.json")):
            data = _read_json(path)
            if not data:
                continue
            # A shard from a different model/shard-count is not reusable.
            if data.get("model") != self.model or data.get("num_shards") != self.num_shards:
                continue
            ids: list[str] = []
            for it in data.get("items", []):
                reuse[it["fragment_id"]] = (it.get("text_hash", ""), it["vector"])
                ids.append(it["fragment_id"])
            shard = data.get("shard")
            if shard is not None:
                existing_ids[int(shard)] = ids
        # If the recorded model changed, treat nothing as reusable.
        if meta and meta.get("model") not in (None, self.model):
            return {}, {}
        return reuse, existing_ids

    def _item(self, frag: Fragment, thash: str, vec: list[float]) -> dict:
        return {
            "fragment_id": frag.fragment_id,
            "record_id": frag.record_id,
            "line_range": list(frag.line_range),
            "source_path": frag.source_path,
            "text_hash": thash,
            "text": frag.text,
            "vector": vec,
        }

    def _header(self, shard: int) -> dict:
        return {
            "backend": self.name,
            "model": self.model,
            "server": self.server,
            "url": self.url,
            "shard": shard,
            "num_shards": self.num_shards,
        }

    def _meta(self, total: int) -> dict:
        return {
            "backend": self.name,
            "model": self.model,
            "server": self.server,
            "url": self.url,
            "num_shards": self.num_shards,
            "fragments": total,
        }

    # --- HTTP to the model server ---

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self.server == "ollama":
            return [self._embed_ollama(t) for t in texts]
        return self._embed_openai(texts)

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"input": texts, "model": self.model}).encode("utf-8")
        data = self._post(f"{self.url}/v1/embeddings", payload)
        try:
            rows = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise EmbeddingsError(f"unexpected /v1/embeddings response: {exc}") from exc

    def _embed_ollama(self, text: str) -> list[float]:
        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        data = self._post(f"{self.url}/api/embeddings", payload)
        try:
            return data["embedding"]
        except (KeyError, TypeError) as exc:
            raise EmbeddingsError(f"unexpected /api/embeddings response: {exc}") from exc

    def _post(self, url: str, payload: bytes) -> dict:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "refcite/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EmbeddingsError(
                f"embeddings server unreachable at {url} ({exc}). "
                f"Start LM Studio/Ollama or set search.backend = \"bm25\"."
            ) from exc


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- shard file IO (human-readable JSON) ---------------------------------

def _read_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_meta(path: Path, meta: dict) -> None:
    Path(path).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def _write_shard(path: Path, header: dict, items: list[dict]) -> None:
    """Write one shard as valid, inspectable JSON: the header fields on their
    own lines, then one fragment per line inside ``items`` (so the big vectors
    stay on a single line each instead of exploding into thousands of lines).
    Written atomically via a temp file so an interrupted write never corrupts
    an existing shard."""
    lines = ["{"]
    for key, value in header.items():
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append('  "items": [')
    for i, item in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append("    " + json.dumps(item, ensure_ascii=False) + comma)
    lines.append("  ]")
    lines.append("}")
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
