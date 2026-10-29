"""Human default: twelve pytest workers per action, independent of EF02 slots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import configparser
import importlib.util
import os
from pathlib import Path
import sys
import shlex
import tomllib

SELECTED_WORKERS = 12
BENCHMARK_PROVENANCE = "HUMAN_REPORTED_PRIOR_BENCHMARK"


@dataclass(frozen=True)
class HostCapacity:
    cpus: int | None
    available_memory_bytes: int | None


def host_capacity() -> HostCapacity:
    try:
        cpus = getattr(os, "process_cpu_count", os.cpu_count)()
    except (OSError, ValueError):
        cpus = None
    memory = None
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total_physical", "available_physical", "total_page", "available_page",
                    "total_virtual", "available_virtual", "available_extended")]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                memory = status.available_physical
        except (OSError, AttributeError):
            pass
    elif hasattr(os, "sysconf"):
        try:
            memory = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError):
            pass
    return HostCapacity(cpus, memory)


def registered_workers(argv: list[str]) -> int:
    """Only the finite registered suffix is eligible; never accept auto/arbitrary argv."""
    if argv[1:3] != ["-m", "pytest"]:
        raise ValueError("REGISTERED_PYTEST_REQUIRED")
    parallel = [i for i, arg in enumerate(argv[3:], 3)
                if arg.startswith(("-n", "--numprocesses", "--dist"))]
    if not parallel:
        return 0
    if len(parallel) != 1 or argv[parallel[0]:] != ["-n", "12"]:
        raise ValueError("REGISTERED_PYTEST_REQUIRES_12_EACH")
    return SELECTED_WORKERS


def worker_decision(*, profile: str = "windows-12", capacity: HostCapacity,
                    host_slots: int = 1, xdist_available: bool = False,
                    isolation_approved: bool = False, flaky: bool = False,
                    windows: bool = False, registered_only: bool = False,
                    registered_argv: list[str] | None = None) -> dict:
    if profile != "windows-12" or type(host_slots) is not int or not 1 <= host_slots <= 128:
        raise ValueError("INVALID_PYTEST_PROFILE_OR_HOST_SLOTS")
    if flaky:
        raise ValueError("PYTEST_FIXTURE_FAILURE_STOP")
    workers, reason, degradation = SELECTED_WORKERS, "HUMAN_DEFAULT_12_EACH_ACTION", None
    if registered_only and registered_workers(list(registered_argv or ["python", "-m", "pytest"])) == 0:
        # Preserve the authoritative profile verbatim. This is a registration
        # gap, not a resource fallback or evidence of twelve-worker execution.
        workers, reason = 0, "EF02_REGISTERED_SERIAL_ARGV_COMPANION_REGISTRATION_REQUIRED"
    elif not xdist_available:
        if registered_only:
            raise ValueError("XDIST_UNAVAILABLE_REGISTERED_PARALLEL_PROFILE_STOP")
        workers, reason, degradation = 0, "XDIST_UNAVAILABLE", "XDIST_UNAVAILABLE"
    return {"profile": profile, "intended_workers": SELECTED_WORKERS,
            "workers_requested": SELECTED_WORKERS, "workers_actual": None,
            "workers": workers, "reason": reason, "host_slots": host_slots,
            "registered_only": registered_only, "degradation_reason": degradation,
            "execution_status": "NOT_EXECUTED", "windows": windows,
            "capacity": asdict(capacity), "benchmark_provenance": BENCHMARK_PROVENANCE,
            "prior_compared_workers": [2, 4, 8, 12, 16], "observed_workers": None}


def profile_command(argv: list[str], *, decision: dict, temp_dir: Path) -> list[str]:
    if decision["registered_only"]:
        if registered_workers(argv) != decision["workers"]:
            raise ValueError("REGISTERED_PYTEST_WORKER_POLICY_MISMATCH")
        return list(argv)
    if not decision["workers"]:
        return list(argv)
    if list(argv[1:3]) != ["-m", "pytest"] or any(a in ("-n", "--numprocesses", "--basetemp") or
                                                  a.startswith(("-n", "--numprocesses=", "--basetemp=")) for a in argv[3:]):
        raise ValueError("PYTEST_PROFILE_REQUIRES_PLAIN_PYTHON_PYTEST_ARGV")
    return [*argv, "-n", str(decision["workers"]), "--dist", "loadfile", "--basetemp", str(temp_dir)]


def require_managed_parallelism(repo: Path, argv: list[str], *, coverage_required: bool = False,
                                registered_only: bool = False) -> None:
    """Reject implicit worker overrides and filtering of claimed MT-01 AC scope."""
    sources = {"argv": argv[3:], "PYTEST_ADDOPTS": shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))}
    # Fail closed on config formats not interpreted by this bounded profile.
    if any((repo / name).exists() for name in ("pytest.toml", ".pytest.toml", ".pytest.ini")):
        raise ValueError("PYTEST_CONFIG_SCOPE_UNPROVEN: unsupported config format")
    project = repo / "pyproject.toml"
    if project.is_file():
        pytest_options = tomllib.loads(project.read_text(encoding="utf-8-sig")).get("tool", {}).get("pytest", {})
        if set(pytest_options) - {"ini_options"}:
            raise ValueError("PYTEST_CONFIG_SCOPE_UNPROVEN: unsupported pytest settings")
        options = pytest_options.get("ini_options", {})
        addopts = options.get("addopts", "")
        sources["pyproject.toml"] = shlex.split(addopts) if isinstance(addopts, str) else addopts
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        path = repo / name
        if path.is_file():
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(path.read_text(encoding="utf-8-sig"))
            sources[name] = shlex.split(config.get(section, "addopts", fallback=""))
    for source, args in sources.items():
        checked = args
        if source == "argv" and registered_only:
            if registered_workers(argv):
                checked = args[:-2]
        if any(arg.startswith(("-n", "--numprocesses", "--dist")) for arg in checked):
            raise ValueError("PYTEST_UNMANAGED_WORKERS: " + source + "; use twelve workers per action")
        if coverage_required and any(arg.startswith(("-k", "-m", "--ignore", "--deselect", "--collect-only",
                                                    "--co", "--override-ini", "-o", "-c", "--lf", "--last-failed",
                                                    "--sw", "--stepwise", "--ff", "--failed-first")) for arg in args):
            raise ValueError("PYTEST_FILTERED_SCOPE_UNPROVEN: " + source)


def xdist_available() -> bool:
    return importlib.util.find_spec("xdist") is not None and os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1"
