# Interview guide: Home Appliance Manual Assistant

Use this to explain the project in plain language and to demo it in about two minutes. Every
number here was measured in this checkout; where something is unverified, it says so.

## The two-minute demo

1. **Start it.** `python app.py` then open `http://127.0.0.1:8099`. Say: "It's a local server
   with no accounts and no uploads; it binds to loopback only."
2. **Show the library.** Point at the Source library panel: "Twelve official manuals across five
   appliance types, each with its PDF URL, SHA256 and the manufacturer support page it came
   from. Two Bosch pages have no extractable text and the app says so rather than hiding it."
3. **Save an appliance.** In My home, name it "Kitchen dishwasher", pick Dishwasher and
   `SHEM63W55N`, click Save. Say: "That's SQLite on this machine; it survives a restart. The
   model list is the curated alias list, so I can't save a model I don't have a manual for."
4. **Ask.** With the appliance selected, ask "How do I clean the filter?" Point at the Scope
   line: "Six passages, all from the Bosch manual - the appliance bound the model before the
   search ran." Open one passage and click through to the PDF page.
5. **Show a refusal.** Ask "How do I clean the filter on ZZ9000XX?" Say: "Unknown model, so it
   refuses instead of answering from a different dishwasher's manual." Then ask "My washer will
   not spin" with no model selected to show the clarification prompt.
6. **Close with the boundary.** Say: "Search is fully offline. The DeepSeek answer writing is
   off by default and I have not run it against the live provider, so I'd describe generated
   quality as unverified; what is tested is the pipeline around it."

## How to explain it in one breath

"I built a local retrieval app over twelve official appliance manuals. It ingests PDFs into
page-aware passages, ranks them with BM25, and filters retrieval by exact curated model aliases to reduce cross-model errors. You can save the appliances you own to a local SQLite
file, and the app binds that exact model before searching. There's an optional DeepSeek answer
writer that has to cite a passage and quote it word-for-word or the answer is thrown away."

## Questions you should expect

**"Walk me through the pipeline."**
Ingestion: `pypdf` reads each page, carries the section heading forward, tracks the printed page
label, and splits into ~170-word overlapping windows. Retrieval: BM25 over those passages with
light suffix folding, plus a field boost when a model-like token from the question appears in
the passage. Then the safety rules: exact model aliases only, unknown models refused for every
question, fault questions require a model, and a topic-coverage gate so a passage has to be
about the question and not just mention the model number. Then an optional generation step, then
validation, then the UI.

**"What makes this more than a search box?"**
Three things. Model isolation with curated aliases and no fuzzy matching; a local household store
that binds an appliance to one manual before the search; and a generation path whose output is
rejected unless every sentence cites a retrieved passage and quotes words that appear verbatim in
it.

**"How do you know it works?"**
176 unit and integration tests, plus two frozen question sets: 36 development cases and 20
fresh cases written after the portfolio work and frozen before their first run. On the fresh set
all 20 pass, with 9/9 grounded questions finding the expected document and page and 9/9 top-1
document accuracy. On the development set, 36/36 pass but top-1 document accuracy is 15/20
(75%) - that's the number I quote, because it's the real one.

**"Why is top-1 only 75% on the dev set?"**
Because BM25 is lexical and several manuals share vocabulary - three dryers all talk about lint
filters. The expected manual is in the returned passages in 20/20 grounded cases, but it isn't
always ranked first. The fix would be embeddings or reranking; I chose to keep the baseline
simple and report the number honestly instead.

**"What stops it from hallucinating?"**
Several layers. Model identity is exact-match only. Diagnostic questions need a model. The prompt
requires a citation and a verbatim quote for every sentence, and the validator throws away any
answer that breaks either rule or cites a passage that wasn't retrieved. The write error is shown
in the UI rather than replaced with invented text.

**"But a quote can still be used dishonestly?"**
Yes - a verbatim quote can be misapplied. The check proves the citation and the quote are real;
it doesn't prove the sentence is a faithful reading. That's stated in the README, in the code
comments and next to the answer in the UI. I don't claim semantic verification.

**"Is it safe to run?"**
It's loopback only and refuses any other host. The endpoints that write or can spend money
require a loopback `Host` with the serving port, a same-origin `Origin`, safe `Sec-Fetch-Site`
and a JSON body under a size limit - all before the body is read. No CORS headers. The key comes
from the environment, is never written to the project, never returned by the API and never
logged; the provider URL must be HTTPS and redirects are refused.

**"What's the database?"**
SQLite, one file per installation. Parameterised statements, transactional writes with rollback,
an idempotent schema migration, random `app_...` ids, bounded nicknames and a cap on how many
appliances you can save. There's a test that a nickname like `x"; DROP TABLE appliances;--` is
stored literally and the table survives.

**"What would you do next?"**
Embeddings plus a reranker to lift top-1 accuracy, then a real held-out set with answer-quality
judging rather than page-hit metrics. I'd also want to run the paid path live with a small budget
and measure how often the strict quote rule rejects otherwise-useful answers.

**"What did you personally do?"**
Be careful and accurate here. The implementation was AI-assisted: a planner agent wrote the
brief and reviewed the result, and an implementation worker wrote the code in this checkout under
that brief, with a separate read-only review pass. Your contribution is the direction, the
constraints (no paid calls, no deployment, honest metrics), the acceptance decisions, and the
manual verification - not hand-written code. Say that plainly.

## Things not to claim

- Do not claim the paid answer writing has been validated live. It has not.
- Do not call either question set a benchmark, a held-out set, or independent evaluation.
- Do not claim semantic grounding, safety certification, or that answers are always correct.
- Do not claim embeddings, reranking, a hosted service, users, or any cost saving.
- Do not present the 75% top-1 figure as anything other than what it is: a small author-created
  development set.

Final verification on 2026-10-12: 176 tests passed; all 56 retrieval cases passed their stated criteria. On the development set, exact expected-page hit was 19/20, while within-one-page hit was 20/20. These are author-created sets, not independent benchmarks. No live model request has been made; only the source-validation pipeline is tested.
