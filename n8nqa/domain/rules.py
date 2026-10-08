"""Audit rules. Each rule is a pure function Workflow -> list[Finding] (empty = pass)."""
from __future__ import annotations

import re
from typing import Callable, List

from .model import (REMOVE_DUPLICATES, ERROR_TRIGGER, HTTP_REQUEST, THROTTLE_TYPES,
                    UPSERT_OPERATIONS, VALIDATOR_TYPES, Finding, Severity, Workflow, ordered)

Rule = Callable[[Workflow], List[Finding]]

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # OpenAI / Anthropic style
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access key id
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),    # Slack token
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),       # GitHub token
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),           # Google API key
)
AUTH_HEADERS = {"authorization", "x-api-key", "api-key", "apikey"}


def _redact(secret: str) -> str:
    return secret[:6] + "..." + f"({len(secret)} chars)"


def hardcoded_credential(wf: Workflow) -> List[Finding]:
    hits = []
    for n in wf.nodes:
        for path, value in n.strings():
            for pat in SECRET_PATTERNS:
                m = pat.search(value)
                if m:
                    hits.append((n.name, f"{n.name}: {path} contains {_redact(m.group(0))}"))
        headers = n.parameters.get("headerParameters", {}).get("parameters", []) \
            if isinstance(n.parameters.get("headerParameters"), dict) else []
        for h in headers:
            name, value = str(h.get("name", "")).lower(), str(h.get("value", ""))
            literal = value and not value.startswith("=")
            if name in AUTH_HEADERS and literal and not any(p.search(value) for p in SECRET_PATTERNS):
                hits.append((n.name, f"{n.name}: header '{h.get('name')}' is a literal value"))
    if not hits:
        return []
    return [Finding(
        "HARDCODED_CREDENTIAL", Severity.CRITICAL, "API key stored in the workflow JSON",
        ordered([h[0] for h in hits]), "; ".join(dict.fromkeys(h[1] for h in hits)),
        "The key ships with every export, backup and copy of this workflow; anyone with the "
        "JSON can spend on the account. It also bypasses n8n's encrypted credential store.",
        "Move the key into an n8n credential (HTTP Request -> Authentication -> Predefined "
        "Credential Type -> OpenAI) and rotate the exposed key at the provider today.")]


def non_idempotent_write(wf: Workflow) -> List[Finding]:
    bad = []
    for n in wf.nodes:
        if not (n.is_write and wf.reachable_from_webhook(n.name)):
            continue
        columns = n.parameters.get("columns", {})
        matching = columns.get("matchingColumns") if isinstance(columns, dict) else None
        if n.operation in UPSERT_OPERATIONS and matching:
            continue
        if any(wf.node(a) and wf.node(a).type == REMOVE_DUPLICATES for a in wf.ancestors(n.name)):
            continue
        bad.append(n.name)
    if not bad:
        return []
    return [Finding(
        "NON_IDEMPOTENT_WRITE", Severity.HIGH, "Redelivered webhooks create duplicate rows",
        tuple(bad), "; ".join(f"{b}: operation '{wf.node(b).operation}', no dedupe key upstream"
                              for b in bad),
        "Webhook senders retry on timeouts and 5xx. Every retry appends another row and pays "
        "for another LLM call; reports built on the sheet double-count.",
        "Add a Remove Duplicates node (operation 'Remove Items Processed in Previous "
        "Executions', key = the sender's event id) right after the webhook, and switch the "
        "write to 'Append or Update Row' matched on event_id. For a strict guarantee under "
        "simultaneous redeliveries, claim the key in a store with a unique constraint "
        "(Postgres INSERT ... ON CONFLICT DO NOTHING, or Redis SET NX).")]


def no_retry(wf: Workflow) -> List[Finding]:
    bad = [n.name for n in wf.nodes if n.is_external and not n.retry_on_fail]
    if not bad:
        return []
    return [Finding(
        "NO_RETRY", Severity.HIGH, "External calls fail on the first transient error",
        tuple(bad), "; ".join(f"{b}: retryOnFail not set" for b in bad),
        "A single 429/5xx/network blip from the LLM or Google API fails the whole execution "
        "and the lead is not written.",
        "Node settings -> Retry On Fail: on, Max Tries 3, Wait Between Tries 1000 ms.")]


def no_error_branch(wf: Workflow) -> List[Finding]:
    bad = [n.name for n in wf.nodes
           if (n.is_llm or n.is_write) and not n.has_error_output]
    if not bad:
        return []
    return [Finding(
        "NO_ERROR_BRANCH", Severity.MEDIUM, "Failed items are dropped instead of parked",
        tuple(bad), "; ".join(f"{b}: onError = '{wf.node(b).on_error}'" for b in bad),
        "When retries run out the execution stops; the payload only survives in the "
        "execution log (if saving failed executions is on) and nobody replays it.",
        "Set Node settings -> On Error to 'Continue (using error output)' and route that "
        "output to a dead-letter row keyed by event_id plus an alert, so the item can be "
        "replayed.")]


