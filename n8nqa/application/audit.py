"""Use case: run every rule against a workflow, most severe first."""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List

from ..domain.model import Finding, Severity, Workflow
from ..domain.rules import ALL_RULES, Rule


def audit(workflow: Workflow, rules: Iterable[Rule] = ALL_RULES) -> List[Finding]:
    findings = [f for rule in rules for f in rule(workflow)]
    return sorted(findings, key=lambda f: f.severity.rank)


def severity_counts(findings: Iterable[Finding]) -> Dict[str, int]:
    c = Counter(f.severity for f in findings)
    return {s.label: c.get(s, 0) for s in Severity}
