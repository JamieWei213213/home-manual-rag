"""Shared helpers for the test suite."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from homemanual.registry import ManualDoc  # noqa: E402


def make_doc(doc_id: str, brand: str, category: str, models: list[str], path: Path | None = None) -> ManualDoc:
    return ManualDoc(
        doc_id=doc_id,
        title=f"{brand} {category} manual",
        brand=brand,
        category=category,
        models=tuple(models),
        doc_type="owner_manual",
        source_page_url=f"https://example.invalid/{doc_id}",
        pdf_url=f"https://example.invalid/{doc_id}.pdf",
        local_path=path or (ROOT / "data" / "manuals" / f"{doc_id}.pdf"),
        sha256="",
        bytes=0,
        pages=10,
        retrieved_at="2026-10-07T00:00:00+00:00",
    )


def make_chunk(doc_id: str, page: int, text: str, section: str | None = None, printed: str | None = None) -> dict:
    return {
        "chunk_id": f"{doc_id}:p{page}:c0",
        "doc_id": doc_id,
        "pdf_page": page,
        "printed_page": printed,
        "section": section,
        "text": text,
        "word_count": len(text.split()),
    }


def sample_library():
    """A small synthetic library used by retrieval and server tests."""
    from homemanual.index import ManualIndex

    docs = [
        make_doc("alpha-washer", "Alpha", "washer", ["AW1000", "AW1000W"]),
        make_doc("beta-dryer", "Beta", "dryer", ["BD2000"]),
        make_doc("gamma-dishwasher", "Gamma", "dishwasher", ["GD3000"]),
    ]
    chunks = [
        make_chunk(
            "alpha-washer",
            12,
            "CLEAN WASHER CYCLE Use this cycle every 30 washes to clean the inside of the washer. "
            "Add affresh washer cleaner to the basket and run the cycle with hot water. "
            "Do not place laundry in the washer during the Clean Washer cycle.",
            "CLEAN WASHER CYCLE",
            printed="11",
        ),
        make_chunk(
            "alpha-washer",
            4,
            "LEVELING THE WASHER Level the washer by turning the front leveling legs clockwise "
            "to raise the washer and counterclockwise to lower it. Use a level across the top.",
            "INSTALLATION",
        ),
        make_chunk(
            "beta-dryer",
            9,
            "CLEANING THE LINT FILTER Remove the lint filter by pulling it straight up. "
            "Roll the lint off with your fingers and replace the filter before each load. "
            "A clogged lint filter increases drying time and can be a fire hazard. "
            "WARNING: Clean the lint filter before every load to reduce the risk of fire.",
            "CARE AND CLEANING",
        ),
        make_chunk(
            "beta-dryer",
            14,
            "TROUBLESHOOTING DRYER WILL NOT HEAT Check that the dryer is plugged into a "
            "working outlet and that the circuit breaker has not tripped. If the dryer is a "
            "gas dryer, confirm the gas supply valve is open before requesting service.",
            "TROUBLESHOOTING",
        ),
        make_chunk(
            "gamma-dishwasher",
            17,
            "CLEANING THE FILTER Remove the filter assembly by turning it counterclockwise. "
            "Rinse the filter under running water and reinstall it by turning clockwise.",
            "CARE AND CLEANING",
        ),
    ]
    return ManualIndex(docs=docs, chunks=chunks, detected_models={}, built_at="test")
