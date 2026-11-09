"""Persist a World between processes.

Why: the agent pauses at the approval interrupt and `pz approve` resumes it later, possibly in a
different process; the MCP server is spawned per session. The synthetic world's mutable state
(actions taken, healed series) must survive that, so the server loads/saves it here.
"""

from __future__ import annotations

import pickle
from pathlib import Path

from pager_zero.synth.world import World


def save_world(world: World, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pickle.dumps(world, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(path)


def load_world(path: Path) -> World:
    return pickle.loads(path.read_bytes())  # noqa: S301 — our own file, never untrusted input
