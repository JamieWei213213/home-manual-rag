"""The frozen evaluation sets and the offline/live generation harness."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.generate import GenerationError  # noqa: E402
from homemanual.index import ManualIndex  # noqa: E402
from homemanual.textutil import model_tokens_ci, normalise_model  # noqa: E402

QUESTIONS_PATH = ROOT / "eval" / "fresh_questions.jsonl"
DEV_QUESTIONS_PATH = ROOT / "eval" / "questions.jsonl"
VALID_KINDS = {"grounded", "isolation", "clarify", "unknown_model", "abstain", "conflict"}
INDEX_EXISTS = config.INDEX_PATH.exists()


def load_generation_eval_module():
    path = ROOT / "scripts" / "run_generation_eval.py"
    spec = importlib.util.spec_from_file_location("run_generation_eval", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evidence_item(source_id: str, text: str, page: int = 9) -> dict:
    return {
        "source_id": source_id,
        "brand": "Beta",
        "category": "dryer",
        "doc_id": "beta-dryer",
        "models": ["BD2000"],
        "pdf_page": page,
        "printed_page": None,
        "section": "CARE AND CLEANING",
        "text": text,
        "has_warning": False,
    }


class FreshSetShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = read_cases(QUESTIONS_PATH)
        self.dev_cases = read_cases(DEV_QUESTIONS_PATH)

    def test_ids_are_unique_and_prefixed(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(case_id.startswith("F") for case_id in ids))

    def test_every_case_declares_a_known_expectation(self):
        for case in self.cases:
            self.assertIn(case["expect"]["kind"], VALID_KINDS, case["id"])
            self.assertTrue(case["question"].strip(), case["id"])

    def test_the_set_covers_every_category_of_question(self):
        kinds = {case["expect"]["kind"] for case in self.cases}
        for kind in ("grounded", "isolation", "clarify", "unknown_model", "abstain"):
            self.assertIn(kind, kinds)

    def test_grounded_labels_carry_source_provenance(self):
        grounded = [case for case in self.cases if case["expect"]["kind"] == "grounded"]
        self.assertTrue(grounded)
        for case in grounded:
            provenance = case["expect"].get("provenance")
            self.assertIsInstance(provenance, dict, case["id"])
            self.assertTrue(provenance.get("doc_id"), case["id"])
            self.assertIsInstance(provenance.get("pdf_page"), int, case["id"])
            self.assertTrue(provenance.get("excerpt"), case["id"])
            self.assertIn("printed_page", provenance, case["id"])
            self.assertIn("PDF page", provenance.get("source", ""), case["id"])

    def test_the_fresh_set_is_not_a_copy_of_the_development_set(self):
        dev_questions = {case["question"].strip().lower() for case in self.dev_cases}
        overlap = [
            case["id"]
            for case in self.cases
            if case["question"].strip().lower() in dev_questions
        ]
        self.assertEqual(overlap, [], "fresh questions must not duplicate the dev set")


@unittest.skipUnless(INDEX_EXISTS, "the search index has not been built")
class FreshSetGroundingTests(unittest.TestCase):
    """Each grounded label must be findable in the downloaded manual text."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = ManualIndex.load()
        cls.cases = read_cases(QUESTIONS_PATH)
        cls.passages: dict[tuple[str, int], list[str]] = {}
        for chunk in cls.index.chunks:
            cls.passages.setdefault((chunk["doc_id"], chunk["pdf_page"]), []).append(chunk["text"])

    def _normalise(self, text: str) -> str:
        return " ".join(text.lower().split())

    def test_provenance_excerpts_appear_on_the_labelled_page(self):
        for case in self.cases:
            expect = case["expect"]
            if expect["kind"] != "grounded":
                continue
            provenance = expect["provenance"]
            key = (provenance["doc_id"], provenance["pdf_page"])
            self.assertIn(key, self.passages, case["id"])
            haystack = self._normalise(" ".join(self.passages[key]))
            needle = self._normalise(provenance["excerpt"])
            self.assertIn(needle, haystack, case["id"])

    def test_printed_page_annotation_matches_the_index(self):
        """The corrected notes must agree with the extracted printed page."""
        for case in self.cases:
            expect = case["expect"]
            if expect["kind"] != "grounded":
                continue
            provenance = expect["provenance"]
            key = (provenance["doc_id"], provenance["pdf_page"])
            extracted = {
                chunk["printed_page"]
                for chunk in self.index.chunks
                if chunk["doc_id"] == key[0] and chunk["pdf_page"] == key[1]
            }
            extracted.discard(None)
            if not extracted:
                self.assertIsNone(provenance["printed_page"], case["id"])
            else:
                self.assertIn(provenance["printed_page"], extracted, case["id"])

    def test_grounded_target_documents_exist_in_the_library(self):
        known = set(self.index.document_ids())
        for case in self.cases:
            expect = case["expect"]
            if expect["kind"] == "grounded":
                accepted = expect.get("answers") or [expect]
                for answer in accepted:
                    self.assertIn(answer["doc_id"], known, case["id"])
                    doc = self.index.doc(answer["doc_id"])
                    self.assertLessEqual(answer["page"], doc.pages, case["id"])
            elif expect["kind"] == "isolation":
                for doc_id in expect["doc_ids"]:
                    self.assertIn(doc_id, known, case["id"])

    def test_unknown_model_cases_name_models_the_library_does_not_hold(self):
        for case in self.cases:
            if case["expect"]["kind"] != "unknown_model":
                continue
            tokens = model_tokens_ci(case["question"])
            self.assertTrue(tokens, case["id"])
            for token in tokens:
                self.assertEqual(self.index.docs_matching_model(token), [], f"{case['id']}:{token}")

    def test_conflict_case_names_two_different_manuals(self):
        for case in self.cases:
            if case["expect"]["kind"] != "conflict":
                continue
            selected = self.index.docs_matching_model(case["filters"]["model"])
            mentioned = {
                doc_id
                for token in model_tokens_ci(case["question"])
                for doc_id in self.index.docs_matching_model(token)
            }
            self.assertTrue(selected, case["id"])
            self.assertTrue(mentioned, case["id"])
            self.assertFalse(set(selected) & mentioned, case["id"])

    def test_every_curated_alias_resolves_to_its_own_manual(self):
        for doc in self.index.docs:
            for model in doc.models:
                self.assertTrue(normalise_model(model), f"{doc.doc_id}:{model}")
                self.assertIn(doc.doc_id, self.index.docs_matching_model(model), model)


class GenerationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_generation_eval_module()
        cls.evidence = [
            evidence_item(
                "S1", "Remove the lint filter and roll the lint off with your fingers."
            )
        ]

    def test_fixture_cycles_through_accept_reject_and_insufficient(self):
        statuses = [self.module.fixture_answer(i, self.evidence)[1] for i in range(4)]
        self.assertEqual(
            statuses,
            ["rejected_citations", "rejected_citations", "insufficient_evidence", "generated"],
        )

    def test_well_formed_fixture_answer_quotes_the_passage(self):
        answer = self.module.well_formed_answer(self.evidence)
        self.assertIn("[S1]", answer)
        self.assertIn('"Remove the lint filter and roll the lint"', answer)

    def test_mock_settings_never_read_a_real_key(self):
        env = dict(os.environ)
        env[config.DEEPSEEK_API_KEY_ENV] = "a-real-looking-key"
        with mock.patch.dict(os.environ, env, clear=True):
            settings = self.module.build_mock_settings()
        self.assertEqual(settings["api_key"], "fixture-only")
        self.assertEqual(settings["base_url"], "https://fixture.invalid")

    def test_live_settings_use_the_configured_key_and_state(self):
        env = dict(os.environ)
        env[config.GENERATION_FLAG_ENV] = "1"
        env[config.DEEPSEEK_API_KEY_ENV] = "configured-app-key"
        env[config.DEEPSEEK_MODEL_ENV] = "deepseek-chat"
        with mock.patch.dict(os.environ, env, clear=True):
            settings = self.module.build_live_settings({}, 512)
        self.assertEqual(settings["api_key"], "configured-app-key")
        self.assertEqual(settings["generation_state"]["status"], "enabled")
        self.assertEqual(settings["model"], "deepseek-chat")
        self.assertEqual(settings["max_tokens"], 512)
        self.assertTrue(settings["base_url"].startswith("https://"))

    def test_live_run_is_refused_without_a_configured_key(self):
        env = {k: v for k, v in os.environ.items() if k != config.DEEPSEEK_API_KEY_ENV}
        env[config.GENERATION_FLAG_ENV] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.main(["--live"]), 2)

    def test_live_run_is_refused_above_the_authorized_request_cap(self):
        env = dict(os.environ)
        env[config.GENERATION_FLAG_ENV] = "1"
        env[config.DEEPSEEK_API_KEY_ENV] = "not-used-because-the-cap-check-is-first"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.main(["--live", "--max-requests", "11"]), 2)
            self.assertEqual(self.module.main(["--live", "--max-requests", "0"]), 2)

    def test_live_run_is_refused_above_the_token_cap(self):
        env = dict(os.environ)
        env[config.GENERATION_FLAG_ENV] = "1"
        env[config.DEEPSEEK_API_KEY_ENV] = "not-used-because-the-cap-check-is-first"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.main(["--live", "--max-tokens", "701"]), 2)
            self.assertEqual(self.module.main(["--live", "--max-tokens", "0"]), 2)


