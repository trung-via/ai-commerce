"""Fixed release contract and byte readback; NO builder, installer or signer.

An artifact match is source evidence only. This action has neither an approved
Windows packaging lock/bootloader nor a protected signer implementation. The
production gate therefore ALWAYS raises PACKAGING_OWNER_ESCALATION, before a
listener is created. No fixture, manifest boolean, CI variable or Issue can
turn an offline readback into installed authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
import hashlib
from pathlib import Path
import re
import struct

from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub.g3_enrollment import SERVICE_IMAGE
from ai_commerce.wakehub.native_journal import canonical, deny, shape


SOURCE_PARENT = "2d783977ade56c7b3af9626f7f414ebf568ca751"
ACTION = "WAKEHUB-WH01B-G4A-NATIVE-TRANSPORT-SOURCE-001"
ATTEMPT = "wakehub_wh01b_g4a_native_transport_2d78397_20261107_r1_codex"
RELEASE_OWNER = "Windows release packaging owner + independent private HostOps custody owner"
SOURCE_CLOSURE = tuple("src/ai_commerce/wakehub/" + name + ".py" for name in (
    "__init__", "private_operator", "native_inventory", "native_journal",
    "native_authority", "native_broker", "native_runtime", "g3_enrollment",
    "g3_service_host", "g3_desktop_observer", "g4_release_provenance",
    "g4_service_transport", "g4_desktop_transport",
))
BUILD_PINS = ("python_exe", "python_dll", "packager_wheel", "bootloader",
              "windows_sdk", "dependency_lock")
PUBLIC_STATES = (
    "SOURCE_ONLY", "NATIVE_SERVICE_NOT_INSTALLED", "SIGNER_NOT_ENROLLED",
    "DESKTOP_OBSERVER_NOT_ENROLLED", "9444_HUMAN_PAUSED",
    "LOGON_TASK_NOT_INSTALLED", "WAKE_NOT_SENT",
)


class PackagingOwnerEscalation(RuntimeError):
    """A finite public error: never include a private path, key or OS exception."""
    owner = RELEASE_OWNER
    required_source_paths = (
        "src/ai_commerce/wakehub/g4_release_provenance.py",
        "src/ai_commerce/wakehub/g4_service_transport.py",
        "src/ai_commerce/wakehub/g4_desktop_transport.py",
        "pyproject.toml (separate packaging scope required)",
    )

    def __init__(self):
        super().__init__("PACKAGING_OWNER_ESCALATION")


@dataclass(frozen=True)
class SourceReadiness:
    SOURCE_CANDIDATE: bool = True
    PACKAGED_RELEASE_PROVEN: bool = False
    SERVICE_INSTALLED_PROVEN: bool = False
    SIGNER_ENROLLED: bool = False
    DESKTOP_OBSERVER_PROVEN: bool = False
    STATUS_NATIVE_OBSERVED: bool = False

    def __post_init__(self):
        # This source-only type cannot be used as a native attestation builder.
        if any(type(v) is not bool for v in asdict(self).values()) or asdict(self) != dict(SOURCE_CANDIDATE=True, PACKAGED_RELEASE_PROVEN=False,
                SERVICE_INSTALLED_PROVEN=False, SIGNER_ENROLLED=False,
                DESKTOP_OBSERVER_PROVEN=False, STATUS_NATIVE_OBSERVED=False):
            deny()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest_map(value, keys=None):
    if (type(value) is not dict or not value or len(value) > 256
            or keys is not None and set(value) != set(keys)
            or any(type(k) is not str or type(v) is not str
                   or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in value.items())):
        deny()


def _relative(name):
    if (type(name) is not str or not 0 < len(name) <= 240
            or "\\" in name or ":" in name or name.startswith("/")
            or any(p in ("", ".", "..") for p in name.split("/"))
            or any(ord(c) < 32 for c in name)):
        deny()


def _tree(root: Path, names):
    """Hash actual regular bytes, refusing links and undeclared source paths."""
    root = Path(root).resolve(strict=True)
    result = {}
    for name in names:
        _relative(name)
        path = root / name
        if root not in path.resolve(strict=True).parents or not path.is_file():
            deny()
        for part in (path, *path.parents):
            if part == root:
                break
            stat = part.lstat()
            if (part.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400
                    or part.is_file() and stat.st_nlink != 1):
                deny()
        result[name] = sha256(path.read_bytes())
    return result


def _source_closure(root: Path):
    """Pin transitive repo imports too, including G2's HostOps/remote helpers.

    This conservative AST closure includes imports inside functions and package
    initializers. It does not claim to replace the packaging owner's locked
    dependency/resource analysis or independently observed bootloader inputs.
    """
    files = {p.relative_to(root).as_posix() for p in (root / "src/ai_commerce").rglob("*.py")}
    modules = {p[4:-3].replace("/", ".").removesuffix(".__init__"): p for p in files}
    pending, selected = list(SOURCE_CLOSURE), set()
    while pending:
        path = pending.pop()
        if path in selected:
            continue
        selected.add(path)
        module = path[4:-3].replace("/", ".")
        package = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
        names = set()
        for node in ast.walk(ast.parse((root / path).read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = (".".join(package.split(".")[:len(package.split(".")) - node.level + 1])
                          if node.level else "")
                name = ".".join(p for p in (prefix, node.module) if p)
                names.add(name)
                names.update(name + "." + alias.name for alias in node.names)
        names.add(module)
        for name in names:
            pieces = name.split(".")
            for i in range(1, len(pieces) + 1):
                found = modules.get(".".join(pieces[:i]))
                if found and found not in selected:
                    pending.append(found)
    return tuple(sorted(selected))


def build_specification(source_root: Path, toolchain: dict) -> dict:
    """Reviewable fixed Windows x64 build INPUT, never proof that a PE exists.

    The packaging owner must pin actual tool bytes and independently build in
    two clean Windows environments. Authenticode timestamps may differ: compare
    complete UNSIGNED bundles first, then separately pin the signed bundle.
    No onefile TEMP extraction, ambient Python, user site, PATH imports, service
    install command or request-controlled argument is permitted.
    """
    _digest_map(toolchain, BUILD_PINS)
    return dict(schema="WakeHubG4BuildSpec.v1", parent=SOURCE_PARENT, action=ACTION,
        attempt=ATTEMPT, service=broker.SERVICE, image=SERVICE_IMAGE,
        entrypoint="ai_commerce.wakehub.g4_service_transport.scm_entrypoint",
        desktop_entrypoint="ai_commerce.wakehub.g4_desktop_transport.desktop_entrypoint",
        sources=_tree(Path(source_root), _source_closure(Path(source_root))), toolchain=dict(toolchain),
        platform="WINDOWS_AMD64", layout="ADMIN_PROTECTED_ONEDIR_NO_EXTRACTION",
        python_isolated=True, user_site=False, arguments=[],
        reproducibility=dict(PYTHONHASHSEED="1", SOURCE_DATE_EPOCH="1794009600"),
        trust="SOURCE_SPECIFICATION_ONLY")


def _pe_readback(path: Path):
    """Structural rejection gate, NOT Authenticode or an executable test."""
    raw = path.read_bytes()
    if len(raw) < 512 or raw[:2] != b"MZ":
        deny()
    offset = struct.unpack_from("<I", raw, 0x3c)[0]
    if not 64 <= offset <= len(raw) - 264 or raw[offset:offset + 4] != b"PE\0\0":
        deny()
    machine, sections = struct.unpack_from("<HH", raw, offset + 4)
    size, flags = struct.unpack_from("<HH", raw, offset + 20)
    if (machine != 0x8664 or not 1 <= sections <= 96 or size < 240
            or offset + 24 + size + sections * 40 > len(raw)
            or not flags & 2 or flags & 0x2000
            or struct.unpack_from("<H", raw, offset + 24)[0] != 0x20b
            or struct.unpack_from("<H", raw, offset + 24 + 68)[0] != 3):
        deny()
    return dict(sha256=sha256(raw), machine="AMD64", subsystem="CONSOLE",
                trust="STRUCTURAL_CHECK_ONLY_NOT_SCM_OR_SIGNATURE_PROOF")


def verify_release_readback(spec: dict, manifest: dict, *, source_root: Path,
                            first_build: Path, second_build: Path, signed_build: Path) -> dict:
    """Verify exact ACTUAL source + two unsigned trees + final signed tree.

    Callers cannot supply a compiled=True shortcut. Every declared bundle file
    is read, every undeclared file fails, and unsigned bundles must match byte
    for byte. This never authenticates a signer or advances native readiness.
    """
    expected = build_specification(source_root, spec["toolchain"])
    if spec != expected:
        deny()
    shape(manifest, "schema spec_sha256 source_generation unsigned signed")
    if (manifest["schema"] != "WakeHubG4ReleaseManifest.v1"
            or manifest["spec_sha256"] != sha256(canonical(spec))
            or type(manifest["source_generation"]) is not int
            or not 1 <= manifest["source_generation"] < 2**53):
        deny()
    for field in ("unsigned", "signed"):
        _digest_map(manifest[field])
        if "WakeHubBroker.exe" not in manifest[field] or "WakeHubDesktop.exe" not in manifest[field]:
            deny()
        # Case aliases can address one file on Windows; do not accept them.
        if len({p.casefold() for p in manifest[field]}) != len(manifest[field]):
            deny()
    if set(manifest["unsigned"]) != set(manifest["signed"]):
        deny()
    for root, field in ((first_build, "unsigned"), (second_build, "unsigned"), (signed_build, "signed")):
        root = Path(root).resolve(strict=True)
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != set(manifest[field]) or _tree(root, actual) != manifest[field]:
            deny()
        for image in ("WakeHubBroker.exe", "WakeHubDesktop.exe"):
            _pe_readback(root / image)
    return dict(schema="WakeHubG4ArtifactReadback.v1", parent=SOURCE_PARENT,
        manifest_sha256=sha256(canonical(manifest)), spec_sha256=manifest["spec_sha256"],
        unsigned_bundle_sha256=sha256(canonical(manifest["unsigned"])),
        signed_bundle_sha256=sha256(canonical(manifest["signed"])),
        state="ARTIFACT_BYTES_MATCH_SOURCE_ONLY", readiness=asdict(SourceReadiness()))


def require_installed_release():
    """Production hard gate; no unimplemented trust check can default to PASS.

    Required owner work: approved frozen entrypoint/locked dependency closure,
    independently reproduced bundle, Authenticode publisher verification,
    admin-protected manifest/monotonic source-epoch readback, installed SCM
    readback and two independent signer custodies. None is available here.
    This is deliberately NOT a generic injected attest/readback callable.
    """
    raise PackagingOwnerEscalation()
