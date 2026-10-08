"""Domain model: an n8n workflow as plain data, and the audit Finding. No I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, List, Mapping, Optional, Set, Tuple


class Severity(Enum):
    CRITICAL = ("critical", 0)
    HIGH = ("high", 1)
    MEDIUM = ("medium", 2)
    LOW = ("low", 3)

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def rank(self) -> int:
        return self.value[1]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    title: str
    nodes: Tuple[str, ...]
    evidence: str
    impact: str
    fix: str


# n8n node type vocabulary -------------------------------------------------
WEBHOOK = "n8n-nodes-base.webhook"
HTTP_REQUEST = "n8n-nodes-base.httpRequest"
GOOGLE_SHEETS = "n8n-nodes-base.googleSheets"
REMOVE_DUPLICATES = "n8n-nodes-base.removeDuplicates"
ERROR_TRIGGER = "n8n-nodes-base.errorTrigger"

LLM_TYPES = {
    "@n8n/n8n-nodes-langchain.openAi",
    "@n8n/n8n-nodes-langchain.lmChatOpenAi",
    "@n8n/n8n-nodes-langchain.lmChatAnthropic",
    "@n8n/n8n-nodes-langchain.anthropic",
    "@n8n/n8n-nodes-langchain.chainLlm",
    "@n8n/n8n-nodes-langchain.agent",
}
LLM_HOSTS = ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com")
EXTERNAL_TYPES = {
    HTTP_REQUEST, GOOGLE_SHEETS,
    "n8n-nodes-base.slack", "n8n-nodes-base.emailSend", "n8n-nodes-base.gmail",
    "n8n-nodes-base.airtable", "n8n-nodes-base.notion", "n8n-nodes-base.postgres",
    "n8n-nodes-base.mySql", "n8n-nodes-base.hubspot",
} | LLM_TYPES
WRITE_OPERATIONS = {
    GOOGLE_SHEETS: {"append", "appendOrUpdate", "update"},
    "n8n-nodes-base.airtable": {"create", "append", "upsert"},
    "n8n-nodes-base.notion": {"create"},
    "n8n-nodes-base.postgres": {"insert", "upsert"},
    "n8n-nodes-base.mySql": {"insert", "upsert"},
}
UPSERT_OPERATIONS = {"appendOrUpdate", "upsert"}
VALIDATOR_TYPES = {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.code",
                   "n8n-nodes-base.filter"}
THROTTLE_TYPES = {"n8n-nodes-base.wait", "n8n-nodes-base.splitInBatches"}


@dataclass(frozen=True)
class Node:
    name: str
    type: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    retry_on_fail: bool = False
    max_tries: int = 1
    wait_between_tries_ms: int = 0
    on_error: str = "stopWorkflow"

    @property
    def is_webhook(self) -> bool:
        return self.type == WEBHOOK

    @property
    def is_llm(self) -> bool:
        if self.type in LLM_TYPES:
            return True
        return self.type == HTTP_REQUEST and any(
            h in str(self.parameters.get("url", "")) for h in LLM_HOSTS)

    @property
    def is_external(self) -> bool:
        return self.type in EXTERNAL_TYPES or self.is_llm

    @property
    def operation(self) -> str:
        return str(self.parameters.get("operation", ""))

    @property
    def is_write(self) -> bool:
        return self.operation in WRITE_OPERATIONS.get(self.type, set())

    @property
    def has_error_output(self) -> bool:
        return self.on_error == "continueErrorOutput"

    def strings(self) -> Iterator[Tuple[str, str]]:
        """Yield (json-path, value) for every string inside parameters."""
        yield from _walk(self.parameters, "parameters")


def _walk(value: Any, path: str) -> Iterator[Tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


@dataclass(frozen=True)
class Workflow:
    name: str
    nodes: Tuple[Node, ...]
    # source node -> output index -> target node names
    connections: Mapping[str, Tuple[Tuple[str, ...], ...]] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)

    def node(self, name: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.name == name), None)

    def successors(self, name: str, output: Optional[int] = None) -> List[str]:
        outputs = self.connections.get(name, ())
        if output is not None:
            return list(outputs[output]) if output < len(outputs) else []
        return [t for targets in outputs for t in targets]

    def predecessors(self, name: str) -> List[str]:
        return [src for src, outs in self.connections.items()
                if any(name in targets for targets in outs)]

    def ancestors(self, name: str) -> Set[str]:
        seen: Set[str] = set()
        stack = self.predecessors(name)
        while stack:
            cur = stack.pop()
            if cur not in seen:
                seen.add(cur)
                stack.extend(self.predecessors(cur))
        return seen

    def reachable_from_webhook(self, name: str) -> bool:
        return any(self.node(a) is not None and self.node(a).is_webhook
                   for a in self.ancestors(name))

    @property
    def webhooks(self) -> List[Node]:
        return [n for n in self.nodes if n.is_webhook]


def ordered(names: List[str]) -> Tuple[str, ...]:
    """Deduplicate while keeping first-seen order."""
    return tuple(dict.fromkeys(names))

