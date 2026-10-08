"""End-to-end checks on the committed sample workflow and report (SPEC success criteria)."""
import ast
import json
import pathlib

from n8nqa.adapters.n8n_json import parse_workflow
from n8nqa.application.audit import audit
from n8nqa.domain.model import Severity
from n8nqa.infrastructure.cli import build_report_text

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    return parse_workflow(json.loads((ROOT / "workflow" / name).read_text()))


def test_sample_workflow_has_at_least_six_rule_ids_and_a_critical():
    findings = audit(load("lead-intake.json"))
    assert len({f.rule_id for f in findings}) >= 6
    assert any(f.severity is Severity.CRITICAL for f in findings)


def test_findings_are_sorted_by_severity():
    ranks = [f.severity.rank for f in audit(load("lead-intake.json"))]
    assert ranks == sorted(ranks)


def test_fixed_workflow_has_no_critical_or_high():
    findings = audit(load("lead-intake.fixed.json"))
    assert not [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]


def test_committed_report_matches_fresh_output():
    committed = (ROOT / "report" / "sample-report.md").read_text()
    assert committed == build_report_text(ROOT)


def test_domain_layer_does_not_import_outer_layers_or_io():
    banned = {"n8nqa.application", "n8nqa.adapters", "n8nqa.infrastructure",
              "json", "os", "pathlib", "sys", "argparse", "io"}
    for path in (ROOT / "n8nqa" / "domain").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(name == b or name.startswith(b + ".") for b in banned), \
                    f"{path.name} imports {name}"


def test_no_real_looking_secrets_outside_the_planted_placeholder():
    import re
    pattern = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})")
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or not path.is_file() or path.suffix not in {".py", ".md", ".json"}:
            continue
        for match in pattern.findall(path.read_text()):
            assert "EXAMPLE" in match, (path, match)


def test_report_covers_both_stress_scenarios():
    from n8nqa.application.report import STRESS_SCENARIOS
    text = build_report_text(ROOT)
    assert len(STRESS_SCENARIOS) == 2
    for label, _ in STRESS_SCENARIOS:
        assert label in text
