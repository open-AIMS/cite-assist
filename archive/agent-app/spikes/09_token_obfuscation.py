"""Spike 09 — token obfuscation round-trip + honest Windows perms (A18).

Risk: the API token is stored obfuscated (XOR with a random key in `.appkey`,
base64 in app.json), NOT encrypted. Two things to verify: (1) the XOR/base64
round-trip reverses exactly, and (2) the `0o600` permission on `.appkey` is
honestly described — on Windows `os.chmod` cannot set POSIX modes, so the threat
model wording (A18) must not over-claim. This spike does both and reports the
actual resulting mode.

Run from the repo root:
    python spikes/09_token_obfuscation.py
"""

from __future__ import annotations

import base64
import os
import stat
import sys
import tempfile
from pathlib import Path


def obfuscate(token: bytes, key: bytes) -> str:
    xored = bytes(c ^ key[i % len(key)] for i, c in enumerate(token))
    return base64.b64encode(xored).decode("ascii")


def deobfuscate(stored: str, key: bytes) -> bytes:
    xored = base64.b64decode(stored)
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(xored))


def main() -> int:
    token = b"sk-this-is-a-fake-token-1234567890"
    key = os.urandom(32)

    stored = obfuscate(token, key)
    recovered = deobfuscate(stored, key)
    roundtrip_ok = recovered == token
    plaintext_absent = b"sk-" not in base64.b64decode(stored)

    print("stored (base64):", stored)
    print("round-trips    :", roundtrip_ok)
    print("token-shaped string absent from obfuscated bytes:", plaintext_absent)

    tmp = Path(tempfile.mkdtemp(prefix="citeapp-key-"))
    appkey = tmp / ".appkey"
    appkey.write_bytes(key)
    try:
        os.chmod(appkey, 0o600)
    except OSError as exc:
        print("chmod failed:", exc)
    mode = stat.S_IMODE(appkey.stat().st_mode)
    print(f".appkey mode   : {oct(mode)}")

    if sys.platform.startswith("win"):
        print("NOTE (Windows): os.chmod cannot set POSIX 0o600 here; mode is "
              "typically 0o666. The A18 threat model must say so honestly and "
              "point users to OPENAI_API_KEY (A29) for real secret protection.")
    else:
        if mode == 0o600:
            print("POSIX perms 0o600 applied as documented.")
        else:
            print(f"WARNING: expected 0o600, got {oct(mode)}.")

    ok = roundtrip_ok and plaintext_absent
    print("\nVERDICT:", "PASS — obfuscation reverses and hides the token shape"
          if ok else "FAIL — round-trip or hiding failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
