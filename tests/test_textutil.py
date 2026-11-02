from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401  (puts src/ on sys.path)

from homemanual.textutil import (
    detect_printed_page,
    heading_is_noise,
    model_tokens,
    model_tokens_ci,
    models_match,
    normalise_model,
    tokenize,
    uppercase_heading,
)
from homemanual.ingest import NON_SECTION_LABELS


class TextUtilTests(unittest.TestCase):
    def test_normalise_model_strips_separators(self):
        self.assertEqual(normalise_model("NN-SN67HS"), "nnsn67hs")
        self.assertEqual(normalise_model("dve45t6000w"), "dve45t6000w")

    def test_models_match_is_exact_only(self):
        # Two aliases of the same manual match; a prefix of an alias does not.
        self.assertTrue(models_match("GTW335ASNWW", "GTW335ASNWW"))
        self.assertTrue(models_match("DVE45T6000W/A3", "dve45t6000wA3"))
        self.assertFalse(models_match("GTW335", "GTW335ASNWW"))
        self.assertFalse(models_match("DVE45T6000", "DVE45T6000W"))
        self.assertFalse(models_match("AW1000", "BD2000"))

    def test_models_match_rejects_lookalike_models(self):
        # A near-miss must never resolve to an existing manual.
        self.assertFalse(models_match("GTW335", "GTW335FAKE"))
        self.assertFalse(models_match("SHEM63W55N", "SHEM63W55NX"))

    def test_model_tokens_finds_realistic_numbers(self):
        text = "Model WDF520PADM and GTW335ASNWW are covered. See page 12."
        tokens = model_tokens(text)
        self.assertIn("WDF520PADM", tokens)
        self.assertIn("GTW335ASNWW", tokens)

    def test_model_tokens_ignores_words_without_digits(self):
        self.assertEqual(model_tokens("WARNING SAFETY NOTICE ENGLISH"), [])

    def test_model_tokens_ci_handles_lowercase_questions(self):
        self.assertIn("WA45T3200AW", model_tokens_ci("my wa45t3200aw will not spin"))

    def test_detect_printed_page_reads_footer_number(self):
        page = "WASHER SAFETY\nSome body text\n12"
        self.assertEqual(detect_printed_page(page, 14), "12")

    def test_detect_printed_page_returns_none_when_absent(self):
        self.assertIsNone(detect_printed_page("Just a sentence with 2026 in it", 3))

    def test_detect_printed_page_labelled_form(self):
        self.assertEqual(detect_printed_page("text\nPage 7 of 40", 9), "7")

    def test_uppercase_heading(self):
        self.assertTrue(uppercase_heading("CLEAN WASHER CYCLE"))
        self.assertFalse(uppercase_heading("Clean Washer Cycle"))
        self.assertFalse(uppercase_heading("A" * 80))

    def test_heading_is_noise(self):
        self.assertTrue(heading_is_noise("A B C L2 L1", NON_SECTION_LABELS))
        self.assertTrue(heading_is_noise("9 WARNING", NON_SECTION_LABELS))
        self.assertTrue(heading_is_noise("CAUTION", NON_SECTION_LABELS))
        self.assertTrue(heading_is_noise("WARNING WARNING", NON_SECTION_LABELS))
        self.assertFalse(heading_is_noise("CLEAN WASHER CYCLE", NON_SECTION_LABELS))
        self.assertFalse(heading_is_noise("CARE AND CLEANING", NON_SECTION_LABELS))

    def test_tokenize_drops_stopwords(self):
        self.assertEqual(tokenize("How do I clean the filter?"), ["clean", "filter"])


if __name__ == "__main__":
    unittest.main()
