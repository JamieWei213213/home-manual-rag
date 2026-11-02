#!/usr/bin/env python3
"""Build data/index/chunks.json from the verified manuals in the manifest."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.ingest import build_index  # noqa: E402
from homemanual.registry import manifest_summary  # noqa: E402
from homemanual.registry import load_manifest  # noqa: E402


def main() -> int:
    docs = load_manifest()
    payload = build_index(docs, config.INDEX_PATH)
    summary = manifest_summary(docs)
    print(f"manuals : {summary['document_count']} ({summary['by_category']})")
    if summary["missing_categories"]:
        print(f"missing : {summary['missing_categories']}")
    print(f"chunks  : {len(payload['chunks'])}")
    for item in payload["documents"]:
        if "error" in item:
            print(f"  FAILED {item['doc_id']}: {item['error']}")
        else:
            print(
                f"  {item['doc_id']:<34} pages={item['pages']:<4} chunks={item['chunks']:<4} "
                f"chars={item['text_chars']:<7} models={len(item['detected_models'])}"
            )
    print(f"written : {config.INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
