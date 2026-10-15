"""tasks.py — the scoped LLM tasks for app mode (DESIGN-app-mode.md §2, §6).

Stage A2 implements the first two of the three calls:

1. :func:`propose_statements` (LLM call 1) — given a block of prose, propose the
   sentences that should carry a citation, returned **verbatim**. Each proposed
   sentence is verified to appear verbatim in the selection using ``refcite``'s
   own sentence matcher (A20/A33/A34), so a sentence that passes here is one
   ``insert`` can later locate. Non-verbatim returns are dropped (and reported),
   not silently kept.

2. :func:`generate_claims` (LLM call 2) — given the author-confirmed sentences,
   classify each (``background | method | own-project | common-knowledge``) and
   write a concise conceptual ``search_query``. The result is reconciled back to
   the **confirmed** sentence strings (which are already verbatim), the line
   number and surrounding context are filled in from the document, and the
   positional ``C``-codes are re-derived in document order (A27).

The third call (candidate evaluation) lands in Stage A3:

3. :func:`evaluate_review` (LLM call 3) — given the whole generated ``review.md``
   text, recommend which candidate code(s) to keep per claim, ranking competing
   candidates against each other (A15/A33). Returns codes the GUI cross-checks
   against the real candidates it already holds (A32).

These functions take a ``client`` exposing ``complete_json(...)`` (see
:mod:`citeapp.llm`); they never touch the network or Tk directly, so they are
unit-testable with a fake client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from refcite import Claim, sentence_matches

# The claim taxonomy (DESIGN.md §7). Kept as a local constant so app mode does
# not reach past the declared integration surface (A31) for it.
VALID_CLAIM_TYPES = ("background", "method", "own-project", "common-knowledge")


# --- LLM call 1: statement proposal --------------------------------------

PROPOSE_SYSTEM = (
    "You help an author decide which sentences in a passage of a scientific "
    "report should carry a literature citation.\n"
    "Rules:\n"
    "- Copy each chosen sentence VERBATIM from the supplied text — same words, "
    "same punctuation, character for character. Do not paraphrase, trim, merge "
    "or fix anything.\n"
    "- Choose only sentences that make a factual claim a reader would expect to "
    "be supported by a reference (background facts, prior findings, methods "
    "attributed to others).\n"
    "- Do NOT choose the author's own new results, common knowledge, headings, "
    "captions, or list fragments.\n"
    "- Never invent text that is not present.\n"
    'Return JSON of the form {"sentences": ["...", "..."]} and nothing else.'
)

_STATEMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def _validate_statements(obj: dict) -> None:
    sents = obj.get("sentences")
    if not isinstance(sents, list) or not all(isinstance(s, str) for s in sents):
        raise ValueError("'sentences' must be a list of strings")


@dataclass
class ProposeResult:
    """The outcome of LLM call 1 after verbatim verification (A20)."""

    sentences: list[str]            # verbatim-verified, de-duplicated, in order
    non_verbatim: list[str]         # returned but not found verbatim (dropped)


def propose_statements(client, selection: str) -> ProposeResult:
    """LLM call 1: propose citable sentences from ``selection``, verbatim (A20)."""
    user = ("Here is the passage. Propose the sentences that should carry a "
            "citation, copied verbatim.\n\n---\n" + selection + "\n---")
    data = client.complete_json(
        system=PROPOSE_SYSTEM, user=user,
        schema=_STATEMENTS_SCHEMA, schema_name="statements",
        validate=_validate_statements)

    verbatim: list[str] = []
    non_verbatim: list[str] = []
    seen: set[str] = set()
    for raw in data.get("sentences", []):
        sent = (raw or "").strip()
        if not sent or sent in seen:
            continue
        seen.add(sent)
        if sentence_matches(selection, sent):
            verbatim.append(sent)
        else:
            non_verbatim.append(sent)
    return ProposeResult(sentences=verbatim, non_verbatim=non_verbatim)


# --- LLM call 2: claim generation ----------------------------------------

CLAIMS_SYSTEM = (
    "You classify citable sentences from a scientific report and write a search "
    "query for each, so a literature-search tool can find supporting sources.\n"
    "For every sentence supplied, return an object with:\n"
    "- 'sentence': the sentence copied back VERBATIM (unchanged),\n"
    "- 'claim_type': one of background | method | own-project | "
    "common-knowledge,\n"
    "- 'search_query': a concise set of conceptual search terms (not a copy of "
    "the sentence) capturing what must be supported,\n"
    "- 'existing_citations': any citation markers already present in the "
    "sentence, e.g. '(Smith, 2019)', as a list (empty if none).\n"
    "Classify honestly: 'own-project' for the authors' own new work, "
    "'common-knowledge' for textbook facts that need no citation.\n"
    'Return JSON of the form {"claims": [ ... ]} and nothing else.'
)

_CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "claim_type": {"type": "string", "enum": list(VALID_CLAIM_TYPES)},
                    "search_query": {"type": "string"},
                    "existing_citations": {
                        "type": "array", "items": {"type": "string"}},
                },
                "required": ["sentence", "claim_type", "search_query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def _validate_claims(obj: dict) -> None:
    claims = obj.get("claims")
    if not isinstance(claims, list):
        raise ValueError("'claims' must be a list")
    for c in claims:
        if not isinstance(c, dict) or not isinstance(c.get("sentence"), str):
            raise ValueError("each claim needs a string 'sentence'")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _find_line(document: str, sentence: str) -> int:
    """1-based line of ``sentence`` in ``document`` (0 if not found).

    Uses the shared whitespace-run-equivalent matcher (A33/A34) per line, so the
    line agrees with what ``insert`` would locate.
    """
    for i, line in enumerate(document.split("\n"), start=1):
        if sentence_matches(line, sentence):
            return i
    return 0


def _context(lines: list[str], line: int) -> tuple[str, str]:
    """Nearest non-empty neighbour lines, for human-readable context."""
    before = after = ""
    if 1 <= line <= len(lines):
        for j in range(line - 2, -1, -1):
            if lines[j].strip():
                before = lines[j].strip()
                break
        for j in range(line, len(lines)):
            if lines[j].strip():
                after = lines[j].strip()
                break
    return before, after


def generate_claims(client, sentences: list[str], document: str,
                    report_path: str = "") -> list[Claim]:
    """LLM call 2: classify the confirmed ``sentences`` and write per-claim data.

    Returns one :class:`refcite.Claim` per confirmed sentence (so the author's
    verbatim set is preserved exactly), classified and queried by the model,
    with line/context filled in and ``C``-codes re-derived in document order
    (A27). Sentences the model omits still get a claim with safe defaults.
    """
    confirmed = [s.strip() for s in sentences if s and s.strip()]
    if not confirmed:
        return []

    user_lines = ["Classify each of these sentences and write a search query.\n"]
    for i, sent in enumerate(confirmed, start=1):
        user_lines.append(f"{i}. {sent}")
    data = client.complete_json(
        system=CLAIMS_SYSTEM, user="\n".join(user_lines),
        schema=_CLAIMS_SCHEMA, schema_name="claims",
        validate=_validate_claims)

    # Index the model's metadata by the confirmed sentence it refers to.
    meta_by_sentence: dict[str, dict] = {}
    norm_to_confirmed = {_norm(s): s for s in confirmed}
    for raw in data.get("claims", []):
        if not isinstance(raw, dict):
            continue
        key = norm_to_confirmed.get(_norm(raw.get("sentence", "")))
        if key is not None:
            meta_by_sentence.setdefault(key, raw)

    lines = document.split("\n")
    built: list[tuple[int, int, Claim]] = []
    for order, sent in enumerate(confirmed):
        raw = meta_by_sentence.get(sent, {})
        claim_type = raw.get("claim_type")
        if claim_type not in VALID_CLAIM_TYPES:
            claim_type = "background"
        query = (raw.get("search_query") or "").strip() or sent
        existing = [c for c in raw.get("existing_citations", []) if isinstance(c, str)]
        line = _find_line(document, sent)
        before, after = _context(lines, line)
        claim = Claim(
            claim_id="", code="", line=line, sentence=sent,
            report_path=report_path, claim_type=claim_type, search_query=query,
            existing_citations=existing, context_before=before, context_after=after)
        # Sort key: located claims by line, unlocated (line 0) last in input order.
        sort_line = line if line > 0 else len(lines) + 1 + order
        built.append((sort_line, order, claim))

    built.sort(key=lambda t: (t[0], t[1]))
    ordered: list[Claim] = []
    for i, (_sl, _o, claim) in enumerate(built, start=1):
        claim.claim_id = f"c{i}"
        claim.code = f"C{i}"
        ordered.append(claim)
    return ordered


# --- LLM call 3: candidate evaluation -------------------------------------

EVALUATE_SYSTEM = (
    "You are a meticulous scientific librarian choosing which candidate sources "
    "best support each claim in a report.\n"
    "You are given a review file. Each claim (C1, C2, ...) shows its sentence "
    "and a list of candidate sources. Each candidate has a code (e.g. C3a), an "
    "in-text citation, a confidence and tier, and supporting extracts.\n"
    "Rules:\n"
    "- Recommend only candidates whose extract genuinely supports the claim.\n"
    "- Rank competing candidates against each other; usually keep the single "
    "strongest, occasionally two, never pad.\n"
    "- If nothing convincingly supports a claim, keep none (leave it uncited).\n"
    "- Use ONLY candidate codes that appear in the review; never invent codes.\n"
    "- Give a one-line rationale per claim.\n"
    'Return JSON of the form {"recommendations": [ {"claim": "C1", '
    '"keep": ["C1a"], "why": "..."} ]} and nothing else.'
)

_EVALUATE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "keep": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
                "required": ["claim", "keep", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


def _validate_recommendations(obj: dict) -> None:
    recs = obj.get("recommendations")
    if not isinstance(recs, list):
        raise ValueError("'recommendations' must be a list")
    for r in recs:
        if not isinstance(r, dict) or not isinstance(r.get("claim"), str):
            raise ValueError("each recommendation needs a string 'claim'")
        if not isinstance(r.get("keep", []), list):
            raise ValueError("'keep' must be a list of candidate codes")


def evaluate_review(client, review_md: str) -> dict[str, dict]:
    """LLM call 3: recommend candidate codes to keep per claim (A33).

    Returns ``{claim_code: {"keep": [codes], "why": str}}`` keyed by the claim
    code (e.g. ``"C3"``). The GUI cross-references the kept codes against the
    real candidate codes it already holds (A32), so any code the model invents
    simply matches nothing and is ignored. The whole ``review.md`` is sent in one
    call because choosing between candidates is a relative judgement (A15).
    """
    user = ("Here is the review file. For each claim, recommend the candidate "
            "code(s) to keep.\n\n---\n" + review_md + "\n---")
    data = client.complete_json(
        system=EVALUATE_SYSTEM, user=user,
        schema=_EVALUATE_SCHEMA, schema_name="recommendations",
        validate=_validate_recommendations)

    recs: dict[str, dict] = {}
    for raw in data.get("recommendations", []):
        if not isinstance(raw, dict):
            continue
        claim = (raw.get("claim") or "").strip()
        if not claim:
            continue
        keep = [str(c).strip() for c in raw.get("keep", []) if str(c).strip()]
        why = (raw.get("why") or "").strip()
        recs[claim] = {"keep": keep, "why": why}
    return recs


# --- claims.json assembly -------------------------------------------------

def claims_to_dict(session: str, report_path: str, claims: list[Claim],
                   created: str = "") -> dict:
    """Build the ``work/claims.json`` payload from generated claims."""
    return {
        "session": session,
        "report_path": report_path,
        "created": created,
        "claims": [
            {
                "claim_id": c.claim_id,
                "code": c.code,
                "report_path": c.report_path or report_path,
                "line": c.line,
                "sentence": c.sentence,
                "claim_type": c.claim_type,
                "search_query": c.search_query,
                "existing_citations": list(c.existing_citations),
                "context_before": c.context_before,
                "context_after": c.context_after,
            }
            for c in claims
        ],
    }
