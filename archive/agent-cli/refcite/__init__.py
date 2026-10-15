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

from .claims import Claim, claims_template, create_session, save_claims
from .config import Config, load_config
from .insert import insert_session, sentence_matches
from .search import build_index, run_search
from .store import Record, load_records, records_by_id
from .zotero_sync import check_connection, sync, zotero_select_uri

# The declared app-mode integration surface (A31). ``citeapp`` imports only
# these names from ``refcite``; ``refcite`` never imports ``citeapp``, so the
# dependency runs one way. Everything below is also usable by the CLI/tests; it
# is grouped here to make the published surface visible in one place.
__all__ = [
    "__version__",
    "Config",
    "load_config",
    "check_connection",
    "sync",
    "zotero_select_uri",
    "Record",
    "load_records",
    "records_by_id",
    "create_session",
    "Claim",
    "save_claims",
    "claims_template",
    "run_search",
    "build_index",
    "insert_session",
    "sentence_matches",
]

__version__ = "0.1.0"
