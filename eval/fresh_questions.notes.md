# Frozen fresh set: annotation record

`fresh_questions.jsonl` was written and frozen before its first run on 2026-10-12.
Every `grounded` label records the document, the **PDF page** used by the app's
`page` expectation, the **extracted printed page** when the PDF prints one, and the
verbatim excerpt that was read out of `data/index/chunks.json`.

## Correction applied 2026-10-12 (notes only)

The first version of the `provenance.source` notes described the recorded number as a
"printed page". That was wrong: the number in `expect.page` is always the **PDF page**.
The affected notes were corrected and a `printed_page` field was added to each
provenance block.

| Case | Recorded page | What the note said | What is true |
| --- | --- | --- | --- |
| FG1 | 13 | "printed page 13" | PDF page 13; no printed label extracted |
| FG2 | 14 | "printed page 14" | PDF page 14; no printed label extracted |
| FG3 | 12 | "printed page 12" | PDF page 12, which does print 12 |
| FG4 | 24 | "printed page 24" | PDF page 24; the page itself prints **70** |
| FG5 | 24 and 7 | "p24" / "p7" | PDF page 24 prints 70; PDF page 7 prints 5 |
| FG6 | 9 | "printed page 9" | PDF page 9; no printed label extracted |
| FG7 | 37 | "printed page 37" | PDF page 37; no printed label extracted |
| FG8 | 36 | "printed page 36" | PDF page 36; no printed label extracted |
| FG9 | 36 | "printed page 36" | PDF page 36; no printed label extracted |

Questions, filters and every `expect.doc_id` / `expect.page` / `expect.answers` value
are unchanged. No expectation was adjusted to improve a score, before or after the
correction; the correction touches note text only.

The printed-page values above were read from the generated index with:

```powershell
python -c "import json,pathlib; p=json.loads(pathlib.Path('data/index/chunks.json').read_text(encoding='utf-8')); print({(c['doc_id'],c['pdf_page']):c['printed_page'] for c in p['chunks'] if c['doc_id']=='bosch-shem63w5xn-9001238146' and c['pdf_page'] in (12,24)})"
```

`tests/test_eval_sets.py` asserts that every provenance excerpt still appears in the
indexed text for its recorded document and PDF page, so a note cannot drift away from
the passage it describes.
