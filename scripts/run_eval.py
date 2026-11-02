#!/usr/bin/env python3
"""Run a manual-grounded evaluation set and write its measured results.

Two sets exist:

* ``--set dev``   - the original 36-case development set (``eval/questions.jsonl``).
* ``--set fresh`` - a 20-case set written on 2026-10-12 after the portfolio work,
  frozen before its first run (``eval/fresh_questions.jsonl``).

Both are author-created sets, not an independent or production benchmark. Every
``grounded`` label records the document and PDF page that were read in
``data/index/chunks.json``; nothing is tuned after the run to make a score
green, and failing cases stay visible in the results file.

Reported separately for the grounded cases, each with its own denominator:

* ``grounded_document_hit_at_k`` - the expected manual is in the returned passages
* ``grounded_exact_expected_page_hit`` - a passage from the expected manual is on
  exactly the expected page (tolerance 0)
* ``grounded_expected_page_hit_within_1`` - the same, allowing +/- 1 page
* ``grounded_top1_document_accuracy`` - the highest-scoring passage is from the
  expected manual

Pass/fail for a grounded case uses the +/- 1 page tolerance, which is the rule the
development set was written against; the exact-page number is reported next to it
so the tolerant figure is never presented as exact.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from homemanual.index import ManualIndex  # noqa: E402
from homemanual.retrieve import analyse  # noqa: E402

PAGE_TOLERANCE = 1
EXACT_PAGE_TOLERANCE = 0
SETS = {
    "dev": (ROOT / "eval" / "questions.jsonl", ROOT / "eval" / "results.json"),
    "fresh": (ROOT / "eval" / "fresh_questions.jsonl", ROOT / "eval" / "fresh_results.json"),
}


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def check(case: dict, result: dict) -> tuple[bool, dict]:
    expect = case["expect"]
    kind = expect["kind"]
    evidence = result.get("evidence", [])
    docs = [item["doc_id"] for item in evidence]
    detail = {
        "status": result["status"],
        "evidence": [
            {"doc_id": item["doc_id"], "pdf_page": item["pdf_page"], "score": item["score"]}
            for item in evidence
        ],
    }
    if kind == "grounded":
        # A question can be answered by more than one manual; every accepted
        # answer was read out of the downloaded text before being listed here.
        accepted = expect.get("answers") or [
            {"doc_id": expect["doc_id"], "page": expect["page"]}
        ]
        accepted_docs = {a["doc_id"] for a in accepted}
        doc_hit = any(item["doc_id"] in accepted_docs for item in evidence)
        def page_match(tolerance: int) -> bool:
            return any(
                item["doc_id"] == answer["doc_id"]
                and abs(item["pdf_page"] - answer["page"]) <= tolerance
                for answer in accepted
                for item in evidence
            )

        page_hit = page_match(PAGE_TOLERANCE)
        exact_page_hit = page_match(EXACT_PAGE_TOLERANCE)
        detail.update(
            {
                "accepted_answers": accepted,
                "doc_hit": doc_hit,
                "page_hit": page_hit,
                "exact_page_hit": exact_page_hit,
                "top_doc": docs[0] if docs else None,
                "top1_doc_hit": bool(docs) and docs[0] in accepted_docs,
            }
        )
        return (doc_hit and page_hit), detail
    if kind == "isolation":
        allowed = set(expect["doc_ids"])
        ok = bool(docs) and set(docs) <= allowed
        detail.update({"allowed_docs": sorted(allowed), "observed_docs": sorted(set(docs))})
        return ok, detail
    if kind == "clarify":
        detail["clarification"] = bool(result.get("clarification"))
        return result["status"] == "needs_clarification", detail
    if kind == "unknown_model":
        detail["unknown_models"] = result.get("unknown_models", [])
        return result["status"] == "unknown_model", detail
    if kind == "conflict":
        detail["status"] = result["status"]
        return result["status"] == "conflicting_models" and not evidence, detail
    if kind == "abstain":
        return result["status"] == "no_evidence" and not evidence, detail
    raise ValueError(f"unknown expectation kind: {kind}")


def _rate(hits: int, cases: int) -> dict:
    return {"hits": hits, "cases": cases, "rate": round(hits / cases, 4) if cases else 0.0}


def build_summary(cases: list[dict], results: list[dict]) -> dict:
    by_kind: dict[str, dict[str, int]] = {}
    passed = 0
    grounded = {"cases": 0, "doc_hit": 0, "page_hit": 0, "exact_page_hit": 0, "top1": 0}
    for case, entry in zip(cases, results):
        kind = case["expect"]["kind"]
        stats = by_kind.setdefault(kind, {"passed": 0, "total": 0})
        stats["total"] += 1
        if entry["passed"]:
            passed += 1
            stats["passed"] += 1
        detail = entry["detail"]
        if kind == "grounded":
            grounded["cases"] += 1
            grounded["doc_hit"] += 1 if detail.get("doc_hit") else 0
            grounded["page_hit"] += 1 if detail.get("page_hit") else 0
            grounded["exact_page_hit"] += 1 if detail.get("exact_page_hit") else 0
            grounded["top1"] += 1 if detail.get("top1_doc_hit") else 0
    total = len(cases)
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "by_kind": by_kind,
        "page_tolerance": PAGE_TOLERANCE,
        "exact_page_tolerance": EXACT_PAGE_TOLERANCE,
        "metrics": {
            "grounded_document_hit_at_k": _rate(grounded["doc_hit"], grounded["cases"]),
            "grounded_exact_expected_page_hit": _rate(
                grounded["exact_page_hit"], grounded["cases"]
            ),
            "grounded_expected_page_hit_within_1": _rate(
                grounded["page_hit"], grounded["cases"]
            ),
            "grounded_top1_document_accuracy": _rate(grounded["top1"], grounded["cases"]),
            "abstention_accuracy": _rate(
                by_kind.get("abstain", {}).get("passed", 0), by_kind.get("abstain", {}).get("total", 0)
            ),
            "clarification_accuracy": _rate(
                by_kind.get("clarify", {}).get("passed", 0), by_kind.get("clarify", {}).get("total", 0)
            ),
            "model_isolation_accuracy": _rate(
                by_kind.get("isolation", {}).get("passed", 0),
                by_kind.get("isolation", {}).get("total", 0),
            ),
            "unknown_model_accuracy": _rate(
                by_kind.get("unknown_model", {}).get("passed", 0),
                by_kind.get("unknown_model", {}).get("total", 0),
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a manual-grounded evaluation set.")
    parser.add_argument(
        "--set",
        dest="which",
        choices=sorted(SETS),
        default="dev",
        help="which frozen question set to run (default: dev)",
    )
    args = parser.parse_args(argv)
    questions_path, results_path = SETS[args.which]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

    index = ManualIndex.load()
    cases = load_cases(questions_path)
    results: list[dict] = []
    for case in cases:
        filters = case.get("filters", {})
        result = analyse(
            index,
            case["question"],
            category=filters.get("category"),
            model=filters.get("model"),
            doc_id=filters.get("doc_id"),
        )
        ok, detail = check(case, result)
        results.append(
            {
                "id": case["id"],
                "question": case["question"],
                "filters": filters,
                "expect": case["expect"],
                "passed": ok,
                "detail": detail,
            }
        )
        top = detail.get("evidence", [{}])[0] if detail.get("evidence") else {}
        print(
            f"[{'PASS' if ok else 'FAIL'}] {case['id']:<5} {case['question'][:52]:<52} "
            f"status={detail['status']:<19} top={top.get('doc_id', '-')} p{top.get('pdf_page', '-')}"
        )

    summary = build_summary(cases, results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "set": args.which,
        "questions_file": str(questions_path.relative_to(ROOT)),
        "index_built_at": index.built_at,
        "documents": index.document_ids(),
        "summary": summary,
        "results": results,
    }
    results_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\n{summary['passed']}/{summary['cases']} cases passed ({summary['pass_rate']:.0%})")
    for kind, stats in sorted(summary["by_kind"].items()):
        print(f"  {kind:<14} {stats['passed']}/{stats['total']}")
    for name, metric in summary["metrics"].items():
        print(f"  {name:<38} {metric['hits']}/{metric['cases']}")
    print(f"written: {results_path}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
