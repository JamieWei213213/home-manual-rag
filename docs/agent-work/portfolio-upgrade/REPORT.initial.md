# Portfolio upgrade: usable local Home Manual RAG - implementation report

STATUS: ready_for_review

Task: `/root/portfolio_upgrade` (contract: `docs/agent-work/portfolio-upgrade/PLAN.md`)
Workspace: `C:\Users\jamie\OneDrive\Desktop\Home Manual`
Executed by the assigned implementation worker. No commits, pushes, deploys, production
migrations, global settings, credential handling, or paid inference. `PLAN.md` and
`CHECKPOINT.md` were not touched; `resume/` was not touched.

Running instance: `http://127.0.0.1:8099/`, PID 34088, loopback only
(`Get-NetTCPConnection -LocalPort 8099` -> `127.0.0.1:8099` owning PID 34088).
Stop it with `Stop-Process -Id 34088`.

## What changed

### 1. My home - persistent appliances (`src/homemanual/household.py`, new)

SQLite at `data/household.db` (override `HOME_MANUAL_HOUSEHOLD_DB`). One table, idempotent
`CREATE TABLE IF NOT EXISTS` migration wrapped in `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`.
Random ids (`app_` + 12 hex chars) with a uniqueness retry; per-write transaction; every
statement parameterised. Nicknames are whitespace-collapsed, 1-40 characters, control-character
free; a cap of 60 appliances. A model is accepted only when it is an exact curated alias of a
manual in the current library **for the chosen category**, so an unknown or wrong-category
model is refused rather than mapped to another appliance's manual. Records that stop resolving
(a manual removed later) are annotated `supported: false` and fail explicitly on use.

### 2. Guarded write endpoints (`src/homemanual/server.py`)

`POST /api/appliances/add|rename|remove` join `POST /api/ask` behind one guard chain that runs
before the body is read: loopback `Host` including the serving port, same-origin `Origin`
(scheme/host/port compared against the real request), `Sec-Fetch-Site` of `same-origin`/`none`,
`application/json` only, and a 32 KiB body limit with whole-body JSON-object validation.
`/api/ask` now accepts `appliance_id`, which binds category+model before retrieval;
`unknown_appliance` and `unsupported_appliance` are returned as explicit result statuses.
`/api/meta` reports the saved appliances and the storage note; `/api/health` reports the count.

### 3. Answering (`src/homemanual/generate.py`, `src/homemanual/retrieve.py`)

- Passages are flagged `has_warning` (WARNING/CAUTION/DANGER) in retrieval, marked in the
  prompt, and labelled on the card.
- `build_messages()` names the selected model when there is one, so the exact model reaches the
  prompt even when the question omits it; context is capped by
  `HOME_MANUAL_GENERATION_MAX_CONTEXT_CHARS` (per-passage truncation included).
- The prompt now requires a verbatim double-quoted excerpt per claim, requires safety notices
  from cited passages to be carried over, and explicitly rejects instructions addressed to an
  AI while still allowing appliance operating instructions to be quoted.
- `validate_citations()` verifies, per claim: a `[S#]` id that was really retrieved, and a
  quoted excerpt that appears word-for-word in one of the passages that claim cites. A missing
  quote or a quote that does not match the cited passage is rejected with a stated reason.
- A procedure or fault question whose evidence spans more than one manual returns
  `needs_clarification` instead of merging models.
- `http_transport()` refuses any non-HTTPS provider URL and uses an opener that raises on
  redirects, so the key cannot be replayed to another host. It can capture provider usage
  metadata (never the key or the body).
- `GenerationLimiter` bounds in-process request count and concurrency; `/api/ask` consults it
  before the transport and reports `rate_limited` / `busy`.

### 4. UI (`src/homemanual/static/`)

New **My home** panel (name / type / exact model / save, then a list with use-rename-remove icon
buttons and honest "no matching manual" state), the ask panel gained a saved-appliance selector
that disables and binds the appliance/model selects, and results now show the answer mode, the
supporting pages and any generation error. The unused `innerHTML` path was removed from `el()`,
so all user and model text renders through `textContent`/`createTextNode`. Direct question URLs
(`?q=`, `?category=`, `?model=`) still work and `?appliance=` was added. One responsive defect
found during QA (a 15 px horizontal overflow on a 390 px viewport caused by an unbreakable
token stretching a grid track) was fixed with `minmax(0, 1fr)` plus `min-width: 0` and
`overflow-wrap`.

