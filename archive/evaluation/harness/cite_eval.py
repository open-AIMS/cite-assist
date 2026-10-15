"""cite_eval: parse inline-tagged gold citation texts for the cite-assist test harness.

Gold tagging format (v1)
------------------------
- A trailing APA parenthetical on a sentence is that sentence's citation POOL,
  e.g. ``... biodiversity hotspots (Roberts et al., 2002; Hoey et al., 2020*).``
- Pool entries are separated by ``;``.
    * a plain entry  -> GOLD  (the ideal citation the author actually used)
    * a ``*`` suffix -> OK    (an acceptable / marginal alternative)
- A sentence with no trailing citation expects NO citation (a "bare" sentence);
  any citation an agent inserts there is a spurious / false-positive.
- Only the APA-looking parentheticals (those containing ", <year>") are treated
  as citations; prose parentheticals such as "(MSL)" are left untouched.

This module does two jobs used across the harness:
  1. ``parse_gold(text)``     -> the structured gold (sentences + pools).
  2. ``strip_to_input(text)`` -> the hint-free input text handed to an agent.

Scoring lives in score.py and consumes ``parse_gold``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A year token: 2002, 2020c, or n.d.
_YEAR = r"(?:\d{4}[a-z]?|n\.d\.)"
# A citation parenthetical: balanced single-level parens that contain ", <year>".
_CITE_PAREN = re.compile(r"\(([^()]*,\s*" + _YEAR + r"[^()]*)\)")
# The same, with any leading whitespace, used when stripping for input text.
_CITE_PAREN_WS = re.compile(r"\s*\(([^()]*,\s*" + _YEAR + r"[^()]*)\)")
_PLACEHOLDER = "\x00{}\x00"


@dataclass
class PoolEntry:
    raw: str        # exact text of the entry, e.g. "Hoey et al., 2020*"
    author: str     # author part, e.g. "Hoey et al."
    year: str       # year token, e.g. "2020"
    grade: str      # "good" | "ok"
    key: str        # normalised match key, e.g. "hoey|2020"


@dataclass
class Sentence:
    index: int          # 0-based position among all sentences
    text: str           # sentence with citations removed
    citable: bool       # True if it carries a gold pool
    pool: list = field(default_factory=list)


def _norm_key(author: str, year: str) -> str:
    """Normalise an author+year into a match key robust to et al./&/and variants."""
    sig = author.lower()
    sig = sig.replace("et al", "").replace("&", " ").replace(" and ", " ")
    sig = re.sub(r"[^a-z0-9]+", "", sig)
    return f"{sig}|{year.lower().replace('.', '')}"


def parse_entry(raw: str) -> PoolEntry:
    raw = raw.strip()
    grade = "good"
    body = raw
    if body.endswith("*"):
        grade = "ok"
        body = body[:-1].strip()
    m = re.search(r"^(.*?),\s*(" + _YEAR + r")\s*$", body)
    if m:
        author, year = m.group(1).strip(), m.group(2)
    else:  # fallback: trailing year, rest is author
        ym = re.search(r"(" + _YEAR + r")\s*$", body)
        year = ym.group(1) if ym else ""
        author = body[: ym.start()].rstrip(" ,") if ym else body
    return PoolEntry(raw=raw, author=author, year=year, grade=grade,
                     key=_norm_key(author, year))


def parse_pool(block: str) -> list[PoolEntry]:
    return [parse_entry(e) for e in block.split(";") if e.strip()]


def _body_text(text: str) -> str:
    """Drop the leading markdown heading lines; keep the prose body."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


def strip_to_input(text: str) -> str:
    """Return the agent-facing input: gold text with all citation parentheticals removed."""
    out = _CITE_PAREN_WS.sub("", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def parse_gold(text: str) -> list[Sentence]:
    """Parse tagged gold text into sentences with their citation pools."""
    body = _body_text(text)

    # Mask citations so sentence-splitting can't trip over "et al." / "n.d.".
    pools: list[list[PoolEntry]] = []

    def _mask(m: re.Match) -> str:
        pools.append(parse_pool(m.group(1)))
        return _PLACEHOLDER.format(len(pools) - 1)

    masked = _CITE_PAREN.sub(_mask, body)

    sentences: list[Sentence] = []
    idx = 0
    for raw_sentence in re.split(r"(?<=[.!?])\s+", masked):
        s = raw_sentence.strip()
        if not s:
            continue
        ids = [int(i) for i in re.findall(r"\x00(\d+)\x00", s)]
        pool: list[PoolEntry] = []
        for i in ids:
            pool.extend(pools[i])
        clean = re.sub(r"\s*\x00\d+\x00", "", s)
        clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
        sentences.append(Sentence(index=idx, text=clean, citable=bool(pool), pool=pool))
        idx += 1
    return sentences


def parse_file(path: str | Path) -> list[Sentence]:
    return parse_gold(Path(path).read_text(encoding="utf-8"))


def _demo(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    sentences = parse_gold(text)
    citable = [s for s in sentences if s.citable]
    n_cites = sum(len(s.pool) for s in citable)

    print(f"=== {path} ===")
    print(f"sentences: {len(sentences)} | citable: {len(citable)} | citations: {n_cites}\n")

    print("--- stripped input (what the agent sees) ---")
    print(strip_to_input(text).strip(), "\n")

    print("--- gold pools ---")
    for s in citable:
        tags = ", ".join(f"{e.key}={e.grade}" for e in s.pool)
        print(f"[{s.index}] {s.text}")
        print(f"      pool: {tags}")

    print("\n--- gold JSON ---")
    print(json.dumps([asdict(s) for s in citable], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _demo(sys.argv[1] if len(sys.argv) > 1 else "datasets/t1-reef-mapping.md")
