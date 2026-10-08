"""Stress simulation: deterministic, and the before/after behaviours in SPEC.md."""
from n8nqa.domain.simulation import (AFTER, BEFORE, PipelineConfig, Scenario,
                                     percentile, simulate)

SPEC_SCENARIO = Scenario(events=50, duplicate_ratio=0.10, llm_failure_ratio=0.05,
                         runs=20, seed=7)


def test_same_seed_gives_identical_metrics():
    assert simulate(SPEC_SCENARIO, BEFORE) == simulate(SPEC_SCENARIO, BEFORE)


def test_different_seed_changes_metrics():
    other = Scenario(events=50, duplicate_ratio=0.10, llm_failure_ratio=0.05, runs=20, seed=8)
    assert simulate(SPEC_SCENARIO, BEFORE) != simulate(other, BEFORE)


def test_delivery_counts_follow_scenario():
    m = simulate(SPEC_SCENARIO, BEFORE)
    assert m.unique_events == 50 * 20
    assert m.deliveries == (50 + 5) * 20


def test_before_config_writes_duplicates_and_loses_events_silently():
    m = simulate(SPEC_SCENARIO, BEFORE)
    assert m.duplicate_rows > 0
    assert m.silent_losses > 0
    assert m.alerted == 0


def test_after_config_has_zero_duplicates_and_zero_silent_losses():
    m = simulate(SPEC_SCENARIO, AFTER)
    assert m.duplicate_rows == 0
    assert m.silent_losses == 0


def test_every_event_is_accounted_for():
    for cfg in (BEFORE, AFTER):
        m = simulate(SPEC_SCENARIO, cfg)
        assert m.recorded_exactly_once + m.recorded_more_than_once \
            + m.silent_losses + m.alerted == m.unique_events


def test_no_failures_and_no_redelivery_means_perfect_run():
    clean = Scenario(events=30, duplicate_ratio=0.0, llm_failure_ratio=0.0, runs=3, seed=1)
    m = simulate(clean, BEFORE)
    assert m.recorded_exactly_once == m.unique_events == 90
    assert m.llm_calls == 90


def test_total_failure_with_alerting_alerts_every_event():
    broken = Scenario(events=10, duplicate_ratio=0.0, llm_failure_ratio=1.0, runs=1, seed=1)
    cfg = PipelineConfig(name="alert-only", max_tries=2, retry_wait_ms=500,
                         dedupe=False, alert_on_failure=True)
    m = simulate(broken, cfg)
    assert m.alerted == 10 and m.silent_losses == 0 and m.llm_calls == 20


def test_dedupe_saves_llm_calls():
    assert simulate(SPEC_SCENARIO, AFTER).skipped_duplicates == 5 * 20


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([5], 95) == 5
    assert percentile([1, 2, 3, 4], 50) == 2


DEGRADED = Scenario(events=50, duplicate_ratio=0.10, llm_failure_ratio=0.30, runs=20, seed=7)


def test_degraded_provider_after_config_alerts_instead_of_losing():
    before, after = simulate(DEGRADED, BEFORE), simulate(DEGRADED, AFTER)
    assert before.silent_losses > 0 and before.alerted == 0
    assert after.silent_losses == 0 and after.alerted > 0
