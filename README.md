# Home Appliance Manual Assistant

A local, single-household assistant for the manuals of the appliances you actually own.
Save an appliance (say "kitchen dishwasher" on a Bosch SHEM63W55N), ask a question, and get
the passages from that exact manual, each linked to the real PDF page it came from. Writing a
short answer with DeepSeek is optional, off by default, and never leaves the house without a
key you configure yourself.

- Loopback only: the server binds `127.0.0.1` and refuses any other host.
- One household, one computer: saved appliances live in a local SQLite file. No accounts, no
  uploads, no URL fetching, no addresses, no analytics.
- Search mode is fully offline. It quotes the manual; it does not rewrite it.
- If you turn answer writing on, every sentence must cite a passage **and** quote words that
  appear word-for-word in it, or the answer is discarded and the passages are shown instead.

## Requirements

- Python 3.10+
- `pypdf` (`pip install -r requirements.txt`)

No web framework, no database server, no model download. The Codex bundled runtime already
includes pypdf:

```powershell
C:\Users\jamie\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

## Quick start

```powershell
# 1. Get the official manuals (needs internet access; the app itself never does)
python scripts/acquire_manuals.py      # re-verifies existing files, downloads what is missing

# 2. Turn the PDFs into page-aware passages
python scripts/build_index.py

