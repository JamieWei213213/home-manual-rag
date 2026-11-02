from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual.ingest import IngestError, ingest_document, read_pdf_pages  # noqa: E402
from tests.support import make_doc  # noqa: E402


def write_pdf(path: Path, pages: list[str]) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=letter)
    for text in pages:
        y = 740
        for line in text.splitlines():
            pdf.drawString(60, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_ingest"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for item in self.tmp.glob("*"):
            item.unlink()
        self.tmp.rmdir()

    def test_pages_chunks_and_metadata(self):
        path = self.tmp / "alpha-washer.pdf"
        write_pdf(
            path,
            [
                "WASHER SAFETY\n"
                + " ".join(f"word{i}" for i in range(60))
                + "\n12",
                "CLEAN WASHER CYCLE\n"
                + " ".join(f"cleaning{i}" for i in range(80))
                + "\nWDF520PADM appears here and WDF520PADM again\n13",
            ],
        )
        doc = make_doc("alpha-washer", "Alpha", "washer", ["AW1000"], path=path)
        result = ingest_document(doc)
        self.assertEqual(result.pages, 2)
        self.assertGreaterEqual(len(result.chunks), 2)
        first = result.chunks[0]
        self.assertEqual(first.doc_id, "alpha-washer")
        self.assertEqual(first.pdf_page, 1)
        # Printed page 12 differs from the PDF page index (1) - both are kept.
        self.assertEqual(first.printed_page, "12")
        self.assertEqual(first.section, "WASHER SAFETY")
        second = [chunk for chunk in result.chunks if chunk.pdf_page == 2]
        self.assertTrue(second)
        self.assertIn("WDF520PADM", " ".join(chunk.text for chunk in second))
        self.assertEqual(second[0].printed_page, "13")
        # Tokens seen at least twice become candidate models; single mentions do not.
        self.assertIn("WDF520PADM", result.detected_models)
        self.assertNotIn("CLEANING0", result.detected_models)

    def test_short_pages_produce_no_chunks(self):
        path = self.tmp / "tiny.pdf"
        write_pdf(path, ["only three words"])
        doc = make_doc("tiny", "Alpha", "washer", ["AW1000"], path=path)
        result = ingest_document(doc)
        self.assertEqual(result.chunks, [])

    def test_corrupt_pdf_reports_plain_reason(self):
        path = self.tmp / "broken.pdf"
        path.write_bytes(b"this is not a pdf at all")
        with self.assertRaises(IngestError):
            read_pdf_pages(path)

    def test_empty_file_reports_plain_reason(self):
        path = self.tmp / "empty.pdf"
        path.write_bytes(b"")
        with self.assertRaises(IngestError):
            read_pdf_pages(path)

    def test_missing_file_reports_plain_reason(self):
        with self.assertRaises(IngestError) as context:
            read_pdf_pages(self.tmp / "nope.pdf")
        self.assertIn("not found", str(context.exception))


if __name__ == "__main__":
    unittest.main()
