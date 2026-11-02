# Home Appliance Manual Assistant - implementation report

STATUS: ready_for_review (correction cycle complete)

Task: `/root/build_home_manual` (bundle: `docs/agent-work/home-manual/PLAN.md`)
Workspace: `C:\Users\jamie\OneDrive\Desktop\Home Manual`
Implemented by one DeepSeek V4.1 Flash worker, local only. No commits, pushes, deploys,
production migrations, global settings or paid inference. `PLAN.md` untouched.

Running instance: `http://127.0.0.1:8099/` (PID 436, loopback only). Stop with
`Stop-Process -Id 436`.

## Round 2 - what the correction cycle changed

| # | Reported defect | Fix | Evidence |
| --- | --- | --- | --- |
| 1 | Model isolation: general questions could return other manuals; `model=GTW335FAKE` was accepted by prefix matching | `models_match` is now exact curated-alias equality only (no prefix, substring or part-number identity). `docs_matching_model` no longer uses heuristic text tokens for identity. Unknown model mentions are refused for **every** question, a selected model that disagrees with a model named in the question returns `conflicting_models`, and an empty document list now means "no documents" instead of "all documents". Curated exact aliases were added from existing manufacturer evidence (for example `GTW335ASNWW`, `RF23R6201SR/AA`, `DVE45T6000W/A3`). | `tests/test_textutil.py::test_models_match_is_exact_only`, `::test_models_match_rejects_lookalike_models`, `tests/test_retrieval.py::test_unknown_model_in_a_general_question_is_refused`, `::test_lookalike_model_filter_is_refused`, `::test_selected_model_conflicting_with_question_model_is_refused`, `tests/test_manifest.py::IndexFreshnessTests::test_empty_document_filter_searches_nothing`; live probes: `ABC99999` -> `unknown_model` (0 passages), `GTW335FAKE` -> `unknown_model` |
| 2 | `detect_category` used substrings, so "dishwasher" also matched "washer" and returned None | Category words are matched with word/phrase boundaries (plus an optional plural) | `tests/test_retrieval.py::test_dishwasher_question_is_not_confused_with_washer`; live probe: "How do I clean the dishwasher filter?" -> only `ge-gdt545psjss` and `bosch-...` passages; eval case `DW6` |
| 3 | Coverage gate accepted a single generic overlap when a model was selected | Topic coverage excludes model tokens and appliance words; the minimum coverage and the "at least two topic terms (or full coverage)" rule now apply to model-restricted searches too | `tests/test_retrieval.py::test_model_selected_question_still_needs_topic_overlap`; eval case `A3` ("How do I clean a carburetor?" with `GTW335ASNWW` -> `no_evidence`) |
| 4 | `validate_citations` accepted `Clean the filter [S1]. Bypass the door switch.` | Validation is now per sentence and per list item: every claim needs a citation, list markers are stripped first, and unknown ids are still rejected. The prompt now states the passages are untrusted data, requires the model label in the citing sentence, and forbids merging steps from different models. UI and README wording was rewritten to say the check validates citation ids, **not** that a sentence is true. | `tests/test_generate.py::test_uncited_trailing_claim_is_rejected`, `::test_uncited_list_item_is_rejected`, `::test_fully_cited_multi_sentence_answer_is_accepted`, `::test_prompt_contains_evidence_and_anti_fabrication_rules`; UI subtitle and the generated-answer disclaimer in `src/homemanual/static/{index.html,app.js}` |
| 5 | The optional paid endpoint could be triggered by a cross-site browser POST; `bool("false")` was accepted; path containment used string `startswith` | `/api/ask` now requires `Content-Type: application/json`, a loopback `Host` header matching the serving port, a non-null same-origin (or absent) `Origin`, and `Sec-Fetch-Site` of `same-origin`/`none`, all **before** the body is read. No CORS headers are sent. `generate` must be a real boolean. Containment uses `Path.is_relative_to`. | `tests/test_server.py::test_text_plain_body_is_refused`, `::test_cross_origin_post_is_refused_without_calling_the_model`, `::test_cross_site_fetch_metadata_is_refused`, `::test_foreign_host_header_is_refused`, `::test_generate_must_be_a_boolean`, `::test_same_origin_generation_still_works` (the last one also asserts the injected transport was never called for the rejected requests, and was called for the accepted one) |
| 6 | The saved index was never checked against the manifest; orphan/out-of-range passages survived; pages without text were invisible | The index stores a schema version plus a manifest fingerprint (ids, hashes, page counts, curated aliases). A mismatch rebuilds; passages for removed documents or pages outside the document range are dropped with a warning; pages with no extractable text are listed per document in `/api/meta`, in the library UI and in the startup warnings. | `tests/test_manifest.py::IndexFreshnessTests` (reused-when-fresh, manifest change, removed document, orphan + out-of-range, schema change, empty filter) and `::RealIndexTests::test_pages_without_extractable_text_are_reported`; live meta shows Bosch pages `[86, 87]` |
| 7 | `.toggle` overrode `[hidden]`, the model dropdown only offered `doc.models[0]`, and the library showed sha256 noise | `[hidden] { display: none !important }` plus defensive `max-width/min-width` on form controls; the dropdown lists **every** curated alias grouped per manual (`optgroup`); the library shows pages/downloaded date and a "Source details" section holding the id, sha256, curated models and the pages without text. | Asset assertions on the served files; screenshots `01-desktop-home.png` (no DeepSeek toggle visible), `06-desktop-model-selected.png` (`SHEM63W55N` selected, all six passages from the Bosch dishwasher manual) |
| 8 | Documentation honesty drift | The registry now states the two-basis model rule and per-entry `model_note`; the acquisition report explains historical attempts; this report and the README were regenerated from the actual measured results. | See "Sources" and "Verification" below; `data/source_registry.json` (v3), `data/acquisition_report.json` (`historical_attempts`) |

