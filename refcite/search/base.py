"""search.base — the pluggable search interface and shared data models (DESIGN.md §6).

``Fragment`` carries ``parent_fragment_id`` from day one so the Stage 3
multi-perspective enrichment (extra vectors pointing back to the same fragment)
needs no interface change.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Default number of words in a review.md extract. Generous so the human (or the
# agent) can judge relevance without opening the cached record; configurable via
# ``search.extract_words`` in cite-assist.toml.
DEFAULT_EXTRACT_WORDS = 120


@dataclass
class Fragment:
    """A retrievable unit of text belonging to a record."""

    fragment_id: str
    record_id: str
    text: str
    source_path: str = ""
    line_range: tuple = (0, 0)
    parent_fragment_id: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["line_range"] = list(self.line_range)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Fragment":
        lr = data.get("line_range", [0, 0])
        return cls(
            fragment_id=data["fragment_id"],
            record_id=data["record_id"],
            text=data.get("text", ""),
            source_path=data.get("source_path", ""),
            line_range=tuple(lr),
            parent_fragment_id=data.get("parent_fragment_id"),
        )


@dataclass
class Hit:
    """A scored match returned by a search backend."""

    record_id: str
    fragment_id: str
    score: float
    snippet: str = ""
    source_path: str = ""
    line_range: tuple = (0, 0)


class SearchBackend:
    """One interface, swappable implementation (chosen in cite-assist.toml)."""

    name = "base"

    def build(self, fragments: list[Fragment], progress=None) -> None:
        """Build the index. ``progress`` is an optional ``ProgressReporter``
        used by slow backends (e.g. embeddings) to report encoding progress."""
        raise NotImplementedError

    def search(self, query: str, top_k: int,
               max_words: int = DEFAULT_EXTRACT_WORDS) -> list[Hit]:
        raise NotImplementedError

    def record_extracts(self, query: str, record_id: str, n: int,
                        max_words: int = DEFAULT_EXTRACT_WORDS) -> list[str]:
        """Return up to ``n`` distinct supporting extracts from a single record.

        Used to show the best few sections of a candidate document in
        ``review.md`` (not just its single top match), so a reviewer can judge
        relevance. Backends without a sensible notion of this may return ``[]``,
        in which case the caller falls back to the single best snippet.
        """
        return []

    def save(self, path) -> None:
        raise NotImplementedError

    @classmethod
    def load(cls, path) -> "SearchBackend":
        raise NotImplementedError


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def make_snippet(text: str, query_tokens: list[str],
                 max_words: int = DEFAULT_EXTRACT_WORDS) -> str:
    """A short extract centred on the densest run of query-term matches.

    ``max_words`` controls the extract length (configurable via
    ``search.extract_words``). It is deliberately generous so a reviewer can
    judge a candidate's relevance from the review file alone, without opening
    the cached record. Records shorter than ``max_words`` (most abstracts) are
    shown in full.
    """
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    if not query_tokens:
        return " ".join(words[:max_words]).rstrip() + " ..."

    wanted = set(query_tokens)
    lowered = [w.lower().strip(".,;:()[]\"'") for w in words]

    best_start, best_hits = 0, -1
    for start in range(0, max(1, len(words) - max_words + 1)):
        hits = sum(1 for w in lowered[start:start + max_words] if w in wanted)
        if hits > best_hits:
            best_hits, best_start = hits, start

    snippet = " ".join(words[best_start:best_start + max_words])
    prefix = "... " if best_start > 0 else ""
    suffix = " ..." if best_start + max_words < len(words) else ""
    return f"{prefix}{snippet}{suffix}"


def top_snippets(scored: list, fragments: list, query_tokens: list[str],
                 n: int, max_words: int = DEFAULT_EXTRACT_WORDS) -> list[str]:
    """Pick up to ``n`` distinct extracts from already-ranked fragments.

    ``scored`` is a list of ``(score, fragment_index)`` sorted best-first.
    Near-duplicate snippets (same opening words) are skipped so the extracts
    show genuinely different sections of the document.
    """
    out: list[str] = []
    seen: set[str] = set()
    for score, i in scored:
        if score <= 0:
            continue
        snippet = make_snippet(fragments[i].text, query_tokens, max_words)
        key = " ".join(snippet.split()[:12]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(snippet)
        if len(out) >= n:
            break
    return out
