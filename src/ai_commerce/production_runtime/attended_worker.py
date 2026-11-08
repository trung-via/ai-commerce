"""B0.3: one explicit attended invocation; only the reviewed local A06 adapter.

No scheduler, provider retry, authority verifier or BrainDecision ingress.
START coordinates a scoped local filesystem render, not a provider effect.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import re

from ..e2e import service
from .contracts import MAX_BYTES, canonical_bytes, parse_contract
from .journal import Checkpoint, Journal
from .recovery import replay_verified
from .state_machine import Event, EventKind, EvidencePin
from .receipt import evidence_packet, export_packet, put_new

# Literal stdlib dependencies, matching B0.2's frozen AST compatibility seam.
# Tests audit every literal load and actual child-process/network boundary.
os = __import__("os")
Path = __import__("pathlib").Path
time = __import__("time")
uuid = __import__("uuid")
msvcrt = __import__("msvcrt") if os.name == "nt" else None

SOURCE_SHA = "32f42b84567e1217a93854389b1c96435bbf454d"
TOOL = "tool:a06-e01-offline-local-stills"
PARAMETERS = {"width": 64, "height": 96, "fps": 12, "frames": 3, "silence": False}
MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_TREE_BYTES = 32 * 1024 * 1024


class WorkerHold(ValueError):
    """Only bounded machine codes may leave the private worker."""


def _need(ok, code):
    if not ok:
        raise WorkerHold(code)


def _local(value, *, directory=False, missing=False, under=None):
    """Reject traversal, ADS, Win32 device/UNC/reparse aliases before opening."""
    _need(type(value) is str and 0 < len(value) < 4096 and "\x00" not in value, "LOCAL_PATH_REQUIRED")
    normalized = value.replace("\\", "/")
    _need(not normalized.startswith("//") and not normalized.startswith("/??/"), "UNC_OR_DEVICE_DENIED")
    path = Path(value)
    _need(path.is_absolute(), "ABSOLUTE_PATH_REQUIRED")
    pieces = normalized.split("/")
    for i, piece in enumerate(pieces):
        if i == 0 and re.fullmatch(r"[A-Za-z]:", piece):
            continue
        _need(piece not in {".", ".."} and not piece.endswith((" ", "."))
              and not any(ord(c) < 32 or c in ':<>|?*' for c in piece), "PATH_AMBIGUITY_DENIED")
        _need(piece.split(".")[0].upper() not in
              {"CON", "PRN", "AUX", "NUL", *("COM" + str(n) for n in range(10)),
               *("LPT" + str(n) for n in range(10))}, "DEVICE_PATH_DENIED")
    for part in (path, *path.parents):
        _need(not part.is_symlink() and not getattr(part, "is_junction", lambda: False)(), "PATH_LINK_DENIED")
        if part.exists():
            _need(not (getattr(part.lstat(), "st_file_attributes", 0) & 0x400), "PATH_REPARSE_DENIED")
    _need(path.exists() or (missing and path.parent.is_dir()), "LOCAL_PATH_MISSING")
    if path.exists():
        _need(path.is_dir() if directory else path.is_file(), "LOCAL_PATH_TYPE_INVALID")
    if under is not None:
        _need(Path(under) in path.parents, "ASSET_PATH_ESCAPE")
    return path


def _private(value):
    path = _local(value, directory=True)
    repo = Path(__file__).resolve().parents[3]
    _need(path != repo and repo not in path.parents, "PRIVATE_PATH_OUTSIDE_REPOSITORY_REQUIRED")
    return path


def _bytes(path, maximum=MAX_BYTES):
    path = _local(str(path))
    _need(path.stat().st_size <= maximum, "LOCAL_FILE_TOO_LARGE")
    raw = path.read_bytes()
    _need(len(raw) <= maximum, "LOCAL_FILE_TOO_LARGE")
    return raw


def _json(path, *, canonical=True):
    raw = _bytes(path)
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, "DUPLICATE_LOCAL_KEY")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    _need(not canonical or canonical_bytes(value) == raw, "LOCAL_RECORD_NONCANONICAL")
    return value


def _runtime_hash():
    root = Path(__file__).parent.parent
    # Pin every shipped Python source, including native owner dependencies.
    # This is drift detection against the retained intent, not a Git signature.
    # Source limits are separate from the unchanged private-document _bytes cap.
    maximum, chunk = 8 * 1024 * 1024, 64 * 1024

    def stamp(info, *, directory=False):
        _need(not (info.st_mode & 0o170000 == 0o120000), "PATH_LINK_DENIED")
        _need(not (getattr(info, "st_file_attributes", 0) & 0x400), "PATH_REPARSE_DENIED")
        _need(info.st_mode & 0o170000 == (0o040000 if directory else 0o100000),
              "LOCAL_PATH_TYPE_INVALID")
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    getattr(info, "st_file_attributes", 0))
        # Cache/non-source writes do not change directory identity. The complete
        # Python inventory and each source's metadata are compared separately.
        return identity if directory else (*identity, info.st_size, info.st_mtime_ns,
                                           getattr(info, "st_birthtime_ns", None), info.st_ctime_ns)

    def inventory():
        files, directories, pending, entries = {}, {}, [root], 0
        while pending:
            directory = _local(str(pending.pop()), directory=True)
            before = stamp(directory.lstat(), directory=True)
            directories[directory] = before
            with os.scandir(directory) as children:
                for entry in children:
                    entries += 1
                    _need(entries <= 8192, "SOURCE_TREE_LIMIT")
                    path = Path(entry.path)
                    # Windows DirEntry.stat() omits inode/device/link count;
                    # use fresh nofollow metadata, consistent with fstat().
                    info = path.lstat()
                    _need(not (getattr(info, "st_file_attributes", 0) & 0x400), "PATH_REPARSE_DENIED")
                    _need(info.st_mode & 0o170000 != 0o120000, "PATH_LINK_DENIED")
                    if info.st_mode & 0o170000 == 0o040000:
                        _need(len(directories) + len(pending) < 1024, "SOURCE_TREE_LIMIT")
                        pending.append(path)
                    elif path.match("*.py"):
                        files[path] = stamp(info)
                        _need(len(files) <= 1024, "SOURCE_TREE_LIMIT")
                        _need(info.st_size <= maximum, "SOURCE_FILE_TOO_LARGE")
            _local(str(directory), directory=True)
            _need(stamp(directory.lstat(), directory=True) == before, "SOURCE_TREE_DRIFT")
        return files, directories

    try:
        files, directories = inventory()
        _need(Path(__file__) in files, "SOURCE_TREE_DRIFT")
        digest = sha256()
        for path in sorted(files):
            _local(str(path), under=root)
            _need(stamp(path.lstat()) == files[path], "SOURCE_TREE_DRIFT")
            flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
            fd = os.open(path, flags)
            try:
                # Validate the opened identity BEFORE reading, including on
                # Windows where O_NOFOLLOW is unavailable. A swapped link may
                # be opened, but its target's contents must never be read.
                opened = stamp(os.fstat(fd))
                # Windows Python versions can expose different ctime semantics
                # for paths and handles. Compare creation time across them,
                # and retain ctime checks within each metadata API separately.
                _need(opened[:-1] == files[path][:-1], "SOURCE_TREE_DRIFT")
                _local(str(path), under=root)
                _need(stamp(path.lstat()) == files[path], "SOURCE_TREE_DRIFT")
                source, total = sha256(), 0
                while True:
                    block = os.read(fd, min(chunk, maximum + 1 - total))
                    if not block:
                        break
                    total += len(block)
                    _need(total <= maximum, "SOURCE_FILE_TOO_LARGE")
                    source.update(block)
                _need(total == files[path][5] and stamp(os.fstat(fd)) == opened, "SOURCE_TREE_DRIFT")
                _local(str(path), under=root)
                _need(stamp(path.lstat()) == files[path], "SOURCE_TREE_DRIFT")
            finally:
                os.close(fd)
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(4, "big") + name + source.digest())
        # Python workers may create/remove bytecode-only __pycache__ folders
        # while independent processes inspect the shipped sources. Such
        # directories are not part of the source hash and must not make an
        # unchanged tree appear to drift. Keep the strict full *.py inventory,
        # and pin identities of EVERY directory containing shipped source
        # files (including their ancestors), not incidental cache directories.
        source_parents = {root}
        for path in files:
            parent = path.parent
            while parent != root:
                source_parents.add(parent)
                parent = parent.parent
        newer_files, newer_dirs = inventory()
        _need(newer_files == files and all(
            newer_dirs.get(path) == directories[path]
            for path in source_parents
        ), "SOURCE_TREE_DRIFT")
        return digest.hexdigest()
    except OSError:
        raise WorkerHold("SOURCE_TREE_UNREADABLE") from None


def _mapping(values):
    _need(type(values) is tuple and len(values) <= 4, "EXPLICIT_LOCAL_INPUTS_REQUIRED")
    result = {}
    for item in values:
        _need(type(item) is str and "=" in item, "LOCAL_INPUT_MAPPING_INVALID")
        ref, path = item.split("=", 1)
        _need(ref not in result, "DUPLICATE_LOCAL_INPUT")
        result[ref] = path
    return result


def _inputs(*, directive_path, assets, rights, asset_root, source_sha, human_attended):
    _need(human_attended is True, "HUMAN_ATTENDED_REQUIRED")
    _need(source_sha == SOURCE_SHA, "SOURCE_SHA_MISMATCH")
    raw = _bytes(_local(directive_path))
    repo = Path(__file__).resolve().parents[3]
    _need(repo not in Path(directive_path).parents, "PRIVATE_DIRECTIVE_OUTSIDE_REPOSITORY_REQUIRED")
    directive = parse_contract("CreativeDirective", raw)
    value = directive.to_dict()
    _need(value["source_sha"] == source_sha, "DIRECTIVE_SOURCE_MISMATCH")
    _need(re.fullmatch(r"journey:[0-9a-f]{32,64}", value["journey_id"]) is not None,
          "SANITIZED_JOURNEY_ID_REQUIRED")
    _need(value["creative"]["product_ref"] is None or re.fullmatch(
        r"product:[0-9a-f]{64}", value["creative"]["product_ref"]) is not None, "OPAQUE_PRODUCT_REQUIRED")
    _need(value["scope"] == {"action_class": "EXPORT_DRAFT", "provider_ref": None,
          "account_ref": None, "surface": None, "asset_sha256": None}
          and value["action_classes"] == ["EXPORT_DRAFT"], "OFFLINE_EXPORT_SCOPE_REQUIRED")
    _need(value["allowed_tools"] == [TOOL], "OFFLINE_TOOL_ALLOWLIST_REQUIRED")
    origin = value["provenance"]
    _need((origin["actor"], origin["evidence_type"]) in
          {("HUMAN", "HUMAN_SUPPLIED"), ("FIXTURE", "SYNTHETIC_FIXTURE")}, "LOCAL_PROVENANCE_REQUIRED")
    for key in ("actor_ref", "source_ref"):
        _need(re.fullmatch(r"(?:human|fixture|local|directive):[0-9a-f]{32,64}", origin[key]) is not None,
              "SANITIZED_SOURCE_REF_REQUIRED")
    root = _private(asset_root)
    chosen, right_files = _mapping(assets), _mapping(rights)
    _need(1 <= len(value["assets"]) <= 4 and len(chosen) == len(value["assets"]), "ASSET_SELECTION_MISMATCH")
    paths, pins, used_rights = [], [], set()
    for asset in value["assets"]:
        _need(asset["ref"] in chosen and re.fullmatch(r"asset:[0-9a-f]{64}", asset["ref"]) is not None,
              "ASSET_SELECTION_MISMATCH")
        path = _local(chosen[asset["ref"]], under=root)
        _need(path.suffix.lower() == ".png", "LOCAL_PNG_REQUIRED")
        image = _bytes(path, MAX_ASSET_BYTES)
        _need(image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) >= 24, "LOCAL_PNG_INVALID")
        width, height = int.from_bytes(image[16:20]), int.from_bytes(image[20:24])
        _need(0 < width <= 1024 and 0 < height <= 1024, "LOCAL_PNG_DIMENSION_LIMIT")
        _need(sha256(image).hexdigest() == asset["sha256"], "ASSET_HASH_MISMATCH")
        _need(asset["evidence_type"] in {"SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"}, "ASSET_ORIGIN_UNPROVEN")
        right = asset["rights"]
        if asset["evidence_type"] == "SYNTHETIC_FIXTURE":
            _need(origin["evidence_type"] == "SYNTHETIC_FIXTURE" and right == {
                "status": "NOT_APPLICABLE", "ref": None, "sha256": None}, "FIXTURE_RIGHTS_REQUIRED")
        else:
            _need(right["status"] == "GRANTED" and right["ref"] in right_files, "LOCAL_RIGHTS_EVIDENCE_REQUIRED")
            _need(re.fullmatch(r"rights:[0-9a-f]{64}", right["ref"]) is not None, "SANITIZED_RIGHTS_REF_REQUIRED")
            claim_raw = _bytes(_local(right_files[right["ref"]], under=root))
            _need(sha256(claim_raw).hexdigest() == right["sha256"], "RIGHTS_HASH_MISMATCH")
            claim = _json(Path(right_files[right["ref"]]))
            _need(set(claim) == {"schema_version", "asset_sha256", "source_sha", "scope", "status", "valid_from", "expires_at"}
                  and claim["schema_version"] == "LocalStillRights.v1"
                  and claim["asset_sha256"] == asset["sha256"] and claim["source_sha"] == source_sha
                  and claim["scope"] == "OFFLINE_LOCAL_RENDER" and claim["status"] == "GRANTED",
                  "LOCAL_RIGHTS_SCOPE_MISMATCH")
            _need(type(claim["valid_from"]) is int and type(claim["expires_at"]) is int
                  and 0 <= claim["valid_from"] <= _now() < claim["expires_at"], "LOCAL_RIGHTS_EXPIRED")
            used_rights.add(right["ref"])
        paths.append(path)
        pins.append({"sha256": asset["sha256"], "evidence_type": asset["evidence_type"],
                     "rights_sha256": right["sha256"]})
    _need(used_rights == set(right_files) and len(set(paths)) == len(paths), "LOCAL_INPUT_SET_MISMATCH")
    identity = sha256(directive.raw).hexdigest()
    intent = {"schema_version": "AttendedOfflineIntent.v1", "source_sha": source_sha,
        "journey_id": value["journey_id"], "directive_sha256": value["directive_sha256"],
        "directive_version": value["directive_version"], "scope": value["scope"],
        "directive_file_sha256": sha256(raw).hexdigest(), "directive_record_sha256": identity,
        "runtime_sha256": _runtime_hash(), "assets": pins, "tool": TOOL,
        "evidence_type": "SYNTHETIC_FIXTURE" if origin["evidence_type"] == "SYNTHETIC_FIXTURE"
            or any(p["evidence_type"] == "SYNTHETIC_FIXTURE" for p in pins) else "HUMAN_SUPPLIED",
        "parameters": PARAMETERS, "render_ref": "local:e01-" + identity[:24],
        "journal_id": "journal:" + identity[:32]}
    return directive, tuple(paths), intent


def _now():
    return int(time.time())


class _Lock:
    """OS-released process exclusion; journal lease/fence is still mandatory."""
    def __init__(self, root, *, create=False):
        self.path, self.create, self.fd = root / ".attended.lock", create, None

    def __enter__(self):
        _need(msvcrt is not None, "WINDOWS_LOCAL_LOCK_REQUIRED")
        _local(str(self.path), missing=self.create)
        self.fd = os.open(self.path, os.O_RDWR | (os.O_CREAT if self.create else 0), 0o600)
        try:
            msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise WorkerHold("PROCESS_LOCK_BUSY") from None
        return self

    def __exit__(self, *args):
        os.close(self.fd)


def _step(name):
    """Named commit cuts for subprocess kill tests; no CLI/env fault switches."""


def _anchor(root, journal):
    checkpoints = sorted(root.glob("checkpoint-*.json"))
    _need(len(checkpoints) <= 64, "CHECKPOINT_LIMIT")
    checkpoint = None
    for path in checkpoints:
        _need(re.fullmatch(r"checkpoint-[0-9]{6}\.json", path.name) is not None, "CHECKPOINT_INVALID")
        item = Checkpoint(**_json(path))
        _need(path.name == f"checkpoint-{item.position:06d}.json", "CHECKPOINT_INVALID")
        Journal.open(journal.path, journal_id=journal.journal_id, source_sha=journal.source_sha, checkpoint=item)
        checkpoint = item
    _need(checkpoint is not None, "CHECKPOINT_MISSING")
    return Journal.open(journal.path, journal_id=journal.journal_id, source_sha=journal.source_sha, checkpoint=checkpoint)


def _checkpoint(root, journal):
    checkpoint = journal.inspect().checkpoint
    put_new(root / f"checkpoint-{checkpoint.position:06d}.json", canonical_bytes(asdict(checkpoint)))


def _render_path(root, intent):
    return root / intent["render_ref"].replace("local:", "")


def _tree_hash(root):
    _local(str(root), directory=True)
    files, size, pending = {}, 0, [root]
    while pending:
        directory = pending.pop()
        children = list(directory.iterdir())
        _need(len(files) + len(pending) + len(children) <= 512, "LOCAL_OUTPUT_LIMIT")
        for path in children:
            if path.is_dir():
                _local(str(path), directory=True, under=root)
                pending.append(path)
            else:
                _local(str(path), under=root)
                size += path.stat().st_size
                _need(size <= MAX_TREE_BYTES, "LOCAL_OUTPUT_LIMIT")
                files[path.relative_to(root).as_posix()] = sha256(_bytes(path, MAX_TREE_BYTES)).hexdigest()
    return sha256(canonical_bytes(files)).hexdigest()


def _verify(root, directive, intent):
    dest = _render_path(root, intent)
    _tree_hash(dest)  # Deny reparse paths BEFORE A06 follows any owner references.
    record = service.inspect(dest)
    checked = service.verify(dest)  # ffprobe AND full decode of native render/custody.
    request = _json(dest / "custody" / "e01" / "request.json", canonical=False)
    _need(request == {"prompt": directive.to_dict()["creative"]["freeform"],
        "asset_sha256s": [a["sha256"] for a in intent["assets"]],
        "canvas": {k: PARAMETERS[k] for k in ("width", "height", "fps")},
        "frames_per_asset": PARAMETERS["frames"], "silence": PARAMETERS["silence"],
        "source_mode": "LOCAL_IMPORT"}, "OUTPUT_DIRECTIVE_BINDING_MISMATCH")
    _need(checked["ffprobe_full_decode"] is True and checked["generation_performed"] is False
          and checked["publication_status"] == "NOT_ATTEMPTED"
          and [a["artifact_sha256"] for a in record["assets"]] == request["asset_sha256s"],
          "OUTPUT_LOCAL_PROVENANCE_MISMATCH")
    body = {"artifact_ref": "local:video", "artifact_sha256": checked["sha256"],
        "manifest_sha256": sha256(_bytes(dest / "e01-manifest.json")).hexdigest(),
        "tree_sha256": _tree_hash(dest), "ffprobe_full_decode": True,
        "origin": "LOCAL_IMPORT", "generation_performed": False, "publication_status": "NOT_ATTEMPTED"}
    return {**body, "verification_ref": "a06:" + sha256(canonical_bytes(body)).hexdigest()}


def _pins(intent, checked):
    evidence_type = intent["evidence_type"]
    return (EvidencePin("MEDIA_CREATIVE", "local:" + checked["artifact_sha256"],
                        checked["artifact_sha256"], evidence_type),
            EvidencePin("MEDIA_CREATIVE", checked["verification_ref"],
                        sha256(canonical_bytes(checked)).hexdigest(), evidence_type))


def _read(root, directive, intent, expected_generation=None):
    _need(_runtime_hash() == intent["runtime_sha256"], "SOURCE_TREE_DRIFT")
    _need(_json(root / "intent.json") == intent, "DIRECTIVE_ASSET_OR_SOURCE_DRIFT")
    journal = Journal.open(str(_local(str(root / "journal.sqlite"))),
                           journal_id=intent["journal_id"], source_sha=intent["source_sha"])
    journal = _anchor(root, journal)
    history = journal.inspect()
    _need({r.journey_id for r in history.records if r.journey_id} == {intent["journey_id"]}, "FOREIGN_JOURNEY")
    result = replay_verified(journal, history, intent["journey_id"], directive=directive,
                             expected_generation=expected_generation)
    if result.state == "UNKNOWN_EFFECT":
        raise WorkerHold("UNKNOWN_EFFECT")
    _need(result.state in {"INPUT_VALIDATED", "RUNNING", "TECHNICAL_QA", "WAIT_BRAIN"}, "JOURNAL_REPLAY_HOLD")
    events = [r for r in history.records if r.kind == "TRANSITION"]
    start_id = "event:start-" + sha256(canonical_bytes(intent)).hexdigest()
    _need(not events or (events[0].event_id == start_id and events[0].data()["tool"] == TOOL), "START_INTENT_MISMATCH")
    return journal, history, result


def _summary(result, checked=None):
    return {"state": result.state, "code": result.state,
            "journal_sequence": len(result.event_sequence),
            "evidence_sha256": result.evidence_sha256,
            "artifact_sha256": checked["artifact_sha256"] if checked else None,
            "provider_network_calls": 0, "paid_generation_calls": 0, "publish_calls": 0}


def _complete(root, directive, intent, journal, lease, *, allow_render, assets):
    _, _, current = _read(root, directive, intent)
    if current.state == "INPUT_VALIDATED":
        _need(allow_render, "PRE_START_STOPPED_NO_TOOL_RUN")
        _need(_runtime_hash() == intent["runtime_sha256"] and all(
            sha256(_bytes(path, MAX_ASSET_BYTES)).hexdigest() == pin["sha256"]
            for path, pin in zip(assets, intent["assets"])), "SOURCE_OR_ASSET_DRIFT")
        _step("before_start")
        journal.append_event(Event("event:start-" + sha256(canonical_bytes(intent)).hexdigest(),
            EventKind.START, intent["journey_id"], intent["source_sha"], tool=TOOL),
            directive=directive, lease=lease, at=_now(), expected_sequence=1)
        _checkpoint(root, journal)
        _step("after_start")
        _need(not _render_path(root, intent).exists(), "OUTPUT_ALREADY_EXISTS")
        _need(_runtime_hash() == intent["runtime_sha256"] and all(
            sha256(_bytes(path, MAX_ASSET_BYTES)).hexdigest() == pin["sha256"]
            for path, pin in zip(assets, intent["assets"])), "SOURCE_OR_ASSET_DRIFT")
        service.run(directive.to_dict()["creative"]["freeform"], list(assets),
                    _render_path(root, intent), **PARAMETERS)
        _step("after_a06")
    try:
        checked = _verify(root, directive, intent)
    except Exception:
        raise WorkerHold("PENDING_OFFLINE_EFFECT_RECONCILIATION") from None
    pins = _pins(intent, checked)
    _, _, current = _read(root, directive, intent)
    if current.state == "RUNNING":
        journal.append_event(Event("event:output-ready", EventKind.OUTPUT_READY,
            intent["journey_id"], intent["source_sha"], evidence=pins),
            directive=directive, lease=lease, at=_now(), expected_sequence=2)
        _checkpoint(root, journal)
        _step("after_output")
    else:
        _need(current.snapshot.evidence == pins, "OUTPUT_EVIDENCE_CHANGED")
    # This is a new technical read after OUTPUT_READY, never a second render.
    _need(_verify(root, directive, intent) == checked, "OUTPUT_EVIDENCE_CHANGED")
    journal.append_event(Event("event:qa-passed", EventKind.QA_PASSED, intent["journey_id"],
        intent["source_sha"], evidence=pins), directive=directive, lease=lease,
        at=_now(), expected_sequence=3)
    _checkpoint(root, journal)
    _step("after_wait")
    return checked


def run_once(*, private_dir, **inputs):
    """Create exactly one journey and attempt the fixed offline adapter once."""
    directive, assets, intent = _inputs(**inputs)
    root = _private(private_dir)
    with _Lock(root, create=True):
        _need({p.name for p in root.iterdir()} == {".attended.lock"}, "NEW_PRIVATE_DIRECTORY_REQUIRED")
        put_new(root / "intent.json", canonical_bytes(intent))
        journal = Journal.create(str(root / "journal.sqlite"), journal_id=intent["journal_id"], source_sha=intent["source_sha"])
        _checkpoint(root, journal)
        lease = journal.acquire(owner="worker:" + uuid.uuid4().hex, at=_now(), ttl=300, event_id="lease:" + uuid.uuid4().hex)
        _checkpoint(root, journal)
        journal.create_journey(directive, directive_ref="directive:" + intent["directive_sha256"],
            lease=lease, at=_now(), event_id="event:input-accepted")
        _checkpoint(root, journal)
        try:
            checked = _complete(root, directive, intent, journal, lease, allow_render=True, assets=assets)
        finally:
            journal.release(lease, at=_now(), event_id="lease:" + uuid.uuid4().hex)
            _checkpoint(root, journal)
        _, _, result = _read(root, directive, intent)
        return _summary(result, checked)


def inspect_once(*, private_dir, expected_generation=None, **inputs):
    """Read-only journal/owner verification. Never acquire or execute a render."""
    directive, _, intent = _inputs(**inputs)
    root = _private(private_dir)
    _, _, result = _read(root, directive, intent, expected_generation)
    checked = None
    if result.state in {"RUNNING", "TECHNICAL_QA", "WAIT_BRAIN"}:
        try:
            checked = _verify(root, directive, intent)
        except Exception:
            raise WorkerHold("PENDING_OFFLINE_EFFECT_RECONCILIATION") from None
        if result.state in {"TECHNICAL_QA", "WAIT_BRAIN"}:
            _need(result.snapshot.evidence == _pins(intent, checked), "OUTPUT_EVIDENCE_CHANGED")
    return _summary(result, checked)


def resume_once(*, private_dir, reconcile_stopped_generation=None, expected_generation=None, **inputs):
    """Never render. A stopped lease requires explicit exact-generation input.

    Obtaining the native lock proves no cooperating local worker holds it.
    This is attended process reconciliation, not a custody/security attestor.
    """
    directive, assets, intent = _inputs(**inputs)
    root = _private(private_dir)
    with _Lock(root):
        journal, history, result = _read(root, directive, intent, expected_generation)
        if result.state == "WAIT_BRAIN":
            _need(reconcile_stopped_generation is None or (history.lease is not None
                  and type(reconcile_stopped_generation) is int
                  and reconcile_stopped_generation == history.lease.generation), "STALE_FENCING_GENERATION")
            return inspect_once(private_dir=private_dir, expected_generation=expected_generation, **inputs)
        _need(result.state != "INPUT_VALIDATED", "PRE_START_STOPPED_NO_TOOL_RUN")
        # Definitive complete native evidence is required BEFORE any mutation.
        inspect_once(private_dir=private_dir, expected_generation=expected_generation, **inputs)
        if history.lease is not None:
            _need(type(reconcile_stopped_generation) is int
                  and reconcile_stopped_generation == history.lease.generation, "STALE_LEASE_ATTENDED_RECONCILIATION_REQUIRED")
            if _now() < history.lease.expires_at:
                journal.release(history.lease, at=_now(), event_id="lease:" + uuid.uuid4().hex)
                _checkpoint(root, journal)
        else:
            _need(reconcile_stopped_generation is None, "STALE_FENCING_GENERATION")
        lease = journal.acquire(owner="worker:" + uuid.uuid4().hex, at=_now(), ttl=300, event_id="lease:" + uuid.uuid4().hex)
        _checkpoint(root, journal)
        try:
            checked = _complete(root, directive, intent, journal, lease, allow_render=False, assets=assets)
        finally:
            journal.release(lease, at=_now(), event_id="lease:" + uuid.uuid4().hex)
            _checkpoint(root, journal)
        _, _, result = _read(root, directive, intent)
        return _summary(result, checked)


def export_once(*, private_dir, destination, **inputs):
    directive, _, intent = _inputs(**inputs)
    root, dest = _private(private_dir), _private(destination)
    _need(dest != root and root not in dest.parents, "SEPARATE_EXPORT_DIRECTORY_REQUIRED")
    _, history, result = _read(root, directive, intent)
    _need(result.state == "WAIT_BRAIN", "WAIT_BRAIN_REQUIRED")
    checked = _verify(root, directive, intent)
    _need(result.snapshot.evidence == _pins(intent, checked), "OUTPUT_EVIDENCE_CHANGED")
    for name in ("ProductJourneyReceipt.v1.json", "attended-evidence.json"):
        _local(str(dest / name), missing=True)
    receipt, packet = evidence_packet(directive, result, intent=intent, verification=checked, history=history)
    return export_packet(dest, receipt, packet)
