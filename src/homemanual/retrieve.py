"""Question analysis: filters, model isolation, abstention, evidence assembly.

Rules this module enforces:

* a model in the question must be an exact curated alias of a manual in the
  library, for every kind of question - otherwise nothing is answered from a
  different model's manual;
* a selected model and a model named in the question must agree;
* fault-style ("diagnostic") questions need a model, because the answer would
  otherwise be taken from a different appliance's manual;
* a passage must genuinely cover the topic of the question (models and
  appliance words do not count towards that coverage).
"""

from __future__ import annotations

import re

from . import config
from .index import ManualIndex
from .textutil import model_tokens_ci, normalise_model, tokenize

DIAGNOSTIC_PHRASES = (
    "why is my",
    "why does my",
    "why won't my",
    "why wont my",
    "stopped working",
    "shuts off",
    "shut off",
    "not turn",
    "fault code",
    "error code",
)
DIAGNOSTIC_FRAGMENT_RE = re.compile(
    r"\b(not|won't|wont|doesn't|doesnt|didn't|didnt|isn't|isnt|aren't|can't|cant|"
    r"stopped|stop|broken|stuck|leak|leaking|leaks|flashing|beeping|beeps|noisy|"
    r"noise|vibrating|smell|odor|clogged|unbalanced|reset|repair|"
    r"troubleshoot|troubleshooting|error|fault)\b"
)

# A question that asks for a procedure or a fix should be answered from one
# model's manual, never from a mixture.
PROCEDURAL_PHRASES = (
    "how do i",
    "how do you",
    "how can i",
    "how to",
    "what are the steps",
    "steps to",
    "the steps for",
)

# Passages that carry a safety notice are flagged so the written answer keeps
# the warning instead of silently dropping it.
WARNING_RE = re.compile(r"\b(WARNING|CAUTION|DANGER)\b")

# Words that name the appliance rather than the topic. They are matched with
# word boundaries (so "dishwasher" never registers as "washer") and excluded
# from topic coverage.
CATEGORY_WORDS = {
    "washer": ("washer", "washing machine"),
    "dryer": ("dryer", "drying machine"),
    "dishwasher": ("dishwasher", "dish washer"),
    "refrigerator": ("refrigerator", "fridge"),
    "microwave": ("microwave", "microwave oven"),
}
CATEGORY_PATTERNS = {
    category: tuple(
        re.compile(rf"\b{re.escape(word)}(?:s)?\b", re.IGNORECASE) for word in words
    )
    for category, words in CATEGORY_WORDS.items()
}
CATEGORY_TOPIC_WORDS = frozenset(
    """
    washer washers washing machine machines dryer dryers drying dishwasher dishwashers
    dish refrigerator refrigerators fridge fridges microwave microwaves oven ovens
    """.split()
)

# Terms that carry no topic signal even though they survive stopword removal.
GENERIC_QUERY_TERMS = frozenset(
    """
    often use using used make makes sure know adjust select turn set add get find put
    need want help tell show please much many long work works thing things way ways
    type types good best new old right left top bottom inside outside
    """.split()
)

# How much of the question's topical vocabulary a passage must cover. A chosen
# model or appliance narrows the search space, but the passage still has to be
# about the question's subject.
COVERAGE_MINIMUMS = {
    "unrestricted": 0.6,
    "category": 0.34,
    "model": 0.34,
}


def is_diagnostic(question: str) -> bool:
    """Fault-style questions are model-dependent; general 'how do I' questions
    are not."""
    lowered = (question or "").lower()
    if any(phrase in lowered for phrase in DIAGNOSTIC_PHRASES):
        return True
    return bool(DIAGNOSTIC_FRAGMENT_RE.search(lowered))


def is_procedural(question: str) -> bool:
    """Questions that ask for steps or a fix should stay inside one manual."""
    if is_diagnostic(question):
        return True
    lowered = (question or "").lower()
    return any(phrase in lowered for phrase in PROCEDURAL_PHRASES)


def detect_category(question: str) -> str | None:
    """Which appliance the question is plainly about, when it names exactly one."""
    found = [
        category
        for category, patterns in CATEGORY_PATTERNS.items()
        if any(pattern.search(question or "") for pattern in patterns)
    ]
    return found[0] if len(found) == 1 else None


def content_terms(question: str, excluded: set[str] | None = None) -> set[str]:
    """Topical words only: no appliance names, no model numbers, no filler."""
    excluded = excluded or set()
    terms: set[str] = set()
    for term in tokenize(question):
        if len(term) < 3:
            continue
        if term in GENERIC_QUERY_TERMS or term in CATEGORY_TOPIC_WORDS or term in excluded:
            continue
        terms.add(term)
    return terms


