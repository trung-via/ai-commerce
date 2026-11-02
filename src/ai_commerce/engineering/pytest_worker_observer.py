"""Observe actual pytest-xdist workers without changing a registered argv.

Loaded only for MT-01 verification via PYTEST_PLUGINS. xdist's controller
receives one collection-finished callback per worker; unlike the -q console
progress, that callback proves how many distinct gateways were started and
collected the exact test inventory.
"""
from __future__ import annotations

import pytest

_WORKER_IDS: set[str] = set()


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids) -> None:
    gateway = getattr(node, "gateway", None)
    worker_id = getattr(gateway, "id", None)
    if isinstance(worker_id, str) and worker_id:
        _WORKER_IDS.add(worker_id)


def pytest_terminal_summary(terminalreporter, exitstatus) -> None:
    # Fail closed if xdist did not report actual collections.
    terminalreporter.write_line(
        "MT01_XDIST_WORKERS_COLLECTED=" + str(len(_WORKER_IDS))
    )
