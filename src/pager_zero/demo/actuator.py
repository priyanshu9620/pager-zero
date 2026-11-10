"""A REAL actuator for the demo stack. Selected with ACTUATOR=demo (D-35: never the default).

Every action here changes the running containers / Redis / flag file, so `verify` reads a real
recovery from Prometheus — or a real non-recovery if the agent chose the wrong fix.
"""

from __future__ import annotations

import json
from typing import Any

from pager_zero.adapters.base import ActionResult
from pager_zero.demo import DEFAULT_ENV, DemoStack


class DemoActuator:
    def __init__(self, stack: DemoStack) -> None:
        self.stack = stack

    @staticmethod
    def _ok(action: str, target: dict[str, Any], detail: str) -> ActionResult:
        return ActionResult(True, action, target, detail)

    def rollback_deploy(self, service: str, to_version: str) -> ActionResult:
        target = {"service": service, "to_version": to_version}
        if service != "shop-api":
            return ActionResult(
                False, "rollback_deploy", target, f"{service}: not a redeployable demo service"
            )
        e = self.stack.redeploy_shop(to_version, slow_query=False, actor="pager-zero")
        return self._ok(
            "rollback_deploy",
            target,
            f"shop-api {e['previous_version']} → {to_version} (container restarted)",
        )

    def set_flag(self, key: str, value: Any) -> ActionResult:
        old = self.stack.set_flag(key, value, actor="pager-zero")
        return self._ok("set_flag", {"key": key, "value": value}, f"{key}: {old!r} → {value!r}")

    def scale_consumers(self, group: str, members: int) -> ActionResult:
        self.stack.scale_workers(members)
        return self._ok(
            "scale_consumers",
            {"group": group, "members": members},
            f"{group} → {members} members (docker compose --scale)",
        )

    def restart_consumer_group(self, group: str) -> ActionResult:
        self.stack.restart_workers()
        return self._ok("restart_consumer_group", {"group": group}, f"{group} workers restarted")

    def set_cache_limit(self, name: str, max_memory_mb: int) -> ActionResult:
        self.stack.redis_maxmemory(f"{int(max_memory_mb)}mb")
        return self._ok(
            "set_cache_limit",
            {"name": name, "max_memory_mb": max_memory_mb},
            f"{name} maxmemory → {max_memory_mb} MB (redis CONFIG SET)",
        )

    def rate_limit_key(self, table: str, key: str, rps: int) -> ActionResult:
        target = {"table": table, "key": key, "rps": rps}
        return ActionResult(
            False, "rate_limit_key", target, "demo stack has no per-key rate limiter"
        )

    def enable_fallback(self, service: str, dependency: str) -> ActionResult:
        target = {"service": service, "dependency": dependency}
        if service == "shop-api" and dependency == "payments-mock":
            self.stack.set_flag("shop.fallback_payments.enabled", True, actor="pager-zero")
            return self._ok(
                "enable_fallback",
                target,
                "shop.fallback_payments.enabled → true (/checkout defers instead of calling payments)",
            )
        return ActionResult(
            False, "enable_fallback", target, f"no fallback flag for {service} → {dependency}"
        )

    def describe(self) -> str:
        return json.dumps({"stack": str(self.stack.root), "defaults": DEFAULT_ENV})