## Sources (12 official manuals, all five categories)

Manifest: `data/manuals_manifest.json`. Registry: `data/source_registry.json` (v3).
Acquisition log: `data/acquisition_report.json`. Total size **122.5 MiB** (128,399,347 bytes).

Model basis is recorded per document, and the manifest keeps the exact strings actually found
in the PDF text (`models_matched_in_text`):

| Brand | Category | doc_id | Pages | Basis | Model strings found in the PDF text |
| --- | --- | --- | --- | --- | --- |
| GE Appliances | washer | ge-gtw335asnww | 60 | pdf_text | GTW220, GTW325, GTW335, GTW385, GTW465, GTW480, GTW485, GTW490, GTW500, GTW525, GTW538, GTW540, HTW240, HTW265 |
| Samsung | washer | samsung-wa45t3200aw | 204 | pdf_text | WA3200T, WA45T3200AW, WA45T3200AW/A4 |
| GE Appliances | dryer | ge-gtd45easjws | 24 | manufacturer_page | (none printed) |
| GE Appliances | dryer | ge-gtd65ebsjws | 32 | manufacturer_page | (none printed) |
| Samsung | dryer | samsung-dve45t6000w | 56 | manufacturer_page | (cover prints the `DVE(G)45T6000*/6005*` shorthand) |
| Bosch | dishwasher | bosch-shem63w5xn-9001238146 | 88 | pdf_text | SHEM63W52N, SHEM63W55N, SHEM63W56N |
| GE Appliances | dishwasher | ge-gdt545psjss | 66 | manufacturer_page | GDT535-635, GDF500-620 (series ranges, kept verbatim) |
| GE Appliances | refrigerator | ge-gne25jsk | 156 | manufacturer_page | GNE25J, GNE25D, GNE27J, GFE26J, GDE25E |
| Samsung | refrigerator | samsung-rf23r6201sr | 264 | manufacturer_page | (none printed) |
| GE Appliances | microwave | ge-jes1095smss | 40 | manufacturer_page | (cover line extracts as "JES1095" merged with the next heading) |
| Panasonic | microwave | panasonic-nn-sn67hs | 28 | manufacturer_page | NN-SN65KB, NN-SN66KB |
| Samsung | microwave | samsung-ms14k6000as | 12 | pdf_text | MS14K6000AS, MS14K6000AS/AA, MW5100J |

4 documents are `pdf_text`, 8 are `manufacturer_page`. An earlier version of this report
claimed a per-model check in the PDF text for every entry; that was wrong for the
`manufacturer_page` documents, and the registry, manifest and report now say which basis
applies to each one.

### Blocked (10 registry entries, all recorded with reasons)

Whirlpool (5 categories), Maytag, Amana and KitchenAid share a platform that returns HTTP 403
to non-browser clients for product pages and `content/dam` PDFs alike; LG builds manual links
with JavaScript and its direct PDF endpoints return HTTP 404 (its Canadian `content/dam`
document is a one-page product sheet). All of these remain in the registry with
`disabled: true` and are listed in the UI.

### Historical attempts reconciled

