"""Spike 01 — multi-line / wrapped sentence matching (HIGHEST PRIORITY).

Risk: refcite's insert matcher (`_find_span` / `_apply_to_lines`) locates a
sentence only *within a single physical line*. A report paragraph that is
hard-wrapped across lines (common in Markdown), or a Tk selection that spans
lines, will fail BOTH the app-mode verbatim check (A33/A34) and `insert`.

This spike demonstrates the failure and validates a candidate fix (matching the
whitespace-joined paragraph). If the per-line match fails, the app must either
constrain claims to a single source line or refcite must gain a multi-line
matcher.

Run from the repo root:
    python spikes/01_multiline_match.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "agent-cli"))

from refcite.insert import _find_span


def main() -> int:
    wrapped = ("Project X mapped 1,023 reefs using the\n"
               "foobar classification across the shelf.")
    sentence = ("Project X mapped 1,023 reefs using the foobar classification "
                "across the shelf.")
    single_line = "Project X mapped 1,023 reefs using the foobar classification across the shelf."

    print("== Per-line match (how insert sees a hard-wrapped file) ==")
    per_line_hit = False
    for ln in wrapped.split("\n"):
        span = _find_span(ln, sentence)
        print(f"  {ln!r:60} -> {span}")
        per_line_hit = per_line_hit or span is not None

    print("\n== Same sentence on a single line ==")
    span_single = _find_span(single_line, sentence)
    print(f"  -> {span_single}")

    print("\n== Candidate fix: match the whitespace-joined paragraph ==")
    joined = " ".join(wrapped.split("\n"))
    span_joined = _find_span(joined, sentence)
    print(f"  joined={joined!r}")
    print(f"  -> {span_joined}")

    print("\n== VERDICT ==")
    if not per_line_hit:
        print("CONFIRMED RISK: a wrapped sentence does NOT match per-line.")
    else:
        print("Unexpected: a wrapped sentence matched per-line (re-check the matcher).")
    print("single-line match :", "OK" if span_single else "FAIL")
    print("joined-paragraph  :", "OK (viable fix)" if span_joined else "FAIL")

    # Exit non-zero only if the proposed fix itself does not work — that would
    # mean even joining lines is insufficient and needs deeper investigation.
    return 0 if span_joined and span_single else 1


if __name__ == "__main__":
    raise SystemExit(main())
