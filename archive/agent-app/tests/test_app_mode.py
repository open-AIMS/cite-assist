"""Tests for the Stage A1 app-mode additions (DESIGN-app-mode.md).

Covers the additive ``refcite`` integration surface (A31) and the pure-logic
parts of ``citeapp`` (settings/token obfuscation A18, metrics log, config
overrides A35, progress callback A22). GUI construction needs a display and is
smoke-tested manually, not here.
"""

import json
from pathlib import Path

import pytest

import refcite
from refcite import claims, insert, progress, store
from refcite.config import Config, load_config

from citeapp import metrics, settings as app_settings

from test_refcite import make_config


# --- A31: the declared integration surface is re-exported ----------------

def test_integration_surface_exported():
    for name in ["load_config", "check_connection", "sync", "zotero_select_uri",
                 "load_records", "records_by_id", "create_session", "Claim",
                 "save_claims", "claims_template", "run_search", "build_index",
                 "insert_session", "sentence_matches", "Config", "Record"]:
        assert hasattr(refcite, name), f"missing surface export: {name}"
        assert name in refcite.__all__


# --- A35: load_config overrides ------------------------------------------

def test_load_config_overrides_backend(tmp_path):
    cfg = load_config(repo_root=tmp_path,
                      overrides={"backend": "embeddings",
                                 "emb_url": "http://x:1234",
                                 "emb_model": "m"})
    assert cfg.backend == "embeddings"
    assert cfg.emb_url == "http://x:1234"
    assert cfg.emb_model == "m"


def test_load_config_overrides_ignores_unknown_keys(tmp_path):
    cfg = load_config(repo_root=tmp_path, overrides={"nonexistent": 1, "backend": None})
    # None values and unknown keys are skipped; defaults survive.
    assert cfg.backend == "bm25"


def test_load_config_no_overrides_unchanged(tmp_path):
    cfg = load_config(repo_root=tmp_path)
    assert cfg.cache_dir.is_absolute()
    assert cfg.sessions_dir == tmp_path / "citations"


# --- A19: create_session collision suffixing -----------------------------

def test_create_session_scaffolds_work_dir(tmp_path):
    cfg = make_config(tmp_path)
    session = claims.create_session(cfg, "Intro Reef Mapping!", today="2026-06-20")
    assert session.name == "2026-06-20_intro-reef-mapping"
    assert (session / "work").is_dir()


def test_create_session_collision_suffix(tmp_path):
    cfg = make_config(tmp_path)
    a = claims.create_session(cfg, "intro", today="2026-06-20")
    b = claims.create_session(cfg, "intro", today="2026-06-20")
    c = claims.create_session(cfg, "intro", today="2026-06-20")
    assert [p.name for p in (a, b, c)] == [
        "2026-06-20_intro", "2026-06-20_intro-2", "2026-06-20_intro-3"]


# --- A33/A34: verbatim sentence matcher ----------------------------------

def test_sentence_matches_verbatim():
    doc = "Line one.\nProject X mapped 1,023 reefs across the shelf.\nLine three."
    assert insert.sentence_matches(doc, "Project X mapped 1,023 reefs across the shelf.")
    # Whitespace runs are equivalent (same rule insert uses to locate sentences).
    assert insert.sentence_matches(doc, "Project X mapped 1,023  reefs across the shelf.")


def test_sentence_matches_rejects_paraphrase():
    doc = "Project X mapped 1,023 reefs across the shelf."
    assert not insert.sentence_matches(doc, "Project X mapped over a thousand reefs.")
    assert not insert.sentence_matches(doc, "")


# --- A13: zotero_select_uri ----------------------------------------------

def test_zotero_select_uri_user_library(tmp_path):
    cfg = make_config(tmp_path)
    rec = store.Record(record_id="0001", front_matter={"zotero_key": "ABCD1234"}, body="")
    assert refcite.zotero_select_uri(rec, cfg) == "zotero://select/library/items/ABCD1234"


def test_zotero_select_uri_group_library(tmp_path):
    cfg = make_config(tmp_path)
    cfg.zotero_library_type = "group"
    cfg.zotero_library_id = "98765"
    rec = store.Record(record_id="0001", front_matter={"zotero_key": "K"}, body="")
    assert refcite.zotero_select_uri(rec, cfg) == "zotero://select/groups/98765/items/K"


