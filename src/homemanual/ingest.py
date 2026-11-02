"""PDF extraction into page-aware chunks with source metadata."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .registry import ManualDoc, load_manifest
from .textutil import (
    collapse_whitespace,
    detect_printed_page,
    heading_is_noise,
    model_token_counts,
    uppercase_heading,
)

# Printed warning labels look like headings but say nothing about the topic.
NON_SECTION_LABELS = frozenset(
    {"WARNING", "CAUTION", "NOTICE", "NOTE", "NOTES", "DANGER", "IMPORTANT", "ATTENTION"}
)


class IngestError(RuntimeError):
    pass

SCHEMA_VERSION = 2


def manifest_fingerprint(docs: list[ManualDoc]) -> str:
    """Identity of the document set plus the schema that shaped the index.

    Any change to a document hash, its page count, its curated model aliases or
    to the index schema invalidates a saved index, so stale and orphaned
    passages can never stay searchable.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "documents": sorted(
            (
                doc.doc_id,
                doc.sha256,
                doc.pages,
                sorted(doc.models),
                doc.category,
            )
            for doc in docs
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=list).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    pdf_page: int
    printed_page: str | None
    section: str | None
    text: str
    word_count: int


@dataclass
class DocIndex:
    doc_id: str
    pages: int
    chunks: list[Chunk] = field(default_factory=list)
    detected_models: list[str] = field(default_factory=list)
    text_chars: int = 0
    unreadable_pages: list[int] = field(default_factory=list)


def read_pdf_pages(path: Path) -> list[str]:
    """Return one text string per PDF page. Raises IngestError with a plain reason."""
    if not path.exists():
        raise IngestError(f"file not found: {path}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment check
        raise IngestError("pypdf is not installed") from exc
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise IngestError("encrypted PDF") from exc
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"could not read PDF: {type(exc).__name__}: {exc}") from exc
    if not pages:
        raise IngestError("PDF has no pages")
    return pages


def page_section(page_text: str, carry: str | None) -> str | None:
    section = carry
    for raw_line in (page_text or "").splitlines():
        line = raw_line.strip()
        if not line or not uppercase_heading(line):
            continue
        if heading_is_noise(line, NON_SECTION_LABELS):
            continue
        if re.fullmatch(r"[A-Z0-9\s\-.,&/']{3,60}", line) and len(line) >= 3:
            section = collapse_whitespace(line)
    return section


def chunk_page(
    text: str,
    doc_id: str,
    pdf_page: int,
    printed_page: str | None,
    section: str | None,
) -> list[Chunk]:
    words = collapse_whitespace(text).split()
    if len(words) < config.MIN_CHUNK_WORDS:
        return []
    step = max(1, config.CHUNK_WORDS - config.CHUNK_OVERLAP_WORDS)
    chunks: list[Chunk] = []
    for start in range(0, len(words), step):
        window = words[start : start + config.CHUNK_WORDS]
        if len(window) < config.MIN_CHUNK_WORDS:
            continue
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}:p{pdf_page}:c{len(chunks)}",
                doc_id=doc_id,
                pdf_page=pdf_page,
                printed_page=printed_page,
                section=section,
                text=" ".join(window),
                word_count=len(window),
            )
        )
    return chunks


def ingest_document(doc: ManualDoc) -> DocIndex:
    pages = read_pdf_pages(doc.local_path)
    result = DocIndex(doc_id=doc.doc_id, pages=len(pages))
    section: str | None = None
    model_counts: dict[str, int] = {}
    for index, page_text in enumerate(pages, start=1):
        section = page_section(page_text, section)
        printed = detect_printed_page(page_text, index)
        result.text_chars += len(page_text)
        if not collapse_whitespace(page_text):
            result.unreadable_pages.append(index)
        for token, count in model_token_counts(page_text).items():
            model_counts[token] = model_counts.get(token, 0) + count
        result.chunks.extend(chunk_page(page_text, doc.doc_id, index, printed, section))
    ordered = sorted(model_counts.items(), key=lambda item: (-item[1], item[0]))
    result.detected_models = [token for token, count in ordered if count >= 2][:40]
    return result


def build_index(
    docs: list[ManualDoc] | None = None,
    output_path: Path | None = None,
) -> dict:
    docs = docs if docs is not None else load_manifest()
    output_path = output_path or config.INDEX_PATH
    payload: dict = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
        "manifest_fingerprint": manifest_fingerprint(docs),
        "documents": [],
        "chunks": [],
    }
    for doc in docs:
        try:
            result = ingest_document(doc)
        except IngestError as exc:
            payload["documents"].append({"doc_id": doc.doc_id, "error": str(exc)})
            continue
        payload["documents"].append(
            {
                "doc_id": doc.doc_id,
                "pages": result.pages,
                "chunks": len(result.chunks),
                "text_chars": result.text_chars,
                "detected_models": result.detected_models,
                "pages_without_text": result.unreadable_pages,
            }
        )
        payload["chunks"].extend(asdict(chunk) for chunk in result.chunks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return payload
