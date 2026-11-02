"""Generate versioned structural contracts; crypto/ACL semantics remain runtime checks."""
from pathlib import Path
import json

from ai_commerce.hostops_v3.contract import PARAMETERS, OPS, PUBLIC_FIELDS, STATUSES


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def schemas():
    string = {"type": "string"}
    identifier = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,64}$"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    source = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
    number = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
    base = {"schema": {"const": "hostops.request.v3"}, "request_id": identifier,
            "repository": {"const": "trung-via/ai-commerce"}, "issuer": identifier, "host_id": identifier,
            "release_id": sha, "source_sha": source, "created_at": number, "expires_at": number,
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            "preview": {"type": "boolean"}}
    variants = []
    for op in sorted(OPS):
        params = {}
        for key in PARAMETERS[op].split():
            if key.endswith("sha256") or key == "selection_id":
                params[key] = sha
            elif key in ("offset", "remove_bytes"):
                params[key] = {"type": "integer", "minimum": 0, "maximum": 67108864}
            elif key in ("limit", "length"):
                params[key] = {"type": "integer", "minimum": 1, "maximum": 256 if key == "limit" else 65536}
            elif key == "script_id":
                params[key] = {"const": "token_inventory_v1"}
            elif key == "classification":
                params[key] = {"enum": ["ordinary", "sensitive"]}
            elif key == "data_b64":
                params[key] = {"type": "string", "maxLength": 87384, "contentEncoding": "base64"}
            else:
                params[key] = {"type": "string", "minLength": 1, "maxLength": 256 if key == "needle" else 4096}
        variants.append(obj({**base, "operation": {"const": op}, "parameters": obj(params)}))
    request = {"oneOf": variants}
    lease = obj({"schema": {"const": "hostops.lease.v3"}, "lease_id": identifier,
        "issuer": identifier, "host_id": identifier, "release_id": sha,
        "roots": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4096}, "minItems": 1, "maxItems": 32},
        "operations": {"type": "array", "items": {"enum": sorted(OPS)}, "minItems": 1, "maxItems": 16, "uniqueItems": True},
        "not_before": number, "expires_at": number, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 67108864},
        "max_entries": {"type": "integer", "minimum": 1, "maximum": 4096}, "allow_unc": {"type": "boolean"}})
    signature = {"type": "string", "maxLength": 16384}
    env = obj({"schema": {"const": "hostops.envelope.v3"}, "request": {"$ref": "hostops-v3.request.schema.json"},
               "issuer_signature_b64": signature, "lease": {"$ref": "hostops-v3.lease.schema.json"},
               "lease_signature_b64": signature, "action_signature_b64": signature})
    fields = {}
    for k in sorted(PUBLIC_FIELDS):
        if k.endswith("sha256") or k == "selection_id":
            fields[k] = sha
        elif k in ("truncated", "reconciled", "exists", "is_directory", "non_elevated", "cross_volume", "local_only"):
            fields[k] = {"type": "boolean"}
        else:
            fields[k] = {"type": "integer", "minimum": 0, "maximum": 67108864}
    result = {"type": "object", "properties": fields, "additionalProperties": False}
    rec = obj({"schema": {"const": "hostops.receipt.v3"}, "request_ref": sha, "request_sha256": sha,
               "release_id": sha, "source_sha": source, "operation": {"enum": sorted(OPS | {"none"})},
               "status": {"enum": sorted(STATUSES)}, "error_code": {"type": "string", "pattern": "^[A-Z_]{2,64}$"}, "result": result})
    return {name: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": "https://ai-commerce.invalid/contracts/hostops-v3." + name + ".schema.json", **value}
            for name, value in (("request", request), ("lease", lease), ("envelope", env), ("receipt", rec))}


if __name__ == "__main__":
    for name, value in schemas().items():
        Path("contracts/hostops-v3." + name + ".schema.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
