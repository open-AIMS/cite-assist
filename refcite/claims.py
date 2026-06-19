"""claims.py — the data contracts shared by ``search`` and ``insert`` (DESIGN.md §7).

Two artefacts:

- ``work/claims.json`` — the machine file the extraction agent writes: one entry
  per citable statement.
- ``review.md`` — the single human-editable file. The author keeps a candidate by
  leaving its ``###`` block and rejects it by deleting that block.

This module owns the JSON schema and the ``review.md`` rendering/parsing so the
format stays in one place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import apa
from .store import Record

VALID_CLAIM_TYPES = ("background", "method", "own-project", "common-knowledge")

# Marker embedded in each candidate subheading so insert can recover the record.
_RECORD_MARKER = "record:"


@dataclass
class Claim:
    claim_id: str
    code: str
    line: int
    sentence: str
    report_path: str = ""
    claim_type: str = "background"
    search_query: str = ""
    existing_citations: list[str] = field(default_factory=list)
    context_before: str = ""
    context_after: str = ""


@dataclass
class ClaimsDoc:
    session: str
    default_report_path: str
    claims: list[Claim]
    created: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Candidate:
    code: str           # e.g. "C3a"
    record_id: str
    in_text: str        # "(Smith, 2019)"
    confidence: str     # high | medium | low
    tier: str           # full | abstract | meta
    extracts: list      # one or more supporting extracts from the record
    lookup: str
    score: float
    placeholder: bool = False


# --- claims.json ---------------------------------------------------------

def load_claims(path: str | Path) -> ClaimsDoc:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    default_report = data.get("report_path", "")
    claims = []
    for i, raw in enumerate(data.get("claims", []), start=1):
        claims.append(Claim(
            claim_id=str(raw.get("claim_id", f"c{i}")),
            code=str(raw.get("code", f"C{i}")),
            line=int(raw.get("line", 0)),
            sentence=raw.get("sentence", ""),
            report_path=raw.get("report_path", "") or default_report,
            claim_type=raw.get("claim_type", "background"),
            search_query=raw.get("search_query", ""),
            existing_citations=list(raw.get("existing_citations", [])),
            context_before=raw.get("context_before", ""),
            context_after=raw.get("context_after", ""),
        ))
    return ClaimsDoc(
        session=data.get("session", ""),
        default_report_path=default_report,
        claims=claims,
        created=data.get("created", ""),
        raw=data,
    )


def claims_template(session: str, report_path: str, created: str) -> dict:
    """A minimal claims.json the extraction agent fills in."""
    return {
        "session": session,
        "report_path": report_path,
        "created": created,
        "claims": [
            {
                "claim_id": "c1",
                "code": "C1",
                "report_path": report_path,
                "line": 0,
                "sentence": "",
                "claim_type": "background",
                "search_query": "",
                "existing_citations": [],
                "context_before": "",
                "context_after": "",
            }
        ],
    }


def save_claims(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --- candidate construction ---------------------------------------------

def build_candidate(*, code_suffix: str, record: Record, score: float,
                    ratio: float, snippets: list, claim: Claim) -> Candidate:
    return Candidate(
        code=f"{claim.code}{code_suffix}",
        record_id=record.record_id,
        in_text=apa.in_text(record),
        confidence=_confidence(ratio, record.content_tier),
        tier=record.content_tier,
        extracts=[s for s in snippets if s],
        lookup=_lookup(record),
        score=score,
        placeholder=apa.is_placeholder(record),
    )


def _confidence(ratio: float, tier: str) -> str:
    if ratio >= 0.66:
        band = "high"
    elif ratio >= 0.33:
        band = "medium"
    else:
        band = "low"
    # Thin tiers are never dropped, but are flagged conservatively (DESIGN.md §6).
    if tier == "meta":
        return "low"
    if tier == "abstract" and band == "high":
        return "medium"
    return band


def _lookup(record: Record) -> str:
    doi = (record.front_matter.get("doi") or "").strip()
    url = (record.front_matter.get("url") or "").strip()
    first_author = record.authors[0] if record.authors else "Author unknown"
    title = record.title or "(untitled)"
    base = f"{first_author} — *{title}*"
    if doi:
        link = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        return f"{base} · {link}"
    if url:
        return f"{base} · {url}"
    return f"{base} · (no DOI; search Zotero)"


# --- review.md -----------------------------------------------------------

def write_review(path: str | Path, doc: ClaimsDoc, results: list, cfg) -> None:
    """Render the human review file from per-claim search results.

    ``results`` is a list of ``(Claim, status, [Candidate, ...])`` where status
    is ``"ok"``, ``"needs_source"``, or ``"skip"``.
    """
    path = Path(path)
    session_dir = path.parent
    try:
        session_rel = os.path.relpath(session_dir, cfg.repo_root).replace(os.sep, "/")
    except ValueError:
        session_rel = str(session_dir)
    try:
        records_rel = os.path.relpath(cfg.records_dir, cfg.repo_root).replace(os.sep, "/")
    except ValueError:
        records_rel = str(cfg.records_dir)

    n_ok = sum(1 for _, status, _ in results if status == "ok")
    n_skip = sum(1 for _, status, _ in results if status == "skip")
    n_needs = sum(1 for _, status, _ in results if status == "needs_source")

    lines: list[str] = []
    lines.append(f"# Citation review — {doc.session or session_dir.name}")
    lines.append("")
    lines.append("<!-- refcite review file. Keep candidates with `insert --select`, or delete the rest by hand. -->")
    lines.append("")
    lines.append("How to use this file:")
    lines.append("")
    lines.append("- Each claim lists candidate sources as `###` subheadings, each with a "
                 "short code (e.g. `C1a`).")
    lines.append("- Decide which candidate(s) to keep for each claim. To leave a claim "
                 "uncited, keep none of its candidates.")
    lines.append("- Each candidate's `Cache:` line points to the full cached record "
                 "(markdown) if you want to read more before deciding.")
    lines.append("- Do not edit the report between `search` and `insert`.")
    lines.append("- When done, run the write-back command from the repository root. "
                 "Either list the codes to keep with `--select` (recommended — no "
                 "block deleting needed):")
    lines.append("")
    lines.append("```bash")
    lines.append(f'PYTHONPATH=".github/agents/cite-assist" python -m refcite insert "{session_rel}" --select C1a,C2b')
    lines.append("```")
    lines.append("")
    lines.append("- ...or delete the `###` blocks you reject and run `insert` with no "
                 "`--select`:")
    lines.append("")
    lines.append("```bash")
    lines.append(f'PYTHONPATH=".github/agents/cite-assist" python -m refcite insert "{session_rel}"')
    lines.append("```")
    lines.append("")
    lines.append(f"Summary: {n_ok} with candidates, {n_needs} need a source, "
                 f"{n_skip} skipped as common knowledge.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for claim, status, candidates in results:
        lines.extend(_render_claim_block(claim, status, candidates, records_rel))
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _render_claim_block(claim: Claim, status: str, candidates: list,
                        records_rel: str) -> list[str]:
    out: list[str] = []
    tag = f"[{claim.claim_type}]"
    suffix = ""
    if status == "skip":
        suffix = " — skipped"
    elif status == "needs_source":
        suffix = " — NEEDS SOURCE"
    out.append(f"## {claim.code} — line {claim.line}   {tag}{suffix}")
    out.append(_quoted_context(claim))

    if claim.existing_citations:
        out.append(f"_Existing citation(s) on this sentence: "
                   f"{'; '.join(claim.existing_citations)} (kept, not duplicated)._")

    if status == "skip":
        out.append("")
        out.append("_Common knowledge; no source needed._")
        return out

    if not candidates:
        out.append("")
        out.append("_No candidate above the confidence floor. "
                   "Add a source manually or rephrase the statement._")
        return out

    for cand in candidates:
        out.append("")
        flag = " · ⚠ placeholder metadata" if cand.placeholder else ""
        out.append(f"### {cand.code} · {cand.in_text} · confidence: {cand.confidence} · "
                   f"tier: {cand.tier}{flag}   <!-- {_RECORD_MARKER}{cand.record_id} -->")
        if len(cand.extracts) == 1:
            out.append(f"Extract: \"{cand.extracts[0]}\"")
        else:
            for k, ex in enumerate(cand.extracts, 1):
                out.append(f"Extract {k}: \"{ex}\"")
        out.append(f"Lookup: {cand.lookup}")
        out.append(f"Cache: {records_rel}/{cand.record_id}.md")
    return out


def _quoted_context(claim: Claim) -> str:
    before = claim.context_before.strip()
    after = claim.context_after.strip()
    sentence = claim.sentence.strip()
    parts = []
    if before:
        parts.append(before)
    parts.append(f"**{sentence}**")
    if after:
        parts.append(after)
    return "> " + " ".join(parts)


# --- review.md parsing (used by insert) ----------------------------------

def parse_review_surviving(path: str | Path) -> dict[str, list[str]]:
    """Return ``{claim_code: [record_id, ...]}`` for candidates still present.

    A candidate survives if its ``### <code> ... <!-- record:NNNN -->`` heading
    is still in the file. Claim codes come from the ``## <code> — line`` headings.
    """
    text = Path(path).read_text(encoding="utf-8")
    surviving: dict[str, list[str]] = {}
    current_code: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_code = _claim_code_from_heading(stripped)
            if current_code is not None:
                surviving.setdefault(current_code, [])
        elif stripped.startswith("### ") and current_code is not None:
            record_id = _record_id_from_heading(stripped)
            if record_id:
                surviving[current_code].append(record_id)
    return surviving


def parse_review_candidates(path: str | Path) -> list[tuple[str, str, str]]:
    """Return ``[(candidate_code, claim_code, record_id), ...]`` in file order.

    Reads every ``### <candidate_code> ... <!-- record:NNNN -->`` heading and
    pairs it with the enclosing ``## <claim_code> — line`` heading. Used by the
    ``--select`` shortcut so the author can name candidate codes (``C1a``) to
    keep instead of deleting their blocks by hand.
    """
    text = Path(path).read_text(encoding="utf-8")
    out: list[tuple[str, str, str]] = []
    current_code: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_code = _claim_code_from_heading(stripped)
        elif stripped.startswith("### ") and current_code is not None:
            cand_code = _claim_code_from_heading(stripped)
            record_id = _record_id_from_heading(stripped)
            if cand_code and record_id:
                out.append((cand_code, current_code, record_id))
    return out


def prune_review_to(path: str | Path, keep_candidate_codes) -> None:
    """Rewrite ``review.md`` in place, dropping every ``###`` candidate block
    whose code is not in ``keep_candidate_codes``.

    A candidate block runs from its ``### `` heading to the next ``###``/``##``
    heading (or end of file). Claim headings, context and the file preamble are
    left untouched so the archived review still reads as a record of the
    decision.
    """
    keep = set(keep_candidate_codes)
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            skipping = _claim_code_from_heading(stripped) not in keep
            if skipping:
                continue
        elif stripped.startswith("## "):  # any non-candidate heading ends a block
            skipping = False
        if not skipping:
            out.append(line)
    Path(path).write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _claim_code_from_heading(heading: str) -> str | None:
    body = heading[3:].strip()
    token = body.split()[0] if body.split() else ""
    return token or None


def _record_id_from_heading(heading: str) -> str | None:
    marker = f"<!-- {_RECORD_MARKER}"
    idx = heading.find(marker)
    if idx == -1:
        return None
    rest = heading[idx + len(marker):]
    end = rest.find("-->")
    if end == -1:
        return None
    return rest[:end].strip()
