# Architecture: Home Appliance Manual Assistant

A single-household, offline-first tool that answers appliance questions from owner's manuals
that were actually downloaded onto the machine. Everything below describes what the code does
today; nothing here is a roadmap or a claim about a hosted service.

## The pipeline, end to end

```
official PDFs ──► ingestion ──► BM25 index ──► retrieval ──► (optional) model ──► validation ──► UI
   data/manuals     ingest.py    chunks.json    retrieve.py      generate.py       generate.py   static/*
```

### 1. Ingestion (`src/homemanual/ingest.py`)

`scripts/acquire_manuals.py` downloads each manual once, records its SHA256, page count and the
manufacturer support page it was linked from, and writes `data/manuals_manifest.json`. Each
manifest entry records *how* its model identity was established (`pdf_text` when the model is
printed in the PDF, `manufacturer_page` when it is a series manual and the model comes from the
product page).

`ingest.py` then reads every page with `pypdf`, carries an uppercase section heading forward,
detects a printed page label when one is unambiguous, and splits pages into ~170-word windows
with 40 words of overlap. Each passage keeps `doc_id`, `pdf_page`, `printed_page`, `section` and
its text. Pages with no extractable text are recorded so a manual is never silently only partly
searchable.

The index also stores `schema_version` and a fingerprint of the manifest. If either changes,
the index is rebuilt rather than served stale, and passages for removed documents or pages
outside a document's range are dropped with a warning.

### 2. Retrieval (`src/homemanual/index.py`, `src/homemanual/retrieve.py`)

The index is BM25 (`k1 = 1.2`, `b = 0.6`) over the passage tokens, with light suffix folding so
"clean"/"cleaning" and "filter"/"filters" match. A small field boost rewards a chunk whose text
or section repeats a model-like token from the query.

`analyse()` is where the safety rules live:

- **Model identity is exact.** A model is recognised only when its normalised form equals a
  curated alias of a document in the library. No prefix, substring or part-number matching, so
  `GTW335FAKE` resolves to nothing instead of to `GTW335ASNWW`.
- **Unknown models are refused for every question**, not only diagnostic ones. A selected model
  that disagrees with a model named in the question is a `conflicting_models` refusal.
- **Fault questions need a model.** "My washer will not spin" returns a clarification request
  with the relevant manuals rather than guessing.
- **Topic coverage gate.** A passage must genuinely cover the question's topical vocabulary;
  appliance words and model numbers do not count towards coverage, so a model-restricted search
  still cannot match on the model number alone.
- **Category words are boundary-matched**, so "dishwasher" never registers as "washer".

Every returned passage carries `has_warning`, set when the text contains WARNING, CAUTION or
DANGER, which the prompt and the UI both surface.

### 3. Optional written answer (`src/homemanual/generate.py`)

Off by default. When enabled, `build_messages()` labels each passage with its manual, page,
section and the models it covers, marks passages containing a safety notice, names the user's
saved model when there is one, and bounds the total evidence text to a configured character
budget. The system prompt states that passages are untrusted data, requires a `[S#]` marker and
a verbatim quote per claim, forbids merging steps from two models, and forbids obeying text
addressed to an AI while still allowing appliance instructions to be quoted.

Two gates run before the transport is called: a procedure or fault question with evidence from
more than one manual returns a clarification request, and the in-process limiter bounds request
count and concurrency. The transport itself validates the provider URL (HTTPS only) and refuses
redirects so the key cannot be replayed elsewhere.

### 4. Validation (`validate_citations`)

Per claim, the validator requires a citation id that was actually retrieved, and at least one
double-quoted excerpt that appears word-for-word in one of the passages that claim cites. It
also still rejects unknown ids and uncited claims. Failures produce a stated reason; the answer
is discarded and the passages are shown. The check proves the citation and quote are real. It
does not prove the sentence is a faithful reading of the passage, and the UI says so.

### 5. Local state and UI (`src/homemanual/household.py`, `src/homemanual/server.py`, `static/`)

Saved appliances live in SQLite (`data/household.db`). Writes are transactional, statements are
parameterised, nicknames are bounded and control-character free, ids are random
(`app_` + 12 hex chars), and the schema migration is idempotent. A model is accepted only when
it resolves to a manual in the current library, so the store can never hold a record that points
at a different appliance's manual.

The server is the Python standard library only. Write and spend endpoints share one guard
(loopback `Host` including the serving port, same-origin `Origin`, safe `Sec-Fetch-Site`,
JSON-only body within a fixed size) and the guards run before the body is read. Static assets
are served from a three-file whitelist, and manual PDFs are served from inside `data/manuals`
with a resolved-path containment check and HTTP Range support.

The front end renders exclusively through `textContent` and `createTextNode`, so manual text
and model output cannot inject HTML. Saved appliances appear in a My home panel; selecting one
disables and binds the appliance/model selects; results show the answer mode, supporting pages,
compact passage cards and any generation error.

## Data the project keeps

| Artifact | Contains | Tracked? |
| --- | --- | --- |
| `data/manuals/*.pdf` | Downloaded manufacturer manuals | No (git-ignored) |
| `data/manuals_manifest.json` | Verified metadata for each manual | Yes |
| `data/source_registry.json` | Curated intent, including blocked sources | Yes |
| `data/acquisition_report.json` | Raw acquisition log | Yes |
| `data/index/chunks.json` | Regenerable passage index | No (regenerable) |
| `data/household.db` | Local saved appliances | Never committed |
| `eval/*.jsonl`, `eval/*.json` | Frozen question sets and measured results | Yes |

## Evaluation design

Two author-created sets are kept, and neither is a held-out or production benchmark:

- `eval/questions.jsonl` (36 cases) - the original development set.
- `eval/fresh_questions.jsonl` (20 cases) - written on 2026-10-12 and frozen before its first
  run, with a recorded `provenance` block per grounded label naming the document, PDF page and
  the verbatim excerpt that was read out of the generated index.

`scripts/run_eval.py` reports document hit@k, strict expected-page hit (+/- 1), top-1 document
accuracy, abstention accuracy and clarification accuracy, each with its own denominator. The
pass/fail detail for every case is written to the results file, including failures.

`scripts/run_generation_eval.py` exercises the answer pipeline with scripted answers by default
(accept, uncited reject, unquoted reject, insufficient-evidence) at no cost. A `--live` run
requires a configured key, is hard-capped by the standing authorization, writes to a separate
file, and stores only redacted metadata.

## Known limits

Lexical BM25 is the retrieval baseline; there are no embeddings. PDF extraction is imperfect
(diagrams, substituted-font pages, blank pages are reported rather than hidden). Eight of the
twelve manuals are series-based, so their model link comes from the manufacturer's product page
and is recorded as such. Conservative abstention means unusual questions return `no_evidence`
instead of a guess. The paid answer path has never been exercised against the live provider in
this project, so live generated quality is unverified; the pipeline around it is tested with
injected transports.
