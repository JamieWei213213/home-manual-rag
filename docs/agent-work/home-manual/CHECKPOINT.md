# Checkpoint

## Current state
2026-10-07: first implementation delivered; acceptance pending one consolidated correction cycle. Empty initial workspace, no Git repository, no user files replaced. Local prototype running at http://127.0.0.1:8099 (worker will restart after fixes). Twelve official PDFs acquired across all five categories, 2714 indexed passages. Initial suite: 70 tests. Initial saved eval: 31/31 criteria passed, expected document/page within top six; top-one document 13/20 (65%), correcting worker's initial 70% statement.

## Workers and routing
Implementation: /root/build_home_manual, native astra_flash_builder role pinned to deepseek/deepseek-v4.1-flash. Independent read-only review: /root/independent_check, router_deepseek_deepseek_v4_1_flash role; completed and interrupted per lifecycle requirement. Root doctor observed gpt-6-astra and configured DeepSeek API worker route. Native child roles executed useful work; provider request/usage metadata was not exposed, so upstream inference routing is not independently certified.

## Accepted decisions
Python stdlib loopback server is acceptable instead of preferred Flask. GE/Samsung/Bosch/Panasonic collection acceptable; additional brands not required. No paid application inference, deployment, commits, or pushes. Generated answers optional and unverified live; offline mode is evidence search.

## Review findings sent together
Unknown-model general-query leakage, unsafe prefix/heuristic model matching, dishwasher category substring collision, weak model-restricted abstention, sentence-level citation gaps, prompt data/instruction separation, cross-origin paid-generation request protection, robust path containment, cache freshness/document binding, unreadable-page disclosure, hidden generation-toggle CSS, full model selector, misleading grounding claims and source/metric reporting discrepancies. Independent review verified PDF hashes and five evaluation target pages. Same worker owns corrections and verification; root must not overlap edits.

## Acceptance after corrections
Accepted as a local manual-search prototype with an optional, live-unverified generation path. Worker completed one correction cycle and was interrupted after completion. Corrected suite reports 90 passing tests; current saved evaluation verified by root: 36/36 criteria passed, top-one document 15/20 (75%). This is a small development set, not a held-out accuracy estimate or proof of semantic answer fidelity.

Root inspected corrected retrieval/generation/server/cache behavior and updated desktop screenshot. Live independent spot checks passed: unknown general-question model rejected; made-up model suffix rejected; model-selected carburetor query abstained; unfiltered dishwasher question only returned dishwasher evidence; foreign-origin POST rejected 403; uncited trailing sentence rejected. Health confirms 12 documents, 2714 passages, generation disabled. App opening requested in Codex and queued by host. Current app PID 436, URL http://127.0.0.1:8099/.

## Remaining limitations and next steps
DeepSeek app generation requires explicit local configuration and has not been exercised against a live API; citation syntax checks do not verify semantic support. Lexical search is the current baseline; no vector embeddings. Some extracted text is noisy; pages without text are disclosed. Model-token heuristic can confuse part numbers with unknown models. Post-correction desktop QA exists; narrow-screen QA was verified only before corrections, not repeated afterward. Some section labels are heuristic and can be noisy. No publication/deployment/commit performed. Native configured worker roles completed tasks; upstream provider request metadata remains unavailable, so no provider-routing certification is claimed.

Next user-facing action: try local evidence search. Future work can improve retrieval and held-out evaluation, then configure and evaluate generation if requested. No further implementation is active.