def no_failure_alert(wf: Workflow) -> List[Finding]:
    if wf.settings.get("errorWorkflow") or any(n.type == ERROR_TRIGGER for n in wf.nodes):
        return []
    return [Finding(
        "NO_FAILURE_ALERT", Severity.HIGH, "Failures are silent", (),
        "settings.errorWorkflow is empty and there is no Error Trigger node",
        "A failed execution is visible only to someone who opens the Executions list. Lost "
        "leads are discovered days later, by the sales team, not by the owner of the flow.",
        "Create a small error workflow (Error Trigger -> Slack or email with workflow name, "
        "execution URL and error message) and select it in Workflow settings -> Error Workflow.")]


def no_timeout(wf: Workflow) -> List[Finding]:
    bad = [n.name for n in wf.nodes if n.type == HTTP_REQUEST
           and not (n.parameters.get("options") or {}).get("timeout")]
    workflow_level = not wf.settings.get("executionTimeout")
    if not bad and not workflow_level:
        return []
    parts = [f"{b}: options.timeout not set" for b in bad]
    if workflow_level:
        parts.append("settings.executionTimeout not set")
    return [Finding(
        "NO_TIMEOUT", Severity.MEDIUM, "A hung API call holds the execution open", tuple(bad),
        "; ".join(parts),
        "A slow or hung response keeps the execution (and the webhook caller) waiting; the "
        "caller gives up and redelivers, which feeds the duplicate-row problem.",
        "Set HTTP Request -> Options -> Timeout to 30000 ms and Workflow settings -> "
        "Timeout Workflow to 120 s.")]


def no_rate_limit_guard(wf: Workflow) -> List[Finding]:
    bad = []
    for n in wf.nodes:
        if not (n.is_llm and wf.reachable_from_webhook(n.name)):
            continue
        backoff = n.retry_on_fail and n.wait_between_tries_ms >= 1000
        throttled = any(wf.node(a) and wf.node(a).type in THROTTLE_TYPES
                        for a in wf.ancestors(n.name))
        if not (backoff or throttled):
            bad.append(n.name)
    if not bad:
        return []
    return [Finding(
        "NO_RATE_LIMIT_GUARD", Severity.MEDIUM, "Bursts hit the LLM provider unthrottled",
        tuple(bad), "; ".join(f"{b}: called once per webhook, no back-off or throttle" for b in bad),
        "Each webhook starts its own execution, so a burst of N submissions makes N parallel "
        "LLM calls. Provider 429 responses become failed executions.",
        "Retry with Wait Between Tries >= 1000 ms so 429s back off, and cap parallel "
        "executions on the instance: N8N_CONCURRENCY_PRODUCTION_LIMIT (regular mode) or the "
        "worker --concurrency flag (queue mode).")]


def webhook_unauthenticated(wf: Workflow) -> List[Finding]:
    bad = [n.name for n in wf.webhooks
           if n.parameters.get("authentication", "none") in ("", "none")]
    if not bad:
        return []
    return [Finding(
        "WEBHOOK_UNAUTHENTICATED", Severity.MEDIUM, "Anyone with the URL can post leads",
        tuple(bad), "; ".join(f"{b}: authentication = none" for b in bad),
        "Whoever learns the URL can write rows into the sheet and spend LLM tokens.",
        "Webhook -> Authentication: Header Auth with a shared secret, or verify the sender's "
        "HMAC signature in a Code node before any other step.")]


def sync_webhook_response(wf: Workflow) -> List[Finding]:
    bad = [n.name for n in wf.webhooks if n.parameters.get("responseMode") == "lastNode"]
    if not bad:
        return []
    return [Finding(
        "SYNC_WEBHOOK_RESPONSE", Severity.LOW, "Caller waits for the whole flow", tuple(bad),
        "; ".join(f"{b}: responseMode = lastNode" for b in bad),
        "The sender waits for LLM + sheet write before getting a response. If that exceeds the "
        "sender's timeout it retries, which multiplies duplicates.",
        "Set Webhook -> Respond to 'Immediately' (onReceived) so the sender is acknowledged "
        "before the work starts.")]


def llm_output_unvalidated(wf: Workflow) -> List[Finding]:
    bad = []
    for n in wf.nodes:
        if not n.is_llm:
            continue
        stack, seen = wf.successors(n.name, output=0), set()
        while stack:
            cur = wf.node(stack.pop())
            if cur is None or cur.name in seen or cur.type in VALIDATOR_TYPES:
                continue
            seen.add(cur.name)
            if cur.is_write:
                bad.append((n.name, cur.name))
                break
            stack.extend(wf.successors(cur.name))
    if not bad:
        return []
    return [Finding(
        "LLM_OUTPUT_UNVALIDATED", Severity.MEDIUM, "Model output is written without a check",
        ordered([b[0] for b in bad]),
        "; ".join(f"{a} -> {b} with no IF/Switch/Code/Filter in between" for a, b in bad),
        "Free-text model output is written as the label. An off-list answer such as "
        "'Probably sales.' lands in the sheet and silently drops out of label-based filters.",
        "Ask for a fixed label set (JSON or enum), then add an IF/Switch that writes only "
        "allowed labels and routes anything else to the dead-letter/review path.")]


ALL_RULES: List[Rule] = [
    hardcoded_credential, non_idempotent_write, no_retry, no_failure_alert,
    no_error_branch, no_timeout, no_rate_limit_guard, webhook_unauthenticated,
    llm_output_unvalidated, sync_webhook_response,
]
