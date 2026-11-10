"""Demo stack control: a real containerised system to break and let the agent fix (notes D-38).

`DemoStack` wraps `docker compose -f demo/docker-compose.yml`. Runtime knobs (service version,
fault toggles) live in `demo/state/state.env`, which compose interpolates; flags / deploy log /
topology live in `demo/state/*.json`, which the live adapters read and the actuator writes.

Chaos (`pz demo chaos <fault>`) does REAL damage to the stack — nothing here is simulated:
  bad-deploy          restart shop-api as a new version with SLOW_QUERY=1 (N+1 per cache miss)
  flag-regression     set shop.offer_cache.ttl_seconds = 0 (every read hits Postgres)
  cache-eviction      redis CONFIG SET maxmemory 2mb (the working set is ~4 MB → eviction storm)
  consumer-lag        worker sleep 5 → 150 ms and scale the group 3 → 1 member
  downstream-outage   docker compose stop payments-mock
Alertmanager then pages `pz serve`, which opens a live incident. `pz demo heal` resets everything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FAULTS = ("bad-deploy", "flag-regression", "cache-eviction", "consumer-lag", "downstream-outage")
DEFAULT_ENV = {"SHOP_VERSION": "v1.4.0", "SHOP_SLOW_QUERY": "0", "WORKER_SLEEP_MS": "5"}
HEALTHY_REDIS_MAXMEMORY = "64mb"


@dataclass
class DemoStack:
    root: Path = Path("demo")
    redis_url: str = "redis://localhost:6380/0"
    runner: Any = field(default=None, repr=False)  # injectable subprocess.run for tests

    # ------------------------------------------------------------ paths & state

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def env_file(self) -> Path:
        return self.state / "state.env"

    def read_env(self) -> dict[str, str]:
        out = dict(DEFAULT_ENV)
        if self.env_file.exists():
            for line in self.env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
        return out

    def write_env(self, **changes: str) -> dict[str, str]:
        env = {**self.read_env(), **changes}
        self.state.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        return env

    def init_state(self, reset: bool = False) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        for name in ("topology.json", "flags.json", "deploys.json"):
            dst = self.state / name
            if reset or not dst.exists():
                shutil.copy(self.root / name, dst)
        if reset or not self.env_file.exists():
            self.write_env(**DEFAULT_ENV)

    # ------------------------------------------------------------ docker compose

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [
            "docker",
            "compose",
            "-f",
            str(self.root / "docker-compose.yml"),
            "--env-file",
            str(self.env_file),
            *args,
        ]
        run = self.runner or subprocess.run
        return run(cmd, check=check, capture_output=True, text=True)

    def up(self, reset: bool = True) -> None:
        self.init_state(reset=reset)
        self.compose("up", "-d", "--build", "--remove-orphans", "--scale", "worker=3")

    def down(self, volumes: bool = False) -> None:
        self.compose("down", *(["-v"] if volumes else []), check=False)

    def ps(self) -> str:
        return self.compose("ps", "--format", "table {{.Service}}\t{{.Status}}", check=False).stdout

    # ------------------------------------------------------------ primitives (used by chaos AND the actuator)

    def redeploy_shop(self, version: str, slow_query: bool, actor: str) -> dict[str, Any]:
        prev = self.read_env()["SHOP_VERSION"]
        self.write_env(SHOP_VERSION=version, SHOP_SLOW_QUERY="1" if slow_query else "0")
        self.compose("up", "-d", "--no-deps", "shop-api")
        entry = {
            "service": "shop-api",
            "version": version,
            "previous_version": prev,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit_sha": f"{abs(hash(version)) % 0xFFFFFFF:07x}",
            "actor": actor,
            "status": "succeeded",
        }
        path = self.state / "deploys.json"
        log = json.loads(path.read_text()) if path.exists() else []
        log.append(entry)
        path.write_text(json.dumps(log, indent=2))
        return entry

    def set_flag(self, key: str, value: Any, actor: str) -> Any:
        path = self.state / "flags.json"
        doc = json.loads(path.read_text()) if path.exists() else {"values": {}, "history": []}
        old = doc["values"].get(key)
        doc["values"][key] = value
        doc["history"].append(
            {
                "key": key,
                "old": old,
                "new": value,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "actor": actor,
            }
        )
        path.write_text(json.dumps(doc, indent=2, default=str))
        return old

    def redis_maxmemory(self, value: str) -> None:
        import redis

        redis.Redis.from_url(self.redis_url, socket_timeout=3).config_set("maxmemory", value)

    def scale_workers(self, members: int, sleep_ms: int | None = None) -> None:
        if sleep_ms is not None:
            self.write_env(WORKER_SLEEP_MS=str(sleep_ms))
        self.compose("up", "-d", "--no-deps", "--scale", f"worker={members}", "worker")

    def restart_workers(self) -> None:
        self.compose("restart", "worker")

    def set_dependency(self, name: str, running: bool) -> None:
        self.compose("start" if running else "stop", name)

    # ------------------------------------------------------------ chaos

    def chaos(self, fault: str) -> str:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}; known: {FAULTS}")
        if fault == "bad-deploy":
            cur = self.read_env()["SHOP_VERSION"]
            major, minor, _ = cur.lstrip("v").split(".")
            new = f"v{major}.{int(minor) + 1}.0"
            e = self.redeploy_shop(new, slow_query=True, actor="ci")
            return f"deployed shop-api {e['previous_version']} → {new} (contains an N+1 query per cache miss)"
        if fault == "flag-regression":
            old = self.set_flag("shop.offer_cache.ttl_seconds", 0, actor="flag-console:someone")
            return f"shop.offer_cache.ttl_seconds {old!r} → 0 (cache disabled; every read hits Postgres)"
        if fault == "cache-eviction":
            self.redis_maxmemory("2mb")
            return "offer-cache maxmemory 64mb → 2mb (working set ~4 MB → eviction storm)"
        if fault == "consumer-lag":
            self.scale_workers(1, sleep_ms=150)
            return "order-events-consumer: 3 → 1 member, 5 → 150 ms per message (lag grows ~13/s)"
        self.set_dependency("payments-mock", running=False)
        return "payments-mock stopped (shop-api /checkout → 502)"

    def heal(self) -> list[str]:
        done = []
        env = self.read_env()
        if env["SHOP_SLOW_QUERY"] != "0":
            self.redeploy_shop(DEFAULT_ENV["SHOP_VERSION"], slow_query=False, actor="pz demo heal")
            done.append("shop-api redeployed healthy")
        if (
            json.loads((self.state / "flags.json").read_text())["values"]
            != json.loads((self.root / "flags.json").read_text())["values"]
        ):
            for k, v in json.loads((self.root / "flags.json").read_text())["values"].items():
                self.set_flag(k, v, actor="pz demo heal")
            done.append("flags reset")
        try:
            self.redis_maxmemory(HEALTHY_REDIS_MAXMEMORY)
            done.append(f"redis maxmemory {HEALTHY_REDIS_MAXMEMORY}")
        except Exception as e:  # noqa: BLE001
            done.append(f"redis unreachable: {e}")
        self.scale_workers(3, sleep_ms=int(DEFAULT_ENV["WORKER_SLEEP_MS"]))
        done.append("workers 3 × 5 ms")
        self.set_dependency("payments-mock", running=True)
        done.append("payments-mock running")
        return done

    def wait_healthy(self, timeout_s: int = 120) -> bool:
        import httpx

        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                if httpx.get("http://localhost:8010/healthz", timeout=2).status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        return False
