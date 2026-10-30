"""Read retries use synthetic HTTP failures and clocks; no network or sending."""
from email.message import Message
from email.utils import formatdate
import io
import json
import urllib.error

import pytest

from ai_commerce.remote.github_read_retry import GitHubReadBudget, GitHubReadBlocked
from ai_commerce.remote.github_artifact_authority import GitHubRestArtifactAuthority

URL = "https://api.github.com/repos/trung-via/ai-commerce/actions/runs/120"


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay

    def budget(self, **options):
        return GitHubReadBudget(clock=lambda: self.now, wall_clock=lambda: 1000 + self.now,
                                sleep=self.sleep, **options)


def http(code, **headers):
    message = Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = str(value)
    return urllib.error.HTTPError(URL, code, "PRIVATE remote payload", message, io.BytesIO(b"secret"))


def sequence(*results):
    calls = []
    iterator = iter(results)
    def read(url, token):
        calls.append((url, token))
        result = next(iterator)
        if isinstance(result, BaseException):
            raise result
        return result
    return read, calls


@pytest.mark.parametrize("error", [http(429), http(500), http(502), http(503), http(599),
    http(403, Retry_After=2), http(403, X_RateLimit_Remaining=0),
    TimeoutError("private"), urllib.error.URLError(TimeoutError("private"))])
def test_only_transient_get_failures_can_recover(error):
    clock = Clock()
    read, calls = sequence(error, {"id": 120})
    assert clock.budget().read(read, URL, "fixture-token") == {"id": 120}
    assert len(calls) == 2
    assert len(clock.sleeps) == 1


@pytest.mark.parametrize("error", [http(400), http(401), http(403), http(404), http(409), http(422),
    urllib.error.URLError("name resolution failed")])
