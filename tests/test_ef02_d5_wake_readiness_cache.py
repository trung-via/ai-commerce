"""Pure/synthetic fast-attestation cache tests. No PowerShell/Chrome/CDP."""
from ai_commerce.remote import chrome_acquisition as ca
from ai_commerce.remote.wake_readiness_cache import ChromeAttestationCache


def cache(*, live=None):
    obj = object.__new__(ChromeAttestationCache)
    obj._fingerprint = lambda: "fingerprint-v1"
    identity = live or {
        "address": "127.0.0.1", "pid": 15924,
        "creation": "2026-11-02T12:30:00.0000000Z", "session": 1,
    }
    obj.identity_reader = lambda: identity
    obj._valid_live = lambda value: (
        value.get("address") == "127.0.0.1" and
        value.get("pid") == 15924 and value.get("session") == 1
    )
    obj.protocol_probe = lambda: None
    obj._read = lambda: {
        "v": 1, "fingerprint": "fingerprint-v1",
        "pid": 15924, "creation": "2026-11-02T12:30:00.0000000Z",
        "session": 1,
    }
    return obj


def test_fast_reuse_when_same_live_identity_and_protocol():
    c = cache()
    result = c.reuse()
    assert result["status"] == "READY_ATTACH"
    assert result["cdp_verified"] is True
    assert result["verification"] == "LIVE_PID_CREATION_AND_CDP_REUSED"
    assert result["launched"] is False


def test_unchanged_live_process_never_forces_periodic_full_rescan():
    assert cache().reuse()["status"] == "READY_ATTACH"


def test_fast_cache_rejected_if_pid_creation_changed():
    c = cache(live={
        "address": "127.0.0.1", "pid": 15924,
        "creation": "2026-11-02T13:00:00.0000000Z", "session": 1,
    })
    assert c.reuse() is None


def test_fast_cache_rejected_if_fingerprint_changed():
    c = cache()
    c._fingerprint = lambda: "different-config"
    assert c.reuse() is None


def test_fast_cache_rejected_if_protocol_rejects():
    c = cache()
    def fail():
        raise ca.AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
    c.protocol_probe = fail
    assert c.reuse() is None


def test_fast_reuse_does_not_invoke_full_acquisition(monkeypatch):
    c = cache()
    def prohibited(*a, **k):
        raise AssertionError("Full Chrome enumeration must be skipped")
    monkeypatch.setattr(ca, "run", prohibited)
    assert c.acquire(opt_in_launch=True)["status"] == "READY_ATTACH"


def test_cache_miss_runs_full_acquisition_once(monkeypatch):
    c = cache(live={"address": "127.0.0.2", "pid": 15924,
                    "creation": "2026-11-02T12:30:00.0000000Z",
                    "session": 1})
    calls = []
    def fallback(env, *, launch, opt_in):
        calls.append((launch, opt_in))
        return {"status": "NOT_RUNNING", "cdp_verified": False,
                "launched": False}
    monkeypatch.setattr(ca, "run", fallback)
    c.env = object()
    assert c.acquire(opt_in_launch=True)["status"] == "NOT_RUNNING"
    assert calls == [(True, True)]


def test_failed_full_acquisition_is_never_replayed(monkeypatch):
    c = cache(live={"address": "127.0.0.2", "pid": 15924,
                    "creation": "2026-11-02T12:30:00.0000000Z", "session": 1})
    c.env = object()
    calls = []
    def fail(env, *, launch, opt_in):
        calls.append(1)
        raise ca.AcquisitionBlocked("CDP_NOT_LOOPBACK_OWNED")
    monkeypatch.setattr(ca, "run", fail)
    try:
        c.acquire(opt_in_launch=True)
        assert False, "Must raise"
    except ca.AcquisitionBlocked as exc:
        assert str(exc) == "CDP_NOT_LOOPBACK_OWNED"
    assert len(calls) == 1
