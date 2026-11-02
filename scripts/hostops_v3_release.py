"""Build an UNSIGNED release manifest for independent Human review/signing.

No signing keys, installation, Git writes or network calls. The workflow digest
must be the exact reviewed integration bytes, not the moving product main SHA.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ai_commerce.hostops_v3.contract import canonical, digest, sha

MODULES = "__init__ contract auth filesystem journal worker security broker transport cli".split()
FILES = ["src/ai_commerce/hostops_v3/" + name + ".py" for name in MODULES] + [
    "src/ai_commerce/__init__.py", "scripts/HostOps-V3-Launch.py", "scripts/HostOps-V3-TokenInventory.ps1"]


def build(root, source_sha, workflow_digest):
    sha(source_sha, 40)
    sha(workflow_digest)
    body = {"schema": "hostops.release.v3", "source_sha": source_sha,
            "workflow_sha256": workflow_digest,
            "files": {name: digest((root / name).read_bytes()) for name in FILES}}
    return {**body, "release_id": digest(canonical(body))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args.root, args.source_sha, args.workflow_sha256)
    args.output.write_bytes(canonical(value))
    args.output.with_suffix(".to-sign.json").write_bytes(canonical({k: v for k, v in value.items() if k != "release_id"}))


if __name__ == "__main__":
    main()



def build_v4(root, source_sha, workflow_digest):
    """Unsigned v4 release; Human signs separate hostops-v4-release bytes."""
    from ai_commerce.hostops_v3.contract import SOURCE_REPOSITORY, CONTROL_REPOSITORY
    sha(source_sha, 40)
    sha(workflow_digest)
    body = {
        "schema": "hostops.release.v4",
        "source_repository": SOURCE_REPOSITORY,
        "control_repository": CONTROL_REPOSITORY,
        "source_sha": source_sha,
        "workflow_sha256": workflow_digest,
        "files": {name: digest((root / name).read_bytes()) for name in FILES},
    }
    return {**body, "release_id": digest(canonical(body))}
