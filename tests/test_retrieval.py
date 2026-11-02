from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.retrieve import analyse, is_diagnostic, is_procedural  # noqa: E402
from tests.support import sample_library  # noqa: E402


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = sample_library()

    def test_general_question_returns_evidence(self):
        result = analyse(self.index, "How do I clean the lint filter?")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["evidence"])
        self.assertEqual(result["evidence"][0]["doc_id"], "beta-dryer")
        self.assertEqual(result["evidence"][0]["source_id"], "S1")

    def test_evidence_carries_page_references(self):
        result = analyse(self.index, "clean washer cycle")
        item = result["evidence"][0]
        self.assertEqual(item["pdf_page"], 12)
        self.assertEqual(item["printed_page"], "11")
        self.assertEqual(item["url"], "/manuals/alpha-washer#page=12")
        self.assertEqual(item["section"], "CLEAN WASHER CYCLE")

    def test_model_filter_isolates_documents(self):
        result = analyse(self.index, "clean lint filter", model="BD2000")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["doc_id"] for item in result["evidence"]}, {"beta-dryer"})

    def test_model_mentioned_in_question_isolates_documents(self):
        result = analyse(self.index, "AW1000 leveling legs")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["doc_id"] for item in result["evidence"]}, {"alpha-washer"})

    def test_unknown_model_is_refused(self):
        result = analyse(self.index, "clean lint filter", model="ZZ9000XX")
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["unknown_models"], ["ZZ9000XX"])

    def test_unknown_model_does_not_use_another_manual(self):
        result = analyse(self.index, "my ZZ9000XX washer will not spin")
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["evidence"], [])
        self.assertTrue(any("ZZ9000XX" in note for note in result["notes"]))

    def test_unknown_model_in_a_general_question_is_refused(self):
        result = analyse(self.index, "How do I clean the filter on ABC99999 washer?")
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["unknown_models"], ["ABC99999"])

    def test_lookalike_model_filter_is_refused(self):
        result = analyse(self.index, "How do I clean the filter?", model="AW1000FAKE")
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["evidence"], [])

    def test_selected_model_conflicting_with_question_model_is_refused(self):
        result = analyse(self.index, "How do I clean the lint filter on AW1000?", model="BD2000")
        self.assertEqual(result["status"], "conflicting_models")
        self.assertEqual(result["evidence"], [])

    def test_model_selected_question_still_needs_topic_overlap(self):
        result = analyse(self.index, "How do I clean a carburetor?", model="BD2000")
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(result["evidence"], [])

    def test_dishwasher_question_is_not_confused_with_washer(self):
        result = analyse(self.index, "How do I clean the dishwasher filter?")
        self.assertEqual(result["implied_category"], "dishwasher")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["doc_id"] for item in result["evidence"]}, {"gamma-dishwasher"})

    def test_diagnostic_without_model_asks_for_clarification(self):
        result = analyse(self.index, "my washer will not spin")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["evidence"], [])
        self.assertTrue(result["clarification"]["options"])
        self.assertEqual(
            {option["doc_id"] for option in result["clarification"]["options"]},
            {"alpha-washer"},
        )

    def test_diagnostic_with_model_answers_from_that_model(self):
        result = analyse(self.index, "my BD2000 dryer will not heat")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["doc_id"] for item in result["evidence"]}, {"beta-dryer"})

    def test_category_filter_limits_search(self):
        result = analyse(self.index, "cleaning the filter", category="dishwasher")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["doc_id"] for item in result["evidence"]}, {"gamma-dishwasher"})

    def test_category_and_model_mismatch_is_reported(self):
        result = analyse(self.index, "cleaning the filter", category="dishwasher", model="BD2000")
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(result["evidence"], [])

    def test_unknown_category_is_rejected(self):
        result = analyse(self.index, "clean filter", category="spaceship")
        self.assertEqual(result["status"], "bad_request")

    def test_unknown_document_is_rejected(self):
        result = analyse(self.index, "clean filter", doc_id="not-a-doc")
        self.assertEqual(result["status"], "unknown_document")

    def test_unmatched_question_returns_no_evidence(self):
        result = analyse(self.index, "how do I rebuild a car engine carburettor")
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(result["evidence"], [])

    def test_empty_and_long_questions_are_rejected(self):
        self.assertEqual(analyse(self.index, "   ")["status"], "bad_request")
        long_question = "clean filter " * 100
        self.assertGreater(len(long_question), config.MAX_QUESTION_CHARS)
        self.assertEqual(analyse(self.index, long_question)["status"], "bad_request")

    def test_is_diagnostic_detection(self):
        self.assertTrue(is_diagnostic("my dryer is not heating"))
        self.assertTrue(is_diagnostic("what does error code E4 mean"))
        self.assertFalse(is_diagnostic("how do I clean the lint filter"))

    def test_is_procedural_detection(self):
        self.assertTrue(is_procedural("How do I clean the lint filter?"))
        self.assertTrue(is_procedural("my washer will not spin"))
        self.assertTrue(is_procedural("what are the steps to level the washer"))
        self.assertFalse(is_procedural("What is the capacity of this dryer?"))

    def test_evidence_flags_safety_notices(self):
        result = analyse(self.index, "clean the lint filter")
        drying = [item for item in result["evidence"] if item["doc_id"] == "beta-dryer"]
        self.assertTrue(drying)
        self.assertTrue(drying[0]["has_warning"])

    def test_evidence_is_capped_and_deduplicated(self):
        result = analyse(self.index, "clean the lint filter", top_k=3)
        self.assertTrue(result["evidence"])
        self.assertLessEqual(len(result["evidence"]), 3)
        keys = [(item["doc_id"], item["pdf_page"]) for item in result["evidence"]]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
