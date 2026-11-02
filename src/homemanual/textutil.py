"""Small text helpers shared by ingestion, indexing and retrieval."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
# Model-like tokens: at least two letters, at least one digit, 6+ characters.
_MODEL_RE = re.compile(r"\b(?=[A-Z0-9\-/]{5,24}\b)(?=[A-Z0-9\-/]*[A-Z])(?=[A-Z0-9\-/]*\d)[A-Z0-9]+(?:[-/][A-Z0-9]+)*\b")

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those is are was were be been being am
    do does did doing done have has had having i you he she it we they me him her us them
    my your his its our their mine yours ours theirs what which who whom whose when where why how
    all any both each few more most other some such no nor not only own same so too very can will
    just should now about into over after before under above between during with without from by for
    on off at to in out up down of as per via etc please tell show give need want help
    """.split()
)


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def normalise_model(text: str) -> str:
    """Model numbers compare ignoring case and separators."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def models_match(known: str, query: str) -> bool:
    """Exact curated-alias match.

    Model identity is never inferred from prefixes, substrings or part numbers:
    GTW335 and GTW335ASNWW only match because both are curated aliases of the
    same manual. Anything else is an unknown model and is refused.
    """
    left, right = normalise_model(known), normalise_model(query)
    return bool(left) and left == right


def _stem(word: str) -> str:
    """Very light suffix folding so "clean"/"cleaning" and "filter"/"filters"
    are the same search term. Applied identically to documents and questions."""
    if len(word) > 4 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 3 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [
        _stem(word) for word in _WORD_RE.findall((text or "").lower()) if word not in STOPWORDS
    ]


def model_tokens(text: str) -> list[str]:
    """Heuristic model-number extraction from raw manual text (unique, in order)."""
    return list(model_token_counts(text))


def model_token_counts(text: str) -> "Counter[str]":
    """Like model_tokens but counts every occurrence."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for match in _MODEL_RE.findall(text or ""):
        token = match.strip("-/")
        if len(token) < 6:
            continue
        digits = sum(char.isdigit() for char in token)
        letters = sum(char.isalpha() for char in token)
        if digits < 1 or letters < 2:
            continue
        counts[token] += 1
    return counts


def model_tokens_ci(text: str) -> list[str]:
    """Model-like tokens from free text such as a user question."""
    return model_tokens((text or "").upper())


def uppercase_heading(line: str) -> bool:
    letters = [char for char in line if char.isalpha()]
    if len(letters) < 3:
        return False
    if len(line) > 60:
        return False
    return all(char.isupper() for char in letters)


def heading_is_noise(line: str, noise_labels: frozenset[str] | set[str]) -> bool:
    """Diagram keys ('A B C L2') and '9 WARNING' look like headings but are not
    useful chapter names."""
    labels = {value.upper() for value in noise_labels}
    words = [token for token in re.findall(r"[A-Z0-9]+", line) if len(token) >= 3]
    if not any(len(token) >= 4 for token in words):
        return True
    return not any(token not in labels for token in words)


def detect_printed_page(page_text: str, pdf_page: int) -> str | None:
    """Best-effort printed page label. Manuals print their own numbering which
    often differs from the PDF page index; we never guess when unsure."""
    lines = [line.strip() for line in (page_text or "").splitlines()]
    candidates = [line for line in (lines[:3] + lines[-3:]) if line and len(line) <= 24]
    for line in reversed(candidates):
        simple = re.fullmatch(r"(?:page\s*)?(\d{1,3})", line, re.IGNORECASE)
        if simple:
            value = int(simple.group(1))
            if 1 <= value <= 999:
                return str(value)
        labelled = re.fullmatch(r"page\s*(\d{1,3})\s*(?:of|/)\s*\d{1,3}", line, re.IGNORECASE)
        if labelled:
            return str(int(labelled.group(1)))
    return None
