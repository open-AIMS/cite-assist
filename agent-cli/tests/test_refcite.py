"""End-to-end and unit tests for refcite (stdlib-only, no live Zotero)."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from refcite import apa, claims, extract, insert, progress, store, yamlfm, zotero_sync
from refcite import search as search_pkg
from refcite.config import Config
from refcite.search.bm25 import BM25Backend
from refcite.search.base import Fragment


# --- fixtures ------------------------------------------------------------

CSL_ITEMS = [
    {
        "id": "ABCD1",
        "type": "article-journal",
        "title": "Mapping turbid-water reefs from satellite imagery",
        "author": [{"family": "Smith", "given": "John A."},
                   {"family": "Jones", "given": "B."}],
        "issued": {"date-parts": [[2019]]},
        "container-title": "Remote Sensing of Environment",
        "volume": "231",
        "page": "111-125",
        "DOI": "10.1016/j.rse.2019.01.001",
        "abstract": ("We mapped 1023 individual reef features across the shelf using "
                     "satellite imagery optimised for turbid water. The foobar "
                     "classification separates coral reef from rocky reef."),
    },
    {
        "id": "ABCD2",
        "type": "dataset",
        "title": "North Australian reef boundaries dataset",
        "author": [{"family": "Lawrey", "given": "Eric"}],
        "issued": {"date-parts": [[2024]]},
        "publisher": "Australian Institute of Marine Science",
        "DOI": "10.26274/abcd-1234",
    },
    {
        "id": "ABCD3",
        "type": "report",
        "title": "Sediment plume modelling for dredging operations",
        "author": [{"literal": "Australian Institute of Marine Science"}],
        "issued": {"date-parts": [[2021]]},
        "publisher": "AIMS",
    },
]


def make_config(tmp_path: Path) -> Config:
    return Config(
        repo_root=tmp_path,
        config_path=tmp_path / "cite-assist.toml",
        zotero_api_url="http://localhost:23119",
        zotero_library_type="user",
        zotero_library_id="0",
        csljson_export="",
        sync_max_delta=1000,
        cache_dir=tmp_path / "cache",
        backend="bm25",
        top_k=8,
        own_collections=["NESP MaC 3.17"],
        own_bias=1.25,
        needs_source_threshold=0.0,
        extract_words=120,
        extracts_per_doc=2,
        fragment_min_words=30,
        emb_server="lmstudio",
        emb_url="http://localhost:1234",
        emb_model="test-model",
        emb_num_shards=20,
        sessions_dir=tmp_path / "citations",
        references_file=tmp_path / "references.md",
        raw={},
    )


def write_csljson(tmp_path: Path) -> Path:
    path = tmp_path / "library.json"
    path.write_text(json.dumps(CSL_ITEMS), encoding="utf-8")
    return path


def _keep_only_record(review_path: Path, record_id: str) -> None:
    """Simulate the author deleting every candidate block except one record."""
    keep_marker = f"record:{record_id}"
    out: list[str] = []
    block: list[str] = []
    block_is_candidate = False
    block_keep = False

    def flush():
        if not block:
            return
        if not block_is_candidate or block_keep:
            out.extend(block)

    for line in review_path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("### "):
            flush()
            block = [line]
            block_is_candidate = True
            block_keep = keep_marker in line
        elif line.startswith("## ") or line.startswith("# "):
            flush()
            block = [line]
            block_is_candidate = False
            block_keep = False
        else:
            block.append(line)
    flush()
    review_path.write_text("\n".join(out), encoding="utf-8")


# --- yamlfm --------------------------------------------------------------

def test_yamlfm_round_trip_with_commas_and_lists():
    fm = {
        "record_id": "0042",
        "year": 2019,
        "authors": ["Smith, J. A.", "Jones, B."],
        "title": "Reefs: a study, of mapping",
        "has_fulltext": True,
        "url": "",
    }
    text = yamlfm.make_record(fm, "body text here")
    parsed, body = yamlfm.parse(text)
    assert parsed["record_id"] == "0042"          # stays zero-padded (quoted)
    assert parsed["year"] == 2019
    assert parsed["authors"] == ["Smith, J. A.", "Jones, B."]
    assert parsed["title"] == "Reefs: a study, of mapping"
    assert parsed["has_fulltext"] is True
    assert parsed["url"] == ""
    assert body.strip() == "body text here"


# --- extract + apa -------------------------------------------------------

def test_extract_normalises_authors_and_year():
    fields = extract.csl_to_fields(CSL_ITEMS[0])
    assert fields["authors"] == ["Smith, J. A.", "Jones, B."]
    assert fields["year"] == 2019
    assert fields["item_type"] == "journal-article"
    assert fields["zotero_key"] == "ABCD1"


def test_extract_literal_author_preserved():
    fields = extract.csl_to_fields(CSL_ITEMS[2])
    assert fields["authors"] == ["Australian Institute of Marine Science"]


def test_content_tier_abstract_vs_meta():
    _, tier_abs, _ = extract.build_body(CSL_ITEMS[0], None)
    assert tier_abs == "abstract"
    _, tier_meta, _ = extract.build_body(CSL_ITEMS[1], None)
    assert tier_meta == "meta"
    long_text = "word " * 100
    _, tier_full, has_ft = extract.build_body(CSL_ITEMS[0], long_text)
    assert tier_full == "full" and has_ft is True


def test_apa_in_text_and_reference():
    rec = extract.make_record("0001", CSL_ITEMS[0], None, [])
    assert apa.in_text(rec) == "(Smith & Jones, 2019)"
    ref = apa.reference(rec)
    assert ref.startswith("Smith, J. A., & Jones, B. (2019).")
    assert "*Remote Sensing of Environment*" in ref
    assert "https://doi.org/10.1016/j.rse.2019.01.001" in ref


def test_apa_placeholder_for_missing_metadata():
    rec = extract.make_record("0009", {"type": "report", "title": "Untitled"}, None, [])
    assert apa.is_placeholder(rec) is True
    assert apa.in_text(rec) == "(Author unknown, n.d.)"


def test_apa_three_authors_uses_et_al():
    item = {"id": "X", "type": "article-journal", "title": "T",
            "author": [{"family": "A", "given": "A"}, {"family": "B", "given": "B"},
                       {"family": "C", "given": "C"}],
            "issued": {"date-parts": [[2020]]}}
    rec = extract.make_record("0002", item, None, [])
    assert apa.in_text(rec) == "(A et al., 2020)"


# --- store + sync --------------------------------------------------------

def test_sync_from_csljson_builds_records(tmp_path):
    cfg = make_config(tmp_path)
    result = zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    assert result["written"] == 3
    assert result["total"] == 3
    records = store.load_records(cfg.records_dir)
    assert {r.zotero_key for r in records} == {"ABCD1", "ABCD2", "ABCD3"}
    # idempotent: re-running writes nothing new
    again = zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    assert again["written"] == 0
    assert again["unchanged"] == 3


def test_diagnose_403_explains_how_to_enable():
    err = urllib.error.HTTPError("http://localhost:23119", 403, "Forbidden", {}, None)
    msg = zotero_sync.diagnose(err)
    assert msg == zotero_sync.ENABLE_LOCAL_API_INSTRUCTIONS
    assert "Allow other applications" in msg
    assert "Settings -> Advanced" in msg


def test_diagnose_connection_refused_mentions_running_zotero():
    err = urllib.error.URLError("Connection refused")
    msg = zotero_sync.diagnose(err, "http://localhost:23119")
    assert "running" in msg.lower()
    assert "http://localhost:23119" in msg


def test_diagnose_other_http_error_reports_code():
    err = urllib.error.HTTPError("http://localhost:23119", 500, "Server Error", {}, None)
    msg = zotero_sync.diagnose(err)
    assert "500" in msg


def test_fetch_item_versions_uses_top_level_only(tmp_path, monkeypatch):
    # Only top-level items must be ingested; /items would also pull a record's
    # child notes and attachments in as their own bogus records.
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return b"{}", {"Last-Modified-Version": "2980"}

    monkeypatch.setattr(zotero_sync, "_http_get", fake_get)
    cfg = make_config(tmp_path)
    zotero_sync._fetch_item_versions(cfg, since=0)
    assert "/items/top?" in captured["url"]
    assert "/items?" not in captured["url"]


def test_http_get_retries_transient_connection_errors(monkeypatch):
    # Zotero's local API occasionally refuses a connection under load; a transient
    # blip must be retried, not allowed to abort the whole sync.
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

        headers = {"Last-Modified-Version": "1"}

    def flaky_urlopen(req, timeout=15):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        return FakeResp()

    monkeypatch.setattr(zotero_sync.time, "sleep", lambda *_: None)
    monkeypatch.setattr(zotero_sync.urllib.request, "urlopen", flaky_urlopen)
    body, headers = zotero_sync._http_get("http://localhost:23119/x")
    assert calls["n"] == 3  # two failures, then success
    assert body == b"{}"


def test_http_get_does_not_retry_http_errors(monkeypatch):
    # A real HTTP response (e.g. 404 "no full text") must surface immediately.
    calls = {"n": 0}

    def always_404(req, timeout=15):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(zotero_sync.urllib.request, "urlopen", always_404)
    try:
        zotero_sync._http_get("http://localhost:23119/x")
        assert False, "expected HTTPError"
    except urllib.error.HTTPError:
        pass
    assert calls["n"] == 1  # not retried


def test_notes_are_folded_into_record_body():
    # A curator's Zotero note is our relevance interpretation; it must end up in
    # the searchable body of the parent record (not as its own record).
    item = {"id": "ABCD1", "type": "report", "title": "Reef inventory",
            "author": [{"family": "Smith", "given": "J."}]}
    rec = extract.make_record("0001", item, None, ["Reef mapping"],
                              "This document uses the same reef identifiers.")
    assert "Curator note: This document uses the same reef identifiers." in rec.body
    assert rec.front_matter["has_notes"] is True
    # Title and authors stay correct — the note never overwrites metadata.
    assert rec.front_matter["title"] == "Reef inventory"
    assert rec.front_matter["authors"] == ["Smith, J."]


def test_notes_make_record_searchable():
    item = {"id": "ABCD1", "type": "report", "title": "Reef inventory"}
    rec = extract.make_record("0001", item, None, [],
                              "turbid water foobar classification")
    frag = Fragment(f"{rec.record_id}#0", rec.record_id, rec.body)
    backend = BM25Backend()
    backend.build([frag])
    hits = backend.search("foobar classification", top_k=5)
    assert hits and hits[0].record_id == "0001"


def test_strip_html_collapses_note_markup():
    raw = "<p>First line.</p><p>Second &amp; third.</p><br/>Fourth"
    out = zotero_sync._strip_html(raw)
    assert "<" not in out
    assert "First line." in out
    assert "Second & third." in out
    assert "Fourth" in out


def test_notes_from_children_skips_attachments():
    children = [
        {"itemType": "note", "note": "<p>relevant context</p>"},
        {"itemType": "attachment", "key": "ATT1"},
        {"itemType": "note", "note": "<p>second note</p>"},
    ]
    text = zotero_sync._notes_from_children(children)
    assert "relevant context" in text
    assert "second note" in text
    assert "ATT1" not in text


def test_standalone_note_and_attachment_are_not_recorded(tmp_path, monkeypatch):
    # In CSL-JSON a standalone note/attachment masquerades as `document`, so the
    # sync must drop them by their true Zotero item type, not the CSL type.
    cfg = make_config(tmp_path)
    csl = [
        {"id": "REAL1", "type": "report", "title": "A real report"},
        {"id": "NOTE1", "type": "document", "title": "just a loose note"},
        {"id": "ATTACH1", "type": "document", "title": "scholar.ris"},
    ]
    types = {"REAL1": "report", "NOTE1": "note", "ATTACH1": "attachment"}

    monkeypatch.setattr(zotero_sync, "_fetch_item_versions",
                        lambda cfg, since: ({k["id"]: 1 for k in csl}, 7))
    monkeypatch.setattr(zotero_sync, "_fetch_collection_names", lambda cfg: {})
    monkeypatch.setattr(zotero_sync, "_fetch_csljson", lambda cfg, keys: csl)
    monkeypatch.setattr(zotero_sync, "_fetch_children", lambda cfg, key: [])
    monkeypatch.setattr(zotero_sync, "_fetch_fulltext", lambda cfg, key, children=None: None)
    monkeypatch.setattr(zotero_sync, "_fetch_item_data",
                        lambda cfg, key, names: (types[key], []))

    result = zotero_sync._sync_live(cfg, force_full=True, verbose=False)
    assert result["written"] == 1  # only the real report
    titles = [r.front_matter["title"] for r in store.load_records(cfg.records_dir)]
    assert titles == ["A real report"]


def test_incremental_sync_detects_changed_content(tmp_path, monkeypatch):
    # When an item's content changes (e.g. an abstract is added in Zotero) the
    # item's library version rises, so an incremental sync re-fetches it; the
    # per-item hash then differs, so it is re-written rather than skipped as
    # unchanged. This is what catches an edited/re-added record.
    cfg = make_config(tmp_path)
    state = {"item": {"id": "K1", "type": "dataset", "title": "A dataset"},
             "version": 1}

    monkeypatch.setattr(zotero_sync, "_fetch_item_versions",
                        lambda cfg, since: ({"K1": state["version"]}, state["version"]))
    monkeypatch.setattr(zotero_sync, "_fetch_collection_names", lambda cfg: {})
    monkeypatch.setattr(zotero_sync, "_fetch_csljson", lambda cfg, keys: [state["item"]])
    monkeypatch.setattr(zotero_sync, "_fetch_children", lambda cfg, key: [])
    monkeypatch.setattr(zotero_sync, "_fetch_fulltext", lambda cfg, key, children=None: None)
    monkeypatch.setattr(zotero_sync, "_fetch_deleted", lambda cfg, since: [])
    monkeypatch.setattr(zotero_sync, "_fetch_item_data",
                        lambda cfg, key, names: ("dataset", []))

    first = zotero_sync._sync_live(cfg, force_full=True, verbose=False)
    assert first["written"] == 1
    rec_before = store.load_records(cfg.records_dir)[0]
    assert rec_before.front_matter["content_tier"] == "meta"  # no abstract yet

    # Re-running with nothing changed is a no-op (hash matches).
    same = zotero_sync._sync_live(cfg, force_full=False, verbose=False)
    assert same["status"] == "current"

    # Now the abstract is added in Zotero and the item version rises.
    state["item"] = {"id": "K1", "type": "dataset", "title": "A dataset",
                     "abstract": "A newly added abstract describing the dataset."}
    state["version"] = 2

    second = zotero_sync._sync_live(cfg, force_full=False, verbose=False)
    assert second["written"] == 1      # change detected, record re-written
    assert second["unchanged"] == 0
    rec_after = store.load_records(cfg.records_dir)[0]
    assert rec_after.front_matter["content_tier"] == "abstract"
    assert "newly added abstract" in rec_after.body


def test_limit_caps_records_and_simulates_smaller_library(tmp_path, monkeypatch):
    # --limit fetches only the first N items to simulate a smaller library for
    # fast development. It must not prune the items it chose not to fetch, but it
    # DOES advance the library version so a follow-up sync is a quick no-op and
    # only genuine edits are re-fetched.
    cfg = make_config(tmp_path)
    csl = [{"id": f"K{i}", "type": "report", "title": f"Report {i}"} for i in range(10)]

    monkeypatch.setattr(zotero_sync, "_fetch_item_versions",
                        lambda cfg, since: ({k["id"]: 1 for k in csl}, 99))
    monkeypatch.setattr(zotero_sync, "_fetch_collection_names", lambda cfg: {})
    monkeypatch.setattr(zotero_sync, "_fetch_csljson",
                        lambda cfg, keys: [k for k in csl if k["id"] in keys])
    monkeypatch.setattr(zotero_sync, "_fetch_children", lambda cfg, key: [])
    monkeypatch.setattr(zotero_sync, "_fetch_fulltext", lambda cfg, key, children=None: None)
    monkeypatch.setattr(zotero_sync, "_fetch_deleted", lambda cfg, since: [])
    monkeypatch.setattr(zotero_sync, "_fetch_item_data",
                        lambda cfg, key, names: ("report", []))

    result = zotero_sync._sync_live(cfg, force_full=True, limit=3, verbose=False)
    assert result["written"] == 3
    assert len(store.load_records(cfg.records_dir)) == 3
    # Version advanced, so the limited cache behaves like a smaller library.
    manifest = store.load_manifest(cfg.manifest_path)
    assert manifest.get("library_version", 0) == 99
    # A follow-up plain sync is a quick no-op (not a re-fetch of everything).
    again = zotero_sync._sync_live(cfg, force_full=False, verbose=False)
    assert again["status"] == "current"
    assert len(store.load_records(cfg.records_dir)) == 3


def test_sync_falls_back_to_csljson_with_actionable_reason(tmp_path, capsys, monkeypatch):
    cfg = make_config(tmp_path)
    cfg.csljson_export = str(write_csljson(tmp_path))

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://localhost:23119", 403, "Forbidden", {}, None)

    monkeypatch.setattr(zotero_sync, "_sync_live", boom)
    result = zotero_sync.sync(cfg, verbose=False)
    assert result["written"] == 3  # built from the CSL-JSON export
    warning = capsys.readouterr().err
    assert "Allow other applications" in warning


# --- progress reporting --------------------------------------------------

def test_progress_non_tty_emits_periodic_lines(capsys):
    stream = io.StringIO()  # not a tty
    p = progress.ProgressReporter("Fetching", total=200, stream=stream)
    p.start()
    for i in range(1, 201):
        p.update(i)
    p.done("200 fetched")
    out = stream.getvalue()
    # Newline-delimited updates (no carriage returns when not a TTY).
    assert "\r" not in out
    assert "Fetching: [" in out
    assert "100%" in out
    assert "200 fetched" in out
    # Throttled to roughly one line per percent, not one per item.
    assert out.count("\n") <= 110


def test_progress_disabled_when_total_zero():
    stream = io.StringIO()
    p = progress.ProgressReporter("Nothing", total=0, stream=stream)
    p.start()
    p.update(0)
    p.done()
    assert stream.getvalue() == ""


def test_progress_tty_uses_carriage_return():
    class _TTY(io.StringIO):
        def isatty(self):
            return True

    stream = _TTY()
    p = progress.ProgressReporter("Embedding", total=10, stream=stream,
                                  min_interval=0.0)
    p.start()
    for i in range(1, 11):
        p.update(i)
    p.done()
    out = stream.getvalue()
    assert "\r" in out          # live, single-line updates
    assert out.endswith("\n")   # terminated cleanly


# --- bm25 ----------------------------------------------------------------

def test_bm25_ranks_matching_fragment():
    frags = [
        Fragment("0001#0", "0001", "turbid water reef foobar classification mapping"),
        Fragment("0002#0", "0002", "completely unrelated text about birds"),
    ]
    backend = BM25Backend()
    backend.build(frags)
    hits = backend.search("foobar turbid classification", top_k=5)
    assert hits and hits[0].record_id == "0001"


# --- fragmenting ---------------------------------------------------------

def _record(record_id: str, body: str, tier: str = "full") -> store.Record:
    return store.Record(record_id=record_id, front_matter={"content_tier": tier}, body=body)


def test_fragment_merges_lone_heading_forward():
    # A short heading line followed by a long single-line paragraph must not be
    # emitted as its own tiny fragment; it merges into the section it precedes.
    para = " ".join(f"word{i}" for i in range(300))  # > max_words -> long-line split
    body = f"Drivers of coral recovery\n{para}\n"
    frags = search_pkg.build_fragments([_record("0059", body)], min_words=30)
    assert frags, "expected fragments"
    # No fragment is a bare heading; the heading rides with following content.
    assert all(len(f.text.split()) >= 30 for f in frags)
    assert frags[0].text.startswith("Drivers of coral recovery")
    assert len(frags[0].text.split()) > 30


def test_fragment_merges_short_tail_backward():
    big = " ".join(f"term{i}" for i in range(150))
    body = f"{big}\n\ntiny tail\n"  # second chunk is below the minimum
    frags = search_pkg.build_fragments([_record("0001", body)], min_words=30)
    assert len(frags) == 1  # the tiny tail is folded into the previous fragment
    assert frags[0].text.rstrip().endswith("tiny tail")


def test_record_extracts_returns_multiple_distinct_sections():
    frags = [
        Fragment("0001#0", "0001", "alpha turbid reef mapping " + "filler " * 40),
        Fragment("0001#1", "0001", "beta turbid reef classification " + "filler " * 40),
        Fragment("0002#0", "0002", "unrelated birds and trees"),
    ]
    backend = BM25Backend()
    backend.build(frags)
    extracts = backend.record_extracts("turbid reef mapping classification", "0001", n=2)
    assert len(extracts) == 2
    assert extracts[0] != extracts[1]  # two different sections of the same doc


# --- embeddings sharded store --------------------------------------------

from refcite.search.embeddings import EmbeddingsBackend  # noqa: E402


def _stub_embed(calls):
    """A deterministic, server-free stand-in for the model API. Records every
    text it is asked to embed in ``calls`` so a test can assert how many
    fragments were (re)embedded."""
    def embed(texts):
        out = []
        for t in texts:
            calls.append(t)
            h = abs(hash(t))
            out.append([(h % 1000) / 1000.0, ((h // 1000) % 1000) / 1000.0, 0.5])
        return out
    return embed


def _frags(n):
    # Spread across record ids so multiple shards are exercised.
    return [Fragment(f"{i:04d}#0", f"{i:04d}", f"fragment text number {i}") for i in range(n)]


def test_sharded_build_writes_readable_shards(tmp_path, monkeypatch):
    backend = EmbeddingsBackend(model="test-model", num_shards=4)
    calls = []
    monkeypatch.setattr(backend, "_embed", _stub_embed(calls))
    shard_dir = tmp_path / "embeddings"
    backend.build_sharded(_frags(10), shard_dir)

    assert (shard_dir / "meta.json").is_file()
    shards = sorted(shard_dir.glob("shard-*.json"))
    assert shards, "expected shard files"
    assert len(shards) <= 4
    for path in shards:
        data = json.loads(path.read_text(encoding="utf-8"))  # valid JSON
        assert data["model"] == "test-model"
        # Each fragment item is on its own line (vectors stay single-line).
        text = path.read_text(encoding="utf-8")
        item_lines = [ln for ln in text.splitlines() if ln.strip().startswith('{"fragment_id"')]
        assert len(item_lines) == len(data["items"])
    assert len(calls) == 10  # every fragment embedded once on a cold build


def test_sharded_build_resumes_and_reuses(tmp_path, monkeypatch):
    shard_dir = tmp_path / "embeddings"
    frags = _frags(10)

    first = EmbeddingsBackend(model="test-model", num_shards=4)
    calls1 = []
    monkeypatch.setattr(first, "_embed", _stub_embed(calls1))
    first.build_sharded(frags, shard_dir)
    assert len(calls1) == 10

    # A second build with the same fragments must reuse every cached vector.
    second = EmbeddingsBackend(model="test-model", num_shards=4)
    calls2 = []
    monkeypatch.setattr(second, "_embed", _stub_embed(calls2))
    stats = second.build_sharded(frags, shard_dir)
    assert calls2 == []
    assert stats["reused"] == 10 and stats["embedded"] == 0

    # Change one fragment's text -> only that fragment is re-embedded.
    frags[3] = Fragment("0003#0", "0003", "completely different wording now")
    third = EmbeddingsBackend(model="test-model", num_shards=4)
    calls3 = []
    monkeypatch.setattr(third, "_embed", _stub_embed(calls3))
    stats = third.build_sharded(frags, shard_dir)
    assert len(calls3) == 1 and stats["embedded"] == 1


def test_sharded_model_change_forces_full_reembed(tmp_path, monkeypatch):
    shard_dir = tmp_path / "embeddings"
    frags = _frags(6)
    a = EmbeddingsBackend(model="model-a", num_shards=3)
    monkeypatch.setattr(a, "_embed", _stub_embed([]))
    a.build_sharded(frags, shard_dir)

    b = EmbeddingsBackend(model="model-b", num_shards=3)
    calls = []
    monkeypatch.setattr(b, "_embed", _stub_embed(calls))
    b.build_sharded(frags, shard_dir)
    assert len(calls) == 6  # different model -> nothing reused


def test_sharded_load_roundtrip_and_search(tmp_path, monkeypatch):
    shard_dir = tmp_path / "embeddings"
    frags = _frags(8)
    backend = EmbeddingsBackend(model="test-model", num_shards=4)
    monkeypatch.setattr(backend, "_embed", _stub_embed([]))
    backend.build_sharded(frags, shard_dir)

    loaded = EmbeddingsBackend.load_sharded(shard_dir)
    assert len(loaded.fragments) == 8
    assert len(loaded.vectors) == 8
    monkeypatch.setattr(loaded, "_embed", _stub_embed([]))
    hits = loaded.search("fragment text number 5", top_k=3)
    assert hits  # returns ranked candidates


# --- insert helpers ------------------------------------------------------

def test_apply_citation_simple():
    out = insert._apply_citation("The project mapped reefs.", ["Smith, 2019"])
    assert out == "The project mapped reefs (Smith, 2019)."


def test_apply_citation_merges_existing_group():
    out = insert._apply_citation("Reefs were mapped (Foo, 2020).", ["Smith, 2019"])
    assert out == "Reefs were mapped (Foo, 2020; Smith, 2019)."


def test_find_span_whitespace_tolerant():
    line = "The   project mapped reefs across waters."
    span = insert._find_span(line, "The project mapped reefs across waters.")
    assert span is not None
    start, end = span
    assert line[start:end] == line


# --- full pipeline -------------------------------------------------------

def test_search_then_insert_end_to_end(tmp_path):
    cfg = make_config(tmp_path)
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    search_pkg.build_index(cfg, verbose=False)

    report = tmp_path / "report.md"
    report.write_text(
        "# Report\n"
        "\n"
        "Coral reefs are important.\n"
        "\n"
        "The project mapped 1023 reef features using the foobar classification "
        "across turbid waters.\n",
        encoding="utf-8",
    )

    session = cfg.active_dir / "2026-06-17_intro"
    (session / "work").mkdir(parents=True)
    claims.save_claims(session / "work" / "claims.json", {
        "session": "2026-06-17_intro",
        "report_path": "report.md",
        "claims": [{
            "claim_id": "c1",
            "code": "C1",
            "report_path": "report.md",
            "line": 5,
            "sentence": ("The project mapped 1023 reef features using the foobar "
                         "classification across turbid waters."),
            "claim_type": "background",
            "search_query": "turbid water reef foobar classification mapping",
            "existing_citations": [],
        }],
    })

    search_pkg.run_search(cfg, session, do_sync=False, verbose=False)
    review = session / "review.md"
    assert review.is_file()
    surviving = claims.parse_review_surviving(review)
    assert "C1" in surviving and "0001" in surviving["C1"]
    # Each candidate carries a Cache: pointer to its full record markdown (D24).
    assert "Cache: cache/records/0001.md" in review.read_text(encoding="utf-8")

    # Simulate the author pruning review.md down to the Smith candidate only.
    _keep_only_record(review, "0001")
    assert claims.parse_review_surviving(review)["C1"] == ["0001"]

    insert.insert_session(cfg, session, verbose=False)

    updated = report.read_text(encoding="utf-8")
    assert "(Smith & Jones, 2019)." in updated
    assert "Lawrey" not in updated
    refs = cfg.references_file.read_text(encoding="utf-8")
    assert "Smith, J. A., & Jones, B. (2019)." in refs
    assert "<!-- refcite:0001 -->" in refs
    # session moved to done
    assert not session.exists()
    assert (cfg.done_dir / "2026-06-17_intro").is_dir()
    # the pre-edit backup was saved
    assert (cfg.done_dir / "2026-06-17_intro" / "work" / "report.md").is_file()


def _make_select_session(cfg, tmp_path):
    """Build a report + session + claims, run search, return the session dir."""
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    search_pkg.build_index(cfg, verbose=False)
    report = tmp_path / "report.md"
    report.write_text(
        "# Report\n\n"
        "The project mapped 1023 reef features using the foobar classification "
        "across turbid waters.\n",
        encoding="utf-8",
    )
    session = cfg.active_dir / "2026-06-17_select"
    (session / "work").mkdir(parents=True)
    claims.save_claims(session / "work" / "claims.json", {
        "session": "2026-06-17_select",
        "report_path": "report.md",
        "claims": [{
            "claim_id": "c1", "code": "C1", "report_path": "report.md", "line": 3,
            "sentence": ("The project mapped 1023 reef features using the foobar "
                         "classification across turbid waters."),
            "claim_type": "background",
            "search_query": "turbid water reef foobar classification mapping",
            "existing_citations": [],
        }],
    })
    search_pkg.run_search(cfg, session, do_sync=False, verbose=False)
    return session, report


def test_parse_review_candidates_pairs_codes(tmp_path):
    cfg = make_config(tmp_path)
    session, _ = _make_select_session(cfg, tmp_path)
    cands = claims.parse_review_candidates(session / "review.md")
    # Codes are unique and each maps to its claim and a record id.
    codes = [c for c, _, _ in cands]
    assert len(codes) == len(set(codes))
    assert all(claim_code == "C1" for _, claim_code, _ in cands)
    assert any(record_id == "0001" for _, _, record_id in cands)


def test_insert_select_applies_named_candidate(tmp_path):
    cfg = make_config(tmp_path)
    session, report = _make_select_session(cfg, tmp_path)
    # Find the candidate code that points at record 0001 (Smith & Jones).
    cands = claims.parse_review_candidates(session / "review.md")
    smith_code = next(c for c, _, rec in cands if rec == "0001")

    insert.insert_session(cfg, session, select=[smith_code], verbose=False)

    updated = report.read_text(encoding="utf-8")
    assert "(Smith & Jones, 2019)." in updated
    assert "Lawrey" not in updated
    # The archived review keeps only the selected block.
    archived = (cfg.done_dir / "2026-06-17_select" / "review.md").read_text(encoding="utf-8")
    assert f"### {smith_code} " in archived
    other_codes = [c for c, _, rec in cands if rec != "0001"]
    for code in other_codes:
        assert f"### {code} " not in archived


def test_insert_select_unknown_code_raises(tmp_path):
    cfg = make_config(tmp_path)
    session, _ = _make_select_session(cfg, tmp_path)
    with pytest.raises(ValueError, match="unknown candidate code"):
        insert.insert_session(cfg, session, select=["C9z"], verbose=False)
    # The session is untouched (not moved) when the selection is invalid.
    assert session.exists()


def test_prune_review_to_drops_unselected(tmp_path):
    cfg = make_config(tmp_path)
    session, _ = _make_select_session(cfg, tmp_path)
    review = session / "review.md"
    cands = claims.parse_review_candidates(review)
    keep_code = cands[0][0]
    claims.prune_review_to(review, [keep_code])
    remaining = claims.parse_review_candidates(review)
    assert [c for c, _, _ in remaining] == [keep_code]


def test_common_knowledge_is_skipped(tmp_path):
    cfg = make_config(tmp_path)
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    search_pkg.build_index(cfg, verbose=False)

    session = cfg.active_dir / "2026-06-17_ck"
    (session / "work").mkdir(parents=True)
    claims.save_claims(session / "work" / "claims.json", {
        "session": "2026-06-17_ck",
        "report_path": "report.md",
        "claims": [{
            "claim_id": "c1", "code": "C1", "report_path": "report.md", "line": 1,
            "sentence": "Water is wet.", "claim_type": "common-knowledge",
            "search_query": "", "existing_citations": [],
        }],
    })
    search_pkg.run_search(cfg, session, do_sync=False, verbose=False)
    review_text = (session / "review.md").read_text(encoding="utf-8")
    assert "skipped" in review_text
    assert "no source needed" in review_text.lower()


def test_index_is_stale_tracks_record_changes(tmp_path):
    cfg = make_config(tmp_path)
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    assert search_pkg.index_is_stale(cfg) is True  # no index yet
    search_pkg.build_index(cfg, verbose=False)
    assert search_pkg.index_is_stale(cfg) is False  # fresh
    # Touching a record makes the index stale again.
    import os
    import time
    record = sorted(cfg.records_dir.glob("*.md"))[0]
    time.sleep(0.01)
    os.utime(record, None)
    assert search_pkg.index_is_stale(cfg) is True


def test_search_reindexes_when_records_change(tmp_path):
    cfg = make_config(tmp_path)
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    search_pkg.build_index(cfg, verbose=False)

    # Add a brand-new record AFTER the index was built. Without an automatic
    # reindex inside run_search, a query for its unique term would find nothing.
    import time
    time.sleep(0.01)
    manifest = store.load_manifest(cfg.manifest_path)
    new_id = store.next_record_id(manifest)
    store.write_record(cfg.records_dir, store.Record(
        record_id=new_id,
        front_matter={
            "record_id": new_id,
            "title": "Quokka habitat mapping on Rottnest Island",
            "authors": ["Quokka, Q."],
            "year": 2025,
            "content_tier": "abstract",
        },
        body="This study used zzqquokka satellite imagery to map quokka habitat.",
    ))

    session = cfg.active_dir / "2026-06-18_reindex"
    (session / "work").mkdir(parents=True)
    claims.save_claims(session / "work" / "claims.json", {
        "session": "2026-06-18_reindex",
        "report_path": "report.md",
        "claims": [{
            "claim_id": "c1", "code": "C1", "report_path": "report.md", "line": 1,
            "sentence": "Quokka habitat was mapped from imagery.",
            "claim_type": "background",
            "search_query": "zzqquokka quokka habitat mapping",
            "existing_citations": [],
        }],
    })
    search_pkg.run_search(cfg, session, do_sync=False, verbose=False)
    review_text = (session / "review.md").read_text(encoding="utf-8")
    assert f"record:{new_id}" in review_text


def test_new_session_reports_cache_state(tmp_path, capsys):
    import argparse

    from refcite import __main__ as cli

    cfg = make_config(tmp_path)
    zotero_sync.sync_from_csljson(cfg, write_csljson(tmp_path), verbose=False)
    args = argparse.Namespace(slug="intro-test", report="report.md")
    rc = cli._cmd_new_session(cfg, args)
    out = capsys.readouterr().out
    assert rc == 0
    # The cache summary lets the agent skip a separate `status` call (D25).
    assert "cache:" in out
    assert "records" in out and "ready to search" in out


def test_new_session_flags_empty_cache(tmp_path, capsys, monkeypatch):
    import argparse

    from refcite import __main__ as cli

    cfg = make_config(tmp_path)  # no sync — cache is empty
    # Empty cache triggers a Zotero probe; stub it so the test stays offline.
    monkeypatch.setattr(zotero_sync, "check_connection",
                        lambda cfg, **kw: {"ok": False, "library_version": None,
                                           "message": "Zotero not running."})
    args = argparse.Namespace(slug="intro-test", report="report.md")
    rc = cli._cmd_new_session(cfg, args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "EMPTY" in out
    assert "UNREACHABLE" in out


def test_make_snippet_respects_max_words():
    from refcite.search.base import make_snippet

    text = " ".join(f"word{i}" for i in range(500))
    short = make_snippet(text, ["word250"], max_words=20)
    long = make_snippet(text, ["word250"], max_words=120)
    # The window is centred on the matched token in both, but the word budget
    # controls how much surrounding text is shown.
    assert len(short.split()) < len(long.split())
    assert "word250" in short and "word250" in long
    # A record shorter than the budget is shown in full, untruncated.
    full = make_snippet("just three words", ["three"], max_words=120)
    assert full == "just three words"


def test_extract_words_config_controls_review_extract(tmp_path):
    cfg_short = make_config(tmp_path)
    cfg_short.extract_words = 15
    long_fulltext = " ".join(
        ["turbid water reef foobar classification mapping context"] * 60)
    zotero_sync.sync_from_csljson(cfg_short, write_csljson(tmp_path), verbose=False)
    # Give record 0001 a long full-text body so the extract is actually truncated.
    rec_path = cfg_short.records_dir / "0001.md"
    fm, _ = yamlfm.parse(rec_path.read_text(encoding="utf-8"))
    fm["content_tier"] = "full"
    rec_path.write_text(yamlfm.make_record(fm, long_fulltext), encoding="utf-8")
    search_pkg.build_index(cfg_short, verbose=False)

    session = cfg_short.active_dir / "2026-06-17_extract"
    (session / "work").mkdir(parents=True)
    claims.save_claims(session / "work" / "claims.json", {
        "session": "2026-06-17_extract",
        "report_path": "report.md",
        "claims": [{
            "claim_id": "c1", "code": "C1", "report_path": "report.md", "line": 1,
            "sentence": "Reefs were mapped.", "claim_type": "background",
            "search_query": "turbid water reef foobar classification mapping",
            "existing_citations": [],
        }],
    })
    search_pkg.run_search(cfg_short, session, do_sync=False, verbose=False)
    extract_line = next(
        ln for ln in (session / "review.md").read_text(encoding="utf-8").splitlines()
        if ln.startswith("Extract:"))
    # ~15 words plus the Extract: label and ellipsis markers; well under 40.
    assert len(extract_line.split()) < 40