### 5. Evaluation (`eval/`, `scripts/run_eval.py`, `scripts/run_generation_eval.py`)

- `eval/questions.jsonl` (36 dev cases) and its content are unchanged; the pre-portfolio copy of
  both it and the old `results.json` remains in
  `backups/2026-10-12-before-portfolio-upgrade/eval/`.
- `eval/fresh_questions.jsonl` is new: 20 author-created cases written and frozen before their
  first run, each grounded label carrying `provenance` with the document, PDF page and the
  verbatim excerpt read out of `data/index/chunks.json`. Composition: 9 grounded, 4 exact-model
  isolation, 2 unknown-model, 2 clarification, 2 abstention, 1 conflict.
- `scripts/run_eval.py` gained `--set dev|fresh` and separate metrics: grounded document hit@k,
  strict expected-page hit (+/- 1), top-1 document accuracy, abstention accuracy, clarification
  accuracy, each with its own denominator; results go to `eval/results.json` /
  `eval/fresh_results.json`.
- `scripts/run_generation_eval.py` is new: offline scripted-answer fixtures by default
  (accept / uncited reject / unquoted reject / insufficient), writing
  `eval/generation_results_mock.json`; `--live` requires a configured key, is hard-capped at 10
  requests (the only standing authorization), and writes to a separate
  `eval/generation_results_live.json` with redacted metadata only.

### 6. Docs

`README.md` rewritten (quick start, My home, the exact answer-check semantics and its limit,
configuration table, `.env.example`, hardening, measured metrics table, limits, ownership).
New `.env.example`, `docs/portfolio/ARCHITECTURE.md` and `docs/portfolio/INTERVIEW_GUIDE.md`
(pipeline explanation, two-minute demo script, honest Q&A, an explicit "do not claim" list).
`.gitignore` now excludes `data/household.db*`, `.env*` (keeping `.env.example`) and `backups/`.

## Verification

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `python -m unittest discover -s tests -t .` | 0 | `Ran 145 tests ... OK` (was 90 before this bundle; +55 tests) |
| 2 | `python scripts/run_eval.py --set dev` | 0 | `36/36 cases passed`; grounded doc hit@k 20/20, strict page hit 20/20, top-1 doc 15/20 (75%), abstain 3/3, clarify 3/3, isolation 5/5, unknown 4/4, conflict 1/1 |
| 3 | `python scripts/run_eval.py --set fresh` | 0 | `20/20 cases passed`; grounded doc hit@k 9/9, strict page hit 9/9, top-1 doc 9/9, abstain 2/2, clarify 2/2, isolation 4/4, unknown 2/2, conflict 1/1 |
| 4 | `python scripts/run_generation_eval.py` | 0 | 13 cases with evidence, 0 fixture mismatches: generated 3, rejected_citations 6, insufficient_evidence 1, needs_clarification 3 |
| 5 | `python scripts/run_generation_eval.py --live` | 2 | Refused: `generation is disabled`. Zero paid requests issued. |
| 6 | `python scripts/run_generation_eval.py --live --max-requests 25` | 2 | Refused: authorization is at most 10 live requests |
| 7 | Live API probes on the running app (`/api/health`, `/api/meta`, appliance add/ask/rename/remove, unknown-model add) | 200/201/400 | 12 documents, 2714 passages, generation disabled; add -> `app_...` bound to `bosch-shem63w5xn-9001238146`; ask with the appliance -> 6 passages, all from that manual, `filters.model = SHEM63W55N`; carburetor question -> `no_evidence`; unknown model -> HTTP 400 |
| 8 | Process restart (`Stop-Process` then `python app.py` again) | 0 | Startup line `my home : 1 saved appliance(s)` - appliances survive a restart |

Full per-case results are in `eval/results.json`, `eval/fresh_results.json` and
`eval/generation_results_mock.json`.

