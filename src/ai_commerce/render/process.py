"""Argv-only, time-bounded tools with disk-spooled and bounded diagnostics."""

from __future__ import annotations

import math
import subprocess
import tempfile
import time
from pathlib import Path

from .models import RenderError

MAX_OUTPUT = 1024 * 1024
MAX_LOG = 64 * 1024


class ToolRunner:
    def __init__(self, directory: Path, timeout: float, diagnostics: list[dict]):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("Tool timeout must be a finite number")
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("Tool timeout must be in (0, 3600] seconds")
        self.directory, self.timeout, self.diagnostics = directory, timeout, diagnostics

    def run(self, argv: list[str], stage: str, *, strict_errors: bool = True) -> bytes:
        started = time.monotonic()
        code, timed_out, launch_error = None, False, None
        with tempfile.TemporaryFile(dir=self.directory) as out, tempfile.TemporaryFile(dir=self.directory) as err:
            try:
                completed = subprocess.run(
                    argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    shell=False, timeout=self.timeout, check=False,
                )
                code = completed.returncode
            except subprocess.TimeoutExpired:
                # subprocess.run kills and waits for its child before raising.
                timed_out = True
            except OSError as exc:
                launch_error = f"{type(exc).__name__}: {exc}"
            except BaseException:
                self._record(argv, stage, started, out, err, code, False, "Interrupted")
                raise
            stdout, stderr = self._record(argv, stage, started, out, err, code, timed_out, launch_error)
            if timed_out:
                raise RenderError("RENDER_TIMEOUT",
                                  f"{stage} exceeded {self.timeout}s: {argv[0]}", stage)
            if launch_error:
                raise RenderError("ENVIRONMENT_TOOL_UNAVAILABLE",
                                  f"Cannot execute {argv[0]}: {launch_error}", stage)
            status = code & 0xFFFFFFFF
            if 0xC0000000 <= status <= 0xCFFFFFFF:
                raise RenderError("ENVIRONMENT_TOOL_UNAVAILABLE",
                                  f"{argv[0]} failed to initialize: exit {code} (0x{status:08X})", stage)
            if out.tell() > MAX_OUTPUT:
                raise RenderError("TOOL_OUTPUT_LIMIT", "Tool stdout exceeded 1 MiB", stage)
            if code != 0 or (strict_errors and stderr.strip()):
                detail = stderr.decode("utf-8", "replace").strip()
                raise RenderError("RENDER_PROCESS_FAILED",
                                  f"{stage}: exit {code}: {detail[-4096:]}", stage)
            return stdout

    def _record(self, argv, stage, started, out, err, code, timed_out, launch_error):
        out_size, err_size = out.tell(), err.tell()
        out.seek(0)
        stdout = out.read(MAX_OUTPUT)
        # Retain the tail of stderr, which includes the useful failure reason.
        err.seek(max(0, err_size - MAX_LOG))
        stderr = err.read(MAX_LOG)
        self.diagnostics.append({
            "stage": stage, "argv": list(argv), "returncode": code,
            "timeout_seconds": self.timeout, "timed_out": timed_out,
            "launch_error": launch_error, "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout": stdout[:MAX_LOG].decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "stdout_bytes": out_size, "stderr_bytes": err_size,
            "stdout_truncated": out_size > MAX_LOG, "stderr_truncated": err_size > MAX_LOG,
        })
        # Restore positions for the overflow check.
        out.seek(out_size)
        return stdout, stderr
