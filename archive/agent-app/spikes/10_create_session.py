"""Spike 10 — create_session collision suffix (A19).

Risk: A19 wants automatic session naming with an incrementing suffix on a name
collision (running the same report twice in a day). The current CLI
(`refcite new-session`) HARD-ERRORS on an existing folder, so this logic does
not exist yet and must be added to refcite as the declared `create_session`
helper (A31). This spike prototypes and validates the slug + collision-suffix
behaviour in isolation, before it is folded into refcite.

Run from the repo root:
    python spikes/10_create_session.py
"""

from __future__ import annotations

import datetime
import re
import tempfile
from pathlib import Path


def slugify(text: str, max_len: int = 30) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "session"


def create_session(active_dir: Path, slug: str, *, today: str | None = None) -> Path:
    """Scaffold active/<date>_<slug>[-N]/work and return the session folder.

    Appends -2, -3, ... until the name is unique, instead of erroring (A19).
    """
    date = today or datetime.date.today().isoformat()
    base = f"{date}_{slugify(slug)}"
    name, n = base, 1
    while (active_dir / name).exists():
        n += 1
        name = f"{base}-{n}"
    (active_dir / name / "work").mkdir(parents=True)
    return active_dir / name


def main() -> int:
    active = Path(tempfile.mkdtemp(prefix="citeapp-sessions-")) / "active"
    active.mkdir(parents=True)
    today = "2026-06-20"

    a = create_session(active, "intro reef mapping", today=today)
    b = create_session(active, "Intro Reef Mapping!", today=today)
    c = create_session(active, "intro reef mapping", today=today)

    print("1st:", a.name)
    print("2nd:", b.name)
    print("3rd:", c.name)

    expected = [
        f"{today}_intro-reef-mapping",
        f"{today}_intro-reef-mapping-2",
        f"{today}_intro-reef-mapping-3",
    ]
    got = [a.name, b.name, c.name]
    all_exist = all((active / n / "work").is_dir() for n in got)
    ok = got == expected and all_exist
    print("\nVERDICT:", "PASS — collisions get -2/-3 suffixes and work/ exists"
          if ok else f"FAIL — expected {expected}, got {got}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
