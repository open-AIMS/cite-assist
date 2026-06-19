"""search — fragmenting, backend factory, index build/load, and the per-claim
search orchestration that writes ``review.md`` (DESIGN.md §6–§7).

``run_search`` is the engine behind ``python -m refcite search``: it does a
best-effort quick sync, ensures an index exists, ranks each claim's candidates
(deduplicated, optionally collection-filtered, own-project biased), then hands
the results to ``claims.write_review``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .. import claims as claims_mod
from .. import store, zotero_sync
from ..config import Config
from ..progress import ProgressReporter
from .base import DEFAULT_EXTRACT_WORDS, Fragment, Hit, SearchBackend
from .bm25 import BM25Backend
from .embeddings import EmbeddingsBackend

_FRAGMENT_TARGET_WORDS = 120
# Hard ceiling on fragment size. PDF-extracted bodies often have whole
# paragraphs on a single line and few blank lines, so the soft paragraph
# boundary rarely fires; without a cap a fragment can grow to thousands of
# words and blow the embedding model's context window (e.g. 2048 tokens).
# A fragment is force-split here regardless of paragraph boundaries. The cap
# is in words; even dense citation text (~2 tokens/word) stays well under
# typical 2048-token limits.
_FRAGMENT_MAX_WORDS = 220
# Minimum fragment size. Sub-minimum chunks (a lone heading, a stray short
# line) are merged into a neighbour rather than emitted on their own, so no
# extract is a bare heading or single short sentence with no surrounding
# context. Merging is forward by default (a heading joins the section it
# introduces) and backward for a trailing remnant.
_FRAGMENT_MIN_WORDS = 30


# --- fragmenting ---------------------------------------------------------

def build_fragments(records: list[store.Record],
                    target_words: int = _FRAGMENT_TARGET_WORDS,
                    max_words: int = _FRAGMENT_MAX_WORDS,
                    min_words: int = _FRAGMENT_MIN_WORDS) -> list[Fragment]:
    """Split records into retrievable fragments.

    Every record is fragmented by paragraph into ~``target_words`` chunks,
    hard-capped at ``max_words`` so no single fragment exceeds the embedding
    model's context window, and with sub-``min_words`` chunks merged into a
    neighbour so no fragment is a bare heading or stray short line. Short
    ``abstract``/``meta`` records that never reach the cap still emit a single
    fragment, as before.
    """
    fragments: list[Fragment] = []
    for rec in records:
        source = str(rec.path) if rec.path else ""
        fragments.extend(_fragment_body(rec, source, target_words, max_words, min_words))
    return fragments


def _fragment_body(rec: store.Record, source: str, target_words: int,
                   max_words: int, min_words: int) -> list[Fragment]:
    lines = rec.body.split("\n")
    # First pass: stage candidate chunks as (text, start_line, end_line).
    staged: list[list] = []
    buf: list[str] = []
    buf_words = 0
    start_line = 0

    def stage(end_line: int) -> None:
        nonlocal buf, buf_words
        text = "\n".join(buf).strip()
        if text:
            staged.append([text, start_line, end_line])
        buf = []
        buf_words = 0

    for i, line in enumerate(lines):
        words = line.split()
        # A single line longer than the cap (a whole paragraph on one line) is
        # broken into word-chunks so no fragment exceeds the model's window.
        if len(words) > max_words:
            if buf:
                stage(i - 1)
            for j in range(0, len(words), max_words):
                staged.append([" ".join(words[j:j + max_words]), i, i])
            continue
        if not buf:
            start_line = i
        buf.append(line)
        buf_words += len(words)
        # Flush at a paragraph boundary once past the soft target, or whenever
        # the hard cap is reached regardless of boundaries.
        if (buf_words >= target_words and line.strip() == "") or buf_words >= max_words:
            stage(i)
    stage(len(lines) - 1)

    if not staged:  # body had no content
        staged = [[rec.body.strip(), 0, len(lines) - 1]]

    merged = _merge_small_chunks(staged, min_words)
    return [
        Fragment(
            fragment_id=f"{rec.record_id}#{k}",
            record_id=rec.record_id,
            text=text,
            source_path=source,
            line_range=(s_line, e_line),
        )
        for k, (text, s_line, e_line) in enumerate(merged)
    ]


def _merge_small_chunks(staged: list[list], min_words: int) -> list[list]:
    """Merge sub-``min_words`` chunks into a neighbour.

    A small chunk is carried *forward* and prepended to the next chunk, so a
    lone heading attaches to the section it introduces. A small chunk left over
    at the very end (no following chunk) is appended to the previous one.
    """
    out: list[list] = []
    pending: list | None = None  # [text, start_line, end_line] carried forward
    for text, s_line, e_line in staged:
        if pending is not None:
            text = pending[0] + "\n" + text
            s_line = pending[1]
            pending = None
        if len(text.split()) < min_words:
            pending = [text, s_line, e_line]
            continue
        out.append([text, s_line, e_line])
    if pending is not None:
        if out:
            out[-1][0] = out[-1][0] + "\n" + pending[0]
            out[-1][2] = pending[2]
        else:
            out.append(pending)
    return out


# --- backend factory + index persistence ---------------------------------

def make_backend(cfg: Config) -> SearchBackend:
    if cfg.backend == "bm25":
        return BM25Backend()
    if cfg.backend == "embeddings":
        return EmbeddingsBackend(cfg.emb_server, cfg.emb_url, cfg.emb_model,
                                 cfg.emb_num_shards)
    if cfg.backend == "hybrid":
        return HybridBackend(
            BM25Backend(),
            EmbeddingsBackend(cfg.emb_server, cfg.emb_url, cfg.emb_model,
                              cfg.emb_num_shards),
        )
    raise ValueError(f"unknown search backend: {cfg.backend!r}")


def index_path(cfg: Config) -> Path:
    """Location of the index for the active backend.

    BM25 is a single JSON file. The embeddings/hybrid backends use a directory
    of shard files under ``index/embeddings/`` (resumable + inspectable); both
    share that directory since hybrid rebuilds its BM25 half from the fragments
    stored alongside the vectors.
    """
    if cfg.backend == "bm25":
        return cfg.index_dir / "bm25.json"
    return cfg.index_dir / "embeddings"


def _emb_meta_path(cfg: Config) -> Path:
    return index_path(cfg) / "meta.json"


def build_index(cfg: Config, verbose: bool = True) -> dict:
    records = store.load_records(cfg.records_dir)
    fragments = build_fragments(records, min_words=cfg.fragment_min_words)
    if verbose:
        print(f"Indexing {len(records)} record(s) -> {len(fragments)} fragment(s) "
              f"({cfg.backend})...", file=sys.stderr)

    if cfg.backend == "bm25":
        backend = BM25Backend()
        backend.build(fragments)
        backend.save(index_path(cfg))
        stats = {"embedded": 0, "reused": 0}
    else:
        # embeddings + hybrid: shard the vectors, embedding only what changed.
        emb = EmbeddingsBackend(cfg.emb_server, cfg.emb_url, cfg.emb_model,
                                cfg.emb_num_shards)
        progress = ProgressReporter("Embedding fragments", len(fragments),
                                    enabled=verbose)
        stats = emb.build_sharded(fragments, index_path(cfg),
                                  progress=progress, verbose=verbose)

    result = {"records": len(records), "fragments": len(fragments),
              "backend": cfg.backend, "path": str(index_path(cfg)),
              "embedded": stats.get("embedded", 0),
              "reused": stats.get("reused", 0)}
    if verbose:
        extra = ""
        if cfg.backend != "bm25":
            extra = f" [{stats.get('embedded', 0)} embedded, {stats.get('reused', 0)} reused]"
        print(f"OK: indexed {result['records']} records into "
              f"{result['fragments']} fragments ({cfg.backend}){extra} -> {index_path(cfg)}")
    return result


def load_index(cfg: Config) -> SearchBackend | None:
    path = index_path(cfg)
    if cfg.backend == "bm25":
        return BM25Backend.load(path) if path.is_file() else None
    # embeddings / hybrid use the sharded store; meta.json marks completeness.
    if not (path / "meta.json").is_file():
        return None
    emb = EmbeddingsBackend.load_sharded(path)
    if cfg.backend == "embeddings":
        return emb
    if cfg.backend == "hybrid":
        bm25 = BM25Backend()
        bm25.build(emb.fragments)
        return HybridBackend(bm25, emb)
    raise ValueError(f"unknown search backend: {cfg.backend!r}")


def index_is_stale(cfg: Config) -> bool:
    """True if the index is missing or older than the cached records.

    A sync writes only changed records, so the newest record file's mtime (and
    the records directory's own mtime, which moves when records are added or
    removed) reliably post-dates the index whenever the corpus has changed.
    Keeping this purely mtime-based means ``search`` can always reindex before
    querying without rebuilding when nothing actually changed. For the sharded
    embeddings index the ``meta.json`` mtime (written last, on completion) is
    the freshness marker.
    """
    if cfg.backend == "bm25":
        marker = index_path(cfg)
    else:
        marker = _emb_meta_path(cfg)
    if not marker.is_file():
        return True
    index_mtime = marker.stat().st_mtime
    records_dir = cfg.records_dir
    if not records_dir.is_dir():
        return False  # no records to index against
    newest = records_dir.stat().st_mtime  # moves on add/remove
    for record in records_dir.glob("*.md"):
        newest = max(newest, record.stat().st_mtime)
    return newest > index_mtime



# --- hybrid backend (reciprocal-rank fusion) -----------------------------

class HybridBackend(SearchBackend):
    name = "hybrid"

    def __init__(self, bm25: BM25Backend, embeddings: EmbeddingsBackend, rrf_k: int = 60):
        self.bm25 = bm25
        self.embeddings = embeddings
        self.rrf_k = rrf_k

    def build(self, fragments: list[Fragment], progress=None) -> None:
        self.bm25.build(fragments)
        self.embeddings.build(fragments, progress=progress)

    def search(self, query: str, top_k: int,
               max_words: int = DEFAULT_EXTRACT_WORDS) -> list[Hit]:
        pool = max(top_k * 3, 20)
        bm = self.bm25.search(query, pool, max_words)
        em = self.embeddings.search(query, pool, max_words)
        fused: dict[str, list] = {}
        for ranking in (bm, em):
            for rank, hit in enumerate(ranking):
                slot = fused.setdefault(hit.fragment_id, [0.0, hit])
                slot[0] += 1.0 / (self.rrf_k + rank + 1)
        ordered = sorted(fused.values(), key=lambda pair: pair[0], reverse=True)
        out: list[Hit] = []
        for score, hit in ordered[:top_k]:
            out.append(Hit(hit.record_id, hit.fragment_id, score, hit.snippet,
                           hit.source_path, hit.line_range))
        return out

    def record_extracts(self, query: str, record_id: str, n: int,
                        max_words: int = DEFAULT_EXTRACT_WORDS) -> list[str]:
        # Prefer the embeddings half for semantically coherent extracts; fall
        # back to BM25 if no vectors are available.
        extracts = self.embeddings.record_extracts(query, record_id, n, max_words)
        return extracts or self.bm25.record_extracts(query, record_id, n, max_words)

    def save(self, path) -> None:
        import json
        data = {
            "backend": self.name,
            "fragments": [f.to_dict() for f in self.bm25.fragments],
            "vectors": self.embeddings.vectors,
            "server": self.embeddings.server,
            "url": self.embeddings.url,
            "model": self.embeddings.model,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "HybridBackend":
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        fragments = [Fragment.from_dict(d) for d in data.get("fragments", [])]
        bm25 = BM25Backend()
        bm25.build(fragments)
        emb = EmbeddingsBackend(data.get("server", "lmstudio"),
                                data.get("url", "http://localhost:1234"),
                                data.get("model", ""))
        emb.fragments = fragments
        emb.vectors = data.get("vectors", [])
        return cls(bm25, emb)


# --- orchestration -------------------------------------------------------

def run_search(cfg: Config, session_dir: str | Path, *, do_sync: bool = True,
               collections: list[str] | None = None, verbose: bool = True) -> dict:
    """Rank every claim in a session and (re)write its ``review.md``."""
    session_dir = Path(session_dir)
    claims_path = session_dir / "work" / "claims.json"
    if not claims_path.is_file():
        raise FileNotFoundError(f"no claims file at {claims_path}")
    doc = claims_mod.load_claims(claims_path)

    if do_sync:
        try:
            zotero_sync.sync(cfg, verbose=False)
        except Exception as exc:  # never let a sync hiccup block a search
            zotero_sync._warn(f"quick sync skipped ({exc}); using existing cache.")

    records = store.load_records(cfg.records_dir)
    rec_by_id = {r.record_id: r for r in records}
    # Rebuild the index whenever the records have changed (e.g. the sync above
    # just wrote new or updated records), so a single `search` call always
    # queries a current index. build_index is a no-op-cheap rebuild for BM25.
    if index_is_stale(cfg):
        build_index(cfg, verbose=verbose)
    backend = load_index(cfg)

    results: list[tuple[claims_mod.Claim, str, list]] = []
    searched = 0
    for claim in doc.claims:
        if claim.claim_type == "common-knowledge":
            results.append((claim, "skip", []))
            continue
        searched += 1
        query = claim.search_query or claim.sentence
        raw_hits = backend.search(query, cfg.top_k * 4, cfg.extract_words) if backend else []
        candidates = _rank_for_claim(raw_hits, rec_by_id, cfg, claim, collections,
                                     backend, query)
        status = "ok" if candidates else "needs_source"
        results.append((claim, status, candidates))

    review_path = session_dir / "review.md"
    claims_mod.write_review(review_path, doc, results, cfg)
    summary = {
        "session": session_dir.name,
        "claims": len(doc.claims),
        "searched": searched,
        "review": str(review_path),
        "records": len(records),
    }
    if verbose:
        print(f"OK: searched {searched}/{len(doc.claims)} claims across "
              f"{len(records)} records -> {review_path}")
    return summary


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _rank_for_claim(raw_hits: list[Hit], rec_by_id: dict, cfg: Config,
                    claim, collections: list[str] | None,
                    backend: SearchBackend | None = None,
                    query: str = "") -> list:
    """Filter, bias, deduplicate, and convert hits into review candidates."""
    bias_own = claim.claim_type == "own-project"
    own = set(cfg.own_collections)
    filter_set = set(collections) if collections else None

    # Best fragment per record, with optional collection filter + own bias.
    best: dict[str, tuple[float, Hit]] = {}
    for hit in raw_hits:
        rec = rec_by_id.get(hit.record_id)
        if rec is None:
            continue
        rec_collections = set(rec.collections)
        if filter_set is not None and not (rec_collections & filter_set):
            continue
        score = hit.score
        if bias_own and (rec_collections & own):
            score *= cfg.own_bias
        current = best.get(hit.record_id)
        if current is None or score > current[0]:
            best[hit.record_id] = (score, hit)

    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)

    # Deduplicate near-identical library items by normalised title.
    seen_titles: set[str] = set()
    deduped: list[tuple[str, float, Hit]] = []
    for record_id, (score, hit) in ranked:
        rec = rec_by_id.get(record_id)
        title_key = _normalise_title(rec.title) if rec else record_id
        if title_key and title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        deduped.append((record_id, score, hit))

    deduped = deduped[:cfg.top_k]
    if not deduped:
        return []

    top_score = deduped[0][1] or 1.0
    candidates = []
    for i, (record_id, score, hit) in enumerate(deduped):
        rec = rec_by_id[record_id]
        # Show the best few distinct sections of the document, not just its
        # single top fragment, so relevance is easier to judge. Fall back to
        # the one ranked snippet if the backend can't provide more.
        snippets: list[str] = []
        if backend is not None and cfg.extracts_per_doc > 1:
            snippets = backend.record_extracts(
                query, record_id, cfg.extracts_per_doc, cfg.extract_words)
        if not snippets:
            snippets = [hit.snippet]
        candidates.append(claims_mod.build_candidate(
            code_suffix=_letter(i),
            record=rec,
            score=score,
            ratio=(score / top_score) if top_score else 0.0,
            snippets=snippets,
            claim=claim,
        ))
    return candidates


def _letter(i: int) -> str:
    # 0 -> a, 25 -> z, 26 -> aa
    label = ""
    i += 1
    while i > 0:
        i, rem = divmod(i - 1, 26)
        label = chr(ord("a") + rem) + label
    return label
