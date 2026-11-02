"""Loading and validation of the curated manual manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import config


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManualDoc:
    doc_id: str
    title: str
    brand: str
    category: str
    models: tuple[str, ...]
    doc_type: str
    source_page_url: str | None
    pdf_url: str | None
    local_path: Path
    sha256: str
    bytes: int
    pages: int
    retrieved_at: str | None

    @property
    def served_path(self) -> str:
        return f"/manuals/{self.doc_id}"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"invalid JSON in {path}: {exc}") from exc


def load_manifest(path: Path | None = None, verify_files: bool = True) -> list[ManualDoc]:
    path = path or config.MANIFEST_PATH
    payload = _load_json(path)
    docs: list[ManualDoc] = []
    for raw in payload.get("documents", []):
        try:
            local_path = (config.ROOT / raw["local_path"]).resolve()
        except (KeyError, TypeError) as exc:
            raise RegistryError(f"manifest entry missing local_path: {raw!r}") from exc
        doc = ManualDoc(
            doc_id=raw["doc_id"],
            title=raw.get("title") or raw["doc_id"],
            brand=raw.get("brand", ""),
            category=raw.get("category", ""),
            models=tuple(raw.get("models") or ()),
            doc_type=raw.get("doc_type", "owner_manual"),
            source_page_url=raw.get("source_page_url"),
            pdf_url=raw.get("pdf_url"),
            local_path=local_path,
            sha256=raw.get("sha256", ""),
            bytes=int(raw.get("bytes") or 0),
            pages=int(raw.get("pages") or 0),
            retrieved_at=raw.get("retrieved_at"),
        )
        if doc.category not in config.CATEGORIES:
            raise RegistryError(f"{doc.doc_id}: unknown category {doc.category!r}")
        if verify_files:
            _verify_file(doc)
        docs.append(doc)
    return docs


def _verify_file(doc: ManualDoc) -> None:
    if not doc.local_path.exists():
        raise RegistryError(f"{doc.doc_id}: manual file missing at {doc.local_path}")
    if doc.sha256:
        digest = hashlib.sha256(doc.local_path.read_bytes()).hexdigest()
        if digest != doc.sha256:
            raise RegistryError(
                f"{doc.doc_id}: sha256 mismatch (recorded {doc.sha256[:12]}..., "
                f"actual {digest[:12]}...)"
            )


def load_registry(path: Path | None = None) -> dict:
    return _load_json(path or config.REGISTRY_PATH)


def blocked_sources(path: Path | None = None) -> list[dict]:
    registry = load_registry(path)
    return [item for item in registry.get("sources", []) if item.get("disabled")]


def manifest_summary(docs: list[ManualDoc]) -> dict:
    counts: dict[str, int] = {category: 0 for category in config.CATEGORIES}
    brands: dict[str, int] = {}
    for doc in docs:
        counts[doc.category] = counts.get(doc.category, 0) + 1
        brands[doc.brand] = brands.get(doc.brand, 0) + 1
    return {
        "document_count": len(docs),
        "by_category": counts,
        "by_brand": brands,
        "missing_categories": [name for name, value in counts.items() if value == 0],
    }
