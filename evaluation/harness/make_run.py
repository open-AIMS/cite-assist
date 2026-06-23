"""make_run.py — set up a sandbox for one evaluation run.

A sandbox is a per-implementation workspace under ``sandboxes/<impl>/`` that is
opened as its own VS Code workspace (agent modes) or pointed at by the app (app
mode). It contains ONLY what the implementation should see for the run: the
stripped (hint-free) report, an empty reference list, the implementation itself
(agent modes), and a ``RUN.md`` that is just the minimal user-side script (the
prompts a real user would type, no workflow hints). The gold texts, scorer and
other runs' results are never copied in.

Usage::

    python harness/make_run.py --impl agent-cli --text t1 --model opus48
    python harness/make_run.py --impl agent-app --text t2 --model gemma-4-e4b \\
        --run-id app_gemmaE4B_t2 --ranking "hybrid (embeddinggemma-300m-qat)"

Re-running rotates the report/reference files in place and clears any prior
citation sessions (the implementation copy and any Zotero cache are kept unless
``--refresh`` is given). Collect and score a run afterwards with
``harness/score.py <sandbox>``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cite_eval import parse_gold, strip_to_input

HARNESS = Path(__file__).resolve().parent
ROOT = HARNESS.parent
_IGNORE_NAMES = {"__pycache__", "tests", "spikes", ".pytest_cache",
                 ".VSCodeCounter", ".git", ".github"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _clear_dir(path: Path) -> None:
    """Empty a directory's contents in place, leaving the directory itself.

    Removing the watched session root can be denied on Windows (OneDrive or the
    sandbox's own VS Code window holds the folder), so only the contents are
    cleared. Read-only bits are cleared and removals retried to ride out
    transient locks on a just-written session folder.
    """
    if not path.exists():
        return
    for child in path.iterdir():
        for attempt in range(4):
            try:
                if child.is_dir():
                    for p in child.rglob("*"):
                        try:
                            os.chmod(p, stat.S_IWRITE)
                        except OSError:
                            pass
                    shutil.rmtree(child)
                else:
                    os.chmod(child, stat.S_IWRITE)
                    child.unlink()
                break
            except (PermissionError, OSError):
                if attempt == 3:
                    raise
                time.sleep(0.5)


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _IGNORE_NAMES}


def _resolve_text(datasets: Path, text: str) -> Path:
    direct = datasets / f"{text}.md"
    if direct.exists():
        return direct
    matches = sorted(datasets.glob(f"{text}-*.md"))
    if not matches:
        sys.exit(f"error: no dataset matching '{text}' in {datasets}")
    return matches[0]


def _apply_toml_overrides(toml_path: Path, overrides: dict[str, str]) -> None:
    """Section-aware replace of ``key = "value"`` lines, e.g. '[cache] dir'."""
    want: dict[tuple[str, str], str] = {}
    for spec, val in overrides.items():
        section, key = spec.split("]", 1)
        want[(section.strip("[ ").strip(), key.strip())] = val
    current = None
    out: list[str] = []
    for line in toml_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
        else:
            m = re.match(r'^(\s*)([A-Za-z_][\w-]*)\s*=\s*"(.*?)"(.*)$', line)
            if m and (current, m.group(2)) in want:
                line = f'{m.group(1)}{m.group(2)} = "{want[(current, m.group(2))]}"{m.group(4)}'
        out.append(line)
    toml_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _copy_implementation(repo: Path, impl: dict, sandbox: Path, refresh: bool) -> None:
    src = repo / impl["src"]
    if not src.is_dir():
        sys.exit(f"error: implementation source not found: {src}")
    dest = sandbox / impl["copy_to"]
    if dest.exists() and refresh:
        shutil.rmtree(dest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, ignore=_ignore)
    toml = dest / "cite-assist.toml"
    if impl.get("toml_overrides") and toml.exists():
        _apply_toml_overrides(toml, impl["toml_overrides"])


def _write_agent_run_md(impl: dict, sandbox: Path, model: str, impl_name: str) -> None:
    template = (HARNESS / "prompts" / "run-prompt-full-auto.md").read_text(encoding="utf-8")
    run_md = (template
              .replace("{IMPL}", impl_name)
              .replace("{SKILL_NAME}", impl["skill_name"])
              .replace("{SKILL_DIR}", impl["skill_dir"])
              .replace("{REPORT_FILE}", impl["report_file"])
              .replace("{REFERENCES_FILE}", impl["references_file"])
              .replace("{MODEL}", model))
    (sandbox / "RUN.md").write_text(run_md, encoding="utf-8")


def _write_app_run_md(impl: dict, sandbox: Path, model: str, run_id: str,
                      ranking: str, repo: Path) -> None:
    template = (HARNESS / "prompts" / "run-prompt-app.md").read_text(encoding="utf-8")
    report_path = (sandbox / impl["report_file"]).as_posix()
    run_md = (template
              .replace("{RUN_ID}", run_id)
              .replace("{MODEL}", model)
              .replace("{RANKING}", ranking or "(see run-index.csv)")
              .replace("{REPO}", repo.as_posix())
              .replace("{REPORT_PATH}", report_path)
              .replace("{REPORT_FILE}", impl["report_file"])
              .replace("{REFERENCES_FILE}", impl["references_file"])
              .replace("{SANDBOX}", sandbox.relative_to(ROOT).as_posix()))
    (sandbox / "RUN.md").write_text(run_md, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up a sandbox for one evaluation run.")
    ap.add_argument("--impl", required=True, help="implementation key (see implementations.json)")
    ap.add_argument("--text", required=True, help="dataset id, e.g. t1")
    ap.add_argument("--model", required=True, help="model label for this run")
    ap.add_argument("--run-id", help="defaults to <impl-short>_<model>_<text>")
    ap.add_argument("--ranking", default="", help="ranking label (app mode RUN.md)")
    ap.add_argument("--refresh", action="store_true", help="re-copy the implementation")
    args = ap.parse_args()

    config = _load_json(HARNESS / "config.json")
    impls = _load_json(HARNESS / "implementations.json")
    if args.impl not in impls:
        sys.exit(f"error: unknown impl '{args.impl}'. Known: {', '.join(impls)}")
    impl = impls[args.impl]
    repo = Path(config["cite_assist_repo"])
    if not repo.is_absolute():
        repo = (ROOT / repo).resolve()
    datasets = ROOT / config["datasets_dir"]
    sandboxes = ROOT / config["sandboxes_dir"]

    gold_path = _resolve_text(datasets, args.text)
    run_id = args.run_id or f"{args.impl.replace('agent-', '')}_{args.model}_{args.text}"
    sandbox = sandboxes / args.impl
    sandbox.mkdir(parents=True, exist_ok=True)

    # 1. implementation (agent modes only; app launches from the repo)
    if impl["kind"] == "agent":
        _copy_implementation(repo, impl, sandbox, args.refresh)

    # 2. stripped input + empty reference list
    stripped = strip_to_input(gold_path.read_text(encoding="utf-8")).strip() + "\n"
    (sandbox / impl["report_file"]).write_text(stripped, encoding="utf-8")
    (sandbox / impl["references_file"]).write_text("# References\n\n", encoding="utf-8")

    # clear any prior citation sessions so each run starts from a clean slate
    sessions_dir = impl.get("sessions_dir")
    if sessions_dir:
        _clear_dir(sandbox / sessions_dir)

    # 3. RUN.md
    if impl["kind"] == "agent":
        _write_agent_run_md(impl, sandbox, args.model, args.impl)
    else:
        _write_app_run_md(impl, sandbox, args.model, run_id, args.ranking, repo)

    # 4. run manifest the scorer reads back
    n_citable = sum(1 for s in parse_gold(gold_path.read_text(encoding="utf-8")) if s.citable)
    manifest = {
        "run_id": run_id,
        "impl": args.impl,
        "model": args.model,
        "text": args.text,
        "ranking": args.ranking,
        "gold_path": gold_path.relative_to(ROOT).as_posix(),
        "report_file": impl["report_file"],
        "references_file": impl["references_file"],
        "n_citable_gold": n_citable,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (sandbox / ".current-run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    rel = sandbox.relative_to(ROOT).as_posix()
    print(f"run set up: {run_id}")
    print(f"  sandbox : {rel}  ({impl['kind']} mode)")
    print(f"  report  : {rel}/{impl['report_file']}  ({n_citable} citable sentences in gold)")
    if impl["kind"] == "agent":
        print(f"  next    : open '{rel}' as the VS Code workspace, set model to "
              f"'{args.model}', then follow RUN.md")
    else:
        print(f"  next    : follow {rel}/RUN.md (launch the app from the repo)")
    print(f"  score   : python harness/score.py {rel}")


if __name__ == "__main__":
    main()
