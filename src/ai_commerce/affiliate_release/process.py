"""Argv-only local tools with live output and wall-clock bounds, including Windows."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class ToolFailure(RuntimeError):
    def __init__(self, code: str, status: str = "UNKNOWN"):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ToolOutput:
    stdout: bytes
    stderr: bytes


class BoundedToolRunner:
    """Drain both pipes without spooling unlimited bytes to memory or disk.

    Only fixed local ffprobe/ffmpeg argv is constructed by the inspector. Child
    environments contain OS/tool necessities, rather than inherited credentials.
    """

    def __init__(self, directory: Path, *, timeout_seconds: float, max_output_bytes: int):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
            raise ValueError("Tool timeout must be in (0, 60] seconds")
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 1024 * 1024:
            raise ValueError("Tool output bound must be in [1, 1048576] bytes")
        self.directory = directory
        self.timeout_seconds, self.max_output_bytes = timeout_seconds, max_output_bytes

    def run(self, argv: list[str], *, strict_errors: bool = True) -> ToolOutput:
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP")
               if key in os.environ}
        env.update(LC_ALL="C", LANG="C", AV_LOG_FORCE_NOCOLOR="1")
        try:
            process = subprocess.Popen(
                argv, cwd=self.directory, env=env, shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise ToolFailure("MEDIA_TOOL_UNAVAILABLE") from exc

        buffers = [bytearray(), bytearray()]
        lock, exceeded, read_failed = threading.Lock(), threading.Event(), threading.Event()
        total = 0

        def drain(pipe, index):
            nonlocal total
            try:
                with pipe:
                    while chunk := pipe.read(16 * 1024):
                        with lock:
                            available = max(0, self.max_output_bytes - total)
                            buffers[index].extend(chunk[:available])
                            total += len(chunk)
                            if total > self.max_output_bytes:
                                exceeded.set()
            except OSError:
                read_failed.set()

        threads = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
                   for index, pipe in enumerate((process.stdout, process.stderr))]
        for thread in threads:
            thread.start()
        deadline, failure = time.monotonic() + self.timeout_seconds, None
        try:
            while process.poll() is None:
                if exceeded.is_set():
                    failure = "MEDIA_TOOL_OUTPUT_LIMIT"
                    break
                if read_failed.is_set():
                    failure = "MEDIA_TOOL_IO_FAILED"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "MEDIA_TOOL_TIMEOUT"
                    break
                exceeded.wait(min(0.02, remaining))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=1)
        if any(thread.is_alive() for thread in threads) or read_failed.is_set():
            raise ToolFailure("MEDIA_TOOL_IO_FAILED")
        if exceeded.is_set():
            raise ToolFailure("MEDIA_TOOL_OUTPUT_LIMIT")
        if failure:
            raise ToolFailure(failure)
        if process.returncode < 0 or process.returncode & 0xFFFFFFFF >= 0xC0000000:
            raise ToolFailure("MEDIA_TOOL_UNAVAILABLE")
        result = ToolOutput(bytes(buffers[0]), bytes(buffers[1]))
        if process.returncode != 0 or (strict_errors and result.stderr.strip()):
            detail = result.stderr.lower()
            if any(marker in detail for marker in (b"cannot allocate memory", b"failed to allocate",
                                                   b"exceeds max_alloc", b"resource temporarily unavailable")):
                raise ToolFailure("MEDIA_TOOL_RESOURCE_LIMIT")
            if any(marker in detail for marker in (b"no decoder found", b"unknown decoder",
                                                   b"decoder not found", b"decoding not supported")):
                raise ToolFailure("MEDIA_TOOL_DECODER_UNAVAILABLE")
            raise ToolFailure("MEDIA_TOOL_REJECTED_INPUT", "FAIL")
        return result
