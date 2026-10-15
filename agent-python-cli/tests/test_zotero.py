import importlib.util
import io
import json
import re
from pathlib import Path
from urllib.error import HTTPError

import pytest


spec = importlib.util.spec_from_file_location("live_zotero", Path(__file__).parents[1] / "scripts" / "zotero.py")
zotero = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zotero)


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}


def item(key="ABCD1234", item_type="journalArticle"):
    return {"key": key, "data": {"itemType": item_type, "title": "Reef mapping", "abstractNote": "Evidence", "creators": []}}


def test_search_deduplicates_queries_and_preserves_claims(tmp_path, monkeypatch):
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        return Response([item(), item("NOTE1234", "note")], {"Total-Results": "5"})

    monkeypatch.setattr(zotero, "urlopen", open_url)
    requests = tmp_path / "requests.json"
    requests.write_text(json.dumps([{"claim_id": "C1", "queries": ["reef", "reef"]}, {"claim_id": "C2", "queries": ["reef"]}]))
    args = zotero.parser().parse_args(["search", "--requests", str(requests), "--limit", "2"])
    client = zotero.Client(tmp_path)
    output = zotero.search(client, args)
    assert len(calls) == 1
    assert len(output["items"]) == 1
    assert [claim["keys"] for claim in output["claims"]] == [["ABCD1234"], ["ABCD1234"]]
    assert output["claims"][0]["probes"][0]["next_start"] == 2
    zotero.search(zotero.Client(tmp_path), args)
    assert len(calls) == 1
    args = zotero.parser().parse_args(["fetch", "ABCD1234"])
    zotero.fetch(zotero.Client(tmp_path), args)
    assert len(calls) == 1


def test_passages_find_late_evidence_and_preserve_exact_offsets():
    text = "irrelevant introduction " * 3000 + "Coral mapping shows reef extent. " + "unrelated ending " * 200
    result = zotero.passages(text, ["coral", "reef extent"], count=1, window=150)
    assert result["total_hits"] == 2
    passage = result["passages"][0]
    assert passage["start"] > 50000
    assert passage["matched_terms"] == ["coral", "reef extent"]
    assert text[passage["start"]:passage["end"]] == passage["text"]


def test_passages_phrases_boundaries_and_no_match():
    result = zotero.passages("coraline coral\nreef", ["coral", "coral reef"], window=80)
    assert result["total_hits"] == 2
    assert zotero.passages("unrelated", ["reef"])["passages"] == []
    with pytest.raises(ValueError):
        zotero.passages("text", [" "])


def test_passages_prioritise_term_coverage_and_report_limits():
    text = "reef " + "x" * 1000 + " reef coral " + "x" * 1000 + " coral"
    result = zotero.passages(text, ["reef", "coral"], count=1, window=80)
    assert result["passages"][0]["matched_terms"] == ["coral", "reef"]
    assert result["selection_limited"]


def test_fetch_follows_children_and_reuses_fulltext(tmp_path, monkeypatch):
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        if "/children?" in url:
            return Response([{"key": "PDF12345", "data": {"contentType": "application/pdf"}},
                             {"key": "PDF67890", "data": {"contentType": "application/pdf"}}])
        if url.endswith("/PDF12345/fulltext"):
            return Response({"content": "Long document. Coral reef evidence."})
        if url.endswith("/PDF67890/fulltext"):
            raise HTTPError(url, 404, "Not indexed", {}, None)
        return Response(item())

    monkeypatch.setattr(zotero, "urlopen", open_url)
    args = zotero.parser().parse_args(["fetch", "ABCD1234", "ABCD1234", "--terms", "coral"])
    output = zotero.fetch(zotero.Client(tmp_path), args)
    record = output["items"][0]
    assert record["evidence_tier"] == "fulltext"
    assert record["attachments"][1]["status"] == "not_indexed"
    assert output["errors"] == []
    assert (tmp_path / "texts" / "PDF12345.txt").read_text() == "Long document. Coral reef evidence."
    zotero.fetch(zotero.Client(tmp_path), args)
    assert len(calls) == 4


def test_metadata_fetch_does_not_read_attachments(tmp_path, monkeypatch):
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        return Response(item())

    monkeypatch.setattr(zotero, "urlopen", open_url)
    args = zotero.parser().parse_args(["fetch", "ABCD1234"])
    assert zotero.fetch(zotero.Client(tmp_path), args)["items"][0]["evidence_tier"] == "abstract"
    assert len(calls) == 1