def coverage_gate(
    question: str,
    chunk_text: str,
    *,
    mode: str,
    excluded_terms: set[str] | None = None,
) -> tuple[bool, float]:
    """Reject passages that only brush against the question's vocabulary."""
    terms = content_terms(question, excluded_terms)
    if not terms:
        return True, 1.0
    matched = terms & set(tokenize(chunk_text))
    coverage = len(matched) / len(terms)
    minimum = COVERAGE_MINIMUMS.get(mode, COVERAGE_MINIMUMS["unrestricted"])
    enough_terms = len(matched) >= 2 or coverage >= 0.99
    return (coverage >= minimum and enough_terms), coverage


def _evidence_item(index: ManualIndex, scored, matched_model: str | None) -> dict:
    chunk = scored.chunk
    doc = index.doc(chunk["doc_id"])
    return {
        "source_id": "S?",
        "doc_id": chunk["doc_id"],
        "title": doc.title if doc else chunk["doc_id"],
        "brand": doc.brand if doc else "",
        "category": doc.category if doc else "",
        "models": list(doc.models) if doc else [],
        "doc_type": doc.doc_type if doc else "owner_manual",
        "pdf_page": chunk["pdf_page"],
        "printed_page": chunk.get("printed_page"),
        "section": chunk.get("section"),
        "text": chunk["text"],
        "has_warning": bool(WARNING_RE.search(chunk["text"])),
        "score": scored.score,
        "url": f"/manuals/{chunk['doc_id']}#page={chunk['pdf_page']}",
        "source_page_url": doc.source_page_url if doc else None,
        "pdf_url": doc.pdf_url if doc else None,
        "matched_model": matched_model,
    }


def _dedupe(evidence: list[dict], top_k: int) -> list[dict]:
    kept: list[dict] = []
    per_page: dict[tuple[str, int], int] = {}
    for item in evidence:
        key = (item["doc_id"], item["pdf_page"])
        if per_page.get(key, 0) >= 2:
            continue
        per_page[key] = per_page.get(key, 0) + 1
        kept.append(item)
        if len(kept) >= top_k:
            break
    return kept


def model_options(index: ManualIndex, scope_category: str | None) -> list[dict]:
    return index.category_models(scope_category)


# Kept for the existing internal call sites and tests.
_model_options = model_options


