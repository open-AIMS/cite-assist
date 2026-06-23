"""score.py — collect a finished run and score its citations against gold.

Reads a sandbox's ``.current-run.json`` (written by make_run.py), copies the
edited report and reference list into ``results/<run_id>/``, scores the inserted
in-text citations against the gold pools, writes ``score.json``, and upserts a
row in ``results/results.csv`` (computed columns only; manual columns such as
``copilot_credits`` are preserved).

Usage::

    python harness/score.py sandboxes/agent-cli
    python harness/score.py sandboxes/agent-cli --run-id cli_opus48_t1   # override

Scoring rubric (per gold sentence)
----------------------------------
Citable sentence (has a gold pool):
  * inserted a GOLD entry                         -> good      (+1.0)
  * inserted only an OK entry, pool has a gold     -> partial   (+0.5)
  * inserted only an OK entry, pool is OK-only      -> ok        (+1.0)  (best available)
  * inserted a citation not in the pool             -> wrong     (-1.0)
  * inserted nothing                                -> miss      ( 0.0)
  * a pool match PLUS an extra non-pool citation    -> spurious  (-0.5 each, added)
Bare sentence (no gold pool):
  * inserted any citation                           -> false_positive (-1.0 each)
  * inserted nothing                                -> ok_bare   ( 0.0)
A gold sentence whose prose cannot be matched in the edited report counts as an
alignment failure (the run is flagged ``alignment_ok = False``; rewording is a
workflow failure per the study design).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cite_eval import parse_gold

HARNESS = Path(__file__).resolve().parent
ROOT = HARNESS.parent

COMPUTED_COLS = [
    "run_id", "datetime", "impl", "model", "text",
    "alignment_ok", "citations_inserted",
    "n_citable", "n_good", "n_ok", "n_partial", "n_wrong", "n_miss",
    "n_false_pos", "n_spurious", "n_align_fail", "score",
]
MANUAL_COLS = ["completed_no_steering", "copilot_credits", "tokens", "seconds", "notes"]
COLUMNS = COMPUTED_COLS + MANUAL_COLS

_SUBS = [("\u2018", "'"), ("\u2019", "'"), ("\u201a", "'"), ("\u201c", '"'),
         ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"), ("\u2026", "...")]


def _norm_align(text: str) -> str:
    """Normalise sentence prose so insertion/quote/dash differences don't block a match."""
    t = text.lower()
    for a, b in _SUBS:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def score_run(gold_text: str, edited_text: str) -> dict:
    gold = parse_gold(gold_text)
    edited = parse_gold(edited_text)

    edited_map: dict[str, object] = {}
    for s in edited:
        edited_map.setdefault(_norm_align(s.text), s)

    counts = {k: 0 for k in ("n_citable", "n_good", "n_ok", "n_partial", "n_wrong",
                             "n_miss", "n_false_pos", "n_spurious", "n_align_fail")}
    score = 0.0
    details = []

    for g in gold:
        e = edited_map.get(_norm_align(g.text))
        if e is None:
            counts["n_align_fail"] += 1
            details.append({"index": g.index, "outcome": "align_fail", "text": g.text})
            continue
        inserted = {pe.key for pe in e.pool}
        if g.citable:
            counts["n_citable"] += 1
            good = {pe.key for pe in g.pool if pe.grade == "good"}
            ok = {pe.key for pe in g.pool if pe.grade == "ok"}
            extra = inserted - good - ok
            if inserted & good:
                counts["n_good"] += 1; score += 1.0; outcome = "good"
            elif inserted & ok:
                if good:
                    counts["n_partial"] += 1; score += 0.5; outcome = "partial"
                else:
                    counts["n_ok"] += 1; score += 1.0; outcome = "ok"
            elif inserted:
                counts["n_wrong"] += 1; score -= 1.0; outcome = "wrong"
            else:
                counts["n_miss"] += 1; outcome = "miss"
            if extra and outcome in ("good", "ok", "partial"):
                counts["n_spurious"] += len(extra)
                score -= 0.5 * len(extra)
                outcome += "+spurious"
        else:
            if inserted:
                counts["n_false_pos"] += len(inserted)
                score -= 1.0 * len(inserted)
                outcome = "false_positive"
            else:
                outcome = "ok_bare"
        details.append({
            "index": g.index, "outcome": outcome,
            "gold": [pe.raw for pe in g.pool],
            "inserted": sorted(inserted),
        })

    return {
        "score": round(score, 2),
        "alignment_ok": counts["n_align_fail"] == 0,
        "citations_inserted": any(s.citable for s in edited),
        **counts,
        "details": details,
    }


def _upsert_csv(csv_path: Path, row: dict) -> None:
    rows: list[dict] = []
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    existing = next((r for r in rows if r.get("run_id") == row["run_id"]), None)
    if existing is None:
        full = {c: "" for c in COLUMNS}
        full.update(row)
        rows.append(full)
    else:
        existing.update(row)  # computed cols only; manual cols stay as edited
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in COLUMNS})


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect and score a finished run.")
    ap.add_argument("sandbox", help="sandbox directory, e.g. sandboxes/agent-cli")
    ap.add_argument("--run-id", help="override the run id from the manifest")
    args = ap.parse_args()

    config = json.loads((HARNESS / "config.json").read_text(encoding="utf-8"))
    sandbox = (ROOT / args.sandbox) if not Path(args.sandbox).is_absolute() else Path(args.sandbox)
    manifest_path = sandbox / ".current-run.json"
    if not manifest_path.exists():
        sys.exit(f"error: no .current-run.json in {sandbox} (run make_run.py first)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = args.run_id or manifest["run_id"]

    gold_path = ROOT / manifest["gold_path"]
    report_path = sandbox / manifest["report_file"]
    refs_path = sandbox / manifest["references_file"]
    if not report_path.exists():
        sys.exit(f"error: edited report not found: {report_path}")

    result = score_run(gold_path.read_text(encoding="utf-8"),
                       report_path.read_text(encoding="utf-8"))

    # collect artifacts
    out_dir = ROOT / config["results_dir"] / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    if refs_path.exists():
        (out_dir / "references.md").write_text(refs_path.read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "score.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    row = {
        "run_id": run_id,
        "datetime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "impl": manifest["impl"], "model": manifest["model"], "text": manifest["text"],
        "alignment_ok": result["alignment_ok"], "citations_inserted": result["citations_inserted"],
        "n_citable": result["n_citable"], "n_good": result["n_good"], "n_ok": result["n_ok"],
        "n_partial": result["n_partial"], "n_wrong": result["n_wrong"], "n_miss": result["n_miss"],
        "n_false_pos": result["n_false_pos"], "n_spurious": result["n_spurious"],
        "n_align_fail": result["n_align_fail"], "score": result["score"],
    }
    _upsert_csv(ROOT / config["results_dir"] / "results.csv", row)

    print(f"scored {run_id}: score={result['score']}  "
          f"good={result['n_good']} ok={result['n_ok']} partial={result['n_partial']} "
          f"wrong={result['n_wrong']} miss={result['n_miss']} "
          f"false_pos={result['n_false_pos']} spurious={result['n_spurious']}")
    if not result["alignment_ok"]:
        print(f"  WARNING: {result['n_align_fail']} gold sentence(s) could not be matched "
              f"(prose reworded?) — run flagged alignment_ok=False")
    print(f"  artifacts: {out_dir.relative_to(ROOT).as_posix()}/")
    print(f"  remember to fill manual columns in results/results.csv "
          f"(completed_no_steering, copilot_credits, tokens, seconds)")


if __name__ == "__main__":
    main()
