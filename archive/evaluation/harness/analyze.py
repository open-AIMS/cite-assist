"""analyze.py — summarise results.csv into the two study metrics.

Metric 1 (workflow completion): how many runs per (implementation, model) lane
completed without the operator steering the model back on track. Uses the manual
``completed_no_steering`` column; falls back to the ``citations_inserted`` /
``alignment_ok`` proxies where the manual flag is blank.

Metric 2 (citation quality): mean score and the good/ok/partial/wrong/miss /
false-positive breakdown over a lane's runs — but only reported for lanes whose
completed-run count meets ``min_samples_for_quality`` (config.json). Lanes below
the threshold are listed as "insufficient samples", so a lucky single perfect run
cannot masquerade as a high-quality lane.

Usage::

    python harness/analyze.py
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
ROOT = HARNESS.parent

_TRUE = {"1", "true", "yes", "y", "t"}
_FALSE = {"0", "false", "no", "n", "f"}


def _as_bool(value: str):
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _num(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    config = json.loads((HARNESS / "config.json").read_text(encoding="utf-8"))
    min_n = int(config.get("min_samples_for_quality", 3))
    csv_path = ROOT / config["results_dir"] / "results.csv"
    if not csv_path.exists():
        print(f"no results yet at {csv_path.relative_to(ROOT).as_posix()}")
        return

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("results.csv is empty")
        return

    lanes: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        lanes[(r.get("impl", ""), r.get("model", ""))].append(r)

    def completed(r: dict) -> bool:
        manual = _as_bool(r.get("completed_no_steering", ""))
        if manual is not None:
            return manual
        return _as_bool(r.get("citations_inserted", "")) is True and \
            _as_bool(r.get("alignment_ok", "")) is True

    print(f"=== Metric 1 — workflow completion (min_samples_for_quality = {min_n}) ===")
    print(f"{'implementation':<22}{'model':<22}{'runs':>5}{'completed':>11}{'cites_inserted':>16}")
    qualifying = []
    for (impl, model), rs in sorted(lanes.items()):
        n = len(rs)
        n_completed = sum(1 for r in rs if completed(r))
        n_cited = sum(1 for r in rs if _as_bool(r.get("citations_inserted", "")) is True)
        print(f"{impl:<22}{model:<22}{n:>5}{n_completed:>11}{n_cited:>16}")
        if n_completed >= min_n:
            qualifying.append((impl, model, [r for r in rs if completed(r)]))

    print(f"\n=== Metric 2 — citation quality (completed runs, lanes with >= {min_n}) ===")
    if not qualifying:
        print("no lane has enough completed runs yet for quality reporting.")
    else:
        print(f"{'implementation':<22}{'model':<22}{'n':>3}{'mean':>7}"
              f"{'good':>6}{'ok':>5}{'part':>6}{'wrong':>7}{'miss':>6}{'fp':>5}")
        for impl, model, rs in qualifying:
            n = len(rs)
            mean = sum(_num(r.get("score")) for r in rs) / n if n else 0.0
            agg = {k: sum(int(_num(r.get(k))) for r in rs)
                   for k in ("n_good", "n_ok", "n_partial", "n_wrong", "n_miss", "n_false_pos")}
            print(f"{impl:<22}{model:<22}{n:>3}{mean:>7.2f}"
                  f"{agg['n_good']:>6}{agg['n_ok']:>5}{agg['n_partial']:>6}"
                  f"{agg['n_wrong']:>7}{agg['n_miss']:>6}{agg['n_false_pos']:>5}")

    below = [(i, m, sum(1 for r in rs if completed(r)))
             for (i, m), rs in sorted(lanes.items())
             if sum(1 for r in rs if completed(r)) < min_n]
    if below:
        print(f"\ninsufficient samples for quality (completed < {min_n}):")
        for impl, model, c in below:
            print(f"  {impl} / {model}: {c} completed")


if __name__ == "__main__":
    main()
