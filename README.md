# n8n Workflow QA: public sample

A complete, reproducible example of a **written audit and stress test for an n8n
workflow**: the workflow goes in, a report comes out. The report has findings ranked by
severity, before/after numbers, fixes, and a runbook.

**Read the report:** [`report/sample-report.md`](report/sample-report.md)

| | |
|---|---|
| Workflow audited | [`workflow/lead-intake.json`](workflow/lead-intake.json): webhook -> OpenAI classification -> Google Sheets row |
| Fixed version | [`workflow/lead-intake.fixed.json`](workflow/lead-intake.fixed.json), so every fix can be diffed node by node |
| Static audit | 10 rules: hardcoded keys, non-idempotent writes, missing retry, missing error branch, no failure alert, missing timeouts, no rate-limit back-off, unauthenticated webhook, unvalidated LLM output, synchronous webhook response |
| Stress simulation | Seeded burst of webhook deliveries with redeliveries and injected LLM failures, run through the *before* and *after* pipeline behaviour |

The workflow is **fictional** and was written for this sample. There is no customer, no
real data and no real credential. The single API key in `lead-intake.json` is a planted
placeholder (`sk-EXAMPLE-not-a-real-key-...`) so the secret check has something to find.

## Reproduce

Python 3.9+. The tool uses only the standard library; pytest runs the tests.

```bash
git clone https://github.com/Ryugi62/n8n-workflow-qa-sample && cd n8n-workflow-qa-sample
python3 -m pip install pytest && python3 -m pytest -q
python3 -m n8nqa report && git diff --exit-code report/
```

The last line regenerates the report and fails if any number differs from the committed
file. Other commands:

```bash
python3 -m n8nqa audit path/to/your-workflow.json   # exit code 1 if critical/high findings
python3 -m n8nqa stress --llm-failure-ratio 0.3      # try your own scenario
```

## What the simulation is (and is not)

It does not run n8n, call an LLM or touch Google Sheets. It replays the same seeded
deliveries and the same failure rolls through two pipeline behaviours (as delivered vs.
fixed), so the difference you see comes from the pipeline, not from luck. Latency is a
model for comparing before and after, not a production forecast. Details are in
[`SPEC.md`](SPEC.md) and in section 3 of the report.

## Layout

```
SPEC.md            purpose, numeric success criteria, Given/When/Then, non-goals
n8nqa/domain/      workflow model, audit rules, simulation (no I/O)
n8nqa/application/ audit and report use cases
n8nqa/adapters/    n8n JSON -> domain, Markdown rendering
n8nqa/infrastructure/  CLI and file access
tests/             pytest suite (rules, simulation, committed artifacts, layer boundaries)
```

## Working on your workflow

The same audit is available for a real workflow. The process is written and async:

1. You send the workflow export (n8n -> Download as JSON). Credentials live in n8n's
   credential store, not in the export, so nothing secret needs to leave your instance
   (keys pasted directly into node fields are the exception, and the audit flags them).
2. Within 48 hours you get a written report in the format above: findings by severity,
   before/after numbers, a fix checklist and a runbook.
3. Communication is by email or the platform message thread only. No calls are needed.
