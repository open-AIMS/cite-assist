# Spike results

Technical-risk verification for app mode (see `DESIGN-app-mode.md`, Stage A0).
Each script in this folder targets one design risk and prints a `VERDICT` plus an
exit code. This file records the outcomes so the design decisions can cite them.

Last run: 2026-06-20.

## Environment

- OS: Windows, Python 3.11+, stdlib only.
- Zotero: desktop reachable, local library v3009, 259 records cached in
  `~/.cite-assist/zotero-cache`.
- LM Studio: running as a local service at `http://localhost:1234/v1`.
  - Chat model used: `google/gemma-4-e4b`.
  - Embedding model used: `text-embedding-embeddinggemma-300m-qat` (768-dim).
  - Other served models seen: `qwen/qwen3-8b`, `google/gemma-3-4b`,
    `lfm2.5-embedding-350m`, `text-embedding-nomic-embed-text-v1.5`.

## Summary

| # | Spike | Risk (design ref) | Result |
|---|-------|-------------------|--------|
| 01 | `01_multiline_match.py` | per-line verbatim matcher vs wrapped sentences (A20/A25/A33/A34) | PASS — risk **confirmed**, join-lines fix validated |
| 02 | `02_chat_json_mode.py` | OpenAI-compatible chat + strict JSON (A4/A5) | **PARTIAL** — `json_object` rejected by LM Studio; `json_schema` works |
| 03 | `03_zotero_select.py` | `zotero://select` deep link (A16/A31) | PASS — desktop jumped to the item |
| 04 | `04_backend_surface.py` | declared integration surface + config defaults (A31/A35) | PASS |
| 05 | `05_run_search_candidates.py` | structured candidate return for the GUI (A32) | PASS |
| 06 | `06_tk_text_selection.py` | Tk Text selection + read-only + tags (A23) | PASS |
| 07 | `07_tk_worker_thread.py` | worker thread → queue → `after()` progress (A22/A35) | PASS |
| 08 | `08_win_drag_drop.py` | Windows file drag-drop (A12) | PASS — after two ctypes fixes (below) |
| 09 | `09_token_obfuscation.py` | token at-rest obfuscation (A18) | PASS — with caveat |
| 10 | `10_create_session.py` | session-folder collision suffixing (A31) | PASS |
| 11 | `11_embeddings_server.py` | embeddings backend reachability (A24) | PASS |

## Key findings (action required)

### 02 — LM Studio needs `json_schema`, not `json_object`

The production-shaped request with `response_format={"type": "json_object"}`
returns:

```
HTTP 400: {"error":"'response_format.type' must be 'json_schema' or 'text'"}
```

Re-sending the same prompt with a `json_schema` response format succeeds and
returns exactly `{"sentences": ["a", "b"]}` from `google/gemma-4-e4b`.

Design impact (A4/A5): the chat client cannot assume `json_object` is accepted.
Either:
- send `json_schema` (works on LM Studio and on current OpenAI models), or
- detect the 400 and fall back from `json_object` → `json_schema` → plain text
  + the A5 parse-retry path.

This makes the A5 "robust parse / retry" requirement non-optional for local
LM Studio backends, and is the strongest argument so far for a small declared
JSON schema rather than free-form `json_object`.

### 08 — Windows drag-drop needs two ctypes care points

The legacy `DragAcceptFiles` + `WM_DROPFILES` subclass works (a path with
spaces decoded intact:
`...OneDrive - Australian Institute...\readme.md`), but only after fixing two
bugs that would otherwise also bite the real app (A12/A21):

1. **64-bit-safe ctypes signatures.** `SetWindowLongPtrW` and `CallWindowProcW`
   must be given explicit `argtypes`/`restype` using `c_void_p` / `c_ssize_t`;
   otherwise the WNDPROC pointer overflows the default 32-bit `c_int`
   (`OverflowError: int too long to convert`).
2. **Use `winfo_id()` directly.** `GetParent(winfo_id())` returned `0` on this
   Tk build, so `DragAcceptFiles` was being applied to the wrong window and the
   drag cursor showed the "no-drop" circle (drops silently ignored). The robust
   form is `hwnd = GetParent(child) or child`.

Also added `ChangeWindowMessageFilterEx` (MSGFLT_ALLOW for
WM_DROPFILES/COPYDATA/COPYGLOBALDATA) defensively. It was **not** required here
(`elevated=False`), but is needed if the app ever runs at a higher integrity
level than Explorer. The File → Open dialog stays the cross-platform baseline,
so drag-drop remaining an enhancement is unchanged.

### 01 — multi-line claim matching is a real constraint

The refcite per-line matcher does not match a sentence that is hard-wrapped
across source lines; joining the candidate lines with single spaces before
matching does. The design must decide between (a) constraining app-mode claims
to a single source line, or (b) adding a multi-line-aware matcher to refcite
(the join-lines approach is validated). Still an open item in §10.

### 09 — Windows file permissions caveat

`os.chmod(path, 0o600)` is effectively a no-op on Windows; the `.appkey` file
ends up `0o666`. Round-trip obfuscation still works, but the A18 threat-model
wording must stay honest: on Windows the file is not ACL-restricted, so the
preferred path remains `OPENAI_API_KEY` in the environment (A29). Obfuscation is
anti-shoulder-surfing only, not encryption.

## Repro commands

```bash
# from the repo root
python spikes/01_multiline_match.py
python spikes/04_backend_surface.py
python spikes/05_run_search_candidates.py
python spikes/06_tk_text_selection.py
python spikes/07_tk_worker_thread.py
python spikes/09_token_obfuscation.py
python spikes/10_create_session.py

# against LM Studio at localhost:1234
CITEAPP_BASE=http://localhost:1234/v1 CITEAPP_MODEL=google/gemma-4-e4b \
    python spikes/02_chat_json_mode.py
EMB_MODEL=text-embedding-embeddinggemma-300m-qat \
    python spikes/11_embeddings_server.py

# manual / interactive
python spikes/03_zotero_select.py
python spikes/08_win_drag_drop.py
```