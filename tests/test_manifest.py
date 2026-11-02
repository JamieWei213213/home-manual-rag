from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.index import ManualIndex  # noqa: E402
from homemanual.ingest import SCHEMA_VERSION, manifest_fingerprint  # noqa: E402
from homemanual.registry import blocked_sources, load_manifest, manifest_summary  # noqa: E402
from homemanual.retrieve import analyse  # noqa: E402
from tests.support import make_chunk, make_doc  # noqa: E402

MANIFEST_EXISTS = config.MANIFEST_PATH.exists()
INDEX_EXISTS = config.INDEX_PATH.exists()


@unittest.skipUnless(MANIFEST_EXISTS, "verified manuals have not been downloaded yet")
class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = load_manifest()  # verifies sha256 for every document
        cls.summary = manifest_summary(cls.docs)

    def test_all_five_categories_are_present(self):
        self.assertEqual(self.summary["missing_categories"], [])
        for category in config.CATEGORIES:
            self.assertGreaterEqual(self.summary["by_category"][category], 1)

    def test_collection_size_matches_the_plan(self):
        self.assertGreaterEqual(len(self.docs), 10)
        self.assertLessEqual(len(self.docs), 15)

    def test_each_document_has_real_pdf_metadata(self):
        for doc in self.docs:
            self.assertTrue(doc.local_path.is_file(), doc.doc_id)
            self.assertGreater(doc.bytes, 100_000, doc.doc_id)
            self.assertGreaterEqual(doc.pages, 4, doc.doc_id)
            self.assertTrue(doc.pdf_url and doc.pdf_url.startswith("http"), doc.doc_id)
            self.assertTrue(doc.retrieved_at, doc.doc_id)
            self.assertTrue(doc.models, doc.doc_id)

    def test_blocked_sources_are_recorded_with_reasons(self):
        blocked = blocked_sources()
        self.assertTrue(blocked)
        for item in blocked:
            self.assertTrue(item.get("blocked_reason"), item["id"])

    def test_manifest_document_ids_are_unique(self):
        ids = [doc.doc_id for doc in self.docs]
        self.assertEqual(len(ids), len(set(ids)))


@unittest.skipUnless(MANIFEST_EXISTS and INDEX_EXISTS, "search index has not been built yet")
class RealIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = ManualIndex.load()

    def test_every_document_contributed_chunks(self):
        indexed = {chunk["doc_id"] for chunk in self.index.chunks}
        self.assertEqual(indexed, set(self.index.document_ids()))

    def test_chunks_reference_real_pages(self):
        payload = json.loads(config.INDEX_PATH.read_text(encoding="utf-8"))
        pages = {item["doc_id"]: item["pages"] for item in payload["documents"]}
        for chunk in self.index.chunks:
            self.assertTrue(chunk["text"].strip())
            self.assertGreaterEqual(chunk["pdf_page"], 1)
            self.assertLessEqual(chunk["pdf_page"], pages[chunk["doc_id"]])

    def test_general_question_returns_grounded_evidence(self):
        result = analyse(self.index, "how do I clean the lint filter?")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["evidence"])
        for item in result["evidence"]:
            self.assertGreaterEqual(item["pdf_page"], 1)
            self.assertTrue(item["url"].startswith("/manuals/"))
            self.assertTrue(item["text"].strip())

    def test_indexed_chunk_lookup_is_not_empty_for_common_terms(self):
        for term in ("filter", "cycle", "water", "door"):
            self.assertTrue(self.index.search(term, top_k=3), term)

    def test_diagnostic_question_without_model_asks_for_the_model(self):
        result = analyse(self.index, "my washer will not spin")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["evidence"], [])

    def test_unknown_model_is_not_answered_from_another_manual(self):
        result = analyse(self.index, "my WTW5000DW washer will not spin")
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["evidence"], [])

    def test_known_model_isolates_the_results(self):
        result = analyse(self.index, "how do I clean the filter", model="SHEM63W55N")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            {item["doc_id"] for item in result["evidence"]}, {"bosch-shem63w5xn-9001238146"}
        )

    def test_pages_without_extractable_text_are_reported(self):
        doc = self.index.doc("bosch-shem63w5xn-9001238146")
        missing = self.index.pages_without_text(doc.doc_id)
        self.assertTrue(missing, "expected the scanned Bosch pages to be reported")
        indexed_pages = {
            chunk["pdf_page"] for chunk in self.index.chunks if chunk["doc_id"] == doc.doc_id
        }
        for page in missing:
            self.assertNotIn(page, indexed_pages)