# 3. Run it
python app.py                          # http://127.0.0.1:8099
```

`python app.py --rebuild-index` rebuilds the index at startup; `--port` and `--open` change
the port or open a browser. The server refuses any host other than `127.0.0.1`, `localhost`
or `::1`.

Questions can be linked directly, and search-only mode needs no configuration at all:

```
http://127.0.0.1:8099/?q=How+do+I+clean+the+lint+filter&category=dryer
http://127.0.0.1:8099/?q=How+do+I+clean+the+filter&model=SHEM63W55N
http://127.0.0.1:8099/?q=How+do+I+clean+the+filter&appliance=app_1a2b3c4d5e6f   # a saved appliance
```

## My home

The **My home** panel lets you name an appliance, pick its appliance type and its exact curated
model, and save it. You can rename it, remove it, or click the check icon to make it the active
appliance for the next question.

- Records are stored in `data/household.db` (SQLite) on this computer and survive a restart.
- The model picker only offers curated models of manuals that are actually in the library, so
  an unsupported model is refused with a plain message instead of being mapped to a different
  appliance's manual.
- Selecting a saved appliance binds its appliance type and model before the search runs, so
  every passage comes from that manual. If the library later stops holding that model (a
  manual was removed), the saved appliance is marked and asking with it fails explicitly.
- Removing a saved appliance deletes only the record. Source PDFs are never touched.

## What the answer check proves, and what it does not

Set `HOME_MANUAL_ENABLE_GENERATION=1` and `DEEPSEEK_API_KEY` (see `.env.example`) to let the
app write a short answer from the retrieved passages. When it does, every sentence or list
item must:

1. carry a `[S#]` marker that belongs to a passage really retrieved for that question, and
2. quote words in double quotes that appear **word-for-word** inside one of the passages it
   cites.

Anything else is discarded with a stated reason, and the quoted passages are shown instead.
A verbatim quote can still be misapplied, so this validates the citations and the quotes, not
whether a sentence is a faithful reading. The UI says the same thing next to any answer.

Two narrower rules on top of that:

- A procedure or a fault question is answered from **one** model's manual. If the evidence
  would come from more than one manual, the app asks which model instead of merging steps.
- The prompt treats the passages as data: text addressed to an AI ("ignore your instructions")
  is never obeyed. Quoting ordinary appliance instructions is expected.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOME_MANUAL_ENABLE_GENERATION` | unset (off) | Opt in to written answers |
| `DEEPSEEK_API_KEY` | unset | Your own key; read from the environment only |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Provider; must be HTTPS |
| `HOME_MANUAL_GENERATION_MAX_REQUESTS` | `60` | Requests this process will make |
| `HOME_MANUAL_GENERATION_MAX_CONCURRENCY` | `2` | Answers written at once |
| `HOME_MANUAL_GENERATION_MAX_CONTEXT_CHARS` | `12000` | Evidence text sent per request |
| `HOME_MANUAL_HOUSEHOLD_DB` | `data/household.db` | Where saved appliances live |

```powershell
$env:HOME_MANUAL_ENABLE_GENERATION = "1"
$env:DEEPSEEK_API_KEY = "<your key>"     # never stored in this project, never logged
python app.py
```

The generation guards are in-process only. They bound what one running app will send; they are
not a durable billing cap, and restarting the app resets the counter. The provider URL must be
HTTPS and redirects are refused, so the key cannot be replayed to another host. The key is
never written to the project, never returned by `/api/meta`, and never printed.

## Verifying it

```powershell
python -m unittest discover -s tests -t .    # 176 tests
python scripts/run_eval.py --set dev         # 36-case development set  -> eval/results.json
python scripts/run_eval.py --set fresh       # 20-case frozen fresh set -> eval/fresh_results.json
python scripts/run_generation_eval.py        # offline fixture run of the answer pipeline
python scripts/run_generation_eval.py --live # only with a key configured; bounded request cap
```

Both question sets are author-created and are **not** an independent or production benchmark.
Every `grounded` label records the document and PDF page that were read in the generated index,
and results are reported per metric with its own denominator:

| Metric | Development set | Frozen fresh set |
| --- | --- | --- |
| Cases passed | 36/36 | 20/20 |
| Grounded: expected document in the returned passages | 20/20 | 9/9 |
| Grounded: exact expected PDF page in returned passages | 19/20 | 9/9 |
| Grounded: expected PDF page within +/- 1 | 20/20 | 9/9 |
| Grounded: top-1 document accuracy | 15/20 (75%) | 9/9 |
| Abstention accuracy | 3/3 | 2/2 |
| Clarification accuracy | 3/3 | 2/2 |
| Model-isolation accuracy | 5/5 | 4/4 |
| Unknown-model accuracy | 4/4 | 2/2 |

Numbers, not adjectives: top-1 document accuracy is the one metric that is not perfect, and it
is reported as measured. `scripts/run_generation_eval.py` runs the real validation pipeline
against scripted answers by default (no network, no cost) and refuses a `--live` run unless a
key is configured; live runs are hard-capped, write to a separate results file, and store reviewable answers and source references without credentials.

## Model identity rules

Model numbers are never guessed. A document's models come from one of two recorded bases, and
`data/manuals_manifest.json` says which applies to each manual:

- `pdf_text` - the model string is printed inside the downloaded PDF.
- `manufacturer_page` - the manual is series-based or does not print the model; the model is
  linked from the manufacturer's own product page, recorded as `source_page_url`.

The app matches a model only when its normalised form equals a curated alias for that document.
There is no prefix, substring or part-number matching, so a near-miss such as `GTW335FAKE`
resolves to nothing. Unknown models are refused for every kind of question, a model selected in
the UI must agree with any model named in the question, and fault-style questions ("will not
spin", "error code", "leaking") ask for the model instead of answering from another appliance's
manual.

## Local service hardening

Every endpoint that writes or can spend money is same-origin, JSON-only, and guarded before the
request body is read:

- the `Host` header must be a loopback host with the port actually being served;
- the `Origin`, when present, must match the scheme/host/port really serving the request - not
  merely "some localhost";
- `Sec-Fetch-Site`, when present, must be `same-origin` or `none`;
- the body must be `application/json` and within a fixed size, and every field is type-checked.

No CORS headers are sent. Manual PDFs are served from inside `data/manuals` only, with a
containment check on the resolved path.

## Index freshness and blank pages

The saved index stores a fingerprint of the manifest (document ids, hashes, page counts,
curated aliases, schema version). If a manual is added, removed or changed, the index is rebuilt
instead of serving stale passages; passages for removed documents or pages outside a document's
range are dropped and reported. Pages with no extractable text are listed per document in
`/api/meta` and in the UI.

## What is in the library

`data/manuals_manifest.json` is the machine-checked record of what was downloaded: official PDF
URL, manufacturer support page, curated models, model basis, page count, byte size, SHA256 and
retrieval date. `data/source_registry.json` is the curated intent, including blocked sources,
and `data/acquisition_report.json` records the raw result of every attempt. The app never
invents a page: cards show the **PDF page** used by the link and, separately, the **printed
page** when one could be read reliably.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | Local launcher |
| `src/homemanual/` | Config, manifest, ingestion, BM25 index, retrieval, generation, household store, HTTP server, UI |
| `scripts/acquire_manuals.py` | Development-time downloader/verifier (network) |
| `scripts/build_index.py` | PDF -> page-aware passages |
| `scripts/run_eval.py` | Retrieval evaluation (dev and frozen fresh sets) |
| `scripts/run_generation_eval.py` | Offline fixture run of the answer pipeline; gated live mode |
| `tests/` | Unit and integration tests |
| `eval/` | Question sets, measured results, generation fixtures |
| `data/` | Registry, manifest, PDFs, acquisition log, search index, local household database |
| `docs/agent-work/` | Plan, checkpoint and reports for each work bundle |
| `docs/portfolio/` | Architecture and interview guide for this project |

## Limits

- Extraction is imperfect: diagrams, symbols and some printed model lists are not interpreted,
  a few pages extract as substituted-font gibberish, and some pages carry no extractable text.
- Eight of the twelve manuals are series-based or print their model as a shorthand, so the model
  is linked from the manufacturer's product page; the basis is recorded per document.
- Retrieval is deliberately conservative: abstaining beats answering from an unrelated manual,
  so broad or unusual questions return `no_evidence`.
- Answers cover what the downloaded manuals say. They are not a substitute for a licensed
  technician, and nothing here claims a procedure is safe for every situation.
- The downloaded manuals are copyrighted manufacturer documents and are git-ignored. Do not
  publish or commit them.
- The paid answer path has never been run against the live provider in this project: the key
  was not configured, so live generated quality is unverified. What is tested is the pipeline
  around it, with injected transports.

## How this was built

This project was built with AI assistance: a GPT-6 Astra agent planned and reviewed the work,
and a DeepSeek V4.1 Flash worker implemented it in this checkout, with a separate read-only
review pass. The design decisions, retrieval rules, validation rules and the manual library are
described in `docs/portfolio/ARCHITECTURE.md`; `docs/portfolio/INTERVIEW_GUIDE.md` is a short
walkthrough and demo script. No claim of independent authorship is made.
