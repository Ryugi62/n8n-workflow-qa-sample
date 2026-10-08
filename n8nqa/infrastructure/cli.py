"""CLI and file I/O. The only layer that touches the filesystem."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import List, Optional

from ..adapters.markdown import findings_text, render_report
from ..adapters.n8n_json import parse_workflow
from ..application.audit import audit
from ..application.report import build_report
from ..domain.simulation import AFTER, BEFORE, Scenario, simulate

REPORT_DATE = "2026-10-08"
WORKFLOW = "workflow/lead-intake.json"
FIXED = "workflow/lead-intake.fixed.json"
REPORT = "report/sample-report.md"


def _load(path: pathlib.Path):
    return parse_workflow(json.loads(path.read_text()))


def build_report_text(root: pathlib.Path) -> str:
    data = build_report(REPORT_DATE, _load(root / WORKFLOW), WORKFLOW, _load(root / FIXED))
    return render_report(data)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m n8nqa",
                                description="n8n workflow audit + stress simulation")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="static audit of an n8n workflow export")
    a.add_argument("workflow")
    st = sub.add_parser("stress", help="run the stress simulation (before vs after)")
    for name, default, typ in (("events", 50, int), ("duplicate-ratio", 0.10, float),
                               ("llm-failure-ratio", 0.05, float), ("runs", 20, int),
                               ("seed", 7, int)):
        st.add_argument(f"--{name}", type=typ, default=default)
    r = sub.add_parser("report", help="regenerate report/sample-report.md")
    r.add_argument("--out", default=REPORT)
    args = p.parse_args(argv)
    root = pathlib.Path.cwd()

    if args.cmd == "audit":
        findings = audit(_load(pathlib.Path(args.workflow)))
        print(findings_text(findings))
        return 1 if any(f.severity.rank <= 1 for f in findings) else 0
    if args.cmd == "stress":
        sc = Scenario(events=args.events, duplicate_ratio=args.duplicate_ratio,
                      llm_failure_ratio=args.llm_failure_ratio, runs=args.runs, seed=args.seed)
        for cfg in (BEFORE, AFTER):
            m = simulate(sc, cfg)
            print(f"{cfg.name}: exactly-once {m.recorded_exactly_once}/{m.unique_events} "
                  f"({m.success_rate * 100:.1f}%), duplicate rows {m.duplicate_rows}, "
                  f"silent losses {m.silent_losses}, alerted {m.alerted}, "
                  f"llm calls {m.llm_calls}, p95 {m.p95_latency_ms} ms")
        return 0
    text = build_report_text(root)
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
