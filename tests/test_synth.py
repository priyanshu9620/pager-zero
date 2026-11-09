from datetime import timedelta

import pytest

from pager_zero.adapters.base import ACTION_NAMES, Adapters
from pager_zero.adapters.synthetic import SyntheticAdapters
from pager_zero.synth import generate, list_scenarios

SEEDS = [1, 7, 42]


@pytest.mark.parametrize("scenario", list_scenarios())
@pytest.mark.parametrize("seed", SEEDS)
def test_world_is_consistent(scenario: str, seed: int) -> None:
    w = generate(scenario, seed)
    assert w.start < w.t0 < w.alert.fired_at < w.now
    assert w.ground_truth.family == scenario
    assert w.ground_truth.fix_action in ACTION_NAMES
    assert w.recovery_plan, "every scenario must declare what recovers after the fix"
    for s in w.metrics:
        assert s.points and s.points[0][0] >= w.start and s.points[-1][0] <= w.now
    assert all(line.ts <= w.now for line in w.logs)


@pytest.mark.parametrize("scenario", list_scenarios())
def test_deterministic(scenario: str) -> None:
    a, b = generate(scenario, 5), generate(scenario, 5)
    assert a.public_summary() == b.public_summary()
    assert a.ground_truth == b.ground_truth
    assert generate(scenario, 6).public_summary() != a.public_summary()


@pytest.mark.parametrize("scenario", list_scenarios())
def test_correct_fix_heals_world(scenario: str) -> None:
    w = generate(scenario, 3)
    ad: Adapters = SyntheticAdapters.from_settings(world=w)
    gt = w.ground_truth
    before_now = w.now
    res = getattr(ad.actuator, gt.fix_action)(**gt.fix_target)
    assert res.ok and w.resolved and w.now > before_now
    for s, baseline in w.recovery_plan:
        assert abs((s.last() or 0) - baseline) <= abs(baseline) * 0.1 + 1e-6, (
            f"{s.metric}{s.labels} did not recover"
        )


def test_wrong_fix_does_not_heal() -> None:
    w = generate("bad-deploy", 2)
    ad = SyntheticAdapters.from_settings(world=w)
    ad.actuator.set_flag("orders.retry.enabled", False)
    assert not w.resolved and w.actions_taken


def test_synthetic_satisfies_protocols() -> None:
    from pager_zero.adapters import base

    ad = SyntheticAdapters.from_settings(scenario="consumer-lag", seed=1)
    for proto in (
        base.MetricsReader,
        base.LogsReader,
        base.MessagingReader,
        base.CacheReader,
        base.DatastoreReader,
        base.SCMReader,
        base.DeployReader,
        base.FlagReader,
        base.TopologyReader,
        base.Actuator,
    ):
        assert isinstance(ad.metrics, proto), proto.__name__


def test_readers_respect_window() -> None:
    w = generate("hot-partition", 9)
    ad = SyntheticAdapters.from_settings(world=w)
    svc = w.alert.service
    series = ad.metrics.query_range(
        "http_p99_ms", {"service": svc}, w.now - timedelta(minutes=5), w.now
    )
    assert series and all(len(s.points) <= 11 for s in series)
    assert ad.deploys.recent_deploys(svc, w.start) == []  # hot-partition has no deploy
