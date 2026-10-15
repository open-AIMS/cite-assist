#!/usr/bin/env python3
"""app.py — thin launcher for cite-assist app mode (A16).

Run from this app folder (archive/agent-app in the repository):

    python app.py

Equivalent to ``python -m citeapp``. Kept as a one-line convenience so a user
can start the GUI without remembering the ``-m`` form.
"""

from __future__ import annotations

import sys

from citeapp import main

if __name__ == "__main__":
    sys.exit(main())
