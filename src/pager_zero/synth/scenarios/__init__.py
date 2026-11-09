"""Scenario registry: name -> builder(seed) -> World.  (notes D-10)"""

from __future__ import annotations

from collections.abc import Callable

from pager_zero.synth.scenarios.bad_deploy import build as bad_deploy
from pager_zero.synth.scenarios.cache_eviction import build as cache_eviction
from pager_zero.synth.scenarios.consumer_lag import build as consumer_lag
from pager_zero.synth.scenarios.downstream_outage import build as downstream_outage
from pager_zero.synth.scenarios.flag_regression import build as flag_regression
from pager_zero.synth.scenarios.hot_partition import build as hot_partition
from pager_zero.synth.world import World

SCENARIOS: dict[str, Callable[[int], World]] = {
    "bad-deploy": bad_deploy,
    "flag-regression": flag_regression,
    "consumer-lag": consumer_lag,
    "cache-eviction": cache_eviction,
    "hot-partition": hot_partition,
    "downstream-outage": downstream_outage,
}
