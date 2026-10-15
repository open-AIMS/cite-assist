"""Small, read-only tools for live Zotero research. Standard library only."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


API = "http://127.0.0.1:23119/api/users/0"
MARKER = ".cite-assist-session.json"


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def bibliography(record):
    authors = [creator.get("name") or creator.get("lastName", "")
               for creator in record.get("creators", []) if creator.get("creatorType") == "author"]
    author = "; ".join(authors[:2]) + (" et al." if len(authors) > 2 else "")
    return (f"{record['key']} | {author or '[author missing]'} | {record.get('date') or 'n.d.'} | "
            f"{record.get('item_type', '')}\n{record.get('title', '')}\n"
            f"DOI: {record.get('doi') or '[none]'}")


def passage_block(passage):
    return (f"Passage [{passage['start']}:{passage['end']}] "
            f"terms={json.dumps(passage.get('matched_terms', []), ensure_ascii=True)}\n{passage['text']}")


def source_blocks(record, preview=False, provenance=None):
    blocks = [("items", bibliography(record))]
    if provenance:
        blocks.append(("provenance", "Matched probes: " + json.dumps(provenance, ensure_ascii=True)))
    if "evidence_tier" in record:
        blocks.append(("details", "Evidence retrieved: " + record["evidence_tier"] + " (not a support judgement)"))
    abstract = record.get("abstract", "")
    if abstract:
        shortened = preview and len(abstract) > 400
        text = abstract[:400] if shortened else abstract
        label = "Abstract preview" if shortened else "Abstract"
        blocks.append(("abstracts", f"{label}:\n{text}\n"
                       f"abstract_truncated={str(record.get('abstract_truncated', False) or shortened).lower()}; "
                       f"complete_abstract_chars={record.get('abstract_chars', len(abstract))}"))
    for attachment in record.get("attachments", []):
        blocks.append(("attachments", f"PDF {attachment['key']}: {attachment['status']}; "
                       f"chars={attachment.get('chars', 0)}; "
                       f"selection_limited={str(attachment.get('selection_limited', False)).lower()}; "
                       f"hits={attachment.get('total_hits', 0)}\n"
                       f"Text: {attachment.get('text_path', '[unavailable]')}"))
        for passage in attachment.get("passages", []):
            blocks.append(("passages", passage_block(passage)))
    return blocks


def output_blocks(value, preview=False):
    blocks = []
    for error in value.get("errors", []):
        blocks.append(("errors", "ERROR: " + json.dumps(error, ensure_ascii=True)))
    if "error" in value:
        blocks.append(("errors", "ERROR: " + value["error"]))
    provenance = {}
    for claim in value.get("claims", []):
        for probe in claim["probes"]:
            blocks.append(("probes", f"Probe {claim['claim_id']}: " + json.dumps(
                {field: detail for field, detail in probe.items() if field != "keys"}, ensure_ascii=True)))
            for key in probe.get("keys", []):
                provenance.setdefault(key, []).append({"claim_id": claim["claim_id"], "query": probe["query"]})
    for record in value.get("items", []):
        blocks.extend(source_blocks(record, preview, provenance.get(record["key"])))
    for passage in value.get("passages", []):
        blocks.append(("passages", passage_block(passage)))
    if "text" in value:
        blocks.append(("passages", f"Window [{value['start']}:{value['end']}] "
                       f"has_more={str(value['has_more']).lower()}\n{value['text']}"))
    if "selection_limited" in value:
        blocks.append(("details", f"selection_limited={str(value['selection_limited']).lower()}; "
                       f"total_hits={value['total_hits']}; candidate_passages={value['candidate_passages']}"))
    return blocks


def save_result(value, work, command):
    folder = work / "results"
    folder.mkdir(exist_ok=True)
    sequence = 1
    while True:
        if any(folder.glob(f"{sequence:04d}-*")):
            sequence += 1
            continue
        target = folder / f"{sequence:04d}-{command}"
        try:
            target.mkdir()
            break
        except FileExistsError:
            sequence += 1
    value["result_path"] = str(target / "result.json")
    value["readable_path"] = str(target / "result.txt")
    value["source_paths"] = {}
    for record in value.get("items", []):
        key = valid_key(record["key"])
        path = target / f"{key}.txt"
        value["source_paths"][key] = str(path)
        text = "\n\n".join(block for category, block in source_blocks(record))
        if "data" in record:
            text += "\n\nFull bibliographic metadata:\n" + json.dumps(record["data"], ensure_ascii=True, indent=2)
        path.write_text(text + "\n", encoding="utf-8")
    (target / "result.txt").write_text(
        json.dumps({key: detail for key, detail in value.items() if key not in {"items", "claims", "errors"}},
                   ensure_ascii=True, indent=2) + "\n\n" +
        "\n\n".join(block for category, block in output_blocks(value)) + "\n", encoding="utf-8")
    (target / "result.json").write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def agent_output(value, budget):
    header_fields = {key: value[key] for key in (
        "work_dir", "status", "ready", "api", "total_results", "library_version", "deleted",
        "api_calls", "reused_responses", "result_path", "readable_path") if key in value}
    header_fields["error_count"] = len(value.get("errors", [])) + int("error" in value)
    header = json.dumps(header_fields, ensure_ascii=True, indent=2) + "\n"
    blocks = output_blocks(value, preview=True)
    counts = {category: sum(kind == category for kind, text in blocks) for category in dict(blocks)}
    previews = sum(len(record.get("abstract", "")) > 400 for record in value.get("items", []))

    def footer(omitted):
        return "\n" + json.dumps({
            "output_limited": any(omitted.values()) or previews > 0,
            "omitted": omitted, "abstract_previews": previews,
            "output_budget_bytes": budget,
            "saved_details": "Read result.json/result.txt or sibling KEY.txt files; do not rerun retrieval."
                             if "result_path" in value else "No session result files for this command."
        }, ensure_ascii=True) + "\n"

    footer_bytes = max(len(footer(counts).encode("utf-8")),
                       len(footer({category: 0 for category in counts}).encode("utf-8")))
    remaining = budget - len(header.encode("utf-8")) - footer_bytes
    rendered = []
    omitted = counts.copy()
    exhausted = False
    for category, text in blocks:
        block = "\n" + text.encode("ascii", errors="backslashreplace").decode("ascii") + "\n"
        size = len(block.encode("utf-8"))
        if not exhausted and size <= remaining:
            rendered.append(block)
            remaining -= size
            omitted[category] -= 1
        else:
            exhausted = True
    result = header + "".join(rendered) + footer(omitted)
    if len(result.encode("utf-8")) > budget:
        raise ValueError("Output budget is too small for result paths and omission counts; increase --output-budget.")
    return result


def emit(value, args, work=None):
    if work is not None:
        save_result(value, work, args.command)
    if args.format == "agent":
        print(agent_output(value, args.output_budget), end="")
    else:
        print(json.dumps(value, ensure_ascii=True, indent=2))


def remove_readonly(function, path, exc_info):
    if not isinstance(exc_info[1], PermissionError):
        raise exc_info[1]
    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    function(path)


def session(path):
    root = Path(path).resolve()
    marker = root / MARKER
    if not marker.is_file():
        raise ValueError("Not a CLI-created working folder; run init first.")
    record = json.loads(marker.read_text(encoding="utf-8"))
    if record.get("work_dir") != str(root) or record.get("tool") != "agent-python-cli":
        raise ValueError("Working-folder marker does not match this folder.")
    return root


class Client:
    def __init__(self, root=None, refresh=False):
        self.root = root
        self.refresh = refresh
        self.memory = {}
        self.calls = 0
        self.reused = 0

    def remember(self, route, result):
        url = f"{API}/{route}"
        self.memory[url] = result
        if self.root:
            folder = self.root / "responses"
            folder.mkdir(exist_ok=True)
            (folder / (hashlib.sha256(url.encode()).hexdigest() + ".json")).write_text(
                json.dumps(result, ensure_ascii=True), encoding="utf-8")

    def get(self, route, **params):
        url = f"{API}/{route}"
        if params:
            url += "?" + urlencode(sorted(params.items()))
        cache = None
        if self.root:
            cache = self.root / "responses" / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if url in self.memory:
            self.reused += 1
            return self.memory[url]
        if cache and cache.exists() and not self.refresh:
            self.reused += 1
            result = json.loads(cache.read_text(encoding="utf-8"))
        else:
            self.calls += 1
            try:
                with urlopen(url, timeout=15) as response:
                    result = {"body": json.load(response), "headers": dict(response.headers)}
            except HTTPError as error:
                if error.code == 404 and route.endswith("/fulltext"):
                    result = {"body": {"content": ""}, "headers": {}, "not_indexed": True}
                elif error.code == 403:
                    message = "Enable Zotero Settings > Advanced > Allow other applications on this computer to communicate with Zotero."
                    raise ApiError(message, error.code) from error
                else:
                    message = f"Zotero HTTP {error.code}: {route}"
                    raise ApiError(message, error.code) from error
            except (URLError, TimeoutError) as error:
                raise ApiError("Cannot reach Zotero at 127.0.0.1:23119. Start Zotero and check its local API setting.") from error
            if cache:
                cache.parent.mkdir(exist_ok=True)
                cache.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
        self.memory[url] = result
        return result

    def children(self, key):
        children = []
        start = 0
        while True:
            result = self.get(f"items/{key}/children", limit=100, start=start)
            page = result["body"]
            children.extend(page)
            start += len(page)
            total = header(result, "Total-Results")
            if not page or (total is not None and start >= int(total)) or (total is None and len(page) < 100):
                return children


def header(result, name, default=None):
    return next((value for key, value in result["headers"].items() if key.lower() == name.lower()), default)


def valid_key(key):
    if not re.fullmatch(r"[A-Z0-9]{8}", key):
        raise ValueError(f"Invalid Zotero item key: {key}")
    return key


def passages(text, terms, count=5, window=900):
    terms = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    if not terms:
        raise ValueError("Supply at least one non-empty passage term.")
    hits = []
    for term in terms:
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in term.split()) + r"(?!\w)"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            hits.append((match.start(), match.end(), term))
    hits.sort()
    candidates = []
    for start, end, term in hits:
        left = max(0, start - window // 2)
        right = min(len(text), max(end, left + window))
        if candidates and left <= candidates[-1]["end"] and right - candidates[-1]["start"] <= window * 2:
            candidate = candidates[-1]
            candidate["end"] = max(candidate["end"], right)
            candidate["terms"].add(term)
            candidate["hits"] += 1
        else:
            candidates.append({"start": left, "end": right, "terms": {term}, "hits": 1})
    candidates.sort(key=lambda candidate: (-len(candidate["terms"]), -candidate["hits"], candidate["start"]))
    selected = []
    for candidate in candidates:
        if any(candidate["start"] < previous["end"] and previous["start"] < candidate["end"] for previous in selected):
            continue
        selected.append(candidate)
        if len(selected) == count:
            break
    return {
        "terms": terms,
        "matched_terms": sorted({hit[2] for hit in hits}),
        "total_hits": len(hits),
        "candidate_passages": len(candidates),
        "selection_limited": len(selected) < len(candidates),
        "passages": [dict(start=candidate["start"], end=candidate["end"],
                          matched_terms=sorted(candidate["terms"]), hits=candidate["hits"],
                          text=text[candidate["start"]:candidate["end"]]) for candidate in selected],
    }


def summary(item, abstract_chars):
    data = item["data"]
    abstract = data.get("abstractNote", "")
    return {
        "key": item["key"], "item_type": data.get("itemType"),
        "title": data.get("title", ""), "creators": data.get("creators", []),
        "date": data.get("date", ""), "doi": data.get("DOI", ""),
        "abstract": abstract[:abstract_chars], "abstract_chars": len(abstract),
        "abstract_truncated": len(abstract) > abstract_chars,
    }


def read_requests(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("Requests file must be a non-empty JSON array.")
    return value


def search_requests(args):
    requests = read_requests(args.requests) if args.requests else [{"claim_id": "search", "queries": args.query or []}]
    for request in requests:
        if not isinstance(request, dict) or not isinstance(request.get("claim_id"), str):
            raise ValueError("Each search request needs a string claim_id.")
        queries = request.get("queries")
        if not isinstance(queries, list) or not queries or any(not isinstance(query, str) or not query.strip() for query in queries):
            raise ValueError("Each search request needs a non-empty queries array of strings.")
    return requests


def search(client, args, requests=None):
    if requests is None:
        requests = search_requests(args)
    output = []
    records = {}
    errors = []
    for request in requests:
        probes = []
        keys = []
        for query in dict.fromkeys(request["queries"]):
            try:
                result = client.get("items/top", q=query, qmode=args.mode, limit=args.limit, start=args.start)
                items = [item for item in result["body"] if item["data"].get("itemType") not in {"note", "attachment"}]
                raw_count = len(result["body"])
                total = int(header(result, "Total-Results", raw_count))
                more = args.start + raw_count < total
                probe_keys = [item["key"] for item in items]
                probes.append({"query": query, "keys": probe_keys, "total_results": total,
                               "start": args.start, "returned_raw": raw_count, "has_more": more,
                               "next_start": args.start + raw_count if more else None})
                keys.extend(probe_keys)
                for item in items:
                    client.remember(f"items/{item['key']}", {"body": item, "headers": result["headers"]})
                    records[item["key"]] = summary(item, args.abstract_chars)
            except ApiError as error:
                failure = {"claim_id": request["claim_id"], "query": query, "error": str(error)}
                probes.append(failure)
                errors.append(failure)
        output.append({"claim_id": request["claim_id"], "probes": probes, "keys": list(dict.fromkeys(keys))})
    return {"claims": output, "items": list(records.values()), "errors": errors}


def fetch(client, args):
    requests = read_requests(args.requests) if args.requests else [{"key": key, "terms": args.terms or []} for key in dict.fromkeys(args.keys)]
    if not requests:
        raise ValueError("Supply item keys or --requests.")
    for request in requests:
        if not isinstance(request, dict) or not isinstance(request.get("key"), str):
            raise ValueError("Each fetch request needs a string key.")
        valid_key(request["key"])
        terms = request.get("terms", [])
        if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError("terms must be an array of non-empty strings.")
    output = []
    errors = []
    for request in requests:
        key = request["key"]
        try:
            item = client.get(f"items/{key}")["body"]
            if item["data"].get("itemType") in {"note", "attachment"}:
                raise ValueError(f"{key} is not a citable parent item.")
            record = summary(item, args.abstract_chars)
            record["data"] = {field: value for field, value in item["data"].items() if field != "abstractNote"}
            record["attachments"] = []
            record["evidence_tier"] = "abstract" if record["abstract_chars"] else "metadata"
            if request.get("terms") or args.fulltext:
                children = client.children(key)
                for child in children:
                    if child["data"].get("contentType") != "application/pdf":
                        continue
                    attachment_key = valid_key(child["key"])
                    attachment = {"key": attachment_key}
                    try:
                        result = client.get(f"items/{attachment_key}/fulltext")
                        text = result["body"].get("content", "")
                        attachment.update(status="not_indexed" if result.get("not_indexed") else "available" if text else "empty", chars=len(text))
                        if text:
                            record["evidence_tier"] = "fulltext"
                            target = client.root / "texts" / f"{attachment_key}.txt"
                            target.parent.mkdir(exist_ok=True)
                            target.write_text(text, encoding="utf-8")
                            attachment["text_path"] = str(target)
                            if request.get("terms"):
                                attachment.update(passages(text, request["terms"], args.count, args.window))
                    except ApiError as error:
                        attachment.update(status="not_indexed" if error.status == 404 else "error", error=str(error))
                        if error.status != 404:
                            errors.append({"key": key, "attachment_key": attachment_key, "error": str(error)})
                    record["attachments"].append(attachment)
            output.append(record)
        except ApiError as error:
            errors.append({"key": key, "error": str(error)})
    return {"items": output, "errors": errors}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive.")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Must be non-negative.")
    return number


def output_budget(value):
    number = positive(value)
    if number < 2048:
        raise argparse.ArgumentTypeError("Output budget must be at least 2048 bytes.")
    return number


def api_status(client):
    result = client.get("items/top", limit=1)
    return {"ready": True, "api": API, "total_results": int(header(result, "Total-Results", len(result["body"]))),
            "library_version": header(result, "Last-Modified-Version")}


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--work-dir", help="Folder returned by init (required except for init/status).")
    root.add_argument("--refresh", action="store_true", help="Refetch responses instead of reusing session files.")
    root.add_argument("--format", choices=["json", "agent"], default="json", help="JSON or bounded readable agent output.")
    root.add_argument("--output-budget", type=output_budget, default=12000, help="Total agent output bytes (default 12000; minimum 2048).")
    commands = root.add_subparsers(dest="command", required=True)
    initialise = commands.add_parser("init", help="Create a temporary folder, optionally checking Zotero and searching.")
    initialise.add_argument("--check", action="store_true", help="Check the live API before creating the folder; implied by a search.")
    commands.add_parser("status", help="Check live API reachability and personal-library size.")
    lookup = commands.add_parser("search", help="Batch independent keyword probes; deduplicate candidates.")
    for command in (initialise, lookup):
        source = command.add_mutually_exclusive_group(required=command is lookup)
        source.add_argument("--query", action="append")
        source.add_argument("--requests", help="JSON array: [{claim_id, queries: [string]}].")
        command.add_argument("--mode", choices=["everything", "titleCreatorYear"], default="everything")
        command.add_argument("--limit", type=positive, default=20)
        command.add_argument("--start", type=nonnegative, default=0)
        command.add_argument("--abstract-chars", type=positive, default=2000)
    retrieve = commands.add_parser("fetch", help="Batch parent metadata and optional PDF passage retrieval.")
    retrieve.add_argument("keys", nargs="*")
    retrieve.add_argument("--requests", help="JSON array: [{key, terms: [string]}].")
    retrieve.add_argument("--terms", nargs="+", help="Words or quoted phrases, matched independently.")
    retrieve.add_argument("--fulltext", action="store_true", help="Save complete PDF text without printing it.")
    retrieve.add_argument("--count", type=positive, default=5)
    retrieve.add_argument("--window", type=positive, default=900)
    retrieve.add_argument("--abstract-chars", type=positive, default=4000)
    extract = commands.add_parser("passages", help="Search previously retrieved PDF text without API calls.")
    extract.add_argument("attachment_key", type=valid_key)
    selection = extract.add_mutually_exclusive_group(required=True)
    selection.add_argument("--terms", nargs="+")
    selection.add_argument("--start", type=nonnegative, help="Read an exact character window instead of term-selected passages.")
    extract.add_argument("--count", type=positive, default=5)
    extract.add_argument("--window", type=positive, default=900)
    commands.add_parser("cleanup", help="Delete only a validated CLI-created working folder.")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    work = None
    try:
        if args.command == "init":
            requests = search_requests(args) if args.query or args.requests else None
            client = Client(refresh=args.refresh)
            status = api_status(client) if args.check or requests is not None else None
            work = Path(tempfile.mkdtemp(prefix=".cite-assist-work-", dir=Path.cwd())).resolve()
            (work / MARKER).write_text(json.dumps({"tool": "agent-python-cli", "work_dir": str(work)}), encoding="utf-8")
            output = {"work_dir": str(work)}
            if status is not None:
                output["status"] = status
                client.root = work
                if requests is not None:
                    output.update(search(client, args, requests))
                output.update(api_calls=client.calls, reused_responses=client.reused)
            emit(output, args, work)
            return 1 if output.get("errors") else 0
        if args.command not in {"status"} and not args.work_dir:
            raise ValueError("--work-dir is required; use the folder returned by init.")
        work = session(args.work_dir) if args.work_dir else None
        if args.command == "cleanup":
            shutil.rmtree(work, onerror=remove_readonly)
            emit({"deleted": str(work)}, args)
            return 0
        if args.command == "passages":
            text = (work / "texts" / f"{args.attachment_key}.txt").read_text(encoding="utf-8")
            if args.start is not None:
                end = min(len(text), args.start + args.window)
                emit({"chars": len(text), "start": args.start, "end": end, "text": text[args.start:end], "has_more": end < len(text)}, args, work)
            else:
                emit(passages(text, args.terms, args.count, args.window), args, work)
            return 0
        client = Client(work, args.refresh)
        if args.command == "status":
            output = api_status(client)
        elif args.command == "search":
            output = search(client, args)
        else:
            if args.requests and (args.keys or args.terms):
                raise ValueError("Use --requests or positional keys/--terms, not both.")
            output = fetch(client, args)
        if args.command != "status":
            output.update(api_calls=client.calls, reused_responses=client.reused)
        emit(output, args, work)
        return 1 if output.get("errors") else 0
    except (ApiError, ValueError, OSError, KeyError, TypeError) as error:
        error_work = work if work is not None and (work / MARKER).is_file() and args.command != "cleanup" else None
        try:
            emit({"error": str(error)}, args, error_work)
        except (OSError, ValueError):
            emit({"error": str(error)}, args)
        return 1


if __name__ == "__main__":
    sys.exit(main())