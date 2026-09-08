# LLM-paying jobs need attempt caps at claim time

- **Date**: 2026-08-14T04:17:38+0800
- **Type**: lesson

## What happened

2026-08-13 arq incident: slow DeepSeek responses made parse_candidate_cv time out at the 300s arq budget. CandidateDocument had no attempts column, so rescan_stuck re-enqueued each CV every 15 min — each loop billing up to several model calls (salary + career x2 passes + client transport retries x3 + no-content retry). 77 TimeoutErrors + 20 CancelledErrors in one 10-min window, jobs delayed 600-712s. Root causes: (1) no attempt bound on the one LLM-paying table lacking it, (2) extract_cv escalated TimeoutError into the expensive strong pass, (3) ExtractedSalary.confidence was non-nullable and the model answers null for a missing salary, failing the whole extraction. Fixes: added attempts column + CV_PARSE_MAX_ATTEMPTS=3 spent in the conditional claim UPDATE (both parse_candidate_cv and ingest_candidate_cv), park at failed when exhausted; TimeoutError now breaks instead of escalating; confidence is float|None + JSON schema nullable. Tests: test_document_past_attempt_ceiling_is_failed_not_retried, test_an_ingest_past_the_attempt_ceiling_is_failed_not_retried, salary null-confidence tests.

## Root cause / fix

Any LLM-paying job table must count attempts at pickup in the same conditional claim UPDATE (mirror CandidateImport.attempts) — a worker killed mid-call never reaches completion, so counting at the end bounds nothing. And when auditing an LLM cost bomb, check: does the stuck-sweep re-enqueue path have a ceiling? does a timeout escalate into a more expensive model? does the schema tolerate the model's literal null answers?