`data/acquisition_report.json` now carries a `historical_attempts` list explaining entries
from earlier runs that are no longer in the registry: `bosch-shem63w55n` (superseded - the
candidate was a 3-page quick-start sheet, replaced by the 88-page manual for the same family),
`lg-dle7000w` (dropped - LG's manual endpoints 404; LG is disabled) and `amana-art104psm`
(dropped - Amana returns 403; the family is disabled).

## Verification (commands, exit status, results)

Bundled runtime:
`C:\Users\jamie\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `scripts/acquire_manuals.py --retries 2` (escalated, network) | 0 | 12 documents verified against the corrected alias set; all five categories present; each file re-hashed. One entry (GE microwave) was correctly re-classified from `pdf_text` to `manufacturer_page` after an exact-match check showed the model string is not printed in the PDF. |
| 2 | `scripts/build_index.py` | 0 | 12 manuals -> 2714 page-aware passages (washer 2, dryer 3, dishwasher 2, refrigerator 2, microwave 3). |
| 3 | `python -m unittest discover -s tests -t .` | 0 | `Ran 90 tests ... OK` (was 70 before this cycle). |
| 4 | `scripts/run_eval.py` | 0 | `36/36 cases passed (100%)` - 20 grounded, 5 isolation, 3 clarification, 4 unknown-model, 3 abstention, 1 conflict. Top-1 document accuracy on the 20 grounded cases: **15/20 (75%)**; every grounded case had the expected document and page (±1) inside the six returned passages. Exact per-case numbers: `eval/results.json`. |
| 5 | Live API probes (`/api/health`, `/api/meta`, `/api/ask`) | 200 | 12 documents, 2714 chunks, generation disabled; Bosch `pages_without_text = [86, 87]`; `ABC99999` -> `unknown_model` with 0 passages; `GTW335FAKE` -> `unknown_model`; dishwasher question -> dishwasher manuals only. |
| 6 | Headless Chrome renders of the running app (local Chrome, `--headless=new`) | 0 | Screenshots show the hidden DeepSeek toggle (generation off), the unknown-model error state with no evidence cards, the dryer results with page links, an explicit `SHEM63W55N` selection restricted to the Bosch manual, and the clarification state. Asset assertions on the served files confirm the `[hidden]` rule, the grouped dropdown code and the removal of the old "Nothing is invented" wording. |

## Browser and visual QA

The Codex in-app browser surface was not attached in this turn (`agent.browsers.list()`
returned empty after a fresh bootstrap), so this cycle's renders come from the locally
installed Chrome in headless mode against the loopback app.

| File | Shows |
| --- | --- |
| `evidence/screenshots/01-desktop-home.png` | Home page: no DeepSeek toggle visible, per-category chips, library with pages/downloaded date, "Source details", Bosch "2 page(s) with no extractable text" |
| `evidence/screenshots/02-desktop-results.png` | Dryer question: "6 passage(s) found. Scope: Dryer." with PDF page links |
| `evidence/screenshots/04-desktop-unknown-model.png` | `ABC99999` question: refusal banner, no evidence cards, library models offered |
| `evidence/screenshots/05-desktop-clarification.png` | Diagnostic question without a model: clarification prompt |
| `evidence/screenshots/06-desktop-model-selected.png` | `SHEM63W55N` selected: every passage from the Bosch dishwasher manual |
| `evidence/screenshots/03-mobile-viewport.jpg` | Mobile layout from the previous in-app-browser pass (390x844, `scrollWidth == clientWidth == 375`) |
| `evidence/screenshots/05-manual-page.jpg` | Manual PDF opened at the cited page from the previous in-app-browser pass |

Two honest gaps in this cycle's browser evidence:

1. Headless Chrome on this host ignored `--window-size` for layout in the reused profile, so
   the narrow-viewport screenshots it produced were cropped rather than truly 390 px wide.
   They were discarded rather than presented as mobile proof; the mobile layout evidence is
   the earlier in-app-browser measurement, and the responsive CSS changes since then are
   additive (`[hidden]` rule, `max-width/min-width` on controls).
2. The real interactive checklist (clicking through the toggle and dropdown in a live
   browser) could not be repeated without the in-app browser surface; the equivalent checks
   were done through the served assets, `/api/meta` and the headless renders above.

## Limits and decisions

- **Whirlpool family and LG are absent by force.** They need a browser-driven acquisition
  mode; that is a new scope decision, not a defect in this bundle.
- **Eight manuals are series-based or shorthand-printing**, so their model link comes from the
  manufacturer's product page. Recorded per document with a note; the app never resolves a
  model to a manual that does not claim it.
- **Extraction noise is visible in passages** (repeated headers/footers, a few pages with
  substituted-font gibberish) and some pages have no extractable text at all. Both are
  reported rather than hidden; diagrams are not interpreted.
- **Retrieval is deliberately conservative.** Abstaining beats answering from an unrelated
  manual, so unusual questions return `no_evidence`.
- **No production surface**: loopback only, no accounts, no uploads, no URL ingestion, no
  analytics, no CORS, no secrets in the repo or logs, no paid inference performed.

## Reproducing everything

```powershell
python scripts/acquire_manuals.py            # network; writes data/manuals/*.pdf, manifest, acquisition log
python scripts/build_index.py                # writes data/index/chunks.json
python -m unittest discover -s tests -t .    # 90 tests
python scripts/run_eval.py                   # 36 cases -> eval/results.json
python app.py                                # http://127.0.0.1:8099
```

Index regeneration is required after any manifest change; the app also detects a stale index
by fingerprint and rebuilds automatically.
