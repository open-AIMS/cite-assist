"""Spike 11 — embeddings server reachability (A24, only for the embeddings backend).

Risk: when the author picks the `embeddings` search backend, the app needs a
reachable model server exposing `/v1/embeddings` (LM Studio default) or
`/api/embeddings` (Ollama). This spike reuses refcite's own `EmbeddingsBackend`
so it exercises exactly the production code path, and confirms a query returns a
vector of the model's dimension.

Environment overrides:
    EMB_SERVER  "lmstudio" (default) or "ollama"
    EMB_URL     default http://localhost:1234
    EMB_MODEL   the served embedding model name

Run from the repo root:
    EMB_MODEL=text-embedding-embeddinggemma-300m-qat python spikes/11_embeddings_server.py
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "agent-cli"))


def main() -> int:
    from refcite.search.embeddings import EmbeddingsBackend, EmbeddingsError

    server = os.environ.get("EMB_SERVER", "lmstudio")
    url = os.environ.get("EMB_URL", "http://localhost:1234")
    model = os.environ.get("EMB_MODEL", "")
    if not model:
        print("Set EMB_MODEL to the served embedding model name.", file=sys.stderr)
        return 2

    backend = EmbeddingsBackend(server, url, model)
    print(f"Embedding a probe query via {server} at {url} (model={model})...")
    try:
        vectors = backend._embed(["coral reef mapping from satellite imagery"])
    except EmbeddingsError as exc:
        print(f"FAIL: server returned an unexpected shape: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any connection error plainly
        print(f"FAIL: could not reach the embeddings server: {exc}", file=sys.stderr)
        return 1

    dim = len(vectors[0]) if vectors else 0
    print(f"got {len(vectors)} vector(s), dimension {dim}")
    ok = len(vectors) == 1 and dim > 0
    print("VERDICT:", "PASS — embeddings server reachable and returns vectors"
          if ok else "FAIL — no usable vector returned")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