def test_refresh_and_partial_search_error(tmp_path, monkeypatch):
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        if "q=bad" in url:
            raise HTTPError(url, 403, "Forbidden", {}, None)
        return Response([item()])

    monkeypatch.setattr(zotero, "urlopen", open_url)
    args = zotero.parser().parse_args(["search", "--query", "reef", "--query", "bad"])
    result = zotero.search(zotero.Client(tmp_path), args)
    assert len(result["items"]) == 1
    assert "Enable Zotero" in result["errors"][0]["error"]
    zotero.search(zotero.Client(tmp_path, refresh=True), args)
    assert len(calls) == 4


def test_init_local_passages_and_safe_cleanup(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert zotero.main(["init"]) == 0
    work = Path(json.loads(capsys.readouterr().out)["work_dir"])
    assert work.parent == tmp_path
    (work / "texts").mkdir()
    (work / "texts" / "PDF12345.txt").write_text("coral reef evidence")
    assert zotero.main(["--work-dir", str(work), "passages", "PDF12345", "--start", "6", "--window", "4"]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "reef"
    assert zotero.main(["--work-dir", str(tmp_path), "cleanup"]) == 1
    capsys.readouterr()
    assert tmp_path.exists()
    assert zotero.main(["--work-dir", str(work), "cleanup"]) == 0
    assert not work.exists()


@pytest.mark.parametrize("check", [[], ["--check"]])
def test_init_search_checks_batches_and_reuses_metadata(tmp_path, monkeypatch, capsys, check):
    monkeypatch.chdir(tmp_path)
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        return Response([item()], {"Total-Results": "3", "Last-Modified-Version": "42"})

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["init", *check, "--query", "reef", "--query", "reef", "--query", "coral",
                        "--limit", "2", "--mode", "titleCreatorYear", "--abstract-chars", "3"]) == 0
    output = json.loads(capsys.readouterr().out)
    work = Path(output["work_dir"])
    assert work.parent == tmp_path
    assert zotero.session(work) == work
    assert output["status"]["library_version"] == "42"
    assert output["api_calls"] == len(calls) == 3
    assert len(output["items"]) == 1
    assert output["items"][0]["abstract"] == "Evi"
    assert output["claims"][0]["probes"][0]["next_start"] == 1
    assert "limit=1" in calls[0] and "q=" not in calls[0]
    assert "qmode=titleCreatorYear" in calls[1]
    assert zotero.main(["--work-dir", str(work), "fetch", "ABCD1234"]) == 0
    assert json.loads(capsys.readouterr().out)["api_calls"] == 0
    assert len(calls) == 3


def test_init_check_without_search(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(zotero, "urlopen", lambda url, timeout: Response([], {"Total-Results": "0"}))
    assert zotero.main(["init", "--check"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"]["ready"]
    assert output["api_calls"] == 1
    assert "items" not in output
    assert Path(output["work_dir"]).is_dir()


@pytest.mark.parametrize("arguments", [["--check"], ["--query", "reef"]])
def test_init_failed_check_creates_no_folder(tmp_path, monkeypatch, capsys, arguments):
    monkeypatch.chdir(tmp_path)

    def open_url(url, timeout):
        raise HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["init", *arguments]) == 1
    assert "Enable Zotero" in json.loads(capsys.readouterr().out)["error"]
    assert list(tmp_path.iterdir()) == []


def test_init_search_requests_preserves_partial_success(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    requests = tmp_path / "requests.json"
    requests.write_text(json.dumps([{"claim_id": "C1", "queries": ["reef", "bad"]}]))

    def open_url(url, timeout):
        if "q=bad" in url:
            raise HTTPError(url, 500, "Failed", {}, None)
        return Response([item()])

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["init", "--requests", str(requests)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["items"][0]["key"] == "ABCD1234"
    assert output["errors"][0]["claim_id"] == "C1"
    assert zotero.session(output["work_dir"]).is_dir()
    assert output["api_calls"] == 3


def test_init_invalid_search_creates_no_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def open_url(url, timeout):
        pytest.fail("Invalid requests must be rejected before checking Zotero.")

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["init", "--query", " "]) == 1
    assert "non-empty queries" in json.loads(capsys.readouterr().out)["error"]
    assert list(tmp_path.iterdir()) == []


def test_invalid_fetch_key_and_numeric_arguments(tmp_path):
    args = zotero.parser().parse_args(["fetch", "../danger"])
    with pytest.raises(ValueError):
        zotero.fetch(zotero.Client(tmp_path), args)
    with pytest.raises(SystemExit):
        zotero.parser().parse_args(["search", "--query", "reef", "--limit", "0"])


def test_attachment_pagination(tmp_path, monkeypatch):
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        if "start=0" in url:
            return Response([{"key": "NOTE1234"}] * 100, {"Total-Results": "101"})
        return Response([{"key": "PDF12345"}], {"Total-Results": "101"})

    monkeypatch.setattr(zotero, "urlopen", open_url)
    children = zotero.Client(tmp_path).children("ABCD1234")
    assert children[-1]["key"] == "PDF12345"
    assert len(calls) == 2


def test_cleanup_retries_readonly_paths_but_not_other_errors(tmp_path):
    target = tmp_path / "readonly.txt"
    target.write_text("temporary data")
    target.chmod(0o444)
    error = PermissionError("read only")
    zotero.remove_readonly(Path.unlink, target, (PermissionError, error, None))
    assert not target.exists()
    error = OSError("other failure")
    with pytest.raises(OSError, match="other failure"):
        zotero.remove_readonly(Path.unlink, target, (OSError, error, None))


def test_agent_budget_saves_complete_batch_and_exact_passages(tmp_path, capsys):
    args = zotero.parser().parse_args(["--format", "agent", "--output-budget", "2048", "fetch", "ABCD1234"])
    passage = {"start": 123, "end": 8123, "matched_terms": ["reef"], "text": "reef \u00e9\n" * 1000}
    record = zotero.summary(item(), 2000)
    record.update(data={"title": "Reef mapping", "publisher": "Test publisher"}, evidence_tier="fulltext",
                  attachments=[{"key": "PDF12345", "status": "available", "selection_limited": True,
                                "passages": [passage]}])
    value = {"items": [record], "errors": [{"key": "FAIL1234", "error": "HTTP 500"}]}
    zotero.emit(value, args, tmp_path)
    output = capsys.readouterr().out
    assert len(output.encode("utf-8")) <= 2048
    assert '"output_limited": true' in output
    assert '"passages": 1' in output
    assert "HTTP 500" in output
    saved = json.loads(Path(value["result_path"]).read_text(encoding="utf-8"))
    assert saved["items"][0]["attachments"][0]["passages"][0] == passage
    source = Path(saved["source_paths"]["ABCD1234"]).read_text(encoding="utf-8")
    assert passage["text"] in source
    assert "Passage [123:8123]" in source
    assert "Test publisher" in source
    assert Path(saved["readable_path"]).is_file()
    zotero.emit(value, args, tmp_path)
    assert "0002-fetch" in value["result_path"]
    assert (tmp_path / "results" / "0001-fetch" / "result.json").is_file()


def test_agent_search_shows_provenance_pagination_and_preview(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    candidate = item()
    candidate["data"]["abstractNote"] = "reef " * 1000
    monkeypatch.setattr(zotero, "urlopen", lambda url, timeout: Response([candidate], {"Total-Results": "25"}))
    assert zotero.main(["--format", "agent", "init", "--query", "reef"]) == 0
    output = capsys.readouterr().out
    assert len(output.encode("utf-8")) <= 12000
    assert "Matched probes:" in output
    assert '"has_more": true' in output and '"next_start": 1' in output
    assert "Abstract preview" in output and "abstract_truncated=true" in output
    assert '"output_limited": true' in output
    work = next(tmp_path.glob(".cite-assist-work-*"))
    saved = json.loads((work / "results" / "0001-init" / "result.json").read_text())
    assert len(saved["items"][0]["abstract"]) == 2000
    assert saved["items"][0]["abstract_truncated"]
    assert "0001-init" in saved["source_paths"]["ABCD1234"]


def test_saved_passages_reading_needs_no_api(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert zotero.main(["init"]) == 0
    work = Path(json.loads(capsys.readouterr().out)["work_dir"])
    (work / "texts").mkdir()
    (work / "texts" / "PDF12345.txt").write_text("coral reef evidence")

    def open_url(url, timeout):
        pytest.fail("Local passage reading must not contact Zotero.")

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["--format", "agent", "--work-dir", str(work), "passages", "PDF12345",
                        "--start", "6", "--window", "4"]) == 0
    output = capsys.readouterr().out
    assert "Window [6:10]" in output and "reef" in output
    saved = json.loads((work / "results" / "0002-passages" / "result.json").read_text())
    assert saved["text"] == "reef"
    assert zotero.main(["--format", "agent", "--work-dir", str(work), "cleanup"]) == 0
    assert not work.exists()


def test_agent_small_result_and_error_without_session(capsys):
    args = zotero.parser().parse_args(["--format", "agent", "status"])
    zotero.emit({"ready": True, "total_results": 0}, args)
    assert '"output_limited": false' in capsys.readouterr().out
    zotero.emit({"error": "Start Zotero"}, args)
    output = capsys.readouterr().out
    assert "ERROR: Start Zotero" in output
    assert '"error_count": 1' in output
    with pytest.raises(SystemExit):
        zotero.parser().parse_args(["--output-budget", "100", "status"])


@pytest.mark.parametrize("budget", [2048, 8000, 12000])
def test_agent_large_batch_budget_and_saved_metadata(tmp_path, monkeypatch, capsys, budget):
    monkeypatch.chdir(tmp_path)
    assert zotero.main(["init"]) == 0
    work = Path(json.loads(capsys.readouterr().out)["work_dir"])
    keys = [f"A{index:07d}" for index in range(10)]
    text = "reef glint \u00e9 " * 3000

    def open_url(url, timeout):
        if "/children?" in url:
            return Response([{"key": "PDF12345", "data": {"contentType": "application/pdf"}},
                             {"key": "PDF67890", "data": {"contentType": "application/pdf"}}])
        if "/fulltext" in url:
            return Response({"content": text})
        if "FAIL1234" in url:
            raise HTTPError(url, 500, "Failed", {}, None)
        key = url.split("/")[-1]
        parent = item(key)
        parent["data"].update(publisher="Only in saved metadata", abstractNote="abstract " * 1000)
        return Response(parent)

    monkeypatch.setattr(zotero, "urlopen", open_url)
    assert zotero.main(["--format", "agent", "--output-budget", str(budget), "--work-dir", str(work),
                        "fetch", *keys, "FAIL1234", "--terms", "reef", "glint"]) == 1
    output = capsys.readouterr().out
    assert len(output.encode("utf-8")) <= budget
    assert "HTTP 500" in output and '"error_count": 1' in output
    assert '"output_limited": true' in output
    assert "Only in saved metadata" not in output
    saved = json.loads((work / "results" / "0002-fetch" / "result.json").read_text())
    assert len(saved["items"]) == len(keys)
    assert saved["errors"][0]["key"] == "FAIL1234"
    for record in saved["items"]:
        assert record["data"]["publisher"] == "Only in saved metadata"
        assert len(record["attachments"]) == 2
        source = Path(saved["source_paths"][record["key"]]).read_text(encoding="utf-8")
        for attachment in record["attachments"]:
            for passage in attachment["passages"]:
                assert passage["text"] == text[passage["start"]:passage["end"]]
                assert passage["text"] in source
    monkeypatch.setattr(zotero, "urlopen", lambda url, timeout: pytest.fail("Result files must be read without API calls."))
    assert Path(saved["readable_path"]).read_text(encoding="utf-8")


def test_agent_oversized_errors_are_explicit_and_saved(tmp_path, capsys):
    args = zotero.parser().parse_args(["--format", "agent", "--output-budget", "2048", "search", "--query", "reef"])
    value = {"errors": [{"error": "Failure " * 2000}], "items": []}
    zotero.emit(value, args, tmp_path)
    output = capsys.readouterr().out
    assert len(output.encode("utf-8")) <= 2048
    assert '"error_count": 1' in output and '"errors": 1' in output
    assert '"output_limited": true' in output
    saved = json.loads(Path(value["result_path"]).read_text())
    assert saved["errors"] == value["errors"]


def test_json_compatibility_and_saved_session_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert zotero.main(["init"]) == 0
    initial = json.loads(capsys.readouterr().out)
    work = Path(initial["work_dir"])
    monkeypatch.setattr(zotero, "urlopen", lambda url, timeout: Response(item()))
    assert zotero.main(["--work-dir", str(work), "fetch", "ABCD1234"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["items"][0]["key"] == "ABCD1234"
    assert output["items"][0]["data"]["title"] == "Reef mapping"
    assert output["api_calls"] == 1 and output["errors"] == []
    assert json.loads(Path(output["result_path"]).read_text()) == output
    assert zotero.main(["--format", "agent", "--work-dir", str(work), "passages", "MISS1234",
                        "--terms", "reef"]) == 1
    error_output = capsys.readouterr().out
    assert '"error_count": 1' in error_output
    saved = json.loads((work / "results" / "0003-passages" / "result.json").read_text())
    assert "MISS1234" in saved["error"]


def test_agent_budget_boundary_never_overflows():
    value = {"items": [], "errors": [{"error": "Failure " * 250}]}
    complete = zotero.agent_output(value, 12000)
    minimum = len(complete.encode("utf-8"))
    for budget in range(minimum - 3, minimum + 4):
        rendered = zotero.agent_output(value, budget)
        assert len(rendered.encode("utf-8")) <= budget
        assert '"error_count": 1' in rendered


def test_skill_frontmatter_and_bundled_links():
    folder = Path(__file__).parents[1]
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    fields = dict(line.split(": ", 1) for line in frontmatter.strip().splitlines())
    assert fields["name"] == folder.name
    metadata = json.loads(fields["metadata"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", metadata["version"])
    description = json.loads(fields["description"])
    assert description and len(description) <= 1024
    for document in ["SKILL.md", "README.md"]:
        for target in re.findall(r"\]\((\./[^)]+)\)", (folder / document).read_text(encoding="utf-8")):
            assert (folder / target).is_file(), target