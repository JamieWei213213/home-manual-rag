"""In-memory BM25 index over page-aware manual chunks."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from math import log
from pathlib import Path

from . import config
from .ingest import SCHEMA_VERSION, build_index, manifest_fingerprint
from .registry import ManualDoc, load_manifest
from .textutil import models_match, normalise_model, tokenize

BM25_K1 = 1.2
BM25_B = 0.6


@dataclass
class ScoredChunk:
    chunk: dict
    score: float

    @property
    def doc_id(self) -> str:
        return self.chunk["doc_id"]


@dataclass
class ManualIndex:
    docs: list[ManualDoc]
    chunks: list[dict]
    detected_models: dict[str, list[str]] = field(default_factory=dict)
    doc_reports: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    built_at: str | None = None
    base_dir: Path | None = None

    def __post_init__(self) -> None:
        self._docs_by_id = {doc.doc_id: doc for doc in self.docs}
        self._registry_models: dict[str, set[str]] = {}
        for doc in self.docs:
            self._registry_models[doc.doc_id] = {normalise_model(model) for model in doc.models}
        self._tokens: list[list[str]] = [tokenize(chunk["text"]) for chunk in self.chunks]
        self._lengths: list[int] = [max(1, len(tokens)) for tokens in self._tokens]
        self._avg_len = sum(self._lengths) / len(self._lengths) if self._lengths else 1.0
        self._doc_freq: Counter[str] = Counter()
        for tokens in self._tokens:
            for term in set(tokens):
                self._doc_freq[term] += 1
        self._term_freq: list[Counter[str]] = [Counter(tokens) for tokens in self._tokens]
        self._total_docs = max(1, len(self._tokens))

    # -- construction -------------------------------------------------
    @classmethod
    def load(
        cls,
        index_path: Path | None = None,
        docs: list[ManualDoc] | None = None,
        rebuild: bool = False,
    ) -> "ManualIndex":
        index_path = index_path or config.INDEX_PATH
        docs = docs if docs is not None else load_manifest()
        warnings: list[str] = []
        fingerprint = manifest_fingerprint(docs)
        payload: dict | None = None
        if not rebuild and index_path.exists():
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                warnings.append("index file was unreadable and has been rebuilt")
                payload = None
        if payload is not None:
            if payload.get("schema_version") != SCHEMA_VERSION:
                warnings.append("search index schema changed; rebuilt")
                payload = None
            elif payload.get("manifest_fingerprint") != fingerprint:
                warnings.append("search index was stale for the current manuals; rebuilt")
                payload = None
        if payload is None:
            payload = build_index(docs, index_path)
        extracted = {
            item["doc_id"]: item.get("detected_models", [])
            for item in payload.get("documents", [])
        }
        reports = {
            item["doc_id"]: item
            for item in payload.get("documents", [])
            if "doc_id" in item
        }
        known = {doc.doc_id: doc for doc in docs}
        kept_chunks: list[dict] = []
        orphaned = 0
        out_of_range = 0
        for chunk in payload.get("chunks", []):
            doc = known.get(chunk.get("doc_id"))
            if doc is None:
                orphaned += 1
                continue
            page = chunk.get("pdf_page")
            if not isinstance(page, int) or page < 1 or page > doc.pages:
                out_of_range += 1
                continue
            kept_chunks.append(chunk)
        if orphaned:
            warnings.append(f"{orphaned} passage(s) from manuals no longer in the manifest were dropped")
        if out_of_range:
            warnings.append(f"{out_of_range} passage(s) outside the document page range were dropped")
        for item in payload.get("documents", []):
            if "error" in item:
                warnings.append(f"{item['doc_id']}: {item['error']}")
        indexed_ids = {chunk["doc_id"] for chunk in kept_chunks}
        for doc in docs:
            if doc.doc_id not in indexed_ids:
                warnings.append(f"{doc.doc_id}: no extractable text, excluded from search")
            else:
                pages_without_text = reports.get(doc.doc_id, {}).get("pages_without_text") or []
                if pages_without_text:
                    warnings.append(
                        f"{doc.doc_id}: page(s) {pages_without_text} contain no extractable text"
                    )
        return cls(
            docs=docs,
            chunks=kept_chunks,
            detected_models=extracted,
            doc_reports=reports,
            warnings=warnings,
            built_at=payload.get("built_at"),
        )

    # -- lookups ------------------------------------------------------
    def doc(self, doc_id: str) -> ManualDoc | None:
        return self._docs_by_id.get(doc_id)

    def models_for(self, doc_id: str) -> set[str]:
        return self._registry_models.get(doc_id, set())

    def docs_matching_model(self, query: str) -> list[str]:
        """Doc ids whose curated model aliases contain the query exactly."""
        return sorted(
            doc.doc_id
            for doc in self.docs
            if any(models_match(model, query) for model in self._registry_models.get(doc.doc_id, ()))
        )

    def pages_without_text(self, doc_id: str) -> list[int]:
        report = self.doc_reports.get(doc_id) or {}
        return list(report.get("pages_without_text") or [])

    def category_models(self, category: str | None = None) -> list[dict]:
        result: list[dict] = []
        for doc in self.docs:
            if category and doc.category != category:
                continue
            result.append(
                {
                    "doc_id": doc.doc_id,
                    "brand": doc.brand,
                    "category": doc.category,
                    "title": doc.title,
                    "models": list(doc.models),
                }
            )
        return result

    def document_ids(self) -> list[str]:
        return [doc.doc_id for doc in self.docs]

    # -- search -------------------------------------------------------
    def search(
        self,
        query: str,
        doc_ids: list[str] | None = None,
        top_k: int | None = None,
    ) -> list[ScoredChunk]:
        # An explicit empty selection means "no documents", never "all documents".
        allowed = set(doc_ids) if doc_ids is not None else None
        if allowed is not None and not allowed:
            return []
        query_terms = tokenize(query)
        if not query_terms:
            return []
        query_counts = Counter(query_terms)
        query_models = {
            normalise_model(part)
            for part in query.replace(",", " ").split()
            if len(normalise_model(part)) >= 5
        }
        scored: list[ScoredChunk] = []
        for position, chunk in enumerate(self.chunks):
            if allowed is not None and chunk["doc_id"] not in allowed:
                continue
            length = self._lengths[position]
            frequencies = self._term_freq[position]
            score = 0.0
            for term, weight in query_counts.items():
                tf = frequencies.get(term, 0)
                if not tf:
                    continue
                df = self._doc_freq.get(term, 0)
                idf = log(1 + (self._total_docs - df + 0.5) / (df + 0.5))
                denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * length / self._avg_len)
                score += weight * idf * (tf * (BM25_K1 + 1)) / denominator
            if score <= 0:
                continue
            score += self._field_boost(chunk, query_models, query_terms)
            scored.append(ScoredChunk(chunk=chunk, score=round(score, 4)))
        scored.sort(key=lambda item: (-item.score, item.chunk["doc_id"], item.chunk["pdf_page"]))
        return scored[: (top_k or config.TOP_K)]

    def _field_boost(self, chunk: dict, query_models: set[str], query_terms: list[str]) -> float:
        boost = 0.0
        text_norm = normalise_model(chunk["text"][:4000])
        for model in query_models:
            if model and model in text_norm:
                boost += 2.5
        doc = self._docs_by_id.get(chunk["doc_id"])
        if doc is not None:
            title = doc.title.lower()
            boost += 0.4 * sum(1 for term in set(query_terms) if term in title)
            section = (chunk.get("section") or "").lower()
            if section and any(term in section for term in query_terms):
                boost += 0.3
        return boost
