#!/usr/bin/env python3
"""Exercise the optional written-answer pipeline on the frozen question set.

Default mode is fully offline and deterministic. It runs the real pipeline
(retrieval -> context cap -> prompt -> validation) with a fixture transport that
returns scripted answers, so the accept/reject behaviour can be checked without
any network call or cost:

* a well-formed answer (citation plus a verbatim quote) must be accepted;
* an answer with no citation must be rejected;
* an answer with a citation but no quote must be rejected;
* the model's own INSUFFICIENT_EVIDENCE reply must be respected.

``--live`` calls the real provider instead. It refuses to run unless a key is
configured and the generation flag is on, it is bounded by ``--max-requests``
(hard-capped at 10 here because that is the only authorization on file) and
``--max-tokens`` (hard-capped at 700), and it writes to a separate results file
that holds the generated text and the sources it was shown so a person can read
it and judge the answer. Answers are never fabricated: a case that fails is
recorded with the validator's reason.

What acceptance means: the answer's citations and quotes were verified against
the passages actually presented. It is **not** a statement that the answer is
correct or complete. A human has to read it for that.

Nothing secret is stored: no API key, no request headers, and no provider URL
containing credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.generate import (  # noqa: E402
    GenerationError,
    generate_answer,
    http_transport,
    present_evidence,
)
from homemanual.index import ManualIndex  # noqa: E402
from homemanual.retrieve import analyse, is_procedural  # noqa: E402

FRESH_PATH = ROOT / "eval" / "fresh_questions.jsonl"
MOCK_RESULTS_PATH = ROOT / "eval" / "generation_results_mock.json"
LIVE_RESULTS_PATH = ROOT / "eval" / "generation_results_live.json"
AUTHORIZED_MAX_REQUESTS = 10
AUTHORIZED_MAX_TOKENS = 700


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for line in FRESH_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


# ------------------------------------------------------------------ settings
def build_mock_settings() -> dict:
    """Offline fixture. No key is real and no provider is contacted."""
    return {
        "generation_state": {
            "status": "enabled",
            "detail": "offline fixture - no provider call",
        },
        "api_key": "fixture-only",
        "model": "fixture-transport",
        "base_url": "https://fixture.invalid",
        "timeout": 5,
    }


def build_live_settings(capture: dict, max_tokens: int) -> dict:
    """Real settings: the configured app key and the real generation state."""
    return {
        "generation_state": config.generation_state(),
        "api_key": config.deepseek_api_key() or "",
        "model": config.deepseek_model(),
        "base_url": config.deepseek_base_url(),
        "timeout": config.GENERATION_TIMEOUT_SECONDS,
        "max_tokens": max_tokens,
        "capture": capture,
    }


class BoundedTransport:
    """Counts real provider calls and refuses to exceed the cap.

    The count lives here, around the actual transport, so a question that is
    answered offline (a clarification, or no evidence at all) does not consume
    any of the request budget.
    """

    def __init__(self, inner, max_requests: int) -> None:
        self.inner = inner
        self.max_requests = max_requests
        self.calls = 0

    def __call__(self, messages, **kwargs):
        if self.calls >= self.max_requests:
            raise GenerationError(
                f"the run cap of {self.max_requests} live request(s) is reached; "
                "no further request was sent"
            )
        self.calls += 1
        return self.inner(messages, **kwargs)


# ------------------------------------------------------------------- helpers
def well_formed_answer(evidence: list[dict]) -> str:
    """A scripted answer that quotes the top *presented* passage word-for-word."""
    presented = present_evidence(evidence) or evidence
    words = (presented[0].get("text") or "").split()
    quote = " ".join(words[:8])
    return f'The manual describes this as "{quote}" for this appliance [S1].'


def fixture_answer(index: int, evidence: list[dict]) -> tuple[str, str]:
    """Return (scripted answer, expected validator status)."""
    behaviour = index % 4
    if behaviour == 0:
        return "Just run it on the normal setting.", "rejected_citations"
    if behaviour == 1:
        return "Run the normal cycle [S1].", "rejected_citations"
    if behaviour == 2:
        return "INSUFFICIENT_EVIDENCE", "insufficient_evidence"
    return well_formed_answer(evidence), "generated"


def prepare(case: dict, index: ManualIndex) -> dict | None:
    """Retrieve for one case. None when there is nothing to write from."""
    filters = case.get("filters", {})
    analysis = analyse(
        index,
        case["question"],
        category=filters.get("category"),
        model=filters.get("model"),
    )
    if analysis["status"] != "ok":
        return None
    evidence = analysis["evidence"]
    selected_model = filters.get("model")
    evidence_docs = sorted({item["doc_id"] for item in evidence})
    single_doc_required = bool(selected_model) or is_procedural(case["question"])
    return {
        "evidence": evidence,
        "selected_model": selected_model,
        "evidence_docs": evidence_docs,
        "single_doc_required": single_doc_required,
        "will_clarify": single_doc_required and len(evidence_docs) > 1,
        "retrieval_status": analysis["status"],
    }


def source_digest(result: dict, evidence: list[dict]) -> list[dict]:
    """The passages the model was actually shown, for manual review."""
    presented_ids = set(result.get("presented_sources") or [])
    digest: list[dict] = []
    for item in evidence:
        if item["source_id"] not in presented_ids:
            continue
        digest.append(
            {
                "source_id": item["source_id"],
                "doc_id": item["doc_id"],
                "pdf_page": item["pdf_page"],
                "has_warning": bool(item.get("has_warning")),
                "excerpt": " ".join((item.get("text") or "").split())[:240],
            }
        )
    return digest


def entry_for(case: dict, prepared: dict, result: dict, capture: dict | None = None) -> dict:
    usage = (capture or {}).get("usage")
    reported = isinstance(usage, dict)
    entry = {
        "id": case["id"],
        "question": case["question"],
        "selected_model": prepared["selected_model"],
        "single_doc_required": prepared["single_doc_required"],
        "evidence_docs": prepared["evidence_docs"],
        "presented_sources": result.get("presented_sources", []),
        "sources": source_digest(result, prepared["evidence"]),
        "status": result["status"],
        "detail": result.get("detail", ""),
        "problems": result["problems"],
        "source_validation": "accepted" if result["status"] == "generated" else "not_accepted",
        "answer": result["answer"],
        "usage": usage if reported else None,
        "usage_reported": reported,
    }
    return entry


def skipped_entry(case: dict, reason: str) -> dict:
    return {"id": case["id"], "question": case["question"], "skipped": True, "reason": reason}


# ---------------------------------------------------------------- runs
def run_mock(cases: list[dict], index: ManualIndex) -> tuple[list[dict], int]:
    entries: list[dict] = []
    mismatches = 0
    for position, case in enumerate(cases):
        prepared = prepare(case, index)
        if prepared is None:
            entries.append(skipped_entry(case, "retrieval returned no evidence to write from"))
            continue
        if prepared["will_clarify"]:
            scripted, expected = "", "needs_clarification"
        else:
            scripted, expected = fixture_answer(position, prepared["evidence"])

        def transport(messages, _scripted=scripted, **kwargs):  # noqa: ANN001
            return _scripted

        result = generate_answer(
            case["question"],
            prepared["evidence"],
            transport=transport,
            settings=build_mock_settings(),
            selected_model=prepared["selected_model"],
            single_doc_required=prepared["single_doc_required"],
        )
        entry = entry_for(case, prepared, result)
        if result["status"] != expected:
            entry["expected_status"] = expected
            entry["fixture_mismatch"] = True
            mismatches += 1
        entries.append(entry)
        flag = "BAD" if entry.get("fixture_mismatch") else "OK "
        print(f"[{flag}] {case['id']:<5} status={result['status']:<20} expected={expected}")
    return entries, mismatches


def run_live(
    cases: list[dict],
    index: ManualIndex,
    transport: BoundedTransport,
    max_tokens: int,
) -> list[dict]:
    entries: list[dict] = []
    for case in cases:
        prepared = prepare(case, index)
        if prepared is None:
            entries.append(skipped_entry(case, "retrieval returned no evidence to write from"))
            print(f"[-- ] {case['id']:<5} skipped (no evidence)")
            continue
        capture: dict = {}
        result = generate_answer(
            case["question"],
            prepared["evidence"],
            transport=transport,
            settings=build_live_settings(capture, max_tokens),
            selected_model=prepared["selected_model"],
            single_doc_required=prepared["single_doc_required"],
        )
        entry = entry_for(case, prepared, result, capture)
        entries.append(entry)
        print(
            f"[{result['status']:<20}] {case['id']:<5} "
            f"validation={entry['source_validation']} usage={'yes' if entry['usage_reported'] else 'unknown'}"
        )
    return entries


def summarise(entries: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.get("skipped"):
            counts["skipped"] = counts.get("skipped", 0) + 1
        else:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    reported = 0
    unknown = 0
    for entry in entries:
        if entry.get("skipped"):
            continue
        if not entry.get("usage_reported"):
            unknown += 1
            continue
        reported += 1
        usage = entry["usage"] or {}
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return {
        "status_counts": counts,
        "cases_with_reported_usage": reported,
        "cases_with_unknown_usage": unknown,
        "usage_totals": totals if reported else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the real provider (costs money)")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=AUTHORIZED_MAX_REQUESTS,
        help=f"live request cap, 1..{AUTHORIZED_MAX_REQUESTS} (default {AUTHORIZED_MAX_REQUESTS})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=AUTHORIZED_MAX_TOKENS,
        help=f"output token cap per request, 1..{AUTHORIZED_MAX_TOKENS} (default {AUTHORIZED_MAX_TOKENS})",
    )
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

    if args.live:
        if not 1 <= args.max_requests <= AUTHORIZED_MAX_REQUESTS:
            print(
                f"refusing --max-requests {args.max_requests}: it must be between 1 and "
                f"{AUTHORIZED_MAX_REQUESTS} (the only authorization on file)."
            )
            return 2
        if not 1 <= args.max_tokens <= AUTHORIZED_MAX_TOKENS:
            print(
                f"refusing --max-tokens {args.max_tokens}: it must be between 1 and "
                f"{AUTHORIZED_MAX_TOKENS}."
            )
            return 2
        state = config.generation_state()
        if state["status"] != "enabled":
            print(f"refusing --live: generation is {state['status']} - {state['detail']}")
            return 2

    index = ManualIndex.load()
    cases = load_cases()

    if args.live:
        transport = BoundedTransport(http_transport, args.max_requests)
        entries = run_live(cases, index, transport, args.max_tokens)
        summary = summarise(entries)
        failed = summary["status_counts"].get("error", 0)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "live",
            "model": config.deepseek_model(),
            "endpoint": config.provider_endpoint_label(config.deepseek_base_url()),
            "requests_attempted": transport.calls,
            "request_cap": args.max_requests,
            "max_tokens": args.max_tokens,
            "failed": failed,
            "summary": summary,
            "acceptance_means": (
                "citations and quotes verified against the presented passages; "
                "it does not mean the answer is correct"
            ),
            "redaction": "no API key, request headers, or credential-bearing URL is stored",
            "cases": len(cases),
            "results": entries,
        }
        target = LIVE_RESULTS_PATH
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nlive requests attempted: {transport.calls}/{args.max_requests}")
        print(f"status counts: {summary['status_counts']}")
        print(f"failed (transport errors): {failed}")
        print(
            f"usage: {summary['cases_with_reported_usage']} reported, "
            f"{summary['cases_with_unknown_usage']} unknown"
        )
        print(f"written: {target}")
        return 1 if failed else 0

    entries, mismatches = run_mock(cases, index)
    summary = summarise(entries)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "mocked",
        "model": "fixture-transport",
        "requests_attempted": 0,
        "fixture_mismatches": mismatches,
        "summary": summary,
        "note": "Fully offline fixture run: no provider was contacted and no cost was incurred.",
        "cases": len(cases),
        "results": entries,
    }
    MOCK_RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nfixture mismatches: {mismatches}")
    print(f"status counts: {summary['status_counts']}")
    print(f"written: {MOCK_RESULTS_PATH}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
