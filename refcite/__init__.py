"""refcite — citation-assist tooling for the NW-Aus report.

A small, dependency-free (baseline) package that caches a Zotero library
locally, searches it for sources that support report statements, and writes
human-reviewable handoff files plus APA citations into the report.

See ``.github/agents/cite-assist/DESIGN.md`` for the full design and
``.github/agents/cite-assist/README.md`` for usage.

The baseline (Stage 1) uses only the Python standard library. The optional
embeddings search backend (Stage 2) talks to a local model server over HTTP
and still installs nothing.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
