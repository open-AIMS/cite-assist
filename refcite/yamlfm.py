"""yamlfm — a deliberately small YAML front-matter reader/writer.

Handles only the subset of YAML that refcite records use: a leading ``---``
block of ``key: value`` pairs whose values are bare/quoted strings, integers,
booleans, or flow-style lists (``["a", "b"]``). This keeps the baseline
dependency-free (no PyYAML) while round-tripping the record front matter
defined in DESIGN.md §4.

It is intentionally not a general YAML parser. It only needs to read back what
``dump`` writes, plus tolerate the hand-written ``cite-assist`` corpus style.
"""

from __future__ import annotations


def split_front_matter(text: str) -> tuple[str, str]:
    """Split ``text`` into (front_matter_block, body).

    The returned front-matter block excludes the surrounding ``---`` fences.
    If no front matter is present, returns ``("", text)`` (newlines normalised).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return fm, body
    # Unterminated front matter — treat the whole thing as body.
    return "", text


def parse(text: str) -> tuple[dict, str]:
    """Parse a document into (front_matter_dict, body)."""
    fm, body = split_front_matter(text)
    data: dict = {}
    for line in fm.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":  # only top-level keys are supported
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        data[key.strip()] = _parse_scalar(raw.strip())
    return data, body


def _parse_scalar(raw: str):
    if raw == "":
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        return _parse_flow_list(raw[1:-1])
    if (raw[0] == '"' and raw[-1] == '"') or (raw[0] == "'" and raw[-1] == "'"):
        return _unquote(raw)
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        return raw


def _unquote(raw: str) -> str:
    quote = raw[0]
    inner = raw[1:-1]
    if quote == '"':
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return inner


def _parse_flow_list(inner: str) -> list:
    if inner.strip() == "":
        return []
    return [_clean_item(part) for part in _split_top_level(inner)]


def _split_top_level(inner: str) -> list[str]:
    """Split on top-level commas, keeping quote characters and ignoring commas
    inside quotes. Each returned piece is later trimmed and unquoted."""
    parts: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_quote is not None:
            buf.append(ch)
            if in_quote == '"' and ch == "\\" and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in "\"'":
            in_quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _clean_item(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"')
                           or (text[0] == "'" and text[-1] == "'")):
        return _unquote(text)
    return text


def dump(data: dict) -> str:
    """Serialise a flat dict to a ``---`` fenced front-matter block (with newline)."""
    lines = ["---"]
    for key, value in data.items():
        lines.append(_dump_pair(key, value))
    lines.append("---")
    return "\n".join(lines) + "\n"


def make_record(front_matter: dict, body: str) -> str:
    """Combine front matter and body into a full record document."""
    return dump(front_matter) + "\n" + body.lstrip("\n")


def _dump_pair(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key}: {value}"
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_quote(str(item)) for item in value)
        return f"{key}: [{inner}]"
    return f"{key}: {_scalar_str(str(value))}"


def _scalar_str(text: str) -> str:
    if text == "":
        return '""'
    if _needs_quote(text):
        return _quote(text)
    return text


def _needs_quote(text: str) -> bool:
    if text != text.strip():
        return True
    if text[0] in "\"'[]{}>|&*!#@`,%":
        return True
    if ":" in text or "#" in text:
        return True
    if text.lower() in ("true", "false", "null", "~"):
        return True
    try:
        int(text)
        return True
    except ValueError:
        return False


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