# --- A22: progress callback fires even when the stderr bar is off --------

def test_progress_callback_fires_when_disabled():
    seen = []
    rep = progress.ProgressReporter("Work", total=3, enabled=False,
                                    on_progress=lambda l, c, t: seen.append((l, c, t)))
    rep.start()
    for i in range(1, 4):
        rep.update(i)
    rep.done()
    assert ("Work", 3, 3) in seen
    assert seen[0][0] == "Work"


def test_progress_callback_exception_is_swallowed():
    def boom(label, current, total):
        raise RuntimeError("ui blew up")
    rep = progress.ProgressReporter("Work", total=2, enabled=False, on_progress=boom)
    rep.start()
    rep.update(1)
    rep.done()  # must not raise


# --- A18: token obfuscation round-trip -----------------------------------

def test_token_obfuscation_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "APP_DIR", tmp_path)
    monkeypatch.setattr(app_settings, "APPKEY_PATH", tmp_path / ".appkey")
    token = "sk-this-is-a-fake-token-1234567890"
    stored = app_settings.obfuscate_token(token)
    assert "sk-" not in stored                       # token shape hidden
    assert app_settings.deobfuscate_token(stored) == token


def test_token_deobfuscate_bad_input_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "APP_DIR", tmp_path)
    monkeypatch.setattr(app_settings, "APPKEY_PATH", tmp_path / ".appkey")
    assert app_settings.deobfuscate_token("") == ""
    assert app_settings.deobfuscate_token("!!!not base64!!!") == ""


def test_settings_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "APP_DIR", tmp_path)
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "app.json")
    monkeypatch.setattr(app_settings, "APPKEY_PATH", tmp_path / ".appkey")
    s = app_settings.Settings(api_base_url="http://api", model="gpt", token="sk-secret",
                              search_backend="embeddings", embeddings_url="http://emb",
                              embeddings_model="emb-model")
    app_settings.save_settings(s)
    # The plaintext token never lands in app.json.
    raw = (tmp_path / "app.json").read_text(encoding="utf-8")
    assert "sk-secret" not in raw
    loaded = app_settings.load_settings()
    assert loaded.token == "sk-secret"
    assert loaded.search_backend == "embeddings"
    assert loaded.config_overrides() == {
        "backend": "embeddings", "emb_url": "http://emb", "emb_model": "emb-model"}


def test_settings_env_token_takes_precedence(monkeypatch):
    monkeypatch.setenv(app_settings.TOKEN_ENV_VAR, "sk-from-env")
    s = app_settings.Settings(token="sk-on-disk")
    assert s.resolved_token() == "sk-from-env"
    monkeypatch.delenv(app_settings.TOKEN_ENV_VAR, raising=False)
    assert s.resolved_token() == "sk-on-disk"


# --- provider presets (Settings dialog) ----------------------------------

def test_detect_provider_matches_known_urls():
    assert app_settings.detect_provider("https://api.openai.com/v1") == "OpenAI"
    # A trailing slash still matches.
    assert app_settings.detect_provider("http://127.0.0.1:1234/v1/") == "LM Studio (local)"
    assert app_settings.detect_provider("http://127.0.0.1:11434/v1") == "Ollama (local)"


def test_detect_provider_unknown_is_custom():
    assert app_settings.detect_provider("http://my-proxy.internal/v1") == "Custom"
    assert app_settings.detect_provider("") == "Custom"


def test_provider_presets_have_required_fields():
    for name, preset in app_settings.PROVIDER_PRESETS.items():
        assert {"api_base_url", "models", "needs_token", "help"} <= preset.keys()
        assert isinstance(preset["models"], list)
        assert preset["help"]
    # OpenAI requires a token; the local servers do not.
    assert app_settings.PROVIDER_PRESETS["OpenAI"]["needs_token"] is True
    assert app_settings.PROVIDER_PRESETS["LM Studio (local)"]["needs_token"] is False


# --- metrics log ----------------------------------------------------------

