"""Spike 05 — run_search can surface structured candidates (A32).

Risk: the GUI's recommendations view needs structured candidate data (code,
record_id, APA in-text, confidence, tier, extracts, lookup). Today
`run_search` returns only a summary dict and renders the candidates into
`review.md` markdown. A32 proposes returning the per-claim `results` list it
already builds. This spike builds a tiny self-contained cache + session, runs
the real `run_search`, captures the `results` it passes to `write_review`, and
checks every field the GUI needs is present — proving the additive return is
clean (no markdown parsing required).

Run from the repo root (BM25 backend, no network):
    python spikes/05_run_search_candidates.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "agent-cli"))

from refcite import claims as claims_mod
from refcite import store
from refcite.config import load_config
from refcite.search import run_search


RECORDS = [
    {
        "fm": {
            "record_id": "0001",
            "zotero_key": "AAAA1111",
            "item_type": "journal-article",
            "authors": ["Smith, J. A."],
            "year": 2019,
            "title": "Mapping turbid-water coral reefs from satellite imagery",
            "container": "Remote Sensing of Environment",
            "doi": "10.1016/j.rse.2019.01.001",
            "collections": ["Reef mapping"],
            "content_tier": "full",
            "has_fulltext": True,
        },
        "body": ("We mapped 1,023 individual coral reef features across the "
                 "continental shelf using a supervised classification of "
                 "multispectral satellite imagery. Coral reef mapping at this "
                 "scale required careful handling of turbid water."),
    },
    {
        "fm": {
            "record_id": "0002",
            "zotero_key": "BBBB2222",
            "item_type": "journal-article",
            "authors": ["Jones, B."],
            "year": 2021,
            "title": "A classification scheme for shelf habitats",
            "container": "Marine Ecology",
            "doi": "",
            "url": "",
            "collections": ["Remote sensing"],
            "content_tier": "abstract",
            "has_fulltext": False,
        },
        "body": ("The foobar classification scheme separates reef habitats by "
                 "spectral signature. Coral reef mapping benefits from the "
                 "approach."),
    },
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="citeapp-search-"))
    cache = tmp / "cache"
    toml = tmp / "cite-assist.toml"
    toml.write_text(
        f'[cache]\ndir = "{cache.as_posix()}"\n'
        f'[search]\nbackend = "bm25"\n'
        f'[sessions]\ndir = "citations"\nreferences_file = "references.md"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path=toml, repo_root=tmp)

    for spec in RECORDS:
        store.write_record(cfg.records_dir, store.Record(
            record_id=spec["fm"]["record_id"], front_matter=spec["fm"], body=spec["body"]))

    session = cfg.active_dir / "2026-06-20_spike"
    (session / "work").mkdir(parents=True, exist_ok=True)
    claims_mod.save_claims(session / "work" / "claims.json", {
        "session": session.name,
        "report_path": "report.md",
        "created": "2026-06-20",
        "claims": [{
            "claim_id": "c1", "code": "C1", "line": 1,
            "sentence": "Project X mapped 1,023 coral reefs across the shelf.",
            "claim_type": "background",
            "search_query": "coral reef mapping satellite imagery shelf",
            "existing_citations": [], "context_before": "", "context_after": "",
        }],
    })

    # Capture the structured results run_search builds before rendering review.md.
    captured = {}
    original = claims_mod.write_review

    def spy(path, doc, results, cfg_):
        captured["results"] = results
        return original(path, doc, results, cfg_)

    claims_mod.write_review = spy
    try:
        summary = run_search(cfg, session, do_sync=False, verbose=False)
    finally:
        claims_mod.write_review = original

    print("run_search summary:", summary)

    results = captured.get("results", [])
    required = ("code", "record_id", "in_text", "confidence", "tier", "extracts", "lookup")
    seen_any = False
    all_ok = True
    for claim, status, cands in results:
        print(f"\n{claim.code} [{status}] {len(cands)} candidate(s)")
        for cand in cands:
            seen_any = True
            missing = [f for f in required if getattr(cand, f, None) in (None,)]
            line = (f"  {cand.code} rec={cand.record_id} {cand.in_text!r} "
                    f"conf={cand.confidence} tier={cand.tier} "
                    f"extracts={len(cand.extracts)}")
            print(line)
            if missing:
                all_ok = False
                print("    MISSING FIELDS:", missing)

    ok = seen_any and all_ok
    print("\nVERDICT:", "PASS — Candidate carries every field the GUI needs"
          if ok else "FAIL — no candidates or missing fields (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
