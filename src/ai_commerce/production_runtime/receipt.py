"""Sanitized attended export. Hashes identify bytes; they authenticate nobody."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

from .contracts import canonical_bytes, draft_authorization, parse_contract, seal_contract

# The frozen B0.1 AST rule predates filesystem adapters. As in B0.2, these
# finite literal stdlib dependencies are audited by this component's tests.
os = __import__("os")
Path = __import__("pathlib").Path
tempfile = __import__("tempfile")


class ReceiptHold(ValueError):
    pass


def put_new(path, raw):
    """Atomic no-replace publication, or an exactly byte-identical re-export.

    Windows rename publishes fully flushed bytes and refuses an existing name.
    POSIX uses a no-replace hard link, avoiding rename's replacement semantics.
    Neither file in a two-file bundle can overwrite divergent existing bytes.
    """
    path = Path(path)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise ReceiptHold("EXPORT_CONFLICT")
        return
    fd, name = tempfile.mkstemp(prefix=".b03-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
                raise ReceiptHold("EXPORT_CONFLICT") from None
    finally:
        temporary.unlink(missing_ok=True)


def seal_wait_receipt(directive, recovery):
    """Use the unchanged B0.1 wire schema and its exact seal/parse functions."""
    if (recovery.state != "WAIT_BRAIN" or recovery.snapshot is None
            or recovery.snapshot.brain_decision is not None
            or recovery.effect_status != "NONE"):
        raise ReceiptHold("WAIT_BRAIN_REQUIRED")
    value = directive.to_dict()
    fixture = any(p.evidence_type == "SYNTHETIC_FIXTURE" for p in recovery.snapshot.evidence)
    evidence_type = "SYNTHETIC_FIXTURE" if fixture else "HUMAN_SUPPLIED"
    sealed = seal_contract("ProductJourneyReceipt", {
        "schema_version": "ProductJourneyReceipt.v1",
        **{k: value[k] for k in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
        "state": "WAIT_BRAIN", "brain_decision": None,
        "owner_evidence_refs": [asdict(p) for p in recovery.snapshot.evidence],
        "effect": {"status": "NONE", "idempotency_key": None,
                   "request_sha256": None, "receipt": None},
        "network_calls_performed": False, "external_effects_performed": False,
        "authorization": draft_authorization(),
        "provenance": {"evidence_type": evidence_type,
            "actor": "FIXTURE" if fixture else "HUMAN",
            "actor_ref": "fixture:attended-offline" if fixture else "human:attended-transfer-claim",
            "source_ref": "directive:" + value["directive_sha256"], "source_sha": value["source_sha"]},
    })
    return parse_contract("ProductJourneyReceipt", sealed.raw)


def evidence_packet(directive, recovery, *, intent, verification, history):
    """A separate envelope holds pins absent from the frozen receipt schema.

    Use the QA commit checkpoint, so lease releases and read-only restarts do
    not change the receipt or packet. No path, prompt or caller provenance ref
    is projected. A06 LOCAL_IMPORT is origin, not PROVIDER_ATTESTED evidence.
    """
    receipt = seal_wait_receipt(directive, recovery)
    rows = [r for r in history.records if r.journey_id == receipt.to_dict()["journey_id"]]
    if len(rows) != 4 or rows[-1].data().get("kind") != "QA_PASSED":
        raise ReceiptHold("EXACT_OFFLINE_SEQUENCE_REQUIRED")
    last = rows[-1]
    body = {
        "schema_version": "AttendedOfflineEvidence.v1",
        "receipt_file_sha256": sha256(receipt.raw).hexdigest(),
        "receipt_sha256": receipt.to_dict()["receipt_sha256"],
        "intent_sha256": sha256(canonical_bytes(intent)).hexdigest(),
        "directive_file_sha256": intent["directive_file_sha256"],
        "runtime_sha256": intent["runtime_sha256"],
        "local_artifact": verification,
        "journal_checkpoint": {"journal_id": intent["journal_id"],
            "source_sha": intent["source_sha"], "position": last.position,
            "sha256": last.sha256, "generation": last.generation},
        "journal_sequence": [{"sequence": r.sequence, "event_id": r.event_id,
                              "sha256": r.sha256} for r in rows],
        "source_origin": "LOCAL_IMPORT", "generation_performed": False,
        "provider_network_calls": 0, "paid_generation_calls": 0, "publish_calls": 0,
        "brain_identity_authenticated": False, "brain_decision": None,
        "anti_rollback_proof": "NOT_PROVEN", "human_attended": True,
    }
    packet = {**body, "packet_sha256": sha256(canonical_bytes(body)).hexdigest()}
    return receipt, canonical_bytes(packet)


def parse_packet(raw):
    """Reject duplicate keys and noncanonical/tampered companion bytes."""
    def pairs(items):
        value = {}
        for key, part in items:
            if key in value:
                raise ReceiptHold("PACKET_INVALID")
            value[key] = part
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        body = {k: v for k, v in value.items() if k != "packet_sha256"}
        expected = {"schema_version", "receipt_file_sha256", "receipt_sha256", "intent_sha256",
            "directive_file_sha256", "runtime_sha256", "local_artifact", "journal_checkpoint",
            "journal_sequence", "source_origin", "generation_performed", "provider_network_calls",
            "paid_generation_calls", "publish_calls", "brain_identity_authenticated", "brain_decision",
            "anti_rollback_proof", "human_attended", "packet_sha256"}
        if (set(value) != expected or canonical_bytes(value) != raw
                or value["schema_version"] != "AttendedOfflineEvidence.v1"
                or value["packet_sha256"] != sha256(canonical_bytes(body)).hexdigest()
                or value["brain_identity_authenticated"] is not False or value["brain_decision"] is not None
                or value["generation_performed"] is not False or value["human_attended"] is not True
                or value["source_origin"] != "LOCAL_IMPORT" or value["anti_rollback_proof"] != "NOT_PROVEN"
                or any(type(value[k]) is not int or value[k] != 0 for k in
                       ("provider_network_calls", "paid_generation_calls", "publish_calls"))):
            raise ReceiptHold("PACKET_INVALID")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ReceiptHold("PACKET_INVALID") from None


def export_packet(destination, receipt, packet):
    record = parse_contract("ProductJourneyReceipt", receipt.raw).to_dict()
    if record["state"] != "WAIT_BRAIN" or record["brain_decision"] is not None or record["effect"]["status"] != "NONE":
        raise ReceiptHold("WAIT_BRAIN_REQUIRED")
    value = parse_packet(packet)
    if value["receipt_file_sha256"] != sha256(receipt.raw).hexdigest():
        raise ReceiptHold("PACKET_RECEIPT_MISMATCH")
    put_new(Path(destination) / "ProductJourneyReceipt.v1.json", receipt.raw)
    put_new(Path(destination) / "attended-evidence.json", packet)
    return {"state": "WAIT_BRAIN", "code": "WAIT_BRAIN",
            "receipt_sha256": receipt.to_dict()["receipt_sha256"],
            "receipt_file_sha256": sha256(receipt.raw).hexdigest(),
            "packet_sha256": value["packet_sha256"]}


# Independent B1.5 envelope. The two B0.3 wire formats and exporters above
# remain frozen. These hashes detect drift, not authorship or release rights.
B15_SCHEMA = "B15OfflineWaitBrainEvidence.v1"
B15_MAX_BYTES = 65536
B15_UNPROVEN = {
    "speech": "NOT_MEASURED", "vietnamese_pronunciation": "NOT_MEASURED",
    "subtitle_semantics": "NOT_MEASURED", "speech_caption_alignment": "NOT_MEASURED",
    "voice_consent": "UNPROVEN", "licensing": "UNPROVEN",
    "rights_authentication": "UNPROVEN", "true_model_generation": "UNPROVEN",
    "release_authority": "UNPROVEN", "brain_identity": "UNPROVEN",
    "anti_rollback_proof": "NOT_PROVEN",
}


def _b15_need(ok):
    if not ok:
        raise ReceiptHold("B15_CURRENT_SOURCE_HOLD")


def _b15_json(raw, maximum):
    _b15_need(type(raw) is bytes and 0 < len(raw) <= maximum)
    value = json.loads(raw)
    # Also rejects duplicate keys, excessive depth/nodes and nonfinite values.
    _b15_need(type(value) is dict and canonical_bytes(value) == raw)
    return value


def _b15_file(path, maximum, *, capture=False):
    from . import attended_worker as worker
    from ..media import files as media_files

    path = worker._local(str(path))  # Check lexical aliases before abspath.
    with media_files.open_regular(path) as stream:
        _b15_need(os.fstat(stream.fileno()).st_size <= maximum)
        digest, size, blocks = sha256(), 0, []
        while True:
            block = stream.read(min(65536, maximum + 1 - size))
            if not block:
                break
            size += len(block)
            _b15_need(size <= maximum)
            digest.update(block)
            if capture:
                blocks.append(block)
        # A second bounded pass supplements the handle/path identity fence.
        stream.seek(0)
        again, count = sha256(), 0
        while True:
            block = stream.read(min(65536, maximum + 1 - count))
            if not block:
                break
            count += len(block)
            _b15_need(count <= maximum)
            again.update(block)
        _b15_need(count == size and again.digest() == digest.digest())
    return b"".join(blocks) if capture else (digest.hexdigest(), size)


def _b15_tree(root, *, sources=False):
    """Bounded no-link inventory; paths are internal and only its hash leaves."""
    from . import attended_worker as worker

    root = worker._local(str(root), directory=True)
    pending, files, total, entries = [root], {}, 0, 0
    while pending:
        directory = worker._local(str(pending.pop()), directory=True)
        with os.scandir(directory) as children:
            for entry in children:
                entries += 1
                _b15_need(entries <= (8192 if sources else 512))
                path = Path(entry.path)
                info = path.lstat()
                _b15_need(not (getattr(info, "st_file_attributes", 0) & 0x400)
                          and info.st_mode & 0o170000 != 0o120000)
                if info.st_mode & 0o170000 == 0o040000:
                    pending.append(path)
                elif not sources or path.match("*.py"):
                    digest, size = _b15_file(path, 8 * 1024 * 1024 if sources else worker.MAX_TREE_BYTES)
                    files[path] = digest
                    total += size
                    _b15_need(len(files) <= (1024 if sources else 128)
                              and total <= (64 * 1024 * 1024 if sources else worker.MAX_TREE_BYTES))
    if sources:
        _b15_need(Path(worker.__file__) in files)
        digest = sha256()
        for path in sorted(files):
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(4, "big") + name + bytes.fromhex(files[path]))
        return digest.hexdigest()
    return sha256(canonical_bytes({p.relative_to(root).as_posix(): h for p, h in files.items()})).hexdigest()


def _b15_current_pair(private_dir, receipt_path, evidence_path, inputs):
    from . import attended_worker as worker

    _b15_need(len(inputs) == 6 and set(inputs) == {
        "directive_path", "assets", "rights", "asset_root", "source_sha", "human_attended"})
    root = worker._private(private_dir)
    source_root = Path(worker.__file__).parent.parent
    source_digest = _b15_tree(source_root, sources=True)
    private_digest = _b15_tree(root)
    # Preflight every original input before the unchanged worker reads it.
    paths = [inputs["directive_path"], *worker._mapping(inputs["assets"]).values(),
             *worker._mapping(inputs["rights"]).values()]
    input_digests = [_b15_file(p, worker.MAX_ASSET_BYTES) for p in paths]
    receipt_raw = _b15_file(receipt_path, worker.MAX_BYTES, capture=True)
    packet_raw = _b15_file(evidence_path, worker.MAX_BYTES, capture=True)
    _b15_json(receipt_raw, worker.MAX_BYTES)
    _b15_json(packet_raw, worker.MAX_BYTES)
    directive, _, intent = worker._inputs(**inputs)
    _b15_need(source_digest == intent["runtime_sha256"])
    _, history, recovery = worker._read(root, directive, intent)
    _b15_need(recovery.state == "WAIT_BRAIN")
    checked = worker._verify(root, directive, intent)  # Existing A06 read/decode only.
    _b15_need(recovery.snapshot.evidence == worker._pins(intent, checked))
    current_receipt, current_packet = evidence_packet(
        directive, recovery, intent=intent, verification=checked, history=history)
    # Reconstruct from original sources/journal, never trust paired self-seals.
    _b15_need(current_receipt.raw == receipt_raw and current_packet == packet_raw)
    packet = parse_packet(packet_raw)
    _b15_need(input_digests == [_b15_file(p, worker.MAX_ASSET_BYTES) for p in paths]
              and source_digest == _b15_tree(source_root, sources=True)
              and private_digest == _b15_tree(root)
              and receipt_raw == _b15_file(receipt_path, worker.MAX_BYTES, capture=True)
              and packet_raw == _b15_file(evidence_path, worker.MAX_BYTES, capture=True))
    value = directive.to_dict()
    return {
        "journey_id": value["journey_id"], "source_sha": value["source_sha"],
        "directive_version": value["directive_version"],
        "directive_sha256": value["directive_sha256"],
        "directive_file_sha256": intent["directive_file_sha256"],
        "runtime_sha256": source_digest, "intent_sha256": packet["intent_sha256"],
        "receipt_file_sha256": sha256(receipt_raw).hexdigest(),
        "receipt_sha256": current_receipt.to_dict()["receipt_sha256"],
        "attended_evidence_file_sha256": sha256(packet_raw).hexdigest(),
        "attended_evidence_sha256": packet["packet_sha256"],
        "journal_checkpoint": packet["journal_checkpoint"],
        "journal_sequence": packet["journal_sequence"],
        "retained_state_sha256": private_digest,
        "local_artifact": checked, "provenance_class": intent["evidence_type"],
        "product_ref": value["creative"]["product_ref"],
    }


def _b15_observation(supplied):
    from . import attended_worker as worker
    from ..creative_quality import audio_observation as pcm
    from ..creative_quality import subtitle_clock_observation as srt

    if supplied is None:
        return None
    required = {"root", "wav_ref", "wav_sha256", "audio_packet"}
    optional = {"srt_ref", "srt_sha256", "clock_packet", "activity_packet", "acoustic_intent"}
    _b15_need(type(supplied) is dict and len(required) <= len(supplied) <= len(required | optional)
              and required <= set(supplied) <= required | optional)
    root = worker._local(str(supplied["root"]), directory=True)
    wav_ref, wav_sha = supplied["wav_ref"], supplied["wav_sha256"]
    audio_raw = supplied["audio_packet"]
    _b15_json(audio_raw, 8192)
    # Public observer owns reference validation, native parsing and replay.
    audio = pcm.recheck_local_pcm_wav(audio_raw, root, wav_ref, wav_sha,
        acoustic_intent=supplied.get("acoustic_intent")).to_dict()
    kwargs = {k: supplied.get(k) for k in ("srt_ref", "srt_sha256")}
    clock_raw = supplied.get("clock_packet")
    activity_raw = supplied.get("activity_packet")
    subtitles = None
    if any(v is not None for v in (*kwargs.values(), clock_raw, activity_raw)):
        if clock_raw is None:
            clock = srt.inspect_local_srt_clock(root, audio_raw, wav_ref, wav_sha, **kwargs)
        else:
            _b15_json(clock_raw, 32768)
            clock = srt.recheck_local_srt_clock(clock_raw, root, audio_raw, wav_ref, wav_sha, **kwargs)
        timing = clock.to_dict()
        subtitles = {"file_sha256": sha256(clock.raw).hexdigest(),
                     "record_sha256": timing["record_sha256"],
                     "asset_sha256": timing["srt_sha256"], "timing": timing["timing"],
                     "packet_supplied": clock_raw is not None}
    activity = None
    if activity_raw is not None:
        _b15_json(activity_raw, 32768)
        measured = srt.recheck_local_srt_pcm_activity(activity_raw, root, audio_raw,
            wav_ref, wav_sha, clock_packet=clock.raw, **kwargs).to_dict()
        activity = {"file_sha256": sha256(activity_raw).hexdigest(),
                    "record_sha256": measured["record_sha256"], "measurement": measured["measurement"]}
    # Project no original reference, subtitle text, prompt or local path.
    return {
        "pcm": {"file_sha256": sha256(audio_raw).hexdigest(),
                "record_sha256": audio["record_sha256"], "asset_sha256": wav_sha,
                "observed": audio["observed"], "intent": audio["intent"]},
        "subtitle_state": "NO_SUBTITLE_SUPPLIED" if kwargs["srt_ref"] is None else "LOCAL_CLOCK_BOUNDS_ONLY",
        "subtitles": subtitles,
        "activity": activity,
    }


def inspect_wait_brain_handoff(*, private_dir, receipt_path, evidence_path,
                              observation=None, **inputs):
    """Return canonical B1.5 bytes from a current genuine B0.3 offline pair.

    This read-only sidecar never exports or changes the original receipts.
    Optional observation is a closed mapping of separately supplied B1.3
    packets and their native WAV/SRT inputs; it never proves MP4 track identity.
    The retained local journal is comparison evidence, not an issuer signature.
    """
    try:
        original = _b15_current_pair(private_dir, receipt_path, evidence_path, inputs)
        measured = _b15_observation(observation)
        _b15_need(measured == _b15_observation(observation)
                  and original == _b15_current_pair(private_dir, receipt_path, evidence_path, inputs))
        body = {
            "schema_version": B15_SCHEMA, "state": "WAIT_BRAIN_DRAFT_ONLY",
            "original": original, "observation": measured,
            "audio_link": "AUDIO_LINK_UNVERIFIED", "unproven": dict(B15_UNPROVEN),
            "source_origin": "LOCAL_IMPORT", "generation_performed": False,
            "provider_network_calls": 0, "paid_generation_calls": 0, "publish_calls": 0,
            "brain_decision": None, "brain_identity_authenticated": False,
            "authorization": draft_authorization(), "authority": "NO_EXTERNAL_EFFECT_OR_CONSENT",
        }
        raw = canonical_bytes({**body, "packet_sha256": sha256(canonical_bytes(body)).hexdigest()})
        _b15_need(len(raw) <= B15_MAX_BYTES)
        return raw
    except Exception:
        # Owner/media errors can contain paths. No exception text is exported.
        raise ReceiptHold("B15_CURRENT_SOURCE_HOLD") from None


def recheck_wait_brain_handoff(sealed, **inputs):
    """A self-resealed handoff is insufficient: replay all original sources."""
    try:
        prior = _b15_json(sealed, B15_MAX_BYTES)
        _b15_need(prior["schema_version"] == B15_SCHEMA)
        body = {k: v for k, v in prior.items() if k != "packet_sha256"}
        _b15_need(prior["packet_sha256"] == sha256(canonical_bytes(body)).hexdigest())
    except Exception:
        raise ReceiptHold("B15_PACKET_INVALID") from None
    current = inspect_wait_brain_handoff(**inputs)
    if current != sealed:
        raise ReceiptHold("B15_CURRENT_REPLAY_REQUIRED")
    return current
