"""Controller-side receipt of distinct pytest-xdist workers that collected tests.

Quiet pytest output and a requested `-n 12` are not evidence of actual
execution. The EF01 verifier loads this plugin through PYTEST_PLUGINS without
changing the immutable registered pytest argv.
"""
from __future__ import annotations

import pytest

_COLLECTED_GATEWAYS: set[str] = set()


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids) -> None:
    gateway = getattr(node, "gateway", None)
    worker_id = getattr(gateway, "id", None)
    if isinstance(worker_id, str) and worker_id:
        _COLLECTED_GATEWAYS.add(worker_id)


def pytest_terminal_summary(terminalreporter, exitstatus) -> None:
    # One terminal receipt from the xdist controller, including the serial
    # (zero-xdist-worker) case. Missing or duplicate receipts are unproven.
    terminalreporter.write_line(
        "MT01_XDIST_WORKERS_COLLECTED=" + str(len(_COLLECTED_GATEWAYS))
    )