def analyse(
    index: ManualIndex,
    question: str,
    category: str | None = None,
    model: str | None = None,
    doc_id: str | None = None,
    top_k: int | None = None,
) -> dict:
    question = (question or "").strip()
    top_k = top_k or config.TOP_K
    result: dict = {
        "question": question,
        "status": "ok",
        "mode": "retrieved_evidence",
        "filters": {"category": category, "model": model, "doc_id": doc_id},
        "evidence": [],
        "notes": [],
        "clarification": None,
        "matched_models": [],
        "unknown_models": [],
        "model_dependent": False,
        "implied_category": None,
    }
    if not question:
        result["status"] = "bad_request"
        result["notes"].append("Ask a question about one of the appliances in the library.")
        return result
    if len(question) > config.MAX_QUESTION_CHARS:
        result["status"] = "bad_request"
        result["notes"].append(f"Questions are limited to {config.MAX_QUESTION_CHARS} characters.")
        return result

    # -- scope from the UI -------------------------------------------------
    selected_ids: list[str] | None = None
    if doc_id:
        if index.doc(doc_id) is None:
            result["status"] = "unknown_document"
            result["notes"].append(f"Unknown manual id: {doc_id}")
            return result
        selected_ids = [doc_id]
    elif category and category not in config.CATEGORIES:
        result["status"] = "bad_request"
        result["notes"].append(f"Unknown appliance category: {category}")
        return result
    elif category:
        selected_ids = [doc.doc_id for doc in index.docs if doc.category == category]

    implied_category = None
    if category is None and doc_id is None:
        implied_category = detect_category(question)
        if implied_category and selected_ids is None:
            selected_ids = [
                doc.doc_id for doc in index.docs if doc.category == implied_category
            ]
    result["implied_category"] = implied_category
    scope_category = category or implied_category

    # -- model handling ----------------------------------------------------
    # A model is only ever recognised through the curated aliases of a manual
    # in the library. Prefixes, substrings and part numbers are not identity.
    mentioned = model_tokens_ci(question)
    known_hits: dict[str, list[str]] = {}
    unknown_mentions: list[str] = []
    for token in mentioned:
        ids = index.docs_matching_model(token)
        if ids:
            known_hits[token] = ids
        else:
            unknown_mentions.append(token)

    filter_ids: list[str] | None = None
    if model:
        filter_ids = index.docs_matching_model(model)
        if not filter_ids:
            result["status"] = "unknown_model"
            result["unknown_models"] = [model]
            result["notes"].append(
                f"{model} is not a model in this library, so no answer was taken from "
                "another model's manual."
            )
            result["clarification"] = {
                "kind": "model",
                "message": f"{model} is not in the library.",
                "options": _model_options(index, scope_category),
            }
            return result

    if unknown_mentions:
        result["status"] = "unknown_model"
        result["unknown_models"] = unknown_mentions
        result["notes"].append(
            "Not a model in this library: "
            + ", ".join(unknown_mentions)
            + ". Only manuals whose curated model list contains that exact model are "
            "searched, so no answer was taken from another manual."
        )
        result["clarification"] = {
            "kind": "model",
            "message": "Those model numbers are not in the library.",
            "options": _model_options(index, scope_category),
        }
        return result

    matched_model_label: str | None = None
    question_doc_ids = sorted({doc for ids in known_hits.values() for doc in ids}) if known_hits else []
    if filter_ids is not None:
        matched_model_label = model
        result["matched_models"] = [{"query": model, "doc_ids": filter_ids}]
        if question_doc_ids and not (set(filter_ids) & set(question_doc_ids)):
            result["status"] = "conflicting_models"
            result["notes"].append(
                f"The selected model ({model}) and the model in the question "
                f"({', '.join(sorted(known_hits))}) belong to different manuals, so no "
                "answer was produced."
            )
            return result
        if question_doc_ids:
            filter_ids = sorted(set(filter_ids) & set(question_doc_ids))
        if selected_ids is None:
            selected_ids = filter_ids
        else:
            intersection = [item for item in selected_ids if item in set(filter_ids)]
            if not intersection:
                result["status"] = "no_evidence"
                result["notes"].append(
                    "The selected appliance and the model number do not belong to the same manual."
                )
                return result
            selected_ids = intersection
    elif question_doc_ids:
        matched_model_label = ", ".join(sorted(known_hits))
        result["matched_models"] = [
            {"query": token, "doc_ids": ids} for token, ids in sorted(known_hits.items())
        ]
        if selected_ids is None:
            selected_ids = question_doc_ids
        else:
            intersection = [item for item in selected_ids if item in set(question_doc_ids)]
            if not intersection:
                result["status"] = "conflicting_models"
                result["notes"].append(
                    "The selected appliance and the model named in the question belong to "
                    "different manuals, so no answer was produced."
                )
                return result
            selected_ids = intersection

    # -- diagnostics need a model -----------------------------------------
    diagnostic = is_diagnostic(question)
    result["model_dependent"] = diagnostic
    if diagnostic and matched_model_label is None and doc_id is None:
        result["status"] = "needs_clarification"
        result["clarification"] = {
            "kind": "model",
            "message": (
                "Which model is it? Diagnostic answers are taken only from the manual "
                "for that model."
            ),
            "options": _model_options(index, scope_category),
        }
        result["notes"].append(
            "Diagnostic questions need a model number so the answer is not mixed with "
            "another appliance's manual."
        )
        return result

    # -- search + topic coverage ------------------------------------------
    excluded_terms = {normalise_model(token) for token in mentioned}
    for word in re.findall(r"[A-Za-z0-9\-/]+", model or ""):
        excluded_terms.add(normalise_model(word))
    if doc_id is not None or matched_model_label is not None:
        gate_mode = "model"
    elif selected_ids is not None:
        gate_mode = "category"
    else:
        gate_mode = "unrestricted"

    scored = index.search(question, doc_ids=selected_ids, top_k=max(top_k * 3, 12))
    evidence = []
    for item in scored:
        if item.score < config.MIN_EVIDENCE_SCORE:
            continue
        allowed, coverage = coverage_gate(
            question,
            item.chunk["text"],
            mode=gate_mode,
            excluded_terms=excluded_terms,
        )
        if not allowed:
            continue
        entry = _evidence_item(index, item, matched_model_label)
        entry["term_coverage"] = round(coverage, 3)
        evidence.append(entry)
    evidence = _dedupe(evidence, top_k)
    for position, item in enumerate(evidence, start=1):
        item["source_id"] = f"S{position}"
    result["evidence"] = evidence

    if not evidence:
        result["status"] = "no_evidence"
        result["notes"].append(
            "No passage in the searched manuals covered that question closely enough."
        )
    return result