def test_nontransient_denial_stops_immediately_and_redacts(error):
    clock = Clock()
    read, calls = sequence(error, "must never run")
    with pytest.raises(GitHubReadBlocked, match="^GITHUB_READ_REJECTED$"):
        clock.budget().read(read, URL, "private-token")
    assert len(calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("error", [ValueError("DIGEST_INVALID"), ValueError("RUN_MISMATCH"),
    json.JSONDecodeError("invalid json", "x", 0)])
def test_payload_and_identity_failures_are_never_retried(error):
    clock = Clock()
    read, calls = sequence(error, "must never run")
    with pytest.raises(type(error)):
        clock.budget().read(read, URL, "fixture-token")
    assert len(calls) == 1 and clock.sleeps == []


@pytest.mark.parametrize("headers,expected", [
    ({"Retry_After": "4"}, 4),
    ({"Retry_After": formatdate(1005, usegmt=True)}, 5),
    ({"X_RateLimit_Remaining": "0", "X_RateLimit_Reset": "1007"}, 7),
])
def test_server_delay_is_honored(headers, expected):
    clock = Clock()
    read, _ = sequence(http(429, **headers), "ok")
    assert clock.budget().read(read, URL, "t") == "ok"
    assert clock.sleeps == [expected]


@pytest.mark.parametrize("error", [http(429), http(403, X_RateLimit_Remaining=0)])
def test_rate_limit_without_timing_hint_waits_at_least_one_minute(error):
    clock = Clock()
    read, calls = sequence(error, "ok")
    assert clock.budget().read(read, URL, "t") == "ok"
    assert len(calls) == 2 and clock.sleeps == [60]


@pytest.mark.parametrize("headers", [{"Retry_After": "100"}, {"Retry_After": "nan"},
    {"Retry_After": "broken"}, {"Retry_After": "-1"},
    {"X_RateLimit_Remaining": "0", "X_RateLimit_Reset": "nan"}])
def test_unhonorable_server_delay_blocks_without_shortening_it(headers):
    clock = Clock()
    read, calls = sequence(http(429, **headers), "must not run")
    with pytest.raises(GitHubReadBlocked):
        clock.budget().read(read, URL, "t")
    assert len(calls) == 1 and clock.sleeps == []


def test_exponential_backoff_and_exact_attempt_exhaustion():
    clock = Clock()
    read, calls = sequence(http(500), http(503), http(429), "must not run")
    with pytest.raises(GitHubReadBlocked, match="RETRY_EXHAUSTED"):
        clock.budget().read(read, URL, "t")
    assert len(calls) == 3 and clock.sleeps == [1, 2]


def test_shared_read_count_covers_distinct_gets_and_retries():
    clock = Clock()
    budget = clock.budget(max_reads=2)
    read, calls = sequence("first", http(503), "must not run")
    assert budget.read(read, URL, "t") == "first"
    with pytest.raises(GitHubReadBlocked, match="READ_COUNT_EXHAUSTED"):
        budget.read(read, URL + "/artifacts", "t")
    assert len(calls) == 2 and clock.sleeps == []


def test_deadline_prevents_retry_poll_and_late_response_adoption():
    clock = Clock()
    read, calls = sequence(http(429, Retry_After=5), "must not run")
    with pytest.raises(GitHubReadBlocked, match="DEADLINE"):
        clock.budget(max_seconds=5).read(read, URL, "t")
    assert len(calls) == 1 and clock.sleeps == []
    budget = clock.budget(max_seconds=2)
    with pytest.raises(GitHubReadBlocked, match="DEADLINE"):
        budget.pause(2)
    def late(url, token):
        clock.now += 3
        return "late"
    with pytest.raises(GitHubReadBlocked, match="DEADLINE"):
        budget.read(late, URL, "t")


@pytest.mark.parametrize("options", [{"max_reads": True}, {"max_reads": 0}, {"max_reads": 5001},
    {"max_seconds": float("nan")}, {"max_seconds": 0}, {"max_seconds": 3601},
    {"max_attempts": 0}, {"max_attempts": 6}, {"max_attempts": True},
    {"base_delay": 0}, {"max_delay": 61}])
def test_invalid_budget_rejected_before_any_operation(options):
    with pytest.raises(GitHubReadBlocked, match="BUDGET_INVALID"):
        Clock().budget(**options)


@pytest.mark.parametrize("url", ["http://api.github.com/repos/a/b", "https://evil.test/repos/a/b",
    "https://api.github.com@evil.test/repos/a/b", URL + "#unsafe"])
def test_non_github_url_never_calls_operation(url):
    with pytest.raises(GitHubReadBlocked, match="READ_URL_INVALID"):
        Clock().budget().read(lambda *a: pytest.fail("unexpected read"), url, "t")


def test_independent_d31_authority_uses_same_shared_budget():
    clock = Clock()
    budget = clock.budget(max_reads=2)
    authority = GitHubRestArtifactAuthority(repository="trung-via/ai-commerce", workflow_id=1,
                                          token_provider=lambda: "t", read_budget=budget)
    read, calls = sequence(http(503), {"id": 120}, "must not run")
    assert authority._get(read, URL, "t") == {"id": 120}
    with pytest.raises(GitHubReadBlocked, match="COUNT_EXHAUSTED"):
        authority._get(read, URL, "t")
    assert len(calls) == 2


def test_default_transport_receives_remaining_deadline(monkeypatch):
    from ai_commerce.remote import github_artifact_authority as module
    clock = Clock()
    budget = clock.budget(max_seconds=2)
    authority = GitHubRestArtifactAuthority(repository="trung-via/ai-commerce", workflow_id=1,
                                          token_provider=lambda: "t", read_budget=budget)
    seen = []
    def fetch(url, token, **options):
        seen.append(options["timeout"])
        return b'{"id":120}'
    monkeypatch.setattr(module, "_fetch", fetch)
    assert authority._get(authority._read_json, URL, "t") == {"id": 120}
    assert seen == [2]
