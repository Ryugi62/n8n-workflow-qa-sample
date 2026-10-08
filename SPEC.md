# SPEC — n8n Workflow QA Sample

## Purpose
A public, reproducible sample of a **48-hour written workflow audit**: one n8n workflow
goes in, a written report comes out (findings ranked by severity, before/after metrics,
fixes, runbook). Anyone can re-run every number in the report from this repository.

The target workflow (`workflow/lead-intake.json`) is fictional and was written for this
sample. It has the common shape *Webhook -> LLM classification -> Google Sheets row*.
It contains no real customer data and no real credentials (the one "key" is a fake
placeholder that exists so the secret scanner has something to find).

## Ubiquitous language
| Term | Meaning (same name in code) |
|---|---|
| Workflow / Node / Connection | The n8n export JSON, parsed into domain objects (`Workflow`, `Node`) |
| Finding | One problem the audit reports: rule id, severity, node, evidence, fix |
| Severity | `critical` > `high` > `medium` > `low` |
| Rule | A pure function `Workflow -> list[Finding]` |
| Delivery | One webhook POST received. A **redelivery** repeats an earlier event id |
| Event | A unique business event (one lead). Several deliveries can carry one event |
| Scenario | Burst parameters: events, duplicate ratio, LLM failure ratio, seed, runs |
| Pipeline config | How the simulated workflow behaves: retries, back-off, dedupe, alerting |
| Recorded exactly once | An event that ends up as exactly one row in the sheet |
| Silent loss | An event with zero rows and no alert sent |

## Success criteria (numeric)
1. Static audit of `workflow/lead-intake.json` reports **>= 6 distinct rule ids**, including
   at least one `critical`.
2. Static audit of `workflow/lead-intake.fixed.json` reports **0 critical and 0 high**.
3. Stress simulation is deterministic: same scenario + seed -> byte-identical metrics.
4. Scenario `50 events, 10% redelivered, 5% LLM failure, 20 runs`:
   the *after* config yields **0 duplicate rows and 0 silent losses**.
   Scenario B (same, but 30% LLM failure) exercises the dead-letter path: *after* yields
   **0 silent losses and > 0 alerted**.
5. Test suite: **>= 20 tests**, all passing, total runtime **< 5 s**, Python 3.9+ stdlib only
   (pytest for tests).
6. Every number in `report/sample-report.md` is produced by `python -m n8nqa report`
   (the committed report equals freshly generated output; a test checks this).
7. Reproduce from a clean clone in **<= 3 commands** (run from the repo root, no install
   step for the tool itself).

## Behaviour (Given / When / Then)
- **Given** a workflow whose HTTP/LLM node has `Authorization: Bearer sk-...` as a literal,
  **when** audited, **then** a `critical` `HARDCODED_CREDENTIAL` finding names that node.
  Expressions like `={{ $env.X }}` and credential-store references are not flagged.
- **Given** a webhook-triggered workflow that appends to a sheet with no dedupe step,
  **when** audited, **then** a `high` `NON_IDEMPOTENT_WRITE` finding is reported.
  An `appendOrUpdate` with a matching column, or a Remove Duplicates node upstream, clears it.
- **Given** an external-call node without `retryOnFail`, **then** `NO_RETRY` (high).
- **Given** no `settings.errorWorkflow` and no Error Trigger node, **then** `NO_FAILURE_ALERT` (high).
- **Given** an HTTP Request node without `options.timeout`, or no `settings.executionTimeout`,
  **then** `NO_TIMEOUT` (medium).
- **Given** an LLM call reachable from a webhook with no batching / Wait / loop node and no
  retry back-off, **then** `NO_RATE_LIMIT_GUARD` (medium).
- **Given** a webhook with `authentication` missing or `none`, **then** `WEBHOOK_UNAUTHENTICATED` (medium).
- **Given** a webhook with `responseMode: lastNode`, **then** `SYNC_WEBHOOK_RESPONSE` (low).
- **Given** an LLM node whose output flows straight into a write node with no IF/Switch/Code
  validation, **then** `LLM_OUTPUT_UNVALIDATED` (medium).
- **Given** the *before* pipeline config (no retry, no dedupe, no alert) and 5 redeliveries,
  **when** simulated, **then** redelivered events produce duplicate rows and failed LLM calls
  produce silent losses.
- **Given** the *after* config (idempotency key claimed on arrival and kept even if that
  delivery later fails, as n8n's Remove Duplicates node does, 3 tries with 1 s back-off,
  error branch -> dead-letter + alert), **then** duplicate rows = 0 and
  silent losses = 0; failures that survive all retries are counted as `alerted`.

## Non-goals
- Running a real n8n instance, calling a real LLM, or writing to a real Google Sheet.
- Modelling provider rate limits (HTTP 429) or concurrency caps; the rate-limit risk is
  reported by the static audit only.
- Predicting production latency. Simulated latency is a model (stated in the report),
  useful only to compare *before* vs *after* under identical inputs.
- Covering every n8n node type. Rules target the common webhook/LLM/sheet shape.
- Auto-fixing workflows. The fixed JSON is hand-written to show the target state.

## Architecture
`domain/` (models, rules, simulation — no I/O) <- `application/` (audit, stress, report use
cases) <- `adapters/` (n8n JSON -> domain, Markdown rendering) <- `infrastructure/` (CLI, files).
No reverse imports.
