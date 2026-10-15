# Installing cite-assist

This file is a runbook for an AI coding assistant: VS Code Copilot, Codex,
Claude Code, or another agent that can read local files and run shell commands.
When a user says something like "Please install the cite-assist skill from
https://github.com/open-AIMS/cite-assist", follow these steps in order.

Pause to confirm with the user at the points marked CONFIRM, and never run
destructive commands without asking.

cite-assist installs as a personal skill: a folder under the user's home
directory that the user's agent harness loads automatically in every project. It
bundles the `refcite` Python tool, so there is nothing to `pip install`; only
system Python 3.11+ is required.

---

## 1. Check prerequisites

```bash
python --version        # must be 3.11 or newer (use python, not python3, on Windows Git Bash)
git --version
```

- If Python is older than 3.11, tell the user and stop. `refcite` uses the
  stdlib `tomllib`, which is 3.11+.
- A local embeddings server (LM Studio / Ollama) and Zotero desktop are not
  needed to install. They are configured later, only if the user wants them.

## 2. Choose the install location

Choose the install path for the harness the user wants to use. The repository's
`archive/agent-cli/` folder is the skill, so the target directory is the final
`cite-assist` folder that will contain `SKILL.md`, `refcite/`, and
`cite-assist.toml`.

| Path | Used by |
|------|---------|
| `~/.codex/skills/cite-assist/` | Codex |
| `~/.copilot/skills/cite-assist/` | VS Code Copilot + Copilot CLI |
| `~/.claude/skills/cite-assist/` | Claude Code / Claude-compatible skills, if enabled |
| `~/.agents/skills/cite-assist/` | Other agents that read this shared location |

Default to the harness the user is currently using. If they ask for broad
compatibility, install one copy per harness, or choose one location and symlink
the other harness locations to it if the operating system supports symlinks.
On Windows Git Bash, `~` / `$HOME` resolves to `C:\Users\<username>`.

Set `SKILL_DIR` to the chosen path and use it for every command below:

```bash
SKILL_DIR="$HOME/.codex/skills/cite-assist"  # replace with the chosen path
```

## 3. Install the skill folder

The skill is the repository's `archive/agent-cli/` folder, not the whole
repository. Clone the repository to a source location, then put
`archive/agent-cli/` at `$SKILL_DIR`.

CONFIRM the target path with the user, then clone:

```bash
SRC="$HOME/.cite-assist/src"
git clone https://github.com/open-AIMS/cite-assist "$SRC"
```

Then make `$SKILL_DIR` the `agent-cli` folder. Prefer a symlink so
`git -C "$SRC" pull` keeps the skill up to date (where the OS supports it):

```bash
ln -s "$SRC/archive/agent-cli" "$SKILL_DIR"
```

If symlinks are unavailable (some Windows setups), copy instead; updates then
require re-copying:

```bash
cp -r "$SRC/archive/agent-cli" "$SKILL_DIR"
```

The skill folder contains `SKILL.md`, `refcite/`, `cite-assist.toml`,
`README.md`, and `DESIGN.md`. After installing, reload or restart the agent
harness if needed so it discovers the new skill.

## 4. Verify the tool runs

```bash
PYTHONPATH="$SKILL_DIR" python -m refcite status
```

A successful run prints a config and cache summary. Because nothing is synced
yet, expect `cache: EMPTY`; that is fine at this stage.

## 5. Configure `cite-assist.toml`

The config lives at `$SKILL_DIR/cite-assist.toml` and travels with the skill. The
important defaults:

- Cache is user-global. `[cache] dir` should point at a single per-user location
  so the library is synced once and shared across all projects. Set it to:

  ```toml
  [cache]
  dir = "~/.cite-assist/zotero-cache"
  ```

  `refcite` expands `~`. If `[cache] dir` is omitted entirely, this is already
  the built-in default.
- Sessions and references are per-project. Leave `[sessions] dir = "citations"`
  and `references_file = "references.md"` as relative paths. They resolve
  against the project the user runs `refcite` from, so each report keeps its own
  `citations/` and `references.md`.
- Search backend. CONFIRM which backend the user wants:
  - `backend = "bm25"`: default, zero setup, pure Python, works offline. Use
    this unless the user wants conceptual search.
  - `backend = "embeddings"` or `"hybrid"`: semantic RAG, requires a local
    embeddings server. Only choose this if completing step 6.
- Own-project biasing. If the user cites their own datasets / reports, set
  `[search] own_collections` to the relevant Zotero collection names.

## 6. Optional: set up conceptual search with LM Studio

Only if the user chose `embeddings` or `hybrid` in step 5. The full walkthrough
is in `README.md` under "Setting up LM Studio for conceptual search".

1. Install LM Studio (<https://lmstudio.ai>) and download the embedding model
   `embeddinggemma` (Google EmbeddingGemma 300M).
2. Start LM Studio's local server (OpenAI-compatible,
   `http://localhost:1234`) and load that model.
3. Confirm `[embeddings]` in `cite-assist.toml` matches the server, model name,
   and URL.
4. Build the index once:

   ```bash
   PYTHONPATH="$SKILL_DIR" python -m refcite index
   ```

## 7. Connect Zotero and do the first sync

CONFIRM that the user has:

1. Opened Zotero desktop.
2. Enabled the local API: Settings > Advanced > "Allow other applications on
   this computer to communicate with Zotero."

Then build the cache. Run this from any project root; the cache is global:

```bash
PYTHONPATH="$SKILL_DIR" python -m refcite sync
```

The first sync fetches the whole library and prints a live progress line. Let it
finish. If it reports the API is unreachable (403 = local API off; connection
refused = Zotero not running), relay that to the user and wait.

## 8. Smoke test

```bash
PYTHONPATH="$SKILL_DIR" python -m refcite status
```

Confirm it now reports `cache: N records ... ready to search`. The skill is
installed. From here, the user can select report text and invoke the
`cite-assist` skill, which drives the normal workflow (see `SKILL.md` /
`README.md`).

## Updating and removing

- Update (symlink install): `git -C "$SRC" pull`.
- Update (copy install): re-run `cp -r "$SRC/archive/agent-cli" "$SKILL_DIR"` after a
  `git -C "$SRC" pull`.
- Uninstall: remove `$SKILL_DIR` (the symlink or copied folder), and if desired
  the clone at `$SRC` and the cache at `~/.cite-assist/`. CONFIRM before deleting
  anything.
