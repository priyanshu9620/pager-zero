"""Adapters: the only way the agent / MCP tools reach data.

Everything above this package imports from `adapters.base` (Protocols) and obtains concrete
implementations through `adapters.get_adapters()`. Backend is chosen by `ADAPTER_BACKEND`.
"""

from __future__ import annotations

from pager_zero.adapters.base import Adapters


def get_adapters(**kwargs) -> Adapters:
    from pager_zero.config import AdapterBackend, settings

    if settings.adapter_backend is AdapterBackend.synthetic:
        from pager_zero.adapters.synthetic import SyntheticAdapters

        return SyntheticAdapters.from_settings(**kwargs)
    from pager_zero.adapters.live import LiveAdapters  # phase 7

    return LiveAdapters.from_settings(**kwargs)
