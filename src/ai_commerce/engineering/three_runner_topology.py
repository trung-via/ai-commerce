"""Pure plan for the HUMAN-attended, single-account Windows runner rollout.

Source/configuration evidence only: this module neither registers a runner nor
authorizes any new HostOps command, GitHub credential or browser/CDP action.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerSpec:
    name: str
    label: str
    root: str
    task: str


EXISTING_RUNNER = RunnerSpec(
    "AI-COMMERCE-EF02-01", "ai-commerce-ef02",
    r"C:\TOOL\ai-commerce-runner", "AI-Commerce-EF02-Interactive",
)

NEW_RUNNERS = (
    RunnerSpec(
        "AI-COMMERCE-EF02-02", "ai-commerce-ef02",
        r"C:\TOOL\ai-commerce-runner-ef02-02", "AI-Commerce-EF02-02-Interactive",
    ),
    RunnerSpec(
        "AI-COMMERCE-HOSTOPS-01", "ai-commerce-hostops",
        r"C:\TOOL\ai-commerce-runner-hostops-01", "AI-Commerce-HostOps-01-Interactive",
    ),
)


def planned_topology() -> tuple[RunnerSpec, ...]:
    """Three runner instances, not three Windows accounts or machines."""
    return (EXISTING_RUNNER, *NEW_RUNNERS)


def hostops_route_activation_allowed(online_runner_names: set[str]) -> bool:
    """Do not move HostOps YAML before the dedicated runner is proven online."""
    return "AI-COMMERCE-HOSTOPS-01" in online_runner_names


def resource_policy() -> str:
    """Human elected observation first, no CPU/RAM quotas in this release."""
    return "OBSERVE_ONLY_NO_CPU_RAM_LIMITS"
