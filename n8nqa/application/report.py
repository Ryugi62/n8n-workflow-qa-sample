"""Use case: assemble everything the written report needs (data only, no formatting)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..domain.model import Finding, Workflow
from ..domain.simulation import AFTER, BEFORE, Metrics, PipelineConfig, Scenario, simulate
from .audit import audit, severity_counts

STRESS_SCENARIOS: List[Tuple[str, Scenario]] = [
    ("Scenario A: burst with redeliveries",
     Scenario(events=50, duplicate_ratio=0.10, llm_failure_ratio=0.05, runs=20, seed=7)),
    ("Scenario B: degraded LLM provider",
     Scenario(events=50, duplicate_ratio=0.10, llm_failure_ratio=0.30, runs=20, seed=7)),
]


@dataclass(frozen=True)
class StressResult:
    label: str
    scenario: Scenario
    before: Metrics
    after: Metrics


@dataclass(frozen=True)
class ReportData:
    report_date: str
    workflow_name: str
    workflow_file: str
    node_count: int
    findings: List[Finding]
    before_counts: Dict[str, int]
    after_counts: Dict[str, int]
    before_cfg: PipelineConfig
    after_cfg: PipelineConfig
    stress: List[StressResult]


def build_report(report_date: str, workflow: Workflow, workflow_file: str,
                 fixed: Workflow,
                 scenarios: List[Tuple[str, Scenario]] = STRESS_SCENARIOS) -> ReportData:
    findings = audit(workflow)
    return ReportData(
        report_date=report_date, workflow_name=workflow.name, workflow_file=workflow_file,
        node_count=len(workflow.nodes), findings=findings,
        before_counts=severity_counts(findings),
        after_counts=severity_counts(audit(fixed)),
        before_cfg=BEFORE, after_cfg=AFTER,
        stress=[StressResult(label, sc, simulate(sc, BEFORE), simulate(sc, AFTER))
                for label, sc in scenarios])
