"""Deterministic stress simulation of the webhook -> LLM -> sheet pipeline. No I/O.

Model (stated in the report as-is):
* `events` unique events arrive uniformly inside a `burst_window_ms` window.
* round(events * duplicate_ratio) of them are redelivered by the sender
  100-5000 ms after the original (sender-side retry).
* Each LLM attempt takes N(900, 300) ms (floor 200 ms) and fails with
  probability `llm_failure_ratio`. A sheet write takes N(150, 30) ms (floor 50 ms)
  and does not fail. Every delivery is processed in its own execution (no queueing).
* Dedupe (after config) mirrors n8n's Remove Duplicates node: the first delivery of an
  event id claims the key; later deliveries are dropped even if the first one failed.
  Failed events are therefore parked (dead-letter + alert) and replayed by a human.
* Each delivery has its own seeded RNG, so attempt 1 of a delivery sees the same
  latency and failure roll under every pipeline config: before/after differ only
  in pipeline behaviour, not in luck.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class Scenario:
    events: int = 50
    duplicate_ratio: float = 0.10
    llm_failure_ratio: float = 0.05
    runs: int = 20
    seed: int = 7
    burst_window_ms: float = 1000.0

    @property
    def redeliveries_per_run(self) -> int:
        return int(round(self.events * self.duplicate_ratio))


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    max_tries: int = 1
    retry_wait_ms: float = 0.0
    dedupe: bool = False            # idempotency key claimed atomically on arrival
    alert_on_failure: bool = False  # error branch -> dead-letter row + alert


BEFORE = PipelineConfig(name="Before (as delivered)")
AFTER = PipelineConfig(name="After (fixes applied)", max_tries=3, retry_wait_ms=1000.0,
                       dedupe=True, alert_on_failure=True)


@dataclass(frozen=True)
class Metrics:
    config: str
    runs: int
    deliveries: int
    unique_events: int
    recorded_exactly_once: int
    recorded_more_than_once: int
    duplicate_rows: int
    silent_losses: int
    alerted: int
    skipped_duplicates: int
    llm_calls: int
    p50_latency_ms: int
    p95_latency_ms: int

    @property
    def success_rate(self) -> float:
        return self.recorded_exactly_once / self.unique_events if self.unique_events else 0.0


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _delivery_rng(seed: int, run: int, idx: int) -> random.Random:
    return random.Random(seed * 1_000_003 + run * 10_007 + idx)


def _deliveries(scenario: Scenario, run: int) -> List[tuple]:
    rng = random.Random(scenario.seed * 7_919 + run)
    arrivals = [rng.uniform(0, scenario.burst_window_ms) for _ in range(scenario.events)]
    deliveries = [(t, ev) for ev, t in enumerate(arrivals)]
    for ev in rng.sample(range(scenario.events), scenario.redeliveries_per_run):
        deliveries.append((arrivals[ev] + rng.uniform(100, 5000), ev))
    deliveries.sort()
    return deliveries


def simulate(scenario: Scenario, config: PipelineConfig) -> Metrics:
    totals: Dict[str, int] = dict.fromkeys(
        ["deliveries", "exact", "multi", "dup_rows", "silent", "alerted", "skipped", "llm"], 0)
    latencies: List[float] = []
    for run in range(scenario.runs):
        rows = [0] * scenario.events
        alerted = [False] * scenario.events
        claimed = set()   # idempotency keys seen (kept even if processing later fails)
        for idx, (arrival, ev) in enumerate(_deliveries(scenario, run)):
            totals["deliveries"] += 1
            if config.dedupe:
                if ev in claimed:
                    totals["skipped"] += 1
                    continue
                claimed.add(ev)
            rng = _delivery_rng(scenario.seed, run, idx)
            sheet_ms = max(50.0, rng.gauss(150, 30))
            t, ok = arrival, False
            for attempt in range(1, config.max_tries + 1):
                t += max(200.0, rng.gauss(900, 300))
                totals["llm"] += 1
                if rng.random() >= scenario.llm_failure_ratio:
                    t += sheet_ms
                    ok = True
                    break
                if attempt < config.max_tries:
                    t += config.retry_wait_ms
            latencies.append(t - arrival)
            if ok:
                rows[ev] += 1
            else:
                alerted[ev] = alerted[ev] or config.alert_on_failure
        for ev in range(scenario.events):
            if rows[ev] == 1:
                totals["exact"] += 1
            elif rows[ev] > 1:
                totals["multi"] += 1
                totals["dup_rows"] += rows[ev] - 1
            elif alerted[ev]:
                totals["alerted"] += 1
            else:
                totals["silent"] += 1
    return Metrics(
        config=config.name, runs=scenario.runs, deliveries=totals["deliveries"],
        unique_events=scenario.events * scenario.runs,
        recorded_exactly_once=totals["exact"], recorded_more_than_once=totals["multi"],
        duplicate_rows=totals["dup_rows"], silent_losses=totals["silent"],
        alerted=totals["alerted"], skipped_duplicates=totals["skipped"],
        llm_calls=totals["llm"],
        p50_latency_ms=int(round(percentile(latencies, 50))),
        p95_latency_ms=int(round(percentile(latencies, 95))))
