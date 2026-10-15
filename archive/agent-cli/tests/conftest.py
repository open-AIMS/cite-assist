"""Make the refcite package importable when pytest is run from this folder."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
