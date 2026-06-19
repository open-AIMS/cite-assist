"""insert.py — write APA citations back into the report (DESIGN.md §7).

``insert`` reads which candidates still exist in ``review.md``, keys on the
report sentence + line hint, and inserts the APA in-text citation(s) at the end
of the matched sentence. It preserves existing citations, appends new APA
entries to the shared ``references.md`` (append-only, deduplicated), backs up
each touched report file, and moves the finished session to ``citations/done/``.

Matching is intentionally conservative: if the sentence cannot be located with
confidence, the report is left unchanged and the claim is reported as skipped.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import apa, claims as claims_mod, store
from .config import Config

_YEAR_RE = re.compile(r"\b\d{4}[a-z]?\b")


def insert_session(cfg: Config, session_dir: str | Path, *,
                   select: list[str] | None = None, verbose: bool = True) -> dict:
    session_dir = Path(session_dir)
    if not session_dir.is_absolute():
        session_dir = cfg.resolve(session_dir)
    claims_path = session_dir / "work" / "claims.json"
    review_path = session_dir / "review.md"
    if not claims_path.is_file():
        raise FileNotFoundError(f"no claims file at {claims_path}")
    if not review_path.is_file():
        raise FileNotFoundError(f"no review file at {review_path}")

    doc = claims_mod.load_claims(claims_path)
    if select is not None:
        surviving = _surviving_from_selection(review_path, select)
        # Prune review.md to match so the archived session records what was kept.
        claims_mod.prune_review_to(review_path, select)
    else:
        surviving = claims_mod.parse_review_surviving(review_path)
    rec_by_id = store.records_by_id(cfg.records_dir)

    by_file: dict[str, list[tuple[claims_mod.Claim, list[str]]]] = {}
    for claim in doc.claims:
        record_ids = [r for r in surviving.get(claim.code, []) if r in rec_by_id]
        if not record_ids:
            continue
        by_file.setdefault(claim.report_path, []).append((claim, record_ids))

    applied: list[str] = []
    skipped: list[tuple[str, str]] = []
    used_records: dict[str, store.Record] = {}
    work_dir = session_dir / "work"

    for report_path, items in by_file.items():
        if not report_path:
            for claim, _ in items:
                skipped.append((claim.code, "no report_path in claims.json"))
            continue
        abs_report = cfg.resolve(report_path)
        if not abs_report.is_file():
            for claim, _ in items:
                skipped.append((claim.code, f"report not found: {report_path}"))
            continue

        # Back up the pre-edit file (D9) — there is no true undo.
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_report, work_dir / abs_report.name)

        original = abs_report.read_text(encoding="utf-8")
        lines = original.split("\n")
        for claim, record_ids in items:
            labels = [apa.in_text_label(rec_by_id[r].authors, rec_by_id[r].year)
                      for r in record_ids]
            if _apply_to_lines(lines, claim, labels):
                applied.append(claim.code)
                for r in record_ids:
                    used_records[r] = rec_by_id[r]
            else:
                skipped.append((claim.code, "sentence not located in report"))
        abs_report.write_text("\n".join(lines), encoding="utf-8")

    ref_stats = _append_references(cfg, list(used_records.values()))
    dest = _move_to_done(cfg, session_dir)

    result = {
        "applied": applied,
        "skipped": skipped,
        "references_added": ref_stats["added"],
        "references_backfilled": ref_stats["backfilled"],
        "moved_to": str(dest),
    }
    if verbose:
        print(f"OK: inserted {len(applied)} claim(s); "
              f"{ref_stats['added']} reference(s) added; "
              f"session -> {dest}")
        for code, reason in skipped:
            print(f"  - skipped {code}: {reason}")
    return result


# --- sentence location + citation insertion ------------------------------

def _surviving_from_selection(review_path: Path, select: list[str]) -> dict[str, list[str]]:
    """Map selected candidate codes back to ``{claim_code: [record_id, ...]}``.

    Lets the author name candidate codes (``C1a``) instead of deleting blocks.
    Unknown codes are an error so a typo never silently drops a citation.
    """
    candidates = claims_mod.parse_review_candidates(review_path)
    by_code = {code: (claim_code, record_id)
               for code, claim_code, record_id in candidates}
    unknown = [c for c in select if c not in by_code]
    if unknown:
        valid = ", ".join(by_code) or "(none)"
        raise ValueError(
            f"unknown candidate code(s): {', '.join(unknown)}. "
            f"valid codes in review.md: {valid}")
    surviving: dict[str, list[str]] = {}
    for code in select:
        claim_code, record_id = by_code[code]
        surviving.setdefault(claim_code, []).append(record_id)
    return surviving


def _apply_to_lines(lines: list[str], claim: claims_mod.Claim, labels: list[str]) -> bool:
    n = len(lines)
    order = _search_order(claim.line - 1, n, radius=5)
    for i in order:
        span = _find_span(lines[i], claim.sentence)
        if span is None:
            continue
        start, end = span
        seg = lines[i][start:end]
        new_labels = _filter_new_labels(labels, seg, claim.existing_citations)
        if not new_labels:
            return True  # already cited — nothing to add, still a success
        lines[i] = lines[i][:start] + _apply_citation(seg, new_labels) + lines[i][end:]
        return True
    return False


def _search_order(hint: int, n: int, radius: int) -> list[int]:
    if n == 0:
        return []
    if hint < 0 or hint >= n:
        return list(range(n))
    order = [hint]
    for d in range(1, radius + 1):
        for cand in (hint - d, hint + d):
            if 0 <= cand < n:
                order.append(cand)
    seen = set(order)
    order.extend(i for i in range(n) if i not in seen)  # exact match is safe anywhere
    return order


def _match_at(line: str, start: int, sentence: str) -> int | None:
    """Return the end index in ``line`` if ``sentence`` matches at ``start``.

    Whitespace runs are treated as equivalent; all other characters must match
    exactly. Returns ``None`` if there is no match.
    """
    j, k = start, 0
    n, m = len(line), len(sentence)
    while k < m:
        if j >= n:
            return None
        lc, sc = line[j], sentence[k]
        if sc.isspace():
            if not lc.isspace():
                return None
            while j < n and line[j].isspace():
                j += 1
            while k < m and sentence[k].isspace():
                k += 1
            continue
        if lc.isspace() or lc != sc:
            return None
        j += 1
        k += 1
    return j


def _find_span(line: str, sentence: str) -> tuple[int, int] | None:
    sentence = sentence.strip()
    if not sentence:
        return None
    for i in range(len(line)):
        if line[i].isspace():
            continue
        end = _match_at(line, i, sentence)
        if end is not None:
            return (i, end)
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _filter_new_labels(labels: list[str], seg: str, existing_citations: list[str]) -> list[str]:
    seg_norm = _norm(seg)
    existing_norm = _norm(" ".join(existing_citations))
    out: list[str] = []
    for label in labels:
        ln = _norm(label)
        if ln and (ln in seg_norm or ln in existing_norm):
            continue
        if label in out:
            continue
        out.append(label)
    return out


def _apply_citation(seg: str, labels: list[str]) -> str:
    """Insert/merge an APA parenthetical at the end of the sentence ``seg`` (D15)."""
    addition = "; ".join(labels)

    # Merge into an existing trailing parenthetical citation if one is present.
    trailing = re.search(r"\(([^()]*)\)(\s*)([.!?]?[\"')\]]*)\s*$", seg)
    if trailing and _YEAR_RE.search(trailing.group(1)):
        inner = trailing.group(1).strip()
        merged = f"{inner}; {addition}"
        return seg[:trailing.start()] + f"({merged})" + trailing.group(2) + trailing.group(3)

    group = f"({addition})"
    end_punct = re.search(r"([.!?])([\"')\]]*)\s*$", seg)
    if end_punct:
        at = end_punct.start()
        return f"{seg[:at]} {group}{seg[at:]}"
    return f"{seg} {group}"


# --- shared references.md ------------------------------------------------

def _append_references(cfg: Config, records: list[store.Record]) -> dict:
    path = cfg.references_file
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        text = "# References\n\n"
    lines = text.rstrip("\n").split("\n")
    existing_markers = set(re.findall(r"<!-- refcite:([^\s>]+) -->", text))

    added = backfilled = 0
    for rec in records:
        rid = rec.record_id
        if rid in existing_markers:
            continue
        marker = f"<!-- refcite:{rid} -->"
        ntitle = _norm(rec.title)

        # Normalised-title fallback: backfill the marker onto a hand-entered line.
        hit_idx = None
        if ntitle:
            for idx, line in enumerate(lines):
                if "refcite:" in line:
                    continue
                if ntitle in _norm(line):
                    hit_idx = idx
                    break
        if hit_idx is not None:
            lines[hit_idx] = lines[hit_idx].rstrip() + f" {marker}"
            existing_markers.add(rid)
            backfilled += 1
            continue

        lines.append("")
        lines.append(f"{apa.reference(rec)} {marker}")
        existing_markers.add(rid)
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"added": added, "backfilled": backfilled}


def _move_to_done(cfg: Config, session_dir: Path) -> Path:
    cfg.done_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.done_dir / session_dir.name
    if dest.exists():
        dest = cfg.done_dir / f"{session_dir.name}_{int(time.time())}"
    shutil.move(str(session_dir), str(dest))
    return dest
