"""Synthetic incident generator (notes D-7, D-10).

`generate(scenario, seed)` builds a `World`: a self-consistent fake universe (services, deploys,
commits, flags, metrics, logs, consumer groups, caches, tables) in which exactly one planted
cause explains the alert, plus the hidden `GroundTruth` the benchmark scores against.
"""

from __future__ import annotations

from pager_zero.synth.world import GroundTruth, World


def generate(scenario: str, seed: int) -> World:
    from pager_zero.synth.scenarios import SCENARIOS

    try:
        builder = SCENARIOS[scenario]
    except KeyError as e:
        raise ValueError(f"unknown scenario {scenario!r}; known: {sorted(SCENARIOS)}") from e
    return builder(seed)


def list_scenarios() -> list[str]:
    from pager_zero.synth.scenarios import SCENARIOS

    return sorted(SCENARIOS)


__all__ = ["World", "GroundTruth", "generate", "list_scenarios"]
