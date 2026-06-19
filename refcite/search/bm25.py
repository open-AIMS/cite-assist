"""search.bm25 — the default, stdlib-only search backend (DESIGN.md §6).

Okapi BM25 over title/abstract/full-text fragments. Better than grep, zero
setup. The index is persisted as the fragment list; ranking statistics are
recomputed on load so the on-disk format stays trivial and forward-compatible.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .base import DEFAULT_EXTRACT_WORDS, Fragment, Hit, SearchBackend, make_snippet, tokenize, top_snippets


class BM25Backend(SearchBackend):
    name = "bm25"

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.fragments: list[Fragment] = []
        self._doc_tokens: list[list[str]] = []
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0

    def build(self, fragments: list[Fragment], progress=None) -> None:
        self.fragments = list(fragments)
        self._doc_tokens = [tokenize(f.text) for f in self.fragments]
        n_docs = len(self._doc_tokens)
        df: Counter = Counter()
        for tokens in self._doc_tokens:
            for term in set(tokens):
                df[term] += 1
        self._avgdl = (sum(len(t) for t in self._doc_tokens) / n_docs) if n_docs else 0.0
        self._idf = {
            term: math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }
        if progress is not None:
            progress.done(f"{n_docs} fragments")

    def search(self, query: str, top_k: int,
               max_words: int = DEFAULT_EXTRACT_WORDS) -> list[Hit]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.fragments:
            return []
        query_terms = set(query_tokens)
        scored: list[tuple[float, int]] = []
        for i, tokens in enumerate(self._doc_tokens):
            score = self._score(query_terms, tokens)
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        hits: list[Hit] = []
        for score, i in scored[:top_k]:
            frag = self.fragments[i]
            hits.append(Hit(
                record_id=frag.record_id,
                fragment_id=frag.fragment_id,
                score=score,
                snippet=make_snippet(frag.text, query_tokens, max_words),
                source_path=frag.source_path,
                line_range=frag.line_range,
            ))
        return hits

    def record_extracts(self, query: str, record_id: str, n: int,
                        max_words: int = DEFAULT_EXTRACT_WORDS) -> list[str]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.fragments:
            return []
        query_terms = set(query_tokens)
        scored = [
            (self._score(query_terms, self._doc_tokens[i]), i)
            for i, frag in enumerate(self.fragments)
            if frag.record_id == record_id
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return top_snippets(scored, self.fragments, query_tokens, n, max_words)

    def _score(self, query_terms: set, tokens: list[str]) -> float:
        if not tokens:
            return 0.0
        tf = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = self._idf.get(term, 0.0)
            denom = freq + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
            score += idf * (freq * (self.k1 + 1)) / denom
        return score

    def save(self, path) -> None:
        data = {"backend": self.name, "fragments": [f.to_dict() for f in self.fragments]}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "BM25Backend":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        backend = cls()
        backend.build([Fragment.from_dict(d) for d in data.get("fragments", [])])
        return backend