class IndexFreshnessTests(unittest.TestCase):
    """The saved index must never outlive the manifest it was built from."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_index"
        self.tmp.mkdir(exist_ok=True)
        self.index_path = self.tmp / "chunks.json"
        self.doc_a = make_doc("doc-a", "Alpha", "washer", ["AW1000"])
        self.doc_b = make_doc("doc-b", "Beta", "dryer", ["BD2000"])

    def tearDown(self) -> None:
        for item in self.tmp.glob("*"):
            item.unlink()
        self.tmp.rmdir()

    def write_index(self, docs, chunks, schema_version: int = SCHEMA_VERSION) -> None:
        payload = {
            "built_at": "test",
            "schema_version": schema_version,
            "manifest_fingerprint": manifest_fingerprint(docs),
            "documents": [
                {
                    "doc_id": doc.doc_id,
                    "pages": doc.pages,
                    "chunks": 1,
                    "text_chars": 10,
                    "detected_models": [],
                    "pages_without_text": [],
                }
                for doc in docs
            ],
            "chunks": chunks,
        }
        self.index_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_matching_index_is_reused(self):
        chunk = make_chunk("doc-a", 3, "leveling legs and a level across the top")
        self.write_index([self.doc_a], [chunk])
        index = ManualIndex.load(index_path=self.index_path, docs=[self.doc_a])
        self.assertEqual(len(index.chunks), 1)
        self.assertFalse(any("rebuilt" in warning for warning in index.warnings))

    def test_manifest_change_invalidates_the_saved_index(self):
        chunk = make_chunk("doc-a", 3, "text")
        self.write_index([self.doc_a, self.doc_b], [chunk])
        index = ManualIndex.load(index_path=self.index_path, docs=[self.doc_a])
        self.assertTrue(any("stale" in warning for warning in index.warnings))
        self.assertEqual([doc.doc_id for doc in index.docs], ["doc-a"])

    def test_orphan_and_out_of_range_chunks_are_dropped(self):
        good = make_chunk("doc-a", 3, "leveling legs")
        orphan = make_chunk("ghost-doc", 1, "leveling legs from nowhere")
        out_of_range = make_chunk("doc-a", 99, "page beyond the document")
        self.write_index([self.doc_a], [good, orphan, out_of_range])
        index = ManualIndex.load(index_path=self.index_path, docs=[self.doc_a])
        self.assertEqual([chunk["chunk_id"] for chunk in index.chunks], [good["chunk_id"]])
        self.assertTrue(any("no longer in the manifest" in w for w in index.warnings))
        self.assertTrue(any("page range" in w for w in index.warnings))

    def test_schema_change_invalidates_the_saved_index(self):
        chunk = make_chunk("doc-a", 3, "leveling legs")
        self.write_index([self.doc_a], [chunk], schema_version=SCHEMA_VERSION - 1)
        index = ManualIndex.load(index_path=self.index_path, docs=[self.doc_a])
        self.assertTrue(any("schema changed" in warning for warning in index.warnings))

    def test_empty_document_filter_searches_nothing(self):
        chunk = make_chunk("doc-a", 3, "leveling legs and a level across the top")
        self.write_index([self.doc_a], [chunk])
        index = ManualIndex.load(index_path=self.index_path, docs=[self.doc_a])
        self.assertEqual(index.search("leveling legs", doc_ids=[]), [])
        self.assertTrue(index.search("leveling legs", doc_ids=None))


if __name__ == "__main__":
    unittest.main()