def test_metrics_log_appends_jsonl(tmp_path):
    log = metrics.MetricsLog("sess-1", path=tmp_path / "metrics.jsonl")
    log.session_started(model="gpt", backend="bm25", report="r.md")
    log.log("custom", value=42)
    log.session_finished(outcome="ok", inserted=3)
    lines = (tmp_path / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["event"] == "session_started"
    assert first["session"] == "sess-1"
    assert first["model"] == "gpt"
    assert json.loads(lines[2])["outcome"] == "ok"


def test_metrics_log_never_raises_on_bad_payload(tmp_path):
    log = metrics.MetricsLog("sess-2", path=tmp_path / "metrics.jsonl")
    # A non-serialisable payload is swallowed rather than raised (best-effort);
    # reaching the assert proves no exception escaped, and the bad line is not
    # written, so a later good line is still the first valid record.
    log.log("weird", obj=object())
    log.log("good", value=1)
    lines = (tmp_path / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "good"


# --- A36/A37: ephemeral paste scratch session ----------------------------

def test_create_scratch_document_writes_pasted_text(tmp_path):
    from citeapp import orchestrator
    scratch_root = tmp_path / "scratch"
    text = "Project X mapped 1,023 reefs across the shelf.\n"
    doc = orchestrator.create_scratch_document(text, scratch_root=scratch_root)
    # The working document holds the pasted prose verbatim ...
    assert doc.read_text(encoding="utf-8") == text
    assert doc.suffix == ".md"
    # ... inside an ephemeral subfolder of the scratch root (A37) that becomes
    # the project root (its parent folder, like a real report's folder, A10).
    assert doc.parent.parent == scratch_root
    assert doc.parent.is_dir()


def test_create_scratch_document_unique_folders(tmp_path):
    from citeapp import orchestrator
    scratch_root = tmp_path / "scratch"
    a = orchestrator.create_scratch_document("a", scratch_root=scratch_root)
    b = orchestrator.create_scratch_document("b", scratch_root=scratch_root)
    assert a.parent != b.parent           # each paste gets its own folder


# --- connection tests (Settings dialog "Test connection" buttons) ---------

import urllib.error  # noqa: E402

from citeapp import connection as app_connection  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_main_model_requires_base_url():
    ok, msg = app_connection.test_main_model("", "gpt-4o-mini", "")
    assert ok is False
    assert "base URL" in msg


def test_main_model_reports_available_model(monkeypatch):
    payload = {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}
    monkeypatch.setattr(app_connection.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    ok, msg = app_connection.test_main_model("http://x/v1", "gpt-4o-mini", "tok")
    assert ok is True
    assert "available" in msg


def test_main_model_warns_when_model_missing(monkeypatch):
    payload = {"data": [{"id": "gpt-4o"}]}
    monkeypatch.setattr(app_connection.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    ok, msg = app_connection.test_main_model("http://x/v1", "absent-model", "tok")
    assert ok is True               # reachable, but the model is not listed
    assert "not in the server's list" in msg


def test_main_model_maps_auth_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("http://x/v1/models", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(app_connection.urllib.request, "urlopen", boom)
    ok, msg = app_connection.test_main_model("http://x/v1", "m", "bad")
    assert ok is False
    assert "token" in msg


def test_main_model_maps_unreachable(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(app_connection.urllib.request, "urlopen", boom)
    ok, msg = app_connection.test_main_model("http://x/v1", "m", "")
    assert ok is False
    assert "could not reach" in msg


def test_embeddings_requires_url():
    ok, msg = app_connection.test_embeddings("", "m", "")
    assert ok is False


def test_embeddings_reports_dimensions(monkeypatch):
    payload = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    monkeypatch.setattr(app_connection.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    ok, msg = app_connection.test_embeddings("http://x", "emb", "")
    assert ok is True
    assert "3 dimensions" in msg


def test_embeddings_rejects_empty_response(monkeypatch):
    payload = {"data": []}
    monkeypatch.setattr(app_connection.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    ok, msg = app_connection.test_embeddings("http://x", "emb", "")
    assert ok is False
    assert "without an embedding" in msg


# --- Stage A2: LLM client (A4/A5) ----------------------------------------

import io  # noqa: E402

from citeapp import llm as app_llm  # noqa: E402
from citeapp import orchestrator as app_orch  # noqa: E402
from citeapp import tasks as app_tasks  # noqa: E402
from refcite import Claim  # noqa: E402


class _ChatResponse:
    def __init__(self, content: str):
        self._body = json.dumps(
            {"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _scripted_urlopen(script):
    """Return a fake urlopen that yields/raises successive ``script`` items."""
    it = iter(script)

    def fake(req, timeout=0):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return _ChatResponse(item)

    return fake


def _format_400():
    return urllib.error.HTTPError(
        "http://x/v1/chat/completions", 400, "Bad Request", {},
        io.BytesIO(b"'response_format.type' must be 'json_schema' or 'text'"))


def test_llm_returns_parsed_json(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen(['{"sentences": ["a"]}']))
    out = client.complete_json(system="s", user="u", schema={}, schema_name="t")
    assert out == {"sentences": ["a"]}


def test_llm_falls_back_when_format_rejected(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    # json_schema is rejected (400), json_object then succeeds.
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen([_format_400(), '{"ok": 1}']))
    assert client.complete_json(system="s", user="u", schema={},
                                schema_name="t") == {"ok": 1}


def test_llm_retries_once_on_bad_json(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen(["not json at all", '{"ok": 2}']))
    assert client.complete_json(system="s", user="u", schema={},
                                schema_name="t") == {"ok": 2}


def test_llm_strips_code_fences(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen(["```json\n{\"x\": 3}\n```"]))
    assert client.complete_json(system="s", user="u", schema={},
                                schema_name="t") == {"x": 3}


def test_llm_raises_after_exhausting_formats(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    # Three formats, two attempts each: six unparseable replies, then give up.
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen(["nope"] * 6))
    with pytest.raises(app_llm.LLMError):
        client.complete_json(system="s", user="u", schema={}, schema_name="t")


def test_llm_network_error_is_friendly(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen([urllib.error.URLError("refused")]))
    with pytest.raises(app_llm.LLMError) as exc:
        client.complete_json(system="s", user="u", schema={}, schema_name="t")
    assert "Could not reach the model API" in str(exc.value)


def test_llm_validate_rejects_wrong_shape(monkeypatch):
    client = app_llm.LLMClient("http://x/v1", "m", "tok")

    def validate(obj):
        if "sentences" not in obj:
            raise ValueError("missing sentences")

    # First reply is valid JSON but wrong shape; the retry supplies the field.
    monkeypatch.setattr(app_llm.urllib.request, "urlopen",
                        _scripted_urlopen(['{"wrong": 1}', '{"sentences": []}']))
    assert client.complete_json(system="s", user="u", schema={},
                                schema_name="t", validate=validate) == {"sentences": []}


# --- Stage A2: tasks (LLM calls 1 & 2) -----------------------------------

class _FakeClient:
    """A stand-in LLM client returning a canned payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


_DOC = ("Intro line.\n"
        "Reefs cover 1023 km2 of the shelf.\n"
        "The survey used sonar mapping.\n"
        "Our new method is faster.")


def test_propose_statements_drops_non_verbatim():
    selection = ("Reefs cover 1023 km2 of the shelf. "
                 "The survey used sonar mapping.")
    client = _FakeClient({"sentences": [
        "Reefs cover 1023 km2 of the shelf.",
        "The survey used sonar mapping.",
        "A fabricated sentence not in the text.",
    ]})
    result = app_tasks.propose_statements(client, selection)
    assert result.sentences == [
        "Reefs cover 1023 km2 of the shelf.",
        "The survey used sonar mapping.",
    ]
    assert result.non_verbatim == ["A fabricated sentence not in the text."]


def test_propose_statements_deduplicates():
    selection = "Reefs cover 1023 km2 of the shelf."
    client = _FakeClient({"sentences": [
        "Reefs cover 1023 km2 of the shelf.",
        "Reefs cover 1023 km2 of the shelf.",
    ]})
    result = app_tasks.propose_statements(client, selection)
    assert result.sentences == ["Reefs cover 1023 km2 of the shelf."]


def test_generate_claims_renumbers_in_document_order():
    sentences = [
        "The survey used sonar mapping.",       # line 3 in _DOC
        "Reefs cover 1023 km2 of the shelf.",   # line 2 in _DOC
    ]
    client = _FakeClient({"claims": [
        {"sentence": "The survey used sonar mapping.",
         "claim_type": "method", "search_query": "sonar reef mapping"},
        {"sentence": "Reefs cover 1023 km2 of the shelf.",
         "claim_type": "background", "search_query": "reef cover shelf area"},
    ]})
    claims = app_tasks.generate_claims(client, sentences, _DOC, "report.md")
    # Re-derived C-codes follow document order, not input order (A27).
    assert [c.code for c in claims] == ["C1", "C2"]
    assert claims[0].sentence == "Reefs cover 1023 km2 of the shelf."
    assert claims[0].line == 2
    assert claims[1].line == 3
    assert claims[1].claim_type == "method"
    assert claims[1].search_query == "sonar reef mapping"


def test_generate_claims_defaults_for_missing_metadata():
    sentences = ["Reefs cover 1023 km2 of the shelf."]
    client = _FakeClient({"claims": []})   # model returned nothing usable
    claims = app_tasks.generate_claims(client, sentences, _DOC, "report.md")
    assert len(claims) == 1
    assert claims[0].claim_type == "background"
    # search_query falls back to the sentence itself.
    assert claims[0].search_query == "Reefs cover 1023 km2 of the shelf."


def test_generate_claims_rejects_invalid_type():
    sentences = ["Reefs cover 1023 km2 of the shelf."]
    client = _FakeClient({"claims": [
        {"sentence": "Reefs cover 1023 km2 of the shelf.",
         "claim_type": "nonsense", "search_query": "q"},
    ]})
    claims = app_tasks.generate_claims(client, sentences, _DOC, "report.md")
    assert claims[0].claim_type == "background"


def test_claims_to_dict_shape():
    claim = Claim(claim_id="c1", code="C1", line=2, sentence="S",
                  claim_type="method", search_query="q")
    data = app_tasks.claims_to_dict("sess", "r.md", [claim])
    assert data["session"] == "sess"
    assert data["report_path"] == "r.md"
    assert data["claims"][0]["code"] == "C1"
    assert data["claims"][0]["claim_type"] == "method"


# --- Stage A3: candidate evaluation (LLM call 3) -------------------------

def test_evaluate_review_keys_by_claim_code():
    client = _FakeClient({"recommendations": [
        {"claim": "C1", "keep": ["C1a"], "why": "Full-text states the figure."},
        {"claim": "C2", "keep": [], "why": "Only weak overlap; leave uncited."},
    ]})
    recs = app_tasks.evaluate_review(client, "## C1 ...\n## C2 ...")
    assert recs["C1"] == {"keep": ["C1a"], "why": "Full-text states the figure."}
    assert recs["C2"]["keep"] == []
    # The whole review text is passed to the model in one call (A15).
    assert "C1" in client.calls[0]["user"]


def test_evaluate_review_skips_blank_claim_codes():
    client = _FakeClient({"recommendations": [
        {"claim": "", "keep": ["X"], "why": "ignored"},
        {"claim": "C3", "keep": ["C3a", " "], "why": " keep one "},
    ]})
    recs = app_tasks.evaluate_review(client, "review")
    assert "" not in recs
    # Blank codes inside keep are dropped and rationale is trimmed.
    assert recs["C3"] == {"keep": ["C3a"], "why": "keep one"}


# --- Stage A2: orchestrator ----------------------------------------------

def test_build_session_writes_claims_and_runs_search(tmp_path):
    cfg = make_config(tmp_path)
    claim = Claim(claim_id="c1", code="C1", line=2, sentence="S",
                  search_query="q")
    captured = {}

    def fake_run_search(cfg, session_dir, *, verbose=True, on_progress=None):
        captured["session_dir"] = session_dir
        return {"review": str(session_dir / "review.md"),
                "claims": 1, "searched": 1, "candidates": []}

    out = app_orch.build_session(cfg, [claim], tmp_path / "report.md",
                                 run_search_fn=fake_run_search)
    session_dir = out["session_dir"]
    claims_file = session_dir / "work" / "claims.json"
    assert claims_file.is_file()
    data = json.loads(claims_file.read_text(encoding="utf-8"))
    assert data["claims"][0]["code"] == "C1"
    assert captured["session_dir"] == session_dir
    assert out["summary"]["searched"] == 1

