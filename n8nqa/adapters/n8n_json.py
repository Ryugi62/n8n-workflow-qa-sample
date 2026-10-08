"""Adapter: n8n export JSON (already decoded to a dict) -> domain Workflow."""
from __future__ import annotations

from typing import Any, Mapping

from ..domain.model import Node, Workflow


def parse_node(raw: Mapping[str, Any]) -> Node:
    on_error = raw.get("onError")
    if on_error is None:
        # legacy flag used by older n8n exports
        on_error = "continueRegularOutput" if raw.get("continueOnFail") else "stopWorkflow"
    return Node(
        name=str(raw["name"]),
        type=str(raw["type"]),
        parameters=raw.get("parameters") or {},
        retry_on_fail=bool(raw.get("retryOnFail", False)),
        max_tries=int(raw.get("maxTries", 3 if raw.get("retryOnFail") else 1)),
        wait_between_tries_ms=int(raw.get("waitBetweenTries", 1000 if raw.get("retryOnFail") else 0)),
        on_error=str(on_error),
    )


def parse_workflow(raw: Mapping[str, Any]) -> Workflow:
    connections = {}
    for src, by_type in (raw.get("connections") or {}).items():
        outputs = by_type.get("main") or []
        connections[src] = tuple(
            tuple(link["node"] for link in (targets or [])) for targets in outputs)
    return Workflow(
        name=str(raw.get("name", "")),
        nodes=tuple(parse_node(n) for n in raw.get("nodes", [])),
        connections=connections,
        settings=raw.get("settings") or {},
    )