## Browser and visual QA

The Codex in-app browser was available this turn (hidden tab, `iab` id `2`); earlier phases had
to fall back to headless Chrome. Screenshots are viewport captures from that tab.

| File | Shows |
| --- | --- |
| `evidence/screenshots/06-desktop-my-home.png` | My home with one saved appliance, the bound ask panel (type and model disabled), 1265 px viewport |
| `evidence/screenshots/07-mobile-results.png` | 390x844: single column, bound state, six result cards, `scrollWidth == clientWidth == 375` after the overflow fix |
| `evidence/screenshots/08-desktop-direct-question.png` | `?q=...&category=dryer` deep link: "6 passage(s) found. Scope: Dryer.", generation toggle hidden |
| `evidence/screenshots/09-desktop-unknown-model.png` | `ZZ9000XX` question: refusal banner, no cards, 12 curated suggestions |

Interactive checks performed in the browser: saving an appliance, the saved appliance appearing
in the ask selector after a reload, asking with it, deep-link search, unknown-model guidance, and
removing the saved appliance through the confirm dialog (status: "Removed. The manual PDFs were
not touched."). `evidence/screenshots/01-06*.png|jpg` are the previous phase's artifacts and
show the pre-upgrade UI; they were left in place rather than deleted.

## Live vs mocked generation

`DEEPSEEK_API_KEY` is not configured in this environment and the generation flag is unset, so
**no live request was made to any provider and no cost was incurred**. Every generation result
above comes from injected transports. Live generated quality therefore remains unverified, and
the harness that would measure it is in place, capped, and self-refusing without a key. The
strict verbatim-quote rule is likely to reject a meaningful share of live answers; that
rejection rate is unknown and is one of the things a live run should measure.

## Metrics and provenance

Test count: 90 -> 145. Fresh-set grounding: every grounded label's `provenance.excerpt` was
asserted to appear in the indexed text for that document and PDF page
(`tests/test_eval_sets.py::FreshSetGroundingTests`), and unknown-model cases were asserted to
navigate to no document in the library. Top-1 document accuracy on the development set is
unchanged at 15/20 (75%) and is reported as measured rather than described as success.
The frozen fresh set scores perfectly on a 20-case author-created set, which is a small sample;
it is not an independent benchmark.

## Commit / integration state

No Git repository exists in this workspace; nothing was staged, committed, pushed or deployed.
`data/household.db` exists and is empty (the QA appliance was removed through the UI); it is
git-ignored. Source PDFs, `data/index/chunks.json`, `data/manuals_manifest.json`,
`data/source_registry.json` and `data/acquisition_report.json` are unchanged.

## Outstanding risks and decisions for Astra

1. **Live generation is unverified.** Decide whether to authorize the bounded live run now that
   the harness exists (it needs an explicitly configured `DEEPSEEK_API_KEY`; the app key is
   absent here).
2. **Strict quote validation may be too strict in practice.** It is the honest reading of the
   plan ("validate each cited ID and exact supporting excerpt"), but it will discard answers
   that are correct yet paraphrased. Worth measuring before the resume claims an answer-writing
   capability.
3. **Lexical retrieval caps top-1 at 75% on the dev set.** Unchanged by this bundle; embeddings
   or reranking would be the next retrieval step and is out of this scope.
4. **The pre-portfolio screenshots (01-06) now show an older UI.** They belong to the earlier
   bundle's report; consider whether they should be relabelled.
5. `retrieve._model_options` is kept as a one-line alias of the new `retrieve.model_options`
   for the existing call sites; it can be dropped in a tidy-up.
6. `resume/Jamie_Wei_Resume.tex` is root-owned and untouched. If the resume entry is updated,
   it should describe only the measured features above (local SQLite household store, exact
   model isolation, evidence-bound quote validation, 145 tests, two author-created question
   sets) and must not claim live generated quality.

## Next checkpoint

Implementation is complete. Waiting on Astra's batched acceptance review (specification
compliance plus quality/security). Nothing further is in flight; the local app remains running
at `http://127.0.0.1:8099/` (PID 34088) for review.