@unittest.skipUnless(INDEX_EXISTS, "the search index has not been built")
class LiveHarnessTests(unittest.TestCase):
    """The live branch, driven by a fake provider. No network call is made."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_generation_eval_module()
        cls.index = ManualIndex.load()
        cls.cases = read_cases(QUESTIONS_PATH)
        cls.isolation = [c for c in cls.cases if c["expect"]["kind"] == "isolation"]
        cls.clarify = [c for c in cls.cases if c["expect"]["kind"] == "clarify"]

    def setUp(self) -> None:
        # The live branch reads the real generation state, so the flag and a
        # fake key are set for the duration of each test. No request is made:
        # the transport is always the injected fake provider.
        patcher = mock.patch.dict(
            os.environ,
            {
                config.GENERATION_FLAG_ENV: "1",
                config.DEEPSEEK_API_KEY_ENV: "fake-live-key",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _provider(self, answers_by_question=None, usage=None, fail=False):
        """A fake provider. Records the questions it was asked."""
        seen: list[str] = []

        def provider(messages, **kwargs):
            prompt = messages[1]["content"]
            seen.append(prompt)
            if fail:
                raise GenerationError("fake provider failure")
            capture = kwargs.get("capture")
            if isinstance(capture, dict) and usage is not None:
                capture["usage"] = usage
            for question, answer in (answers_by_question or {}).items():
                if question in prompt:
                    return answer
            return "INSUFFICIENT_EVIDENCE"

        provider.seen = seen
        return provider

    def _answer_for(self, case) -> str:
        prepared = self.module.prepare(case, self.index)
        self.assertIsNotNone(prepared, case["id"])
        return self.module.well_formed_answer(prepared["evidence"])

    def test_offline_clarifications_do_not_consume_the_request_budget(self):
        answers = {case["question"]: self._answer_for(case) for case in self.isolation}
        provider = self._provider(answers)
        transport = self.module.BoundedTransport(provider, 10)
        entries = self.module.run_live(self.cases, self.index, transport, 700)
        clarified = [e for e in entries if e.get("status") == "needs_clarification"]
        skipped = [e for e in entries if e.get("skipped")]
        self.assertTrue(clarified, "a procedural multi-model case should clarify offline")
        self.assertTrue(skipped, "a case with no evidence should be skipped")
        # Only the cases that actually reached the transport are charged.
        charged = [
            e
            for e in entries
            if not e.get("skipped") and e.get("status") != "needs_clarification"
        ]
        self.assertEqual(transport.calls, len(charged))
        self.assertEqual(transport.calls, len(provider.seen))
        self.assertLess(transport.calls, len(self.cases))

    def test_generated_answer_and_its_sources_are_stored_for_review(self):
        answers = {case["question"]: self._answer_for(case) for case in self.isolation[:1]}
        transport = self.module.BoundedTransport(self._provider(answers), 10)
        entries = self.module.run_live(self.isolation[:1], self.index, transport, 700)
        entry = entries[0]
        self.assertEqual(entry["status"], "generated")
        self.assertEqual(entry["source_validation"], "accepted")
        self.assertIn("[S1]", entry["answer"])
        self.assertTrue(entry["sources"])
        self.assertEqual(entry["sources"][0]["source_id"], "S1")
        self.assertTrue(entry["presented_sources"])
        self.assertNotIn("configured-app-key", json.dumps(entry))

    def test_missing_usage_is_unknown_not_zero(self):
        answers = {case["question"]: self._answer_for(case) for case in self.isolation[:2]}
        transport = self.module.BoundedTransport(self._provider(answers), 10)
        entries = self.module.run_live(self.isolation[:2], self.index, transport, 700)
        for entry in entries:
            self.assertFalse(entry["usage_reported"])
            self.assertIsNone(entry["usage"])
        summary = self.module.summarise(entries)
        self.assertEqual(summary["cases_with_reported_usage"], 0)
        self.assertEqual(summary["cases_with_unknown_usage"], len(entries))
        self.assertIsNone(summary["usage_totals"])

    def test_reported_usage_is_totalled(self):
        answers = {case["question"]: self._answer_for(case) for case in self.isolation[:1]}
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        transport = self.module.BoundedTransport(self._provider(answers, usage=usage), 10)
        entries = self.module.run_live(self.isolation[:1], self.index, transport, 700)
        summary = self.module.summarise(entries)
        self.assertEqual(summary["cases_with_reported_usage"], 1)
        self.assertEqual(summary["usage_totals"]["total_tokens"], 120)

    def test_provider_failures_are_recorded_and_counted(self):
        transport = self.module.BoundedTransport(self._provider(fail=True), 10)
        entries = self.module.run_live(self.isolation[:2], self.index, transport, 700)
        self.assertTrue(all(entry["status"] == "error" for entry in entries))
        summary = self.module.summarise(entries)
        self.assertEqual(summary["status_counts"].get("error"), 2)

    def test_the_run_stops_at_the_request_cap(self):
        answers = {case["question"]: self._answer_for(case) for case in self.isolation}
        transport = self.module.BoundedTransport(self._provider(answers), 1)
        entries = self.module.run_live(self.isolation[:3], self.index, transport, 700)
        self.assertEqual(transport.calls, 1)
        statuses = [entry["status"] for entry in entries]
        self.assertEqual(statuses[0], "generated")
        self.assertEqual(statuses[1], "error")
        self.assertIn("cap", entries[1]["detail"])

    def test_a_cited_passage_must_be_one_that_was_presented(self):
        transport = self.module.BoundedTransport(
            lambda messages, **kwargs: 'Use "clean washer cycle" [S9].', 10
        )
        entries = self.module.run_live(self.isolation[:1], self.index, transport, 700)
        self.assertEqual(entries[0]["status"], "rejected_citations")
        self.assertEqual(entries[0]["source_validation"], "not_accepted")
        self.assertIsNone(entries[0]["answer"])


if __name__ == "__main__":
    unittest.main()
