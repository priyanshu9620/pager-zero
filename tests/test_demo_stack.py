"""Demo stack control + real actuator, with `docker compose` and Redis faked out."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pager_zero.demo import DEFAULT_ENV, FAULTS, DemoStack
from pager_zero.demo.actuator import DemoActuator

DEMO = Path(__file__).resolve().parents[1] / "demo"


class FakeRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, check=True, capture_output=True, text=True):
        self.calls.append(cmd)

        class R:
            stdout = "SERVICE STATUS\n"
            returncode = 0

        return R()

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    root.mkdir()
    for name in ("docker-compose.yml", "topology.json", "flags.json", "deploys.json"):
        shutil.copy(DEMO / name, root / name)
    st = DemoStack(root, "redis://localhost:1/0", runner=FakeRun())
    st.init_state(reset=True)
    maxmem: list[str] = []
    monkeypatch.setattr(DemoStack, "redis_maxmemory", lambda self, v: maxmem.append(v))
    st._maxmem = maxmem  # type: ignore[attr-defined]
    return st


def test_init_state_copies_templates_and_defaults(stack):
    assert stack.read_env() == DEFAULT_ENV
    assert (
        json.loads((stack.state / "flags.json").read_text())["values"][
            "shop.offer_cache.ttl_seconds"
        ]
        == 300
    )
    assert json.loads((stack.state / "deploys.json").read_text()) == []


def test_compose_uses_demo_file_and_state_env(stack):
    stack.compose("ps")
    cmd = stack.runner.calls[-1]
    assert cmd[:3] == ["docker", "compose", "-f"]
    assert cmd[3].endswith("demo/docker-compose.yml")
    assert "--env-file" in cmd and cmd[cmd.index("--env-file") + 1].endswith("state.env")


@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_changes_something_real(stack, fault):
    before = (stack.read_env(), (stack.state / "flags.json").read_text(), len(stack.runner.calls))
    msg = stack.chaos(fault)
    assert msg
    after = (stack.read_env(), (stack.state / "flags.json").read_text(), len(stack.runner.calls))
    assert before != after or stack._maxmem, fault


def test_bad_deploy_bumps_version_and_logs_deploy(stack):
    stack.chaos("bad-deploy")
    env = stack.read_env()
    assert env["SHOP_VERSION"] == "v1.5.0" and env["SHOP_SLOW_QUERY"] == "1"
    log = json.loads((stack.state / "deploys.json").read_text())
    assert log[-1]["version"] == "v1.5.0" and log[-1]["previous_version"] == "v1.4.0"
    assert any("shop-api" in c and "up" in c for c in stack.runner.joined())


def test_consumer_lag_scales_to_one_slow_worker(stack):
    stack.chaos("consumer-lag")
    assert stack.read_env()["WORKER_SLEEP_MS"] == "150"
    assert any("--scale worker=1" in c for c in stack.runner.joined())


def test_downstream_outage_stops_payments(stack):
    stack.chaos("downstream-outage")
    assert stack.runner.joined()[-1].endswith("stop payments-mock")


def test_unknown_fault_rejected(stack):
    with pytest.raises(ValueError):
        stack.chaos("hot-partition")


def test_heal_resets_everything(stack):
    stack.chaos("bad-deploy")
    stack.chaos("flag-regression")
    stack.chaos("consumer-lag")
    done = stack.heal()
    env = stack.read_env()
    assert env["SHOP_SLOW_QUERY"] == "0" and env["SHOP_VERSION"] == DEFAULT_ENV["SHOP_VERSION"]
    assert env["WORKER_SLEEP_MS"] == DEFAULT_ENV["WORKER_SLEEP_MS"]
    flags = json.loads((stack.state / "flags.json").read_text())["values"]
    assert flags["shop.offer_cache.ttl_seconds"] == 300
    assert "64mb" in stack._maxmem
    assert any("--scale worker=3" in c for c in stack.runner.joined())
    assert len(done) >= 4


def test_actuator_rollback_is_a_real_redeploy(stack):
    stack.chaos("bad-deploy")
    act = DemoActuator(stack)
    r = act.rollback_deploy("shop-api", "v1.4.0")
    assert r.ok and stack.read_env() == {**DEFAULT_ENV, "SHOP_VERSION": "v1.4.0"}
    log = json.loads((stack.state / "deploys.json").read_text())
    assert log[-1]["actor"] == "pager-zero" and log[-1]["version"] == "v1.4.0"
    assert not act.rollback_deploy("payments-mock", "v9").ok


def test_actuator_other_actions(stack):
    act = DemoActuator(stack)
    assert act.set_flag("shop.offer_cache.ttl_seconds", 300).ok
    assert act.scale_consumers("order-events-consumer", 4).ok
    assert any("--scale worker=4" in c for c in stack.runner.joined())
    assert act.set_cache_limit("offer-cache", 16).ok and stack._maxmem[-1] == "16mb"
    assert act.enable_fallback("shop-api", "payments-mock").ok
    assert (
        json.loads((stack.state / "flags.json").read_text())["values"][
            "shop.fallback_payments.enabled"
        ]
        is True
    )
    assert not act.enable_fallback("shop-api", "shop-db").ok
    assert not act.rate_limit_key("t", "k", 10).ok
