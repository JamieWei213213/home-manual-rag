# Final portfolio upgrade acceptance report

2026-10-12: Accepted as a local portfolio prototype. This report supersedes REPORT.initial.md, retained as worker handoff history.

Implemented SQLite appliance profiles, exact-model retrieval binding, optional DeepSeek generation with quotation/citation checks, bounded request handling, and authored retrieval/generation evaluations. Original PDFs preserved. No commit, push or deployment.

## Verified evidence
- Final full suite: 176 tests pass; evidence/portfolio-tests-final.txt.
- Development: 36 cases, expected document top-six 20/20 grounded cases; exact page 19/20; tolerant page plus/minus one 20/20; first document 15/20.
- Fresh authored set: 20 cases; 9/9 grounded document, exact-page and top-one checks. These are small author-created evaluations, not an independent benchmark.
- Mock generation: 13 exercised cases, 0 mismatches, 7 skipped; zero provider requests. Injected responses do not establish live model quality.
- Root browser check: saved-appliance unbinding resets model/category; search returns six page-linked passages; phone viewport 390x844 and desktop reviewed. Screenshots: evidence/screenshots/portfolio-final-mobile.png and portfolio-final-desktop.png. Mobile screenshot contains task-created QA record, subsequently removed.
- Final local health: 12 documents, 2714 chunks, generation disabled, zero saved appliances. App available at http://127.0.0.1:8099/.
- Resume Home Manual entry updated; other entries preserved. Built-in LaTeX compiler succeeded. Final PDF layout not visually reviewed.

## Review and limitations
GPT-6 Astra planned, reviewed and completed final integration. Native DeepSeek V4.1 Flash implementation worker and separate DeepSeek reviewer ran. Upstream provider identity was not independently audited. Root completed final integration after incomplete correction handoffs. Quote validation now checks only text actually presented; arbitrary short colon-terminated instructions cannot bypass it. Same-origin checks, numeric quotation fidelity, stale appliance recovery and exact-page metrics were corrected.

User authorized at most 10 live DeepSeek calls, each maximum 700 output tokens. Application key is absent: zero live calls made. Do not use Router credentials. Live generated-answer quality remains unverified. Citation validation is not semantic entailment proof. Local prototype is neither deployed nor validated with real users. One existing index warning reports two Bosch pages without extractable text.
