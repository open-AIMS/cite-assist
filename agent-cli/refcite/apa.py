"""apa.py — render APA in-text citations and reference-list entries (DESIGN.md §7).

A deliberately "dumb" formatter: author names and years are normalised to APA
form at cache-build time (``extract.py``), so this module only assembles the
stored CSL fields. It covers the common item types in a marine-science report
library (journal article, report, dataset, web page, book/chapter, thesis,
conference paper) and renders a visible placeholder rather than a silently
broken citation when author/year metadata is missing.
"""

from __future__ import annotations

# Item types that carry full-text-like CSL and use a journal-style reference.
_JOURNAL_TYPES = {"journal-article", "article-journal", "article", "magazine-article"}


def _surname(author: str) -> str:
    """Return the family name from an APA-normalised author string.

    ``"Smith, J. A."`` -> ``"Smith"``; an institutional/literal name is returned
    whole.
    """
    author = author.strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    return author


def in_text_authors(authors: list[str]) -> str:
    surnames = [_surname(a) for a in authors if a and a.strip()]
    if not surnames:
        return "Author unknown"
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}"
    return f"{surnames[0]} et al."


def _year_or_nd(year) -> str:
    if year in ("", None):
        return "n.d."
    return str(year)


def in_text_label(authors: list[str], year) -> str:
    """The ``Author, Year`` core used inside a parenthetical group."""
    return f"{in_text_authors(authors)}, {_year_or_nd(year)}"


def in_text(record) -> str:
    """A standalone parenthetical APA in-text citation, e.g. ``(Smith, 2019)``."""
    return f"({in_text_label(record.authors, record.year)})"


def is_placeholder(record) -> bool:
    """True when the citation will render with an ``Author unknown`` / ``n.d.`` placeholder."""
    has_author = any(a and a.strip() for a in record.authors)
    has_year = record.year not in ("", None)
    return not (has_author and has_year)


def _reference_authors(authors: list[str]) -> str:
    names = [a for a in authors if a and a.strip()]
    if not names:
        return "Author unknown"
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    # APA: first 19, an ellipsis, then the final author.
    return ", ".join(names[:19]) + ", ... " + names[-1]


def _doi_or_url(fm: dict) -> str:
    doi = (fm.get("doi") or "").strip()
    if doi:
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    return (fm.get("url") or "").strip()


def reference(record) -> str:
    """Render a full APA reference-list entry for ``record``.

    Container titles are wrapped in markdown ``*emphasis*`` so the shared
    ``references.md`` shows the italics APA expects.
    """
    fm = record.front_matter
    authors = _reference_authors(record.authors)
    year = _year_or_nd(record.year)
    title = (record.title or "").strip()
    item_type = record.item_type
    container = (fm.get("container") or "").strip()
    publisher = (fm.get("publisher") or "").strip()
    volume = str(fm.get("volume") or "").strip()
    issue = str(fm.get("issue") or "").strip()
    pages = str(fm.get("pages") or "").strip()
    link = _doi_or_url(fm)

    head = f"{authors} ({year}). "

    if item_type in _JOURNAL_TYPES:
        body = f"{title}. " if title else ""
        if container:
            src = f"*{container}*"
            if volume:
                src += f", *{volume}*"
            if issue:
                src += f"({issue})"
            if pages:
                src += f", {pages}"
            body += src + ". "
        return (head + body + link).strip()

    if item_type == "dataset":
        body = f"{title} [Data set]. " if title else ""
        if publisher or container:
            body += f"{publisher or container}. "
        return (head + body + link).strip()

    if item_type == "report":
        body = f"{title}. " if title else ""
        if publisher or container:
            body += f"{publisher or container}. "
        return (head + body + link).strip()

    if item_type in ("webpage", "web", "post-weblog"):
        body = f"{title}. " if title else ""
        if container:
            body += f"*{container}*. "
        return (head + body + link).strip()

    if item_type in ("book", "monograph"):
        body = f"*{title}*. " if title else ""
        if publisher:
            body += f"{publisher}. "
        return (head + body + link).strip()

    if item_type in ("chapter", "chapter-book"):
        body = f"{title}. " if title else ""
        if container:
            body += f"In *{container}*"
            body += f", {pages}. " if pages else ". "
        if publisher:
            body += f"{publisher}. "
        return (head + body + link).strip()

    if item_type == "thesis":
        body = f"*{title}* " if title else ""
        body += "[Thesis]. "
        if publisher:
            body += f"{publisher}. "
        return (head + body + link).strip()

    if item_type in ("paper-conference", "conference-paper"):
        body = f"{title}. " if title else ""
        if container:
            body += f"In *{container}*"
            body += f", {pages}. " if pages else ". "
        return (head + body + link).strip()

    # Generic fallback covering anything else.
    body = f"{title}. " if title else ""
    return (head + body + link).strip()
