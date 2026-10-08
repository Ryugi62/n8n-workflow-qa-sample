"""Static audit rules: one Given/When/Then per rule (see SPEC.md)."""
from n8nqa.adapters.n8n_json import parse_workflow
from n8nqa.domain.model import Severity
from n8nqa.domain import rules

FAKE_KEY = "sk-EXAMPLE-not-a-real-key-0000000000"


def node(name, type_, params=None, **extra):
    n = {"name": name, "type": type_, "typeVersion": 1, "parameters": params or {}}
    n.update(extra)
    return n


def wf(nodes, edges=(), settings=None):
    connections = {}
    for src, dst, *idx in edges:
        out = idx[0] if idx else 0
        outputs = connections.setdefault(src, {"main": []})["main"]
        while len(outputs) <= out:
            outputs.append([])
        outputs[out].append({"node": dst, "type": "main", "index": 0})
    return parse_workflow({"name": "t", "nodes": nodes, "connections": connections,
                           "settings": settings or {}})


HOOK = node("Hook", "n8n-nodes-base.webhook",
            {"httpMethod": "POST", "path": "x", "authentication": "headerAuth",
             "responseMode": "onReceived"})
LLM_URL = "https://api.openai.com/v1/chat/completions"


def llm(**extra):
    base = dict(retryOnFail=True, maxTries=3, waitBetweenTries=1000,
                onError="continueErrorOutput")
    base.update(extra)
    return node("LLM", "n8n-nodes-base.httpRequest",
                {"method": "POST", "url": LLM_URL, "options": {"timeout": 30000}}, **base)


def ids(findings):
    return {f.rule_id for f in findings}


# --- HARDCODED_CREDENTIAL -------------------------------------------------

def test_literal_bearer_key_is_critical_and_names_node():
    n = llm()
    n["parameters"]["headerParameters"] = {
        "parameters": [{"name": "Authorization", "value": "Bearer " + FAKE_KEY}]}
    found = rules.hardcoded_credential(wf([HOOK, n]))
    assert len(found) == 1
    assert found[0].severity is Severity.CRITICAL
    assert found[0].nodes == ("LLM",)


def test_secret_evidence_is_redacted():
    n = llm()
    n["parameters"]["jsonBody"] = '{"key": "%s"}' % FAKE_KEY
    found = rules.hardcoded_credential(wf([HOOK, n]))
    assert FAKE_KEY not in found[0].evidence


def test_env_expression_header_is_not_flagged():
    n = llm()
    n["parameters"]["headerParameters"] = {
        "parameters": [{"name": "Authorization", "value": "=Bearer {{ $env.OPENAI_KEY }}"}]}
    assert rules.hardcoded_credential(wf([HOOK, n])) == []


# --- NON_IDEMPOTENT_WRITE --------------------------------------------------

def sheet(op, **params):
    p = {"operation": op}
    p.update(params)
    return node("Sheet", "n8n-nodes-base.googleSheets", p,
                retryOnFail=True, onError="continueErrorOutput")


def test_webhook_to_sheet_append_without_dedupe_is_high():
    found = rules.non_idempotent_write(wf([HOOK, sheet("append")], [("Hook", "Sheet")]))
    assert [f.severity for f in found] == [Severity.HIGH]


def test_upsert_with_matching_column_clears_idempotency():
    s = sheet("appendOrUpdate", columns={"matchingColumns": ["event_id"]})
    assert rules.non_idempotent_write(wf([HOOK, s], [("Hook", "Sheet")])) == []


def test_remove_duplicates_upstream_clears_idempotency():
    dd = node("Dedupe", "n8n-nodes-base.removeDuplicates",
              {"operation": "removeItemsProcessedInPreviousExecutions"})
    w = wf([HOOK, dd, sheet("append")], [("Hook", "Dedupe"), ("Dedupe", "Sheet")])
    assert rules.non_idempotent_write(w) == []


# --- NO_RETRY / NO_ERROR_BRANCH ------------------------------------------

def test_external_node_without_retry_is_flagged():
    found = rules.no_retry(wf([HOOK, llm(retryOnFail=False)], [("Hook", "LLM")]))
    assert found and found[0].severity is Severity.HIGH and "LLM" in found[0].nodes


def test_llm_without_error_output_is_flagged():
    found = rules.no_error_branch(wf([HOOK, llm(onError="stopWorkflow")], [("Hook", "LLM")]))
    assert ids(found) == {"NO_ERROR_BRANCH"}


# --- NO_FAILURE_ALERT -----------------------------------------------------

def test_no_error_workflow_and_no_error_trigger_is_high():
    found = rules.no_failure_alert(wf([HOOK]))
    assert [f.severity for f in found] == [Severity.HIGH]


def test_error_workflow_setting_clears_alert_rule():
    assert rules.no_failure_alert(wf([HOOK], settings={"errorWorkflow": "42"})) == []


# --- NO_TIMEOUT -----------------------------------------------------------

def test_http_node_without_timeout_is_flagged():
    n = llm()
    n["parameters"]["options"] = {}
    found = rules.no_timeout(wf([HOOK, n], settings={"executionTimeout": 120}))
    assert found and found[0].nodes == ("LLM",)


def test_missing_execution_timeout_is_flagged_even_if_nodes_ok():
    assert ids(rules.no_timeout(wf([HOOK, llm()]))) == {"NO_TIMEOUT"}


# --- NO_RATE_LIMIT_GUARD ---------------------------------------------------

def test_llm_reachable_from_webhook_without_backoff_is_flagged():
    found = rules.no_rate_limit_guard(
        wf([HOOK, llm(retryOnFail=False)], [("Hook", "LLM")]))
    assert ids(found) == {"NO_RATE_LIMIT_GUARD"}


def test_retry_with_backoff_clears_rate_limit_rule():
    assert rules.no_rate_limit_guard(wf([HOOK, llm()], [("Hook", "LLM")])) == []


# --- webhook rules ---------------------------------------------------------

def test_webhook_without_auth_is_flagged():
    h = node("Hook", "n8n-nodes-base.webhook", {"httpMethod": "POST", "path": "x"})
    assert ids(rules.webhook_unauthenticated(wf([h]))) == {"WEBHOOK_UNAUTHENTICATED"}


def test_last_node_response_mode_is_low():
    h = node("Hook", "n8n-nodes-base.webhook", {"responseMode": "lastNode"})
    found = rules.sync_webhook_response(wf([h]))
    assert [f.severity for f in found] == [Severity.LOW]


# --- LLM_OUTPUT_UNVALIDATED ------------------------------------------------

def test_llm_straight_into_write_is_flagged():
    setn = node("Parse", "n8n-nodes-base.set")
    w = wf([HOOK, llm(), setn, sheet("append")],
           [("Hook", "LLM"), ("LLM", "Parse"), ("Parse", "Sheet")])
    assert ids(rules.llm_output_unvalidated(w)) == {"LLM_OUTPUT_UNVALIDATED"}


def test_if_node_between_llm_and_write_clears_validation_rule():
    check = node("Check", "n8n-nodes-base.if")
    w = wf([HOOK, llm(), check, sheet("append")],
           [("Hook", "LLM"), ("LLM", "Check"), ("Check", "Sheet")])
    assert rules.llm_output_unvalidated(w) == []


def test_error_output_of_llm_is_not_treated_as_success_path():
    dead = sheet("append")
    w = wf([HOOK, llm(), node("Check", "n8n-nodes-base.if"), dead],
           [("Hook", "LLM"), ("LLM", "Check", 0), ("LLM", "Sheet", 1)])
    assert rules.llm_output_unvalidated(w) == []
