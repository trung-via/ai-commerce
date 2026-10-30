"""Finite, shared budgets for GitHub GETs only; never wrap a delivery call."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import math
import time
import urllib.error
from urllib.parse import urlsplit


class GitHubReadBlocked(ValueError):
    """Fixed codes only: HTTP error bodies and credentials are private."""


class GitHubReadBudget:
    def __init__(self, *, max_reads=200, max_seconds=600, max_attempts=3,
                 base_delay=1.0, max_delay=60.0, clock=time.monotonic,
                 wall_clock=time.time, sleep=time.sleep):
        if (type(max_reads) is not int or not 1 <= max_reads <= 5000 or
                type(max_attempts) is not int or not 1 <= max_attempts <= 5 or
                any(type(v) not in (int, float) or not math.isfinite(v)
                    for v in (max_seconds, base_delay, max_delay)) or
                not 1 <= max_seconds <= 3600 or
                not 0 < base_delay <= max_delay <= 60):
            raise GitHubReadBlocked("READ_BUDGET_INVALID")
        self.remaining = max_reads
        self.max_attempts = max_attempts
        self.base_delay, self.max_delay = base_delay, max_delay
        self.clock, self.wall_clock, self.sleep = clock, wall_clock, sleep
        self.deadline = clock() + max_seconds

    def timeout(self):
        remaining = self.deadline - self.clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise GitHubReadBlocked("READ_DEADLINE_EXHAUSTED")
        return min(15.0, remaining)

    def pause(self, seconds):
        if (not math.isfinite(seconds) or seconds < 0 or
                seconds >= self.deadline - self.clock()):
            raise GitHubReadBlocked("READ_DEADLINE_EXHAUSTED")
        self.sleep(seconds)
        self.timeout()

    def _delay(self, error, attempt):
        delay = min(self.max_delay, self.base_delay * (2 ** attempt))
        if isinstance(error, urllib.error.HTTPError):
            headers = error.headers or {}
            retry_after = headers.get("Retry-After")
            reset = headers.get("X-RateLimit-Reset")
            if (error.code in (403, 429) and retry_after is None and
                    not (headers.get("X-RateLimit-Remaining") == "0" and reset is not None)):
                # No reliable server time hint: GitHub requires at least a minute
                # for rate limits, then increasing waits or a bounded stop.
                delay = max(delay, 60.0 * (2 ** attempt))
            if retry_after is not None:
                try:
                    hint = float(retry_after)
                except (TypeError, ValueError):
                    try:
                        hint = parsedate_to_datetime(retry_after).timestamp() - self.wall_clock()
                    except (TypeError, ValueError, OverflowError):
                        raise GitHubReadBlocked("RATE_LIMIT_DELAY_INVALID") from None
                if not math.isfinite(hint) or hint < 0:
                    raise GitHubReadBlocked("RATE_LIMIT_DELAY_INVALID")
                delay = max(delay, hint)
            if headers.get("X-RateLimit-Remaining") == "0" and reset is not None:
                try:
                    hint = float(reset) - self.wall_clock()
                except (TypeError, ValueError, OverflowError):
                    raise GitHubReadBlocked("RATE_LIMIT_DELAY_INVALID") from None
                if not math.isfinite(hint):
                    raise GitHubReadBlocked("RATE_LIMIT_DELAY_INVALID")
                delay = max(delay, hint)
        # Never shorten a server delay just to fit our local budget.
        if delay > self.max_delay:
            raise GitHubReadBlocked("RATE_LIMIT_DELAY_EXCEEDS_BUDGET")
        return delay

    @staticmethod
    def _transient(error):
        if isinstance(error, urllib.error.HTTPError):
            headers = error.headers or {}
            return (error.code == 429 or 500 <= error.code <= 599 or
                    (error.code == 403 and
                     (headers.get("Retry-After") is not None or
                      headers.get("X-RateLimit-Remaining") == "0")))
        return (isinstance(error, TimeoutError) or
                (isinstance(error, urllib.error.URLError) and
                 isinstance(error.reason, TimeoutError)))

    def read(self, operation, url, token):
        target = urlsplit(url)
        if (target.scheme != "https" or target.netloc != "api.github.com" or
                target.fragment or not target.path.startswith("/repos/")):
            raise GitHubReadBlocked("READ_URL_INVALID")
        for attempt in range(self.max_attempts):
            self.timeout()
            if self.remaining <= 0:
                raise GitHubReadBlocked("READ_COUNT_EXHAUSTED")
            self.remaining -= 1
            try:
                result = operation(url, token)
            except (urllib.error.URLError, TimeoutError) as error:
                try:
                    if not self._transient(error):
                        raise GitHubReadBlocked("GITHUB_READ_REJECTED") from None
                    if attempt + 1 == self.max_attempts:
                        raise GitHubReadBlocked("GITHUB_READ_RETRY_EXHAUSTED") from None
                    delay = self._delay(error, attempt)
                finally:
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
                if self.remaining <= 0:
                    raise GitHubReadBlocked("READ_COUNT_EXHAUSTED")
                self.pause(delay)
            else:
                self.timeout()
                return result
